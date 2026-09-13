-- Fails (returns rows) if (msno, spell_id) is not unique in fct_membership_spell.
select msno, spell_id, count(*)
from {{ ref('fct_membership_spell') }}
group by msno, spell_id
having count(*) > 1
