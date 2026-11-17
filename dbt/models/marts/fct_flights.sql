{{
    config(
        materialized='table',
        partition_by={
            'field': 'flight_date',
            'data_type': 'date',
            'granularity': 'day',
        },
        cluster_by=['origin', 'dest', 'op_carrier'],
    )
}}

-- Core Domestic Flight Fact Table (`fct_flights`).
-- Grain: 1 row per scheduled US domestic flight marketed by UA departing configured hub(s).
-- Partitioned by DAY on `flight_date` (NONE expiry) and clustered by `origin, dest, op_carrier`.
-- Exposes Phase 4 `soi_historical`, leakage-free `soi_planning`, and all 8 component sub-scores.

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

    -- Derived aircraft & seat configuration
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

    -- T-100 provenance & base estimates (never overwritten by modeled adjustments)
    t100_available,
    lf_source,
    is_shared_regional_spoke,
    est_load_factor,
    est_passengers,
    est_open_seats,

    -- Modeled TSA / hour-block demand adjustments (stored separately)
    tsa_throughput,
    tsa_demand_multiplier,
    tsa_ratio_year,
    hour_block_multiplier,
    est_lf_adjusted,
    est_passengers_adjusted,
    est_open_seats_adjusted,

    -- Same-day route displacement, backup & holiday signals
    route_day_seq,
    prior_scheduled_count,
    prior_strict_scheduled_count,
    prior_disruption_count,
    weighted_displacement_hist,
    remaining_scheduled_backups,
    remaining_performed_backups,
    is_holiday_window,
    holiday_name,

    -- Confidence tier metadata & Phase 4 Standby Opportunity Index (0-100)
    confidence_tier,
    record_confidence_summary,
    s_open_hist,
    s_disp_hist,
    s_backup_hist,
    s_demand_hist,
    soi_historical,
    s_open_plan,
    s_disp_plan,
    s_backup_plan,
    s_demand_plan,
    soi_planning
from {{ ref('int_flights_soi_scored') }}
