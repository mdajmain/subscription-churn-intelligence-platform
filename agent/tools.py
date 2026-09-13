"""
Step 8.1 — the investigation agent's four read-only tools. Every function
here:
  1. connects as `agent_readonly` (sql/agent_grants.sql) -- a role with
     SELECT on the four views in sql/agent_views.sql and nothing else,
     enforced by Postgres itself, not by this code choosing to behave.
  2. sets an explicit per-query statement_timeout, on top of the role-level
     default set in agent_grants.sql -- two independent layers, not one.
  3. picks from a fixed, named set of metrics/segments rather than
     accepting free-text SQL or column names from the LLM -- the model
     chooses arguments from an enum, this module is what turns that into
     a query, so a hallucinated column name fails a Python-level
     validation, not a database call.
  4. logs a structured trace entry (tool name, args, row count, duration,
     timestamp) via log_call() -- every claim the agent's final report
     makes must trace back to one of these entries (step 8.1's "Done
     when").
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import psycopg2
import psycopg2.extras

DB_DSN = "host=localhost port=5432 dbname=churn user=agent_readonly password=agent_readonly_pw"
QUERY_TIMEOUT_MS = 5000

METRIC_CATALOG = {
    "active_subscribers": {"source": "monthly", "column": "n_active_subscribers"},
    "new_subscriptions": {"source": "monthly", "column": "n_new_spells"},
    "churn_rate": {"source": "segment", "kind": "rate", "numerator": "churn"},
    "renewal_rate": {"source": "segment", "kind": "rate", "numerator": "(1 - churn)"},
    "avg_predicted_risk": {"source": "segment", "kind": "avg", "column": "calibrated_churn_probability"},
    "avg_revenue_exposure": {"source": "segment", "kind": "avg", "column": "revenue_exposure_30d"},
}
SEGMENT_COLUMNS = {"payment_plan_days", "city", "gender", "registered_via"}


class ToolError(Exception):
    """Raised for a caller mistake (bad metric name, bad segment column) --
    distinct from a database/timeout error, so the agent can tell 'you
    asked for something not supported' from 'the query itself failed'."""


@dataclass
class ToolTrace:
    calls: list = field(default_factory=list)

    def log_call(self, tool_name, args, row_count, duration_s, error=None):
        self.calls.append({
            "tool": tool_name, "args": args, "row_count": row_count,
            "duration_s": round(duration_s, 4), "error": error,
            "at": datetime.now(timezone.utc).isoformat(),
        })


def _connect():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS}")
    return conn


def _validate_segment(segment_column, segment_value):
    """Segments are passed as two flat scalar args (segment_column,
    segment_value), never a nested {column: value} object -- a nested
    object here was empirically found to make tool-call argument
    generation unreliable (see CLAUDE.md's Week 8 entry: a compare_segments
    call with a nested group_a.segment object came back malformed, split
    across two tool_use blocks). Flat scalars are simpler for a model to
    fill in correctly and simpler for this code to validate."""
    if segment_column is None and segment_value is None:
        return None
    if segment_column is None or segment_value is None:
        raise ToolError("segment_column and segment_value must both be given, or both omitted")
    if segment_column not in SEGMENT_COLUMNS:
        raise ToolError(f"unsupported segment column {segment_column!r}; allowed: {sorted(SEGMENT_COLUMNS)}")
    return segment_column, segment_value


def query_metrics(trace: ToolTrace, metric: str, period_start: str, period_end: str,
                   segment_column: str = None, segment_value=None):
    """A defined metric for a period and optional segment. `metric` must be
    one of METRIC_CATALOG's keys -- there is no free-text metric."""
    t0 = time.perf_counter()
    segment = {segment_column: segment_value} if segment_column else None
    args = {"metric": metric, "period_start": period_start, "period_end": period_end,
            "segment_column": segment_column, "segment_value": segment_value}
    try:
        if metric not in METRIC_CATALOG:
            raise ToolError(f"unknown metric {metric!r}; allowed: {sorted(METRIC_CATALOG)}")
        spec = METRIC_CATALOG[metric]
        seg = _validate_segment(segment_column, segment_value)

        if spec["source"] == "monthly":
            if seg is not None:
                raise ToolError(
                    f"metric {metric!r} has no segment breakdown available (only v_monthly_metrics, "
                    "which is not segmented) -- ask for churn_rate, renewal_rate, avg_predicted_risk, "
                    "or avg_revenue_exposure instead if a segment cut is needed."
                )
            conn = _connect()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"""
                SELECT month, {spec['column']} AS value FROM v_monthly_metrics
                WHERE month >= %s AND month <= %s ORDER BY month
            """, (period_start, period_end))
            rows = cur.fetchall()
            conn.close()
            result = {
                "metric": metric, "period": [period_start, period_end], "segment": segment,
                "by_month": rows,
                "total": sum(r["value"] for r in rows if r["value"] is not None),
            }
        else:
            where = ["cutoff_date >= %s", "cutoff_date <= %s", "label_censored = false"]
            params = [period_start, period_end]
            if seg is not None:
                where.append(f"{seg[0]} = %s")
                params.append(seg[1])
            where_clause = " AND ".join(where)

            conn = _connect()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if spec["kind"] == "rate":
                cur.execute(f"""
                    SELECT count(*) AS n, avg({spec['numerator']}::float) AS value
                    FROM v_segment_snapshot WHERE {where_clause}
                """, params)
            else:
                # v_segment_snapshot already carries both calibrated_churn_probability
                # and revenue_exposure_30d directly (see sql/agent_views.sql) -- no
                # join to v_prediction_summary needed. An earlier version joined it
                # anyway "just in case" and that's exactly what caused a real bug
                # (step 8.2's eval prompt 8): both views share the column name, so
                # the unqualified avg(revenue_exposure_30d) became ambiguous.
                cur.execute(f"""
                    SELECT count(*) AS n, avg({spec['column']}) AS value
                    FROM v_segment_snapshot WHERE {where_clause}
                """, params)
            row = cur.fetchone()
            conn.close()
            result = {
                "metric": metric, "period": [period_start, period_end], "segment": segment,
                "n": row["n"], "value": row["value"],
            }

        trace.log_call("query_metrics", args, row_count=result.get("n", len(result.get("by_month", []))), duration_s=time.perf_counter() - t0)
        return result
    except Exception as e:
        trace.log_call("query_metrics", args, row_count=0, duration_s=time.perf_counter() - t0, error=str(e))
        raise


