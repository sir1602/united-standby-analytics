-- Looker Studio route summary: 1 row per (origin, dest, is_international) for the
-- Page 1 bubble map and destination leaderboard. Mirrors `_build_looker_route_summary`.

with rm as (
    select * from {{ ref('agg_route_month') }}
    where origin != dest
),

grouped as (
    select
        origin,
        dest,
        is_international,
        any_value(scope_label) as scope_label,
        any_value(data_granularity) as data_granularity,
        sum(departures_scheduled) as total_departures_scheduled,
        sum(departures_performed) as total_departures_performed,
        sum(seats) as total_seats,
        sum(passengers) as total_passengers,
        sum(distance_miles * departures_performed) as sum_dist_x_deps,
        max(distance_miles) as max_distance,
        count(distinct format('%d-%02d', year, month)) as months_active,
        count(distinct op_carrier) as carrier_count,
        logical_or(coalesce(is_shared_regional_spoke, false)) as is_shared_regional_spoke,
        sum(otp_scheduled_flights) as otp_scheduled_flights,
        sum(otp_performed_flights) as otp_performed_flights,
        sum(otp_cancelled_flights) as otp_cancelled_flights,
        sum(otp_avg_est_open_seats_adjusted * otp_performed_flights) as sum_open_adj_x_perf
    from rm
    group by 1, 2, 3
),

route_soi as (
    select
        origin,
        dest,
        avg(soi_historical) as otp_avg_soi_historical,
        avg(soi_planning) as otp_avg_soi_planning
    from {{ ref('fct_flights') }}
    group by 1, 2
)

select
    concat(g.origin, '-', g.dest, '-', if(g.is_international, 'INTL', 'DOM')) as route_id,
    g.origin,
    g.dest,
    concat(g.origin, ' -> ', g.dest) as route_label,
    g.is_international,
    g.scope_label,
    g.data_granularity,
    a.airport_name as dest_airport_name,
    a.city_name as dest_city_name,
    a.state_or_country as dest_state_or_country,
    a.country_code as dest_country_code,
    a.geo_query as dest_geo_query,
    g.total_departures_scheduled,
    g.total_departures_performed,
    g.total_seats,
    g.total_passengers,
    if(g.total_seats > 0, least(greatest(g.total_passengers / g.total_seats, 0), 1), null) as avg_t100_load_factor,
    if(g.total_departures_performed > 0, g.total_seats / g.total_departures_performed, null) as avg_t100_seats_per_departure,
    if(g.total_departures_performed > 0, g.sum_dist_x_deps / g.total_departures_performed, g.max_distance) as distance_miles,
    g.months_active,
    g.carrier_count,
    g.is_shared_regional_spoke,
    if(g.is_international, null, g.otp_scheduled_flights) as otp_scheduled_flights,
    if(g.is_international, null, g.otp_performed_flights) as otp_performed_flights,
    if(g.is_international, null, g.otp_cancelled_flights) as otp_cancelled_flights,
    if(not g.is_international and g.otp_scheduled_flights > 0, g.otp_cancelled_flights / g.otp_scheduled_flights, null) as otp_cancellation_rate,
    if(not g.is_international and g.otp_performed_flights > 0, g.sum_open_adj_x_perf / g.otp_performed_flights, null) as otp_avg_est_open_seats_adjusted,
    if(g.is_international, null, s.otp_avg_soi_historical) as otp_avg_soi_historical,
    if(g.is_international, null, s.otp_avg_soi_planning) as otp_avg_soi_planning
from grouped as g
left join {{ ref('lk_airport_geo') }} as a
    on g.dest = a.airport_code
left join route_soi as s
    on g.origin = s.origin
    and g.dest = s.dest
