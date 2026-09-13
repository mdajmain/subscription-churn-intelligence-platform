select
    msno,
    date,
    num_25,
    num_50,
    num_75,
    num_985,
    num_100,
    num_unq,
    total_secs
from {{ source('raw', 'user_logs') }}