def compare_segments(trace: ToolTrace, metric: str,
                      a_period_start: str, a_period_end: str,
                      b_period_start: str, b_period_end: str,
                      a_segment_column: str = None, a_segment_value=None,
                      b_segment_column: str = None, b_segment_value=None):
    """A metric across two segments or periods, with an effect size. Each
    group's period + segment are flat scalar args (a_*/b_* prefixed)
    rather than a nested group_a/group_b object -- see _validate_segment's
    docstring for why nesting was dropped."""
    t0 = time.perf_counter()
    args = {
        "metric": metric,
        "a_period_start": a_period_start, "a_period_end": a_period_end,
        "a_segment_column": a_segment_column, "a_segment_value": a_segment_value,
        "b_period_start": b_period_start, "b_period_end": b_period_end,
        "b_segment_column": b_segment_column, "b_segment_value": b_segment_value,
    }
    try:
        if metric not in ("churn_rate", "renewal_rate", "avg_predicted_risk", "avg_revenue_exposure"):
            raise ToolError(f"compare_segments only supports rate/avg metrics with row-level data, got {metric!r}")

        def fetch_raw(period_start, period_end, segment_column, segment_value):
            seg = _validate_segment(segment_column, segment_value)
            where = ["cutoff_date >= %s", "cutoff_date <= %s"]
            params = [period_start, period_end]
            spec = METRIC_CATALOG[metric]
            if spec["source"] == "segment" and spec["kind"] == "rate":
                where.append("label_censored = false")
            if seg is not None:
                where.append(f"{seg[0]} = %s")
                params.append(seg[1])
            where_clause = " AND ".join(where)
            col = spec["numerator"] if spec["kind"] == "rate" else spec["column"]
            # Same fix as query_metrics above: v_segment_snapshot already has this
            # column directly, no join needed (and joining caused an ambiguous-column bug).
            conn = _connect()
            cur = conn.cursor()
            cur.execute(f"""
                SELECT {col}::float FROM v_segment_snapshot WHERE {where_clause}
            """, params)
            vals = [r[0] for r in cur.fetchall() if r[0] is not None]
            conn.close()
            return vals

        import statistics
        vals_a = fetch_raw(a_period_start, a_period_end, a_segment_column, a_segment_value)
        vals_b = fetch_raw(b_period_start, b_period_end, b_segment_column, b_segment_value)
        if not vals_a or not vals_b:
            raise ToolError(f"one of the groups has no matching rows (n_a={len(vals_a)}, n_b={len(vals_b)}) -- cannot compute an effect size")

        mean_a, mean_b = statistics.mean(vals_a), statistics.mean(vals_b)
        # Pooled-SD Cohen's d -- same effect-size convention used throughout
        # this project (notebooks/06_cohort_retention.md's activity-decay finding).
        var_a = statistics.pvariance(vals_a) if len(vals_a) > 1 else 0.0
        var_b = statistics.pvariance(vals_b) if len(vals_b) > 1 else 0.0
        n_a, n_b = len(vals_a), len(vals_b)
        pooled_sd = (((n_a - 1) * var_a + (n_b - 1) * var_b) / max(n_a + n_b - 2, 1)) ** 0.5
        cohens_d = (mean_a - mean_b) / pooled_sd if pooled_sd > 0 else None

        result = {
            "metric": metric,
            "group_a": {"period": [a_period_start, a_period_end], "segment_column": a_segment_column, "segment_value": a_segment_value},
            "group_b": {"period": [b_period_start, b_period_end], "segment_column": b_segment_column, "segment_value": b_segment_value},
            "n_a": n_a, "n_b": n_b, "value_a": mean_a, "value_b": mean_b,
            "absolute_difference": mean_a - mean_b,
            "cohens_d": cohens_d,
        }
        trace.log_call("compare_segments", args, row_count=n_a + n_b, duration_s=time.perf_counter() - t0)
        return result
    except Exception as e:
        trace.log_call("compare_segments", args, row_count=0, duration_s=time.perf_counter() - t0, error=str(e))
        raise


