-- Step 3.1's automated cutoff test, as a dbt test: fails (returns rows) if
-- any fct_prediction row's last recorded activity is not strictly before
-- its own cutoff_date, or if tenure is negative (cutoff before
-- registration — would imply a row is anchored to a date that couldn't
-- have produced the member record it's joined to).
--
-- This is the structural, always-on counterpart to
-- tests/test_no_leakage.py (outside dbt), which additionally proves this
-- check isn't vacuous by showing a deliberately leaky recomputation would
-- disagree with the stored values on real rows.
select msno, cutoff_date, last_activity_date, tenure_since_registration_days
from {{ ref('fct_prediction') }}
where (last_activity_date is not null and last_activity_date >= cutoff_date)
   or tenure_since_registration_days < 0
