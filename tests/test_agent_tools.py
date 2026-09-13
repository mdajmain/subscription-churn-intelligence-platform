"""
Step 8.1 tests: the four investigation-agent tools, against the real
database (no LLM involved — these test the tools, not the agent's
reasoning). Requires a live Postgres with sql/agent_views.sql and
sql/agent_grants.sql already applied.
"""

import psycopg2
import pytest

from agent.tools import (
    ToolError, ToolTrace, check_freshness, compare_segments,
    get_prediction_summary, query_metrics,
)


def _agent_role_available():
    try:
        psycopg2.connect(
            "host=localhost port=5432 dbname=churn user=agent_readonly password=agent_readonly_pw"
        ).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _agent_role_available(), reason="requires sql/agent_grants.sql applied to a live Postgres"
)


def test_agent_role_cannot_read_raw_tables():
    conn = psycopg2.connect("host=localhost port=5432 dbname=churn user=agent_readonly password=agent_readonly_pw")
    cur = conn.cursor()
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("select count(*) from fct_prediction")
    conn.close()


def test_query_metrics_monthly():
    trace = ToolTrace()
    result = query_metrics(trace, "active_subscribers", "2016-01-01", "2016-12-31")
    assert result["total"] > 0
    assert len(trace.calls) == 1
    assert trace.calls[0]["error"] is None


def test_query_metrics_segmented_rate():
    trace = ToolTrace()
    result = query_metrics(trace, "churn_rate", "2016-01-01", "2016-12-31", segment_column="payment_plan_days", segment_value=30)
    assert 0.0 <= result["value"] <= 1.0
    assert result["n"] > 0


def test_query_metrics_rejects_unsupported_segment_combo():
    trace = ToolTrace()
    with pytest.raises(ToolError):
        query_metrics(trace, "active_subscribers", "2016-01-01", "2016-12-31", segment_column="payment_plan_days", segment_value=30)
    assert trace.calls[0]["error"] is not None


def test_query_metrics_rejects_unknown_segment_column():
    trace = ToolTrace()
    with pytest.raises(ToolError):
        query_metrics(trace, "churn_rate", "2016-01-01", "2016-12-31", segment_column="favorite_color", segment_value="blue")


def test_query_metrics_rejects_partial_segment_args():
    trace = ToolTrace()
    with pytest.raises(ToolError):
        query_metrics(trace, "churn_rate", "2016-01-01", "2016-12-31", segment_column="payment_plan_days")


def test_compare_segments_plan_length():
    trace = ToolTrace()
    result = compare_segments(
        trace, "churn_rate",
        a_period_start="2016-01-01", a_period_end="2017-02-28", a_segment_column="payment_plan_days", a_segment_value=30,
        b_period_start="2016-01-01", b_period_end="2017-02-28", b_segment_column="payment_plan_days", b_segment_value=410,
    )
    assert result["n_a"] > 0 and result["n_b"] > 0
    # matches the known Week 2 finding: churn rises with plan length
    assert result["value_b"] > result["value_a"]
    assert result["cohens_d"] is not None


def test_compare_segments_across_periods():
    trace = ToolTrace()
    result = compare_segments(
        trace, "avg_predicted_risk",
        a_period_start="2016-01-01", a_period_end="2016-06-30",
        b_period_start="2017-01-01", b_period_end="2017-02-28",
    )
    assert result["n_a"] > 0 and result["n_b"] > 0


def test_compare_segments_insufficient_data_raises():
    trace = ToolTrace()
    with pytest.raises(ToolError):
        compare_segments(
            trace, "churn_rate",
            a_period_start="1999-01-01", a_period_end="1999-01-02",
            b_period_start="2016-01-01", b_period_end="2016-12-31",
        )


def test_check_freshness():
    trace = ToolTrace()
    result = check_freshness(trace)
    assert result["n_customers"] == 6000
    assert result["max_activity_date"] is not None
    assert 0.0 <= result["pct_customers_with_any_activity"] <= 1.0


def test_get_prediction_summary_returns_contributing_features():
    trace = ToolTrace()
    result = get_prediction_summary(trace, "2017-01-01", "2017-02-28")
    assert result["n"] > 0
    assert result["risk_distribution"]["p50"] is not None
    assert len(result["top_contributing_features_in_cohort"]) > 0
    assert "feature" in result["top_contributing_features_in_cohort"][0]


def test_query_metrics_revenue_exposure_no_ambiguous_column():
    """Regression test for step 8.2 eval prompt 8: an earlier version
    joined v_prediction_summary for avg_revenue_exposure even though
    v_segment_snapshot already carries that column, causing a real
    'column reference is ambiguous' error surfaced by the live agent."""
    trace = ToolTrace()
    result = query_metrics(trace, "avg_revenue_exposure", "2016-01-01", "2016-06-30")
    assert trace.calls[0]["error"] is None
    assert result["n"] > 0


def test_compare_segments_revenue_exposure_no_ambiguous_column():
    trace = ToolTrace()
    result = compare_segments(
        trace, "avg_revenue_exposure",
        a_period_start="2016-01-01", a_period_end="2016-06-30",
        b_period_start="2016-07-01", b_period_end="2016-12-31",
    )
    assert trace.calls[0]["error"] is None
    assert result["n_a"] > 0 and result["n_b"] > 0


def test_trace_accumulates_across_calls():
    trace = ToolTrace()
    check_freshness(trace)
    query_metrics(trace, "churn_rate", "2016-01-01", "2016-12-31")
    assert len(trace.calls) == 2
    assert {c["tool"] for c in trace.calls} == {"check_freshness", "query_metrics"}
