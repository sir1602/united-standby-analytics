-- Conformed Aircraft Dimension (`dim_aircraft`).
-- Primary Key: `tail_num` (1:1 tail -> FAA model / fleet subtype / seat config across multi-carrier regional tails).

with observed_tails as (
    select
        tail_num,
        tail_num_clean,
        aircraft_source,
        faa_manufacturer,
        faa_model,
        faa_tc_max_seats,
        fleet_subtype,
        bts_aircraft_type,
        seat_config_source,
        seats_config,
        seats_premium,
        seats_economy,
        flight_date,
        op_carrier
    from {{ ref('int_aircraft_enriched') }}
    where tail_num is not null
),

carrier_agg as (
    select
        tail_num,
        string_agg(distinct op_carrier, ',' order by op_carrier) as operating_carriers,
        count(distinct op_carrier) as operating_carrier_count,
        count(*) as observed_flights_count,
        min(flight_date) as first_observed_flight_date,
        max(flight_date) as last_observed_flight_date
    from observed_tails
    group by tail_num
),

deduped_specs as (
    select
        tail_num,
        tail_num_clean,
        aircraft_source,
        faa_manufacturer,
        faa_model,
        faa_tc_max_seats,
        fleet_subtype,
        bts_aircraft_type,
        seat_config_source,
        seats_config,
        seats_premium,
        seats_economy
    from observed_tails
    qualify row_number() over (
        partition by tail_num
        order by
            case aircraft_source
                when 'TAIL_OVERRIDE' then 1
                when 'FAA_MASTER' then 2
                when 'FAA_DEREG' then 3
                else 4
            end asc,
            flight_date desc
    ) = 1
),

seat_cfg as (
    select
        trim(subtype) as subtype,
        upper(trim(op_carrier_group)) as op_carrier_group,
        trim(source_url) as source_url,
        cast(as_of as date) as as_of
    from {{ ref('seat_config') }}
),

tail_ov as (
    select
        upper(trim(tail_num)) as tail_num,
        trim(source_url) as source_url,
        cast(as_of as date) as as_of,
        trim(override_reason) as override_reason
    from {{ ref('tail_overrides') }}
)

select
    d.tail_num,
    d.tail_num_clean,
    d.aircraft_source,
    d.faa_manufacturer,
    d.faa_model,
    d.faa_tc_max_seats,
    d.fleet_subtype,
    d.bts_aircraft_type,
    sc.op_carrier_group,
    d.seat_config_source,
    d.seats_config,
    d.seats_premium,
    d.seats_economy,
    ca.operating_carriers,
    ca.operating_carrier_count,
    ca.observed_flights_count,
    ca.first_observed_flight_date,
    ca.last_observed_flight_date,
    coalesce(ov.source_url, sc.source_url) as source_url,
    coalesce(ov.as_of, sc.as_of) as as_of,
    ov.override_reason,
    if(d.seats_config is not null, 'derived', 'actual') as confidence_tier
from deduped_specs as d
inner join carrier_agg as ca
    on d.tail_num = ca.tail_num
left join seat_cfg as sc
    on d.fleet_subtype = sc.subtype
left join tail_ov as ov
    on d.tail_num = ov.tail_num
