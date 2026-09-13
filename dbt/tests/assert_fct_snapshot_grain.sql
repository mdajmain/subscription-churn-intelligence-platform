-- Fails (returns rows) if (msno, cutoff_date) is not unique in fct_snapshot.
-- This exact check caught a real bug during development (3,462 duplicate
-- groups from a cancel/renewal pair sharing a cutoff on different
-- transaction_dates) — kept here permanently as a regression test.
select msno, cutoff_date, count(*)
from {{ ref('fct_snapshot') }}
group by msno, cutoff_date
having count(*) > 1
