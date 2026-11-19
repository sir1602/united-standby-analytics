-- Looker Studio predictor priors: the empirical seat-availability priors behind the
-- Trip Predictor (`agg_predictor_priors`), with labels and destination geo added.
-- These are the *inputs* to the Monte Carlo simulator (D-41), not predictions: Looker
-- Studio cannot run the simulator, so the Predictor page links to the live web tool.

select
    p.prior_id,
    p.prior_tier,
    case p.prior_tier
        when 'TIER1_ROUTE_MONTH_DOW_HB' then '1 - Route x month x day x hour block'
        when 'TIER2_ROUTE_MONTH_HB' then '2 - Route x month x hour block'
        when 'TIER3_ROUTE_MONTH' then '3 - Route x month'
        when 'TIER3_INTL_T100_V1' then '3 - International route x month (v1)'
        else p.prior_tier
    end as prior_tier_label,
    p.origin,
    p.dest,
    concat(p.origin, ' -> ', p.dest) as route_label,
    g.city_name as dest_city_name,
    g.state_or_country as dest_state_or_country,
    g.country_code as dest_country_code,
    g.geo_query as dest_geo_query,
    p.month,
    format_date('%m - %b', date(2001, p.month, 1)) as month_label,
    p.day_of_week,
    case p.day_of_week
        when 1 then '1 - Mon'
        when 2 then '2 - Tue'
        when 3 then '3 - Wed'
        when 4 then '4 - Thu'
        when 5 then '5 - Fri'
        when 6 then '6 - Sat'
        when 7 then '7 - Sun'
        else 'All days'
    end as day_of_week_label,
    p.hour_block,
    case lower(trim(p.hour_block))
        when 'early' then '1 - Early (05-07)'
        when 'morning' then '2 - Morning (08-10)'
        when 'midday' then '3 - Midday (11-13)'
        when 'afternoon' then '4 - Afternoon (14-16)'
        when 'evening' then '5 - Evening (17-19)'
        when 'late' then '6 - Late (20-04)'
        else coalesce(p.hour_block, 'All hour blocks')
    end as hour_block_label,
    p.is_international,
    p.data_granularity,
    p.sample_flights,
    p.mean_open_seats,
    p.std_open_seats,
    p.mean_seats_config,
    p.mean_load_factor,
    p.cancellation_rate
from {{ ref('agg_predictor_priors') }} as p
left join {{ ref('lk_airport_geo') }} as g
    on p.dest = g.airport_code
