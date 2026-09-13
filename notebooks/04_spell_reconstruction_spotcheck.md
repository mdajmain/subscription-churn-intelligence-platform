# Spell reconstruction — manual spot check (step 2.1)

`sql/build_spells.py` builds `fct_membership_spell` (6,381 spells across
6,000 users — most users have exactly one continuous spell; ~6% have two or
more, from the simulated reactivation-after-churn path).

Spot-checked all 10 sampled users' raw transaction histories against the
reconstructed spells by hand (full output: run `python sql/spotcheck_spells.py`).
Three worked examples:

## `594cfb30a887914166212354910f3a0d` — same-day collapse + real gap

22 raw transaction rows, including a same-day cancel/renewal pair on
2015-09-14 and another on 2017-02-28. After collapsing same-day rows to the
one with the latest `membership_expire_date` (step 1.3 decision), the
gap between 2016-02-11 (spell 1's last expiry) and the next transaction on
2016-05-04 is 83 days — correctly split into two spells:

- Spell 1: 2015-08-16 → 2016-02-11, 7 transactions, had_cancel=True
- Spell 2: 2016-05-04 → 2017-04-29, 13 transactions, had_cancel=True

## `6989cfed57c4c78e4d40d1359820f659` — clean short spells

Two short one-month memberships nine months apart, each with a same-cycle
cancel that doesn't reduce the expiry (cancel's own `membership_expire_date`
matches the standing value). Both correctly reconstructed as separate
2-transaction spells.

## `569805d9330031c7d013d88b23dc582f` — spell correctly ends on a cancel

8 transactions, the last one an `is_cancel=1` row with no transaction
following it. The spell's `spell_expire` correctly takes this last row's own
`membership_expire_date` (2016-01-11) rather than an earlier higher value
seen mid-spell, because the SQL takes the **chronologically last**
transaction's expiry, not `MAX(membership_expire_date)` — an easy mistake:
`MAX` would silently ignore a cancel that reduces coverage below a value
already seen earlier in the spell.

## Result

All 10 users reconstruct correctly — no bug found in this pass, unlike the
one the guide warns about (that one already surfaced one step earlier, in
step 1.3/1.4's censoring bug).

## Known limitation of the synthetic generator

In this dataset, `is_cancel=1` rows always carry the *same*
`membership_expire_date` as the spell's standing value — the generator
never produces a cancel that reduces expiry to a date **strictly earlier**
than an already-higher value seen earlier in the spell. So while the SQL is
written to handle that case correctly (by construction: it takes the last
value in transaction-date order, not a max), this synthetic dataset never
actually exercises it. Noted honestly rather than claimed as tested.
