-- Conformed Carrier Dimension (`dim_carrier`).
-- Primary Key: `op_carrier` (UA mainline + United Express partners).

with configured_carriers as (
    select carrier_code as op_carrier
    from unnest(['{{ var("ua_family_carriers") | join("', '") }}']) as carrier_code
),

observed_carriers as (
    select distinct op_carrier from {{ ref('stg_otp_flights') }}
    union distinct
    select distinct op_carrier from {{ ref('stg_t100_segments') }} where is_ua_family = true
),

all_carriers as (
    select op_carrier from configured_carriers
    union distinct
    select op_carrier from observed_carriers
)

select
    c.op_carrier,
    lkp.carrier_name,
    if(c.op_carrier = upper('{{ var("marketing_carrier") }}'), 'MAINLINE', 'EXPRESS') as carrier_group,
    (c.op_carrier = upper('{{ var("marketing_carrier") }}')) as is_mainline,
    c.op_carrier in ('{{ var("shared_cpa_carriers") | join("', '") }}') as is_shared_cpa_carrier,
    lkp.history_summary,
    coalesce(lkp.history_era_count, 0) as history_era_count,
    'actual' as confidence_tier
from all_carriers as c
left join {{ ref('stg_bts_carriers') }} as lkp
    on c.op_carrier = lkp.op_carrier
