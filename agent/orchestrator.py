"""
Step 8.1 — the investigation agent's orchestration loop: an LLM (Claude,
via the Anthropic API, tool use) choosing among the four bounded tools in
agent/tools.py, with a hard cap on tool calls per investigation and a
full logged trace.

Built last, deliberately, per the guide's own reasoning: it depends on
the views in sql/agent_views.sql being stable, and the four tools were
built and tested (tests/test_agent_tools.py) against the real database
before this file existed.

The system prompt requires the final answer to separate three things —
data quality problems, observed changes, candidate explanations — and to
cite which tool call backs each claim. That separation is the actual
point of the exercise: collapsing them is how an agent turns a broken ETL
job into a confident story about customer behavior (BUILD-GUIDE.md step
8.1).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from agent.tools import ToolError, ToolTrace, check_freshness, compare_segments, get_prediction_summary, query_metrics

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent / "logs"
MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
MAX_TOOL_CALLS = 6
MAX_TOKENS = 2000

SYSTEM_PROMPT = """You are a read-only investigation agent over a subscription churn data warehouse.
You have exactly four tools, described below. You may call at most {max_calls} tools total in one investigation — you will be told when you've run out.

Ground rules:
- Every metric you cite must come from a tool call in this conversation. Never state a number you didn't get from a tool.
- segment_column only ever accepts payment_plan_days, city, gender, or registered_via — there is no tenure, activity, or revenue segment filter in any tool. If a question asks you to filter or segment by something these tools cannot express (e.g. "subscribers under 90 days of tenure", "high-activity users"), you MUST say explicitly, in "Data quality problems", that this dimension could not be filtered on and that the numbers you report are therefore for a broader population than asked about. Never silently compute the unfiltered number and describe it in your answer as if the filter had been applied — that is a false claim, worse than declining to answer.
- If the tools cannot answer the question (not enough data, wrong time range available, metric not supported), say so explicitly rather than guessing or extrapolating. Saying "the data doesn't support an answer to this" is a correct, complete answer when it's true.
- This is a synthetic dataset ending in 2018 — there is no "today"; freshness is relative to the max date present in the data, not the wall clock.
- Structure your final answer under exactly three headers, in this order:
  ## Data quality problems
  Anything about the data itself that affects how much to trust the numbers below (e.g. censored rows, a metric with too few observations, tools returning errors). Say "None found" if there's nothing to report.
  ## Observed changes
  What the tools actually measured — numbers, with their period and segment, and which tool call each came from (e.g. "call #2").
  ## Candidate explanations
  Hypotheses for WHY the observed changes happened. Label these clearly as hypotheses, not facts — nothing here should be stated with the same confidence as the "Observed changes" section. If you have no tool-grounded basis for even a hypothesis, say so instead of inventing one.

