-- Staging model for BTS carrier lookup (L_UNIQUE_CARRIERS + deduplicated L_CARRIER_HISTORY).
-- Keyed strictly 1:1 on op_carrier with zero fan-out from multi-era L_CARRIER_HISTORY rows.

with unique_carriers as (
    select
        upper(trim(code)) as op_carrier,
        trim(description) as carrier_name,
        coalesce(nullif(lower(trim(confidence_tier)), ''), 'actual') as confidence_tier
    from {{ source('raw', 'bts_lookups') }}
    where lookup_table = 'L_UNIQUE_CARRIERS'
      and nullif(trim(code), '') is not null
    qualify row_number() over (partition by upper(trim(code)) order by trim(description)) = 1
),

carrier_history as (
    select
        upper(trim(code)) as op_carrier,
        string_agg(distinct trim(description), ' | ' order by trim(description)) as history_summary,
        count(*) as history_era_count
    from {{ source('raw', 'bts_lookups') }}
    where lookup_table = 'L_CARRIER_HISTORY'
      and nullif(trim(code), '') is not null
    group by upper(trim(code))
)

select
    u.op_carrier,
    u.carrier_name,
    ch.history_summary,
    coalesce(ch.history_era_count, 0) as history_era_count,
    u.confidence_tier
from unique_carriers as u
left join carrier_history as ch
    on u.op_carrier = ch.op_carrier
