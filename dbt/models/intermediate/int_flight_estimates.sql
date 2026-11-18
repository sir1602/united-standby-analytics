-- Intermediate model: Base flight-level passenger/open-seat estimates vs.
-- separate modeled TSA & hour-block adjustments (D-22, SC-5).

with base as (
    select
        f.*,
        tsa.tsa_throughput,
        tsa.tsa_demand_multiplier,
        {{ hour_block_multiplier('f.hour_block') }} as hour_block_multiplier,
        (
            f.t100_available
            and f.lf_source != 'UNMATCHED'
            and f.est_load_factor is not null
        ) as has_valid_t100
    from {{ ref('int_t100_load_factors') }} as f
    left join {{ ref('stg_tsa_throughput') }} as tsa
        on f.flight_date = tsa.date_day
),

with_lf_adjusted as (
    select
        *,
        case
            when has_valid_t100
             and tsa_demand_multiplier is not null
             and hour_block_multiplier is not null
                then {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, est_load_factor * tsa_demand_multiplier * hour_block_multiplier))
            else cast(null as float64)
        end as est_lf_adjusted
    from base
),

with_passengers as (
    select
        *,
        case
            when has_valid_t100 and seats_config is not null
                then cast({{ bq_least() }}(seats_config, {{ bq_greatest() }}(0, round(seats_config * est_load_factor))) as int64)
            else cast(null as int64)
        end as est_passengers,
        case
            when est_lf_adjusted is not null and seats_config is not null
                then cast({{ bq_least() }}(seats_config, {{ bq_greatest() }}(0, round(seats_config * est_lf_adjusted))) as int64)
            else cast(null as int64)
        end as est_passengers_adjusted
    from with_lf_adjusted
)

select
    flight_id,
    flight_date,
    year,
    quarter,
    month,
    day_of_month,
    day_of_week,
    mkt_carrier,
    op_carrier,
    flight_num_mkt,
    flight_num_op,
    tail_num,
    tail_num_clean,
    origin,
    origin_airport_id,
    dest,
    dest_airport_id,
    crs_dep_time,
    dep_time,
    crs_arr_time,
    arr_time,
    dep_hour,
    hour_block,
    dep_delay_min,
    arr_delay_min,
    is_cancelled,
    cancellation_code,
    is_diverted,
    crs_elapsed_time_min,
    actual_elapsed_time_min,
    distance_miles,
    confidence_tier,
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
    t100_available,
    lf_source,
    is_shared_regional_spoke,
    if(has_valid_t100, est_load_factor, cast(null as float64)) as est_load_factor,
    tsa_throughput,
    tsa_demand_multiplier,
    hour_block_multiplier,
    est_passengers,
    case
        when est_passengers is not null and seats_config is not null
            then seats_config - est_passengers
        else cast(null as int64)
    end as est_open_seats,
    est_lf_adjusted,
    est_passengers_adjusted,
    case
        when est_passengers_adjusted is not null and seats_config is not null
            then seats_config - est_passengers_adjusted
        else cast(null as int64)
    end as est_open_seats_adjusted,
    case
        when has_valid_t100 then 'actual+derived+estimated'
        else 'actual+derived (unmatched_t100)'
    end as record_confidence_summary
from with_passengers
