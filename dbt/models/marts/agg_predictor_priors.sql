{{
    config(
        materialized='table',
        cluster_by=['origin', 'dest', 'prior_tier'],
    )
}}

-- Hierarchical Empirical Bayes Predictor Priors Mart (`agg_predictor_priors`).
-- Mirrors `src/standby/prediction/priors.py` inside the warehouse so BigQuery and
-- DuckDB expose the exact 4-tier empirical seat-availability priors for both
-- domestic ORD routes (route x month x DOW x hour_block) and international v1
-- routes (`TIER3_INTL_T100_V1`, route x month from T-100 segments).

with dom_base as (
    select
        origin,
        dest,
        month,
        day_of_week,
        hour_block,
        is_cancelled,
        cast(seats_config as float64) as seats_config,
        cast(est_lf_adjusted as float64) as est_lf_adjusted,
        cast(est_open_seats_adjusted as float64) as est_open_seats_adjusted
    from {{ ref('fct_flights') }}
    where is_cancelled or est_open_seats_adjusted is not null
),

tier1_dom as (
    select
        'TIER1_ROUTE_MONTH_DOW_HB' as prior_tier,
        origin,
        dest,
        month,
        day_of_week,
        hour_block,
        false as is_international,
        'flight_and_route_month' as data_granularity,
        count(*) as sample_flights,
        avg(if(not is_cancelled, est_open_seats_adjusted, cast(null as float64))) as mean_open_seats,
        coalesce(
            stddev_pop(if(not is_cancelled, est_open_seats_adjusted, cast(null as float64))),
            6.0
        ) as std_open_seats,
        avg(if(not is_cancelled, seats_config, cast(null as float64))) as mean_seats_config,
        {{ bq_least() }}(
            1.0,
            {{ bq_greatest() }}(
                0.0,
                avg(if(not is_cancelled, est_lf_adjusted, cast(null as float64)))
            )
        ) as mean_load_factor,
        safe_divide(
            cast(countif(is_cancelled) as float64),
            nullif(cast(count(*) as float64), 0.0)
        ) as cancellation_rate
    from dom_base
    group by origin, dest, month, day_of_week, hour_block
    having count(*) >= 1
),

tier2_dom as (
    select
        'TIER2_ROUTE_MONTH_HB' as prior_tier,
        origin,
        dest,
        month,
        cast(null as int64) as day_of_week,
        hour_block,
        false as is_international,
        'flight_and_route_month' as data_granularity,
        count(*) as sample_flights,
        avg(if(not is_cancelled, est_open_seats_adjusted, cast(null as float64))) as mean_open_seats,
        coalesce(
            stddev_pop(if(not is_cancelled, est_open_seats_adjusted, cast(null as float64))),
            8.0
        ) as std_open_seats,
        avg(if(not is_cancelled, seats_config, cast(null as float64))) as mean_seats_config,
        {{ bq_least() }}(
            1.0,
            {{ bq_greatest() }}(
                0.0,
                avg(if(not is_cancelled, est_lf_adjusted, cast(null as float64)))
            )
        ) as mean_load_factor,
        safe_divide(
            cast(countif(is_cancelled) as float64),
            nullif(cast(count(*) as float64), 0.0)
        ) as cancellation_rate
    from dom_base
    group by origin, dest, month, hour_block
),

tier3_dom as (
    select
        'TIER3_ROUTE_MONTH' as prior_tier,
        origin,
        dest,
        month,
        cast(null as int64) as day_of_week,
        cast(null as string) as hour_block,
        false as is_international,
        'flight_and_route_month' as data_granularity,
        count(*) as sample_flights,
        avg(if(not is_cancelled, est_open_seats_adjusted, cast(null as float64))) as mean_open_seats,
        coalesce(
            stddev_pop(if(not is_cancelled, est_open_seats_adjusted, cast(null as float64))),
            10.0
        ) as std_open_seats,
        avg(if(not is_cancelled, seats_config, cast(null as float64))) as mean_seats_config,
        {{ bq_least() }}(
            1.0,
            {{ bq_greatest() }}(
                0.0,
                avg(if(not is_cancelled, est_lf_adjusted, cast(null as float64)))
            )
        ) as mean_load_factor,
        safe_divide(
            cast(countif(is_cancelled) as float64),
            nullif(cast(count(*) as float64), 0.0)
        ) as cancellation_rate
    from dom_base
    group by origin, dest, month
),

tier3_intl as (
    select
        'TIER3_INTL_T100_V1' as prior_tier,
        origin,
        dest,
        month,
        cast(null as int64) as day_of_week,
        cast(null as string) as hour_block,
        true as is_international,
        'route_month_only_v1' as data_granularity,
        cast(round(sum(departures_performed)) as int64) as sample_flights,
        {{ bq_greatest() }}(
            0.0,
            safe_divide(sum(seats) - sum(passengers), nullif(sum(departures_performed), 0.0))
        ) as mean_open_seats,
        {{ bq_greatest() }}(
            4.0,
            0.09 * safe_divide(sum(seats), nullif(sum(departures_performed), 0.0))
        ) as std_open_seats,
        safe_divide(sum(seats), nullif(sum(departures_performed), 0.0)) as mean_seats_config,
        {{ bq_least() }}(
            1.0,
            {{ bq_greatest() }}(
                0.0,
                safe_divide(sum(passengers), nullif(sum(seats), 0.0))
            )
        ) as mean_load_factor,
        0.015 as cancellation_rate
    from {{ ref('agg_route_month') }}
    where is_international = true
    group by origin, dest, month
    having sum(departures_performed) > 0 and sum(seats) > 0
),

unioned as (
    select * from tier1_dom
    union all
    select * from tier2_dom
    union all
    select * from tier3_dom
    union all
    select * from tier3_intl
)

select
    concat(
        prior_tier,
        '|', origin,
        '|', dest,
        '|', lpad(cast(month as string), 2, '0'),
        '|', coalesce(cast(day_of_week as string), 'ALL'),
        '|', coalesce(hour_block, 'ALL')
    ) as prior_id,
    prior_tier,
    origin,
    dest,
    month,
    day_of_week,
    hour_block,
    is_international,
    data_granularity,
    sample_flights,
    mean_open_seats,
    {{ bq_greatest() }}(1.5, std_open_seats) as std_open_seats,
    mean_seats_config,
    mean_load_factor,
    {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, cancellation_rate)) as cancellation_rate
from unioned
