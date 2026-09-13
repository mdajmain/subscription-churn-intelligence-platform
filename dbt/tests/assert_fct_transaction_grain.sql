-- Fails (returns rows) if (msno, transaction_date) is not unique in fct_transaction.
select msno, transaction_date, count(*)
from {{ ref('fct_transaction') }}
group by msno, transaction_date
having count(*) > 1