Available tools:
- query_metrics(metric, period_start, period_end, segment_column=None, segment_value=None): a defined metric over a date range, optionally filtered to one segment (segment_column and segment_value must be given together, or both omitted). metric is one of: active_subscribers, new_subscriptions, churn_rate, renewal_rate, avg_predicted_risk, avg_revenue_exposure. active_subscribers/new_subscriptions have NO segment breakdown available — calling them with a segment will error. segment_column, when used, is one of: payment_plan_days, city, gender, registered_via.
- compare_segments(metric, a_period_start, a_period_end, b_period_start, b_period_end, a_segment_column=None, a_segment_value=None, b_segment_column=None, b_segment_value=None): compares one metric (churn_rate, renewal_rate, avg_predicted_risk, avg_revenue_exposure only) between two groups (group A and group B, each its own period and optional segment), with an effect size (Cohen's d). All arguments are flat scalars — there is no nested "group" object. Use this instead of two separate query_metrics calls when you specifically need the comparison and effect size.
- check_freshness(): data recency and missingness — no arguments.
- get_prediction_summary(period_start, period_end, segment_column=None, segment_value=None): risk distribution (p10/p50/p90/mean), revenue exposure, and the most common top-contributing features for a cohort.

The data covers registrations from 2015-01 to 2016-06 and transactions through 2018-05; labeled (non-censored) outcomes are reliable only through 2017-02 (see README.md's split strategy) — a query asking about churn_rate in a period after that will return mostly-censored, unreliable results, which is itself a data quality problem worth reporting, not silently working around.
""".format(max_calls=MAX_TOOL_CALLS)

TOOL_SPECS = [
    {
        "name": "query_metrics",
        "description": "A defined metric for a period and optional single-column segment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": [
                    "active_subscribers", "new_subscriptions", "churn_rate",
                    "renewal_rate", "avg_predicted_risk", "avg_revenue_exposure",
                ]},
                "period_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "YYYY-MM-DD"},
                "segment_column": {"type": "string", "enum": ["payment_plan_days", "city", "gender", "registered_via"],
                                    "description": "Omit for no segment filter. If given, segment_value must also be given."},
                "segment_value": {"type": ["string", "number"], "description": "Required if segment_column is given."},
            },
            "required": ["metric", "period_start", "period_end"],
        },
    },
    {
        "name": "compare_segments",
        "description": "Compares one metric between two groups (segment and/or period), with Cohen's d. All fields are flat scalars, not nested objects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": [
                    "churn_rate", "renewal_rate", "avg_predicted_risk", "avg_revenue_exposure",
                ]},
                "a_period_start": {"type": "string", "description": "YYYY-MM-DD, group A"},
                "a_period_end": {"type": "string", "description": "YYYY-MM-DD, group A"},
                "a_segment_column": {"type": "string", "enum": ["payment_plan_days", "city", "gender", "registered_via"]},
                "a_segment_value": {"type": ["string", "number"]},
                "b_period_start": {"type": "string", "description": "YYYY-MM-DD, group B"},
                "b_period_end": {"type": "string", "description": "YYYY-MM-DD, group B"},
                "b_segment_column": {"type": "string", "enum": ["payment_plan_days", "city", "gender", "registered_via"]},
                "b_segment_value": {"type": ["string", "number"]},
            },
            "required": ["metric", "a_period_start", "a_period_end", "b_period_start", "b_period_end"],
        },
    },
    {
        "name": "check_freshness",
        "description": "Data recency and missingness — no arguments.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_prediction_summary",
        "description": "Risk distribution, revenue exposure, and top contributing features for a cohort.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string"},
                "period_end": {"type": "string"},
                "segment_column": {"type": "string", "enum": ["payment_plan_days", "city", "gender", "registered_via"]},
                "segment_value": {"type": ["string", "number"]},
            },
            "required": ["period_start", "period_end"],
        },
    },
]

TOOL_FUNCS = {
    "query_metrics": query_metrics,
    "compare_segments": compare_segments,
    "check_freshness": check_freshness,
    "get_prediction_summary": get_prediction_summary,
}


def _execute_tool(trace, name, args):
    func = TOOL_FUNCS.get(name)
    if func is None:
        # Seen in practice: a model can occasionally malform a nested-object
        # tool call into an extra, invalid tool_use block (see CLAUDE.md's
        # Week 8 entry). Report it as a normal tool error rather than
        # crashing the whole investigation on a KeyError.
        return {"error": f"unknown tool {name!r}; available tools: {sorted(TOOL_FUNCS)}"}
    try:
        return func(trace, **args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"malformed arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"unexpected tool failure: {e}"}


def _text_of(message) -> str:
    return "\n".join(block.text for block in message.content if block.type == "text")


def run_investigation(question: str, max_tool_calls: int = MAX_TOOL_CALLS, model: str = MODEL) -> dict:
    client = Anthropic()
    trace = ToolTrace()
    messages = [{"role": "user", "content": question}]

    n_calls = 0
    answer = None
    while True:
        out_of_budget = n_calls >= max_tool_calls
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=[] if out_of_budget else TOOL_SPECS,
        )
        messages.append({"role": "assistant", "content": resp.content})

        tool_use_blocks = [b for b in resp.content if b.type == "tool_use"]
        if not tool_use_blocks:
            answer = _text_of(resp)
            break

        if out_of_budget:
            answer = _text_of(resp) or "(tool call budget exhausted before a final answer was produced)"
            break

        tool_results = []
        for block in tool_use_blocks:
            if n_calls >= max_tool_calls:
                tool_results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": json.dumps({"error": f"tool call budget of {max_tool_calls} exhausted"}),
                })
                continue
            result = _execute_tool(trace, block.name, block.input)
            n_calls += 1
            tool_results.append({
                "type": "tool_result", "tool_use_id": block.id,
                "content": json.dumps(result, default=str),
            })
        messages.append({"role": "user", "content": tool_results})

    run_id = str(uuid.uuid4())[:8]
    record = {
        "run_id": run_id,
        "question": question,
        "model": model,
        "answer": answer,
        "num_tool_calls": n_calls,
        "tool_trace": trace.calls,
        "at": datetime.now(timezone.utc).isoformat(),
    }

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{run_id}.json"
    log_path.write_text(json.dumps(record, indent=2, default=str))
    record["log_path"] = str(log_path)
    return record
