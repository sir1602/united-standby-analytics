-- Staging model for BTS T-100 aircraft type lookup (L_AIRCRAFT_TYPE).

select
    trim(cast(code as string)) as bts_aircraft_type,
    trim(description) as bts_aircraft_description,
    coalesce(nullif(lower(trim(confidence_tier)), ''), 'actual') as confidence_tier
from {{ source('raw', 'bts_lookups') }}
where lookup_table = 'L_AIRCRAFT_TYPE'
  and nullif(trim(cast(code as string)), '') is not null
qualify row_number() over (partition by trim(cast(code as string)) order by trim(description)) = 1
