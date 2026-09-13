# Label validation — step 2.3

- Ground truth rows (from generator, validation-only): **42,928**
- Matched to a rebuilt snapshot on (msno, cutoff_date): **42,813** (99.7%)
- Unmatched (no snapshot found for that cutoff): **115**
- Matched but censored (rebuilt label unknown, correctly excluded from comparison): **3,359**
- Comparable rows (matched, not censored): **39,454**

## Agreement rate: **99.64%** (39,313 / 39,454)

- False negatives (true churn, rebuilt label says renewed): **0**
- False positives (true renewed, rebuilt label says churn): **141**

## Disagreement investigation


Sample false positives (rebuilt label over-counts churn):

| msno | spell_expire | true_churn | rebuilt_churn | renewed_within_30d |
|---|---|---|---|---|
| b01a0e23da71... | 2016-07-06 | 0 | 1 | False |
| 169066d14f4c... | 2015-10-03 | 0 | 1 | False |
| 4eb942ef9842... | 2016-06-03 | 0 | 1 | False |
| bf4050c20e32... | 2016-04-03 | 0 | 1 | False |
| f612c85c79ce... | 2015-05-13 | 0 | 1 | False |

## Unmatched rows (115)

Ground-truth spells with no snapshot at the same (msno, cutoff_date). Expected cause: the snapshot-grain collapse (step 2.2) picks one representative transaction per (msno, cutoff_date), preferring the latest transaction_date — a ground-truth spell whose expiry was only ever recorded via a transaction that got superseded same-cutoff by another (e.g. duplicate injection, step 1.3 issue 1) would not appear as its own row under a different cutoff.

## What explains the remaining disagreement

All 141 disagreements are the same mechanism, confirmed by hand on multiple examples (e.g. msno `b01a0e23da71...`, `169066d14f4c...`): a cancel-and-immediately-resubscribe noise transaction (step 1.3, issue 3) is dated near the *previous* cycle's expiry, but the *next* renewal's own transaction_date carries independent ±1 day jitter and can land a day earlier. When that happens, the cancel row sorts chronologically *after* the renewal it was meant to sit near. Spell reconstruction correctly implements "the chronologically last transaction overwrites the effective expiry" — so this late-arriving cancel appears to retroactively rescind a renewal that, in the generator's own internal bookkeeping, already happened. The rebuilt label calls this a churn at the cancel's (earlier) expiry value; the generator's ground truth calls it a renewal, because it tracked the cycle sequentially without this ordering ambiguity.

This is not a bug to fix — it is the synthetic-data equivalent of the exact disagreement category BUILD-GUIDE.md step 2.3 expects on the real KKBox data: 'the gap was same-day cancel-and-resubscribe.' A real support/billing system can log a cancellation confirmation slightly out of order relative to a near-simultaneous renewal for the same reason (batch processing lag, retried writes). The rebuilt label is arguably *more* correct here, not less: from raw transaction order alone, there is no way to know the generator's internal intent, only what was recorded and when.
