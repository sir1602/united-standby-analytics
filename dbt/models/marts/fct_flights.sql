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
-- Phase 3 exposes typed `CAST(NULL AS FLOAT64)` placeholders for `soi_historical` & `soi_planning`
-- prior to Phase 4 SOI scoring implementation.

select
    e.flight_id,
    e.flight_date,
    e.year,
    e.quarter,
    e.month,
    e.day_of_month,
    e.day_of_week,
    e.mkt_carrier,
    e.op_carrier,
    e.flight_num_mkt,
    e.flight_num_op,
    e.tail_num,
    e.tail_num_clean,
    e.origin,
    e.origin_airport_id,
    e.dest,
    e.dest_airport_id,
    e.crs_dep_time,
    e.dep_time,
    e.crs_arr_time,
    e.arr_time,
    e.dep_hour,
    e.hour_block,
    e.dep_delay_min,
    e.arr_delay_min,
    e.is_cancelled,
    e.cancellation_code,
    e.is_diverted,
    e.crs_elapsed_time_min,
    e.actual_elapsed_time_min,
    e.distance_miles,

    -- Derived aircraft & seat configuration
    e.aircraft_source,
    e.faa_manufacturer,
    e.faa_model,
    e.faa_tc_max_seats,
    e.fleet_subtype,
    e.bts_aircraft_type,
    e.seat_config_source,
    e.seats_config,
    e.seats_premium,
    e.seats_economy,

    -- T-100 provenance & base estimates (never overwritten by modeled adjustments)
    e.t100_available,
    e.lf_source,
    e.is_shared_regional_spoke,
    e.est_load_factor,
    e.est_passengers,
    e.est_open_seats,

    -- Modeled TSA / hour-block demand adjustments (stored separately)
    e.tsa_throughput,
    e.tsa_demand_multiplier,
    e.hour_block_multiplier,
    e.est_lf_adjusted,
    e.est_passengers_adjusted,
    e.est_open_seats_adjusted,

    -- Same-day route displacement & backup prep metrics (for Phase 4 SOI)
    db.route_day_seq,
    db.prior_scheduled_count,
    db.prior_disruption_count,
    db.weighted_displacement_hist,
    db.remaining_scheduled_backups,
    db.remaining_performed_backups,

    -- Confidence tier metadata & Phase 4 SOI typed placeholders
    e.confidence_tier,
    e.record_confidence_summary,
    cast(null as float64) as soi_historical,
    cast(null as float64) as soi_planning
from {{ ref('int_flight_estimates') }} as e
left join {{ ref('int_flight_displacements_backups') }} as db
    on e.flight_id = db.flight_id
