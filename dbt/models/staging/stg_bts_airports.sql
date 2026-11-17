-- Staging model for BTS IATA airport lookup (L_AIRPORT + L_AIRPORT_ID).
-- Keyed cleanly on airport_code (IATA) so airport_id=14027 (shared by PBI and DJT)
-- never causes a duplicate PK collision.

with airports as (
    select
        upper(trim(code)) as airport_code,
        trim(description) as airport_name,
        coalesce(nullif(lower(trim(confidence_tier)), ''), 'actual') as confidence_tier
    from {{ source('raw', 'bts_lookups') }}
    where lookup_table = 'L_AIRPORT'
      and nullif(trim(code), '') is not null
    qualify row_number() over (partition by upper(trim(code)) order by trim(description)) = 1
),

airport_ids as (
    select
        safe_cast(trim(code) as int64) as airport_id,
        trim(description) as airport_name
    from {{ source('raw', 'bts_lookups') }}
    where lookup_table = 'L_AIRPORT_ID'
      and safe_cast(trim(code) as int64) is not null
    qualify row_number() over (partition by safe_cast(trim(code) as int64) order by trim(description)) = 1
)

select
    a.airport_code,
    ids.airport_id,
    a.airport_name,
    a.confidence_tier
from airports as a
left join airport_ids as ids
    on lower(a.airport_name) = lower(ids.airport_name)
qualify row_number() over (partition by a.airport_code order by ids.airport_id desc nulls last) = 1
