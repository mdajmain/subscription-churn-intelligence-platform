"""
Step 8.2 — a fixed set of ~10 investigation prompts, including two where
the data/tools genuinely cannot answer. Run with:
    python3 -m agent.eval_prompts
Writes agent/logs/eval_run.json (raw) and notebooks/13_agent_evaluation.md
(scored write-up, scored by hand against this raw output — see that
file's own note on why scoring isn't automated here).
"""

import json
from pathlib import Path

from agent.orchestrator import run_investigation

LOG_PATH = Path(__file__).resolve().parent / "logs" / "eval_run.json"

PROMPTS = [
    {
        "id": 1, "answerable": True,
        "question": "What was the churn rate for subscribers on 30-day plans in Q4 2016 (Oct-Dec)?",
        "expects": "one query_metrics call, metric=churn_rate, segment payment_plan_days=30",
    },
    {
        "id": 2, "answerable": True,
        "question": "Compare churn rate between 30-day and 410-day plans across all of 2016. Which is higher and by how much?",
        "expects": "compare_segments, metric=churn_rate; higher for 410-day plans per the known Week 2 finding",
    },
    {
        "id": 3, "answerable": True,
        "question": "How many active subscribers were there in June 2016, and how many new subscriptions started that month?",
        "expects": "one or two query_metrics calls, metrics=active_subscribers and new_subscriptions, no segment",
    },
    {
        "id": 4, "answerable": True,
        "question": "What's the current data freshness -- how recent is the activity data, and are there any subscribers with no activity data at all?",
        "expects": "check_freshness, no arguments",
    },
    {
        "id": 5, "answerable": True,
        "question": "Give me a risk and revenue-exposure summary for subscribers on 90-day plans in January-February 2017.",
        "expects": "get_prediction_summary, segment_column=payment_plan_days, segment_value=90",
    },
    {
        "id": 6, "answerable": True,
        "question": "Compare average predicted churn risk between male and female subscribers in 2016. Is there a meaningful difference?",
        "expects": "compare_segments, metric=avg_predicted_risk, segment_column=gender; small/negligible Cohen's d expected (gender wasn't a generator-designed risk driver)",
    },
    {
        "id": 7, "answerable": True,
        "question": "What are the top contributing features driving predicted churn for subscribers registered via channel 7, in Jan-Feb 2017?",
        "expects": "get_prediction_summary, segment_column=registered_via, segment_value=7",
    },
    {
        "id": 8, "answerable": True,
        "question": "Did average revenue exposure per subscriber change between the first half of 2016 and the second half of 2016?",
        "expects": "compare_segments, metric=avg_revenue_exposure, two periods, no segment",
    },
    {
        "id": 9, "answerable": False,
        "question": "Why did churn rise among subscribers who joined through our new referral program last quarter?",
        "expects": "insufficient evidence: no 'referral program' concept exists in the data (registered_via is numeric channel codes with no referral label), and there is no live 'today' for 'last quarter' to resolve against",
    },
    {
        "id": 10, "answerable": False,
        "question": "What was the churn rate for subscribers under 90 days of tenure in March 2017?",
        "expects": "insufficient evidence for two independent reasons: tenure is not a supported segment column, and March 2017 is beyond/at the edge of the reliable labeled window (2017-02)",
    },
]


def main():
    results = []
    for p in PROMPTS:
        print(f"[{p['id']}/{len(PROMPTS)}] {p['question']}")
        record = run_investigation(p["question"])
        results.append({**p, "record": record})
        print(f"  {record['num_tool_calls']} tool calls, log {record['log_path']}")

    LOG_PATH.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {LOG_PATH}")


if __name__ == "__main__":
    main()
