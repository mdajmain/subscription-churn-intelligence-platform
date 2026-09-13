# Data quality findings — step 1.3

## 1. Duplicate transactions
- Exact duplicate rows (all columns identical): **754**
- (msno, transaction_date) groups with >1 row (includes near-dupes and legitimate same-day multi-tx): **2,996**
- **Decision:** dedup key is the full row (all columns) — drop exact duplicates. Rows that share (msno, transaction_date) but differ in amount/method are NOT deduplicated; they are kept and disambiguated by transaction semantics (issue 4).

## 2. `bd` (age) outliers
- Negative values: **271**
- Values > 110: **310**
- Plausible range [10, 90]: **5,419** of 6,000
- **Decision:** treat `bd` outside [10, 90] as missing (set to NULL at the feature layer, carry a separate `bd_is_valid` flag). Do not drop the rows — age is not the join key and the rest of the member record is still usable.

## 3. `is_cancel` semantics
- Total `is_cancel=1` rows: **4,204**
- Of those, followed by a new (non-cancel) transaction within 7 days: **2,093** (49.8%)
- **Decision:** `is_cancel` is never used as the churn target. A cancel followed by a prompt resubscription is a plan change, not a lapse. The churn label (step 2.3) is defined purely on absence of a later membership-extending transaction.

## 4. Same-day transactions
- (msno, transaction_date) pairs with more than one transaction row: **2,996**
- **Decision:** when multiple rows share a transaction_date, the row with the latest `membership_expire_date` governs the effective expiry (mirrors 'last write wins' in the spell-reconstruction window function in step 2.1); a same-day `is_cancel=1` row is superseded by a same-day non-cancel row.

## 5. Zero-value transactions
- `actual_amount_paid = 0` rows: **6,397**
- Of those, not flagged as a cancel: **2,232** (trials/promotions)
- **Decision:** zero-value non-cancel transactions are kept as legitimate membership events (they still extend `membership_expire_date`); they are excluded from the revenue-exposure calculation in step 6.1 since they carry no renewal value signal.

**Addendum, found while building step 6.1 (not one of the original eight issues):** `actual_amount_paid = -1` rows: **35** in raw `transactions` (11 survive into `fct_prediction`), always paired with `is_cancel = 1`. Reads as a sentinel for "no payment on a cancellation" rather than a real negative amount. Same treatment as the zero-value case: excluded from revenue exposure (`mart_risk_exposure.excluded_nonpositive_value`), not clamped to zero — a cancel row's payment field was never a renewal-value estimate regardless of its sign.

## 6. Missing activity logs
- Users present in `transactions` with zero rows in `user_logs`: **637**
- **Decision:** absence of a log row is ambiguous (no activity vs. nothing recorded). A `has_activity_data` flag is carried at the feature layer; missing activity is never silently filled with zero.

## 7. Class balance over time (naive, pre-spell-reconstruction proxy)
| expire_month | n_transactions | naive_churn_rate |
|---|---|---|
| 2015-01 | 42 | 23.8% |
| 2015-02 | 328 | 27.1% |
| 2015-03 | 559 | 27.5% |
| 2015-04 | 774 | 34.5% |
| 2015-05 | 1,006 | 34.2% |
| 2015-06 | 1,142 | 32.5% |
| 2015-07 | 1,369 | 33.1% |
| 2015-08 | 1,501 | 34.0% |
| 2015-09 | 1,670 | 34.1% |
| 2015-10 | 1,815 | 34.8% |
| 2015-11 | 1,848 | 33.8% |
| 2015-12 | 1,975 | 36.0% |
| 2016-01 | 2,120 | 37.4% |
| 2016-02 | 2,145 | 35.9% |
| 2016-03 | 2,401 | 33.1% |
| 2016-04 | 2,493 | 35.8% |
| 2016-05 | 2,653 | 36.2% |
| 2016-06 | 2,543 | 37.6% |
| 2016-07 | 2,477 | 35.1% |
| 2016-08 | 2,419 | 38.8% |
| 2016-09 | 2,100 | 37.6% |
| 2016-10 | 2,071 | 36.8% |
| 2016-11 | 1,921 | 41.1% |
| 2016-12 | 1,807 | 37.7% |
| 2017-01 | 1,780 | 37.9% |
| 2017-02 | 1,526 | 38.9% |
| 2017-03 | 52 | 36.5% |

- **Note on censoring:** the reference date for "has enough time passed to observe a renewal" must be `max(transaction_date)` — when the data stops being collected — not `max(membership_expire_date)`. Long plans (410 days) push expiry a year past the last recorded transaction; using expiry as the reference right-censors every recent month to a false 100% churn. First run of this query showed exactly that (100% from 2017-04 onward) before the reference date was fixed.
- **Decision:** this is a rough pre-spell-reconstruction proxy (multiple transaction rows per user per month, not one row per membership spell) — used only to sanity-check that the rate doesn't move drastically month to month before committing to a time-based split. The authoritative rate comes from step 1.4 / 2.3 after spells and labels are built.

## 8. Feature availability at prediction time
Assessed by design, not by query — for each candidate feature in step 3.1, confirm no input row can be dated at or after the snapshot cutoff:

| Candidate feature | Source table | Available at cutoff? |
|---|---|---|
| Tenure, prior renewal/cancel counts | transactions | Yes — computed from rows strictly before cutoff |
| Current plan/price/auto-renew | transactions | Yes — the row that established the current spell, dated before cutoff |
| 7/30/90-day activity aggregates | user_logs | Yes, if windowed strictly to `date < cutoff` |
| Days since last activity | user_logs | Yes, same constraint |
| `bd`, gender, city | members | Yes — static, not time-varying |
- **Decision:** every feature query in step 3.1 filters on `< cutoff_date`, never `<=`, and this is enforced by an automated test rather than left to convention.
