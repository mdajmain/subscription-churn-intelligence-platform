select
    msno,
    city,
    bd,
    (bd between 10 and 90) as bd_is_valid,
    nullif(gender, '') as gender,
    registered_via,
    registration_init_time
from {{ source('raw', 'members') }}
