# Step 8.2 — investigation agent evaluation

Ten fixed prompts (`agent/eval_prompts.py`), 8 answerable + 2 deliberately
unanswerable, run against the live agent (Claude, via the Anthropic API
tool-use loop in `agent/orchestrator.py`) and scored by hand against the
raw trace + answer in `agent/logs/eval_run.json`. **Scoring wasn't
automated** (no LLM-judge pass) — with only 10 prompts, checking each
tool-called number against an independent SQL query directly, as done
below, is both more reliable and about the same effort as building and
trusting a separate judge.

## Scoring criteria (from BUILD-GUIDE.md step 8.2)

1. **Correct tool selection** — did it call the tool(s) that actually answer the question?
2. **Arithmetic matches query results** — does every number in the final answer match what the trace's tool calls actually returned, and does the trace itself match an independent SQL recomputation?
3. **Every claim traceable** — does each factual claim cite or clearly derive from a specific tool call, with nothing stated that didn't come from a tool?
4. **Correct identification of insufficient evidence** — for the 2 unanswerable prompts, did it decline rather than guess?

## Results

| # | Question (abbreviated) | Tool selection | Arithmetic | Traceable | Insufficient evidence |
|---|---|---|---|---|---|
| 1 | churn_rate, 30-day plans, Q4 2016 | ✅ | ✅ (6.53%, n=3,490 — matches direct SQL exactly) | ✅ | n/a |
| 2 | compare churn_rate, 30d vs 410d plans, 2016 | ✅ | ✅ (7.02%/12.46%, n=17,030/634 — matches exactly) | ✅ | n/a |
| 3 | active subscribers + new subs, June 2016 | ✅ | ✅ (4,333 / 28 — matches exactly) | ✅ | n/a |
| 4 | data freshness + no-activity count | ✅ | ✅ (2017-03-30, 637/6,000 — matches exactly) | ✅ | n/a |
| 5 | risk + exposure summary, 90-day plans, Jan-Feb 2017 | ✅ | ✅ (p10/p50/p90/mean, exposure — all match direct SQL) | ✅ | n/a |
| 6 | compare avg_predicted_risk, male vs female, 2016 | ⚠️ (1 extra, unused `check_freshness` call) | ✅ (0.0910/0.0891, n=9,704/9,965 — matches exactly) | ✅ | n/a |
| 7 | top contributing features, registered_via=7, Jan-Feb 2017 | ✅ | ✅ (n=396 matches; feature list is a direct passthrough) | ✅ | n/a |
| 8 | compare avg_revenue_exposure, H1 vs H2 2016 | ✅ (after a bug fix — see below) | ✅ (6.18/5.27, n=12,308/11,066 — matches exactly) | ✅ | n/a |
| 9 | *(unanswerable)* churn among "referral program" joiners | ✅ (0 calls — correctly recognized nothing to query yet) | n/a | ✅ | ✅ asked for clarification instead of inventing a `registered_via` mapping |
| 10 | *(unanswerable)* churn rate, under-90-days tenure, March 2017 | ✅ (0 calls) | n/a | ✅ | ✅ named both real reasons (no tenure filter; March 2017 near/past the reliable-label boundary) |

**8/8 answerable prompts correct on tool selection, arithmetic, and traceability. 2/2 unanswerable prompts correctly identified as insufficient evidence, for the actual right reasons** (not just a generic refusal).

## Two real bugs this evaluation actually found

This is the point of writing an evaluation with fixed prompts and running
it for real rather than only unit-testing the tools in isolation — both
of these were found by prompts 6 and 8 exposing a live model against the
real tool surface, not by static review.

**Bug 1 (prompt 8): `avg_revenue_exposure` failed outright the first run.**
`agent/tools.py`'s `query_metrics`/`compare_segments` joined
`v_prediction_summary` for the `revenue_exposure_30d` column "just in
case," but `v_segment_snapshot` already carries that column directly —
the unqualified `avg(revenue_exposure_30d)` became ambiguous across the
two joined views, and the query errored on every call. The agent itself
handled the failure correctly (reported it under "Data quality problems"
rather than fabricating a number), but the tool itself was broken. Fixed
by dropping the unnecessary join; added
`test_query_metrics_revenue_exposure_no_ambiguous_column` and
`test_compare_segments_revenue_exposure_no_ambiguous_column` to
`tests/test_agent_tools.py` as regression tests, then reran the full
eval set to confirm the fix (prompt 8's table row above is post-fix).

**Bug 2 (found on an earlier, non-eval-set question): a silently dropped
segment filter.** Before this evaluation set was even run, an ad-hoc
question asking to filter by subscriber tenure — not a supported segment
column — got an answer that *described* the result as tenure-filtered
("subscribers with 30-day tenure") while the tool call underneath had
silently ignored the filter and computed the unfiltered population. This
is exactly the overclaiming failure mode step 8.1 warns an agent must
not have. Fixed by adding an explicit rule to the system prompt: if a
requested filter isn't expressible by `segment_column`'s enum, the agent
must say so under "Data quality problems," never silently compute a
broader number and describe it as filtered. Prompt 10 (post-fix) shows
this working correctly — the exact same tenure-filter request is now
correctly declined rather than silently answered wrong.

## What "correct tool selection" missed once

Prompt 6 made an extra, unused `check_freshness()` call before the
`compare_segments()` call that actually answered the question — not
wrong (nothing in the final answer relies on it, and it's within the
6-call budget), but not necessary either. Left as-is rather than
re-prompted around: a system prompt that successfully suppresses every
occasionally-unnecessary call would likely also suppress some legitimate
exploratory ones, which isn't obviously a good trade for an investigation
agent whose job includes exploring the data. Consistent with the
guide's B/C sequence-model framing (README.md, Week 5): an imperfect but
transparent, fully-logged result is worth more than one polished by
retrying until it looks clean.

## Bounded calls and logging, confirmed working

Every run in this evaluation stayed well within the 6-tool-call budget
(`MAX_TOOL_CALLS` in `agent/orchestrator.py`) — the largest was 2 calls.
The budget-exhaustion path (forcing a final answer once the cap is hit)
was not exercised by these 10 prompts, since none needed more than 2
calls; it's covered structurally in the orchestrator's loop (`tools=[]`
once `out_of_budget`) but not empirically demonstrated here. Every call
in every run is in `agent/logs/<run_id>.json`, including the full text
of every claim in the final answer traced back to specific tool-call
arguments and results — that's what made the arithmetic verification in
this evaluation possible to do in the first place.
