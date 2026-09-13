select
    msno,
    city,
    bd,
    bd_is_valid,
    gender,
    registered_via,
    registration_init_time
from {{ ref('stg_members') }}
