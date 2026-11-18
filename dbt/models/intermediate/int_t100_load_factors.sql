-- Intermediate model: 3-tier domestic T-100 load factor hierarchy
-- (`CARRIER_ROUTE_MONTH_TYPE` -> `CARRIER_ROUTE_MONTH` -> `ROUTE_MONTH` -> `UNMATCHED`)
-- plus `t100_available` and `is_shared_regional_spoke` detection (D-17, D-21).

with flights as (
    select * from {{ ref('int_aircraft_enriched') }}
),

dom_t100 as (
    select
        year,
        month,
        op_carrier,
        origin,
        dest,
        bts_aircraft_type,
        is_ua_family,
        departures_performed,
        seats,
        passengers
    from {{ ref('stg_t100_segments') }}
    where data_source in ('DU', 'DF')
),

available_months as (
    select distinct
        year,
        month
    from dom_t100
),

l1_carrier_route_month_type as (
    select
        op_carrier,
        origin,
        dest,
        year,
        month,
        bts_aircraft_type,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf_l1
    from dom_t100
    group by op_carrier, origin, dest, year, month, bts_aircraft_type
    having sum(seats) > 0 and sum(departures_performed) > 0
),

l2_carrier_route_month as (
    select
        op_carrier,
        origin,
        dest,
        year,
        month,
        sum(departures_performed) as t100_deps_l2,
        case
            when sum(seats) > 0 and sum(departures_performed) > 0
                then {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0))))
            else cast(null as float64)
        end as lf_l2
    from dom_t100
    group by op_carrier, origin, dest, year, month
),

l3_route_month as (
    select
        origin,
        dest,
        year,
        month,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf_l3
    from dom_t100
    where is_ua_family = true
    group by origin, dest, year, month
    having sum(seats) > 0 and sum(departures_performed) > 0
),

otp_performed_counts as (
    select
        op_carrier,
        origin,
        dest,
        year,
        month,
        count(*) as otp_performed_flights
    from flights
    where not is_cancelled
    group by op_carrier, origin, dest, year, month
)

select
    f.*,
    (avail.year is not null) as t100_available,

    case
        when avail.year is null then 'UNMATCHED'
        when l1.lf_l1 is not null then 'CARRIER_ROUTE_MONTH_TYPE'
        when l2.lf_l2 is not null then 'CARRIER_ROUTE_MONTH'
        when l3.lf_l3 is not null then 'ROUTE_MONTH'
        else 'UNMATCHED'
    end as lf_source,

    case
        when avail.year is null then false
        when (
            f.op_carrier in ('{{ var("shared_cpa_carriers") | join("', '") }}')
            and not (f.op_carrier = 'ZW' and f.year = 2019)
            and coalesce(otp_cnt.otp_performed_flights, 0) > 0
            and coalesce(l2.t100_deps_l2, 0.0) > 1.10 * otp_cnt.otp_performed_flights
        ) then true
        else false
    end as is_shared_regional_spoke,

    case
        when avail.year is null then cast(null as float64)
        else coalesce(l1.lf_l1, l2.lf_l2, l3.lf_l3)
    end as est_load_factor
from flights as f
left join available_months as avail
    on f.year = avail.year
    and f.month = avail.month
left join l1_carrier_route_month_type as l1
    on f.op_carrier = l1.op_carrier
    and f.origin = l1.origin
    and f.dest = l1.dest
    and f.year = l1.year
    and f.month = l1.month
    and f.bts_aircraft_type = l1.bts_aircraft_type
left join l2_carrier_route_month as l2
    on f.op_carrier = l2.op_carrier
    and f.origin = l2.origin
    and f.dest = l2.dest
    and f.year = l2.year
    and f.month = l2.month
left join l3_route_month as l3
    on f.origin = l3.origin
    and f.dest = l3.dest
    and f.year = l3.year
    and f.month = l3.month
left join otp_performed_counts as otp_cnt
    on f.op_carrier = otp_cnt.op_carrier
    and f.origin = otp_cnt.origin
    and f.dest = otp_cnt.dest
    and f.year = otp_cnt.year
    and f.month = otp_cnt.month