def check_freshness(trace: ToolTrace):
    """Data recency and missingness. Freshness is reported relative to the
    max date actually present in the activity log (this is a static
    historical dataset, not a live feed — comparing to wall-clock today()
    would misreport every check as "stale by years")."""
    t0 = time.perf_counter()
    try:
        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                max(last_activity_date) AS max_activity_date,
                count(*) AS n_customers,
                count(*) FILTER (WHERE has_no_activity_ever) AS n_customers_no_activity,
                avg((last_activity_date IS NOT NULL)::int)::float AS pct_with_any_activity
            FROM v_activity_freshness
        """)
        row = cur.fetchone()

        cur.execute("SELECT max(cutoff_date) AS max_snapshot_date, min(cutoff_date) AS min_snapshot_date FROM v_segment_snapshot")
        snap_row = cur.fetchone()
        conn.close()

        result = {
            "max_activity_date": str(row["max_activity_date"]) if row["max_activity_date"] else None,
            "n_customers": row["n_customers"],
            "n_customers_no_activity": row["n_customers_no_activity"],
            "pct_customers_with_any_activity": round(row["pct_with_any_activity"], 4),
            "min_snapshot_date": str(snap_row["min_snapshot_date"]),
            "max_snapshot_date": str(snap_row["max_snapshot_date"]),
            "note": "Freshness is relative to the max date present in this static dataset, not wall-clock time.",
        }
        trace.log_call("check_freshness", {}, row_count=row["n_customers"], duration_s=time.perf_counter() - t0)
        return result
    except Exception as e:
        trace.log_call("check_freshness", {}, row_count=0, duration_s=time.perf_counter() - t0, error=str(e))
        raise


def get_prediction_summary(trace: ToolTrace, period_start: str, period_end: str,
                            segment_column: str = None, segment_value=None):
    """Risk distribution + exposure + aggregate feature contributions for a cohort."""
    t0 = time.perf_counter()
    segment = {segment_column: segment_value} if segment_column else None
    args = {"period_start": period_start, "period_end": period_end,
            "segment_column": segment_column, "segment_value": segment_value}
    try:
        seg = _validate_segment(segment_column, segment_value)
        where = ["cutoff_date >= %s", "cutoff_date <= %s"]
        params = [period_start, period_end]
        if seg is not None:
            where.append(f"{seg[0]} = %s")
            params.append(seg[1])
        where_clause = " AND ".join(where)

        conn = _connect()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT
                count(*) AS n,
                percentile_cont(0.10) WITHIN GROUP (ORDER BY calibrated_churn_probability) AS p10,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY calibrated_churn_probability) AS p50,
                percentile_cont(0.90) WITHIN GROUP (ORDER BY calibrated_churn_probability) AS p90,
                avg(calibrated_churn_probability) AS mean_risk,
                avg(revenue_exposure_30d) AS mean_exposure,
                sum(revenue_exposure_30d) FILTER (WHERE revenue_exposure_30d IS NOT NULL) AS total_exposure,
                count(*) FILTER (WHERE revenue_exposure_30d IS NOT NULL) AS n_with_exposure
            FROM v_prediction_summary WHERE {where_clause}
        """, params)
        stats = cur.fetchone()

        cur.execute(f"""
            SELECT top_contributing_features FROM v_prediction_summary
            WHERE {where_clause} AND top_contributing_features IS NOT NULL
        """, params)
        raw_rows = cur.fetchall()
        conn.close()

        from collections import defaultdict
        feature_hits = defaultdict(list)
        for r in raw_rows:
            for part in r["top_contributing_features"].split(", "):
                if ":" not in part:
                    continue
                name, val = part.rsplit(":", 1)
                try:
                    feature_hits[name].append(float(val))
                except ValueError:
                    continue
        top_features = sorted(
            ({"feature": k, "n_appearances": len(v), "avg_contribution": sum(v) / len(v)} for k, v in feature_hits.items()),
            key=lambda x: -x["n_appearances"],
        )[:5]

        result = {
            "period": [period_start, period_end], "segment": segment,
            "n": stats["n"],
            "risk_distribution": {"p10": stats["p10"], "p50": stats["p50"], "p90": stats["p90"], "mean": stats["mean_risk"]},
            "exposure": {
                "mean_per_subscriber": stats["mean_exposure"], "total": stats["total_exposure"],
                "n_with_defined_exposure": stats["n_with_exposure"],
            },
            "top_contributing_features_in_cohort": top_features,
        }
        trace.log_call("get_prediction_summary", args, row_count=stats["n"], duration_s=time.perf_counter() - t0)
        return result
    except Exception as e:
        trace.log_call("get_prediction_summary", args, row_count=0, duration_s=time.perf_counter() - t0, error=str(e))
        raise
