{{
    config(
        materialized='table',
        cluster_by=['origin', 'dest', 'op_carrier'],
    )
}}

-- Route x Month x Operating Carrier Aggregate Mart (`agg_route_month`).
-- Covers BOTH Domestic (DU/DF) and International (IU/IF) BTS T-100 segments for the
-- United Airlines carrier family, left-joined to monthly domestic OTP metrics.
-- International routes are clearly labeled `is_international = true` and
-- `data_granularity = 'route_month_only_v1'` per D-04.

with t100_agg as (
    select
        year,
        min(quarter) as quarter,
        month,
        op_carrier,
        origin,
        max(origin_country) as origin_country,
        dest,
        max(dest_country) as dest_country,
        is_international,
        sum(departures_scheduled) as departures_scheduled,
        sum(departures_performed) as departures_performed,
        sum(seats) as seats,
        sum(passengers) as passengers,
        coalesce(
            safe_divide(sum(distance_miles * departures_performed), nullif(sum(departures_performed), 0.0)),
            max(distance_miles)
        ) as distance_miles,
        count(distinct bts_aircraft_type) as aircraft_type_count
    from {{ ref('stg_t100_segments') }}
    where is_ua_family = true
    group by
        year,
        month,
        op_carrier,
        origin,
        dest,
        is_international
),

otp_monthly as (
    select
        year,
        month,
        op_carrier,
        origin,
        dest,
        false as is_international,
        count(*) as otp_scheduled_flights,
        countif(not is_cancelled) as otp_performed_flights,
        countif(is_cancelled) as otp_cancelled_flights,
        countif(is_diverted) as otp_diverted_flights,
        avg(if(not is_cancelled, dep_delay_min, cast(null as float64))) as otp_avg_dep_delay_min,
        avg(if(not is_cancelled and not is_diverted, arr_delay_min, cast(null as float64))) as otp_avg_arr_delay_min,
        avg(cast(est_open_seats as float64)) as otp_avg_est_open_seats,
        avg(cast(est_open_seats_adjusted as float64)) as otp_avg_est_open_seats_adjusted,
        logical_or(is_shared_regional_spoke) as is_shared_regional_spoke
    from {{ ref('fct_flights') }}
    group by
        year,
        month,
        op_carrier,
        origin,
        dest
)

select
    concat(
        cast(t.year as string),
        '-',
        lpad(cast(t.month as string), 2, '0'),
        '|', t.op_carrier,
        '|', t.origin,
        '|', t.dest,
        '|', if(t.is_international, 'INTL', 'DOM')
    ) as route_month_id,
    t.year,
    t.quarter,
    t.month,
    t.op_carrier,
    t.origin,
    t.origin_country,
    t.dest,
    t.dest_country,
    t.is_international,
    if(
        t.is_international,
        'International (T-100 Route-Month Only v1)',
        'Domestic (OTP Flight + T-100)'
    ) as scope_label,
    if(
        t.is_international,
        'route_month_only_v1',
        'flight_and_route_month'
    ) as data_granularity,
    t.departures_scheduled,
    t.departures_performed,
    t.seats,
    t.passengers,
    t.distance_miles,
    t.aircraft_type_count,
    {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(t.passengers, nullif(t.seats, 0.0)))) as t100_load_factor,
    safe_divide(t.seats, nullif(t.departures_performed, 0.0)) as t100_seats_per_departure,
    coalesce(o.is_shared_regional_spoke, false) as is_shared_regional_spoke,
    o.otp_scheduled_flights,
    o.otp_performed_flights,
    o.otp_cancelled_flights,
    o.otp_diverted_flights,
    safe_divide(cast(o.otp_cancelled_flights as float64), nullif(cast(o.otp_scheduled_flights as float64), 0.0)) as otp_cancellation_rate,
    o.otp_avg_dep_delay_min,
    o.otp_avg_arr_delay_min,
    o.otp_avg_est_open_seats,
    o.otp_avg_est_open_seats_adjusted
from t100_agg as t
left join otp_monthly as o
    on t.year = o.year
    and t.month = o.month
    and t.op_carrier = o.op_carrier
    and t.origin = o.origin
    and t.dest = o.dest
    and t.is_international = o.is_international
