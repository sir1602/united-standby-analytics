-- Intermediate model: Phase 4 Standby Opportunity Index (`soi_historical` & leakage-free `soi_planning`).
-- Implements exact 1:1 parity with Python `src/standby/scoring/soi.py`:
--   - Expanding `train.year < eval.year` priors (`2019` receives `NULL` `soi_planning`)
--   - 4-tier modal `(seats_config, bts_aircraft_type)` with deterministic `(cnt DESC, seats ASC, type ASC)` tie-breaker
--   - 5-tier T-100 LF lookup (`CARRIER_ROUTE_MONTH_TYPE -> CARRIER_ROUTE_MONTH -> UA_ROUTE_MONTH -> CARRIER_ROUTE -> UA_ROUTE`)
--   - 4-tier disruption rate `q_hat` (`min_obs=5` on Tiers 1 & 2) using `prior_strict_scheduled_count`
--   - 4-tier TSA ratio lookup `(month, iso_dow, is_holiday_window) -> (month, iso_dow) -> (month) -> global`
--   - Strict SC-5 NULL gates (`not t100_available or lf_source = 'UNMATCHED'`, plus `is_cancelled` for `soi_historical`).

with base_flights as (
    select
        e.*,
        db.route_day_seq,
        db.prior_scheduled_count,
        db.prior_strict_scheduled_count,
        db.prior_disruption_count,
        db.weighted_displacement_hist,
        db.remaining_scheduled_backups,
        db.remaining_performed_backups,
        tsa.tsa_ratio_year,
        e.day_of_week as iso_dow,
        {{ holiday_name_for_date('e.flight_date') }} as holiday_name,
        {{ is_holiday_window('e.flight_date') }} as is_holiday_window,
        (e.t100_available and e.lf_source != 'UNMATCHED') as has_t100_gate,
        (
            e.t100_available
            and e.lf_source != 'UNMATCHED'
            and e.est_load_factor is not null
        ) as has_valid_t100
    from {{ ref('int_flight_estimates') }} as e
    left join {{ ref('int_flight_displacements_backups') }} as db
        on e.flight_id = db.flight_id
    left join {{ ref('stg_tsa_throughput') }} as tsa
        on e.flight_date = tsa.date_day
),

eval_years as (
    select distinct year as eval_year
    from base_flights
),

-- 1. 4-Tier Modal (seats_config, bts_aircraft_type) from prior years (year < eval_year)
modal_t1_counts as (
    select
        ey.eval_year,
        f.op_carrier,
        f.origin,
        f.dest,
        f.month,
        f.hour_block,
        f.seats_config,
        f.bts_aircraft_type,
        count(*) as cnt
    from base_flights as f
    inner join eval_years as ey
        on f.year < ey.eval_year
    where f.seats_config is not null
      and f.seats_config > 0
      and f.bts_aircraft_type is not null
      and trim(f.bts_aircraft_type) != ''
    group by
        ey.eval_year,
        f.op_carrier,
        f.origin,
        f.dest,
        f.month,
        f.hour_block,
        f.seats_config,
        f.bts_aircraft_type
),

modal_t1 as (
    select
        eval_year,
        op_carrier,
        origin,
        dest,
        month,
        hour_block,
        seats_config as seats_config_plan,
        bts_aircraft_type as bts_type_plan
    from modal_t1_counts
    qualify row_number() over (
        partition by eval_year, op_carrier, origin, dest, month, hour_block
        order by cnt desc, seats_config asc, bts_aircraft_type asc
    ) = 1
),

modal_t2_counts as (
    select
        ey.eval_year,
        f.op_carrier,
        f.origin,
        f.dest,
        f.month,
        f.seats_config,
        f.bts_aircraft_type,
        count(*) as cnt
    from base_flights as f
    inner join eval_years as ey
        on f.year < ey.eval_year
    where f.seats_config is not null
      and f.seats_config > 0
      and f.bts_aircraft_type is not null
      and trim(f.bts_aircraft_type) != ''
    group by
        ey.eval_year,
        f.op_carrier,
        f.origin,
        f.dest,
        f.month,
        f.seats_config,
        f.bts_aircraft_type
),

modal_t2 as (
    select
        eval_year,
        op_carrier,
        origin,
        dest,
        month,
        seats_config as seats_config_plan,
        bts_aircraft_type as bts_type_plan
    from modal_t2_counts
    qualify row_number() over (
        partition by eval_year, op_carrier, origin, dest, month
        order by cnt desc, seats_config asc, bts_aircraft_type asc
    ) = 1
),

modal_t3_counts as (
    select
        ey.eval_year,
        f.op_carrier,
        f.origin,
        f.dest,
        f.seats_config,
        f.bts_aircraft_type,
        count(*) as cnt
    from base_flights as f
    inner join eval_years as ey
        on f.year < ey.eval_year
    where f.seats_config is not null
      and f.seats_config > 0
      and f.bts_aircraft_type is not null
      and trim(f.bts_aircraft_type) != ''
    group by
        ey.eval_year,
        f.op_carrier,
        f.origin,
        f.dest,
        f.seats_config,
        f.bts_aircraft_type
),

modal_t3 as (
    select
        eval_year,
        op_carrier,
        origin,
        dest,
        seats_config as seats_config_plan,
        bts_aircraft_type as bts_type_plan
    from modal_t3_counts
    qualify row_number() over (
        partition by eval_year, op_carrier, origin, dest
        order by cnt desc, seats_config asc, bts_aircraft_type asc
    ) = 1
),

modal_t4_counts as (
    select
        ey.eval_year,
        f.origin,
        f.dest,
        f.seats_config,
        f.bts_aircraft_type,
        count(*) as cnt
    from base_flights as f
    inner join eval_years as ey
        on f.year < ey.eval_year
    where f.seats_config is not null
      and f.seats_config > 0
      and f.bts_aircraft_type is not null
      and trim(f.bts_aircraft_type) != ''
    group by
        ey.eval_year,
        f.origin,
        f.dest,
        f.seats_config,
        f.bts_aircraft_type
),

modal_t4 as (
    select
        eval_year,
        origin,
        dest,
        seats_config as seats_config_plan,
        bts_aircraft_type as bts_type_plan
    from modal_t4_counts
    qualify row_number() over (
        partition by eval_year, origin, dest
        order by cnt desc, seats_config asc, bts_aircraft_type asc
    ) = 1
),

-- 2. 5-Tier Historical Domestic T-100 LF Lookup (year < eval_year)
dom_t100_train as (
    select
        ey.eval_year,
        t.op_carrier,
        t.origin,
        t.dest,
        t.month,
        t.bts_aircraft_type,
        t.is_ua_family,
        t.departures_performed,
        t.seats,
        t.passengers
    from {{ ref('stg_t100_segments') }} as t
    inner join eval_years as ey
        on t.year < ey.eval_year
    where t.data_source in ('DU', 'DF')
),

lf_t1 as (
    select
        eval_year,
        op_carrier,
        origin,
        dest,
        month,
        bts_aircraft_type,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf
    from dom_t100_train
    where bts_aircraft_type is not null
      and trim(bts_aircraft_type) != ''
    group by eval_year, op_carrier, origin, dest, month, bts_aircraft_type
    having sum(seats) > 0 and sum(departures_performed) > 0
),

lf_t2 as (
    select
        eval_year,
        op_carrier,
        origin,
        dest,
        month,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf
    from dom_t100_train
    group by eval_year, op_carrier, origin, dest, month
    having sum(seats) > 0 and sum(departures_performed) > 0
),

lf_t3 as (
    select
        eval_year,
        origin,
        dest,
        month,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf
    from dom_t100_train
    where is_ua_family = true
    group by eval_year, origin, dest, month
    having sum(seats) > 0 and sum(departures_performed) > 0
),

lf_t4 as (
    select
        eval_year,
        op_carrier,
        origin,
        dest,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf
    from dom_t100_train
    group by eval_year, op_carrier, origin, dest
    having sum(seats) > 0 and sum(departures_performed) > 0
),

lf_t5 as (
    select
        eval_year,
        origin,
        dest,
        {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as lf
    from dom_t100_train
    where is_ua_family = true
    group by eval_year, origin, dest
    having sum(seats) > 0 and sum(departures_performed) > 0
),

-- 3. 4-Tier Historical Disruption Rate q_hat (year < eval_year; min_obs=5 on Tiers 1 & 2)
disruption_train as (
    select
        ey.eval_year,
        f.origin,
        f.dest,
        f.month,
        f.hour_block,
        case
            when f.is_cancelled then 1.0
            when f.is_diverted then {{ var('disp_diversion_weight', 0.50) }}
            else 0.0
        end as disruption_weight
    from base_flights as f
    inner join eval_years as ey
        on f.year < ey.eval_year
),

q_t1 as (
    select
        eval_year,
        origin,
        dest,
        month,
        hour_block,
        avg(disruption_weight) as q_hat
    from disruption_train
    group by eval_year, origin, dest, month, hour_block
    having count(*) >= {{ var('disp_plan_min_obs', 5) }}
),

q_t2 as (
    select
        eval_year,
        origin,
        dest,
        month,
        avg(disruption_weight) as q_hat
    from disruption_train
    group by eval_year, origin, dest, month
    having count(*) >= {{ var('disp_plan_min_obs', 5) }}
),

q_t3 as (
    select
        eval_year,
        origin,
        dest,
        avg(disruption_weight) as q_hat
    from disruption_train
    group by eval_year, origin, dest
),

q_t4 as (
    select
        eval_year,
        avg(disruption_weight) as q_hat
    from disruption_train
    group by eval_year
),

-- 4. 4-Tier Historical TSA Ratios (year < eval_year)
tsa_train as (
    select
        ey.eval_year,
        tsa.month,
        tsa.iso_dow,
        tsa.is_holiday_window,
        tsa.tsa_ratio_month,
        tsa.tsa_ratio_year
    from {{ ref('stg_tsa_throughput') }} as tsa
    inner join eval_years as ey
        on tsa.year < ey.eval_year
    where tsa.tsa_ratio_month is not null
      and tsa.tsa_ratio_year is not null
),

tsa_t1 as (
    select
        eval_year,
        month,
        iso_dow,
        is_holiday_window,
        avg(tsa_ratio_month) as r_month,
        avg(tsa_ratio_year) as r_year
    from tsa_train
    group by eval_year, month, iso_dow, is_holiday_window
),

tsa_t2 as (
    select
        eval_year,
        month,
        iso_dow,
        avg(tsa_ratio_month) as r_month,
        avg(tsa_ratio_year) as r_year
    from tsa_train
    group by eval_year, month, iso_dow
),

tsa_t3 as (
    select
        eval_year,
        month,
        avg(tsa_ratio_month) as r_month,
        avg(tsa_ratio_year) as r_year
    from tsa_train
    group by eval_year, month
),

tsa_t4 as (
    select
        eval_year,
        avg(tsa_ratio_month) as r_month,
        avg(tsa_ratio_year) as r_year
    from tsa_train
    group by eval_year
),

-- 5. Resolve modal seats/type, disruption rate, and TSA priors per flight
planning_modal as (
    select
        f.*,
        case
            when m1.seats_config_plan is not null then m1.seats_config_plan
            when m2.seats_config_plan is not null then m2.seats_config_plan
            when m3.seats_config_plan is not null then m3.seats_config_plan
            else m4.seats_config_plan
        end as seats_config_plan,
        case
            when m1.seats_config_plan is not null then m1.bts_type_plan
            when m2.seats_config_plan is not null then m2.bts_type_plan
            when m3.seats_config_plan is not null then m3.bts_type_plan
            else m4.bts_type_plan
        end as bts_type_plan,
        coalesce(q1.q_hat, q2.q_hat, q3.q_hat, q4.q_hat) as q_hat_plan,
        case
            when tt1.r_month is not null then tt1.r_month
            when tt2.r_month is not null then tt2.r_month
            when tt3.r_month is not null then tt3.r_month
            else tt4.r_month
        end as r_tsa_month_plan,
        case
            when tt1.r_year is not null then tt1.r_year
            when tt2.r_year is not null then tt2.r_year
            when tt3.r_year is not null then tt3.r_year
            else tt4.r_year
        end as r_tsa_year_plan
    from base_flights as f
    left join modal_t1 as m1
        on f.year = m1.eval_year
        and f.op_carrier = m1.op_carrier
        and f.origin = m1.origin
        and f.dest = m1.dest
        and f.month = m1.month
        and f.hour_block = m1.hour_block
    left join modal_t2 as m2
        on f.year = m2.eval_year
        and f.op_carrier = m2.op_carrier
        and f.origin = m2.origin
        and f.dest = m2.dest
        and f.month = m2.month
    left join modal_t3 as m3
        on f.year = m3.eval_year
        and f.op_carrier = m3.op_carrier
        and f.origin = m3.origin
        and f.dest = m3.dest
    left join modal_t4 as m4
        on f.year = m4.eval_year
        and f.origin = m4.origin
        and f.dest = m4.dest
    left join q_t1 as q1
        on f.year = q1.eval_year
        and f.origin = q1.origin
        and f.dest = q1.dest
        and f.month = q1.month
        and f.hour_block = q1.hour_block
    left join q_t2 as q2
        on f.year = q2.eval_year
        and f.origin = q2.origin
        and f.dest = q2.dest
        and f.month = q2.month
    left join q_t3 as q3
        on f.year = q3.eval_year
        and f.origin = q3.origin
        and f.dest = q3.dest
    left join q_t4 as q4
        on f.year = q4.eval_year
    left join tsa_t1 as tt1
        on f.year = tt1.eval_year
        and f.month = tt1.month
        and f.iso_dow = tt1.iso_dow
        and f.is_holiday_window = tt1.is_holiday_window
    left join tsa_t2 as tt2
        on f.year = tt2.eval_year
        and f.month = tt2.month
        and f.iso_dow = tt2.iso_dow
    left join tsa_t3 as tt3
        on f.year = tt3.eval_year
        and f.month = tt3.month
    left join tsa_t4 as tt4
        on f.year = tt4.eval_year
),

planning_inputs as (
    select
        pm.*,
        coalesce(l1.lf, l2.lf, l3.lf, l4.lf, l5.lf) as lf_plan
    from planning_modal as pm
    left join lf_t1 as l1
        on pm.year = l1.eval_year
        and pm.op_carrier = l1.op_carrier
        and pm.origin = l1.origin
        and pm.dest = l1.dest
        and pm.month = l1.month
        and pm.bts_type_plan = l1.bts_aircraft_type
    left join lf_t2 as l2
        on pm.year = l2.eval_year
        and pm.op_carrier = l2.op_carrier
        and pm.origin = l2.origin
        and pm.dest = l2.dest
        and pm.month = l2.month
    left join lf_t3 as l3
        on pm.year = l3.eval_year
        and pm.origin = l3.origin
        and pm.dest = l3.dest
        and pm.month = l3.month
    left join lf_t4 as l4
        on pm.year = l4.eval_year
        and pm.op_carrier = l4.op_carrier
        and pm.origin = l4.origin
        and pm.dest = l4.dest
    left join lf_t5 as l5
        on pm.year = l5.eval_year
        and pm.origin = l5.origin
        and pm.dest = l5.dest
),

planning_open_seats as (
    select
        *,
        (
            has_valid_t100
            and not is_cancelled
            and seats_config is not null
            and seats_config > 0
            and est_open_seats_adjusted is not null
            and tsa_ratio_year is not null
        ) as eligible_hist,
        (
            has_t100_gate
            and seats_config_plan is not null
            and seats_config_plan > 0
            and lf_plan is not null
            and q_hat_plan is not null
            and r_tsa_month_plan is not null
            and r_tsa_year_plan is not null
        ) as eligible_plan,
        case
            when seats_config_plan is not null
             and seats_config_plan > 0
             and lf_plan is not null
             and r_tsa_month_plan is not null
            then (
                seats_config_plan - cast(
                    {{ bq_least() }}(
                        seats_config_plan,
                        {{ bq_greatest() }}(
                            0,
                            round(
                                cast(seats_config_plan as float64) * {{ bq_least() }}(
                                    1.0,
                                    {{ bq_greatest() }}(
                                        0.0,
                                        lf_plan
                                        * {{ bq_least() }}(
                                            {{ var('tsa_multiplier_max', 1.20) }},
                                            {{ bq_greatest() }}(
                                                {{ var('tsa_multiplier_min', 0.80) }},
                                                1.0 + {{ var('tsa_damping_alpha', 0.35) }} * (r_tsa_month_plan - 1.0)
                                            )
                                        )
                                        * hour_block_multiplier
                                    )
                                )
                            )
                        )
                    ) as int64
                )
            )
            else cast(null as int64)
        end as est_open_plan_adj
    from planning_inputs
),

subscores as (
    select
        *,
        -- Historical sub-scores
        case
            when eligible_hist then (
                {{ var('open_abs_weight', 0.50) }} * (
                    100.0 * {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(cast(est_open_seats_adjusted as float64), {{ var('open_abs_cap', 25.0) }})))
                )
                + (1.0 - {{ var('open_abs_weight', 0.50) }}) * (
                    100.0 * {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(safe_divide(cast(est_open_seats_adjusted as float64), cast(seats_config as float64)), {{ var('open_pct_cap', 0.30) }})))
                )
            )
            else cast(null as float64)
        end as s_open_hist,

        case
            when eligible_hist then (
                100.0 * {{ bq_greatest() }}(0.0, {{ bq_least() }}(1.0, 1.0 - safe_divide(weighted_displacement_hist, {{ var('disp_hist_cap', 2.0) }})))
            )
            else cast(null as float64)
        end as s_disp_hist,

        case
            when eligible_hist then (
                100.0 * {{ bq_least() }}(
                    1.0,
                    {{ bq_greatest() }}(
                        0.0,
                        safe_divide(
                            ln(1.0 + cast(remaining_performed_backups as float64)),
                            ln(1.0 + cast({{ var('backup_cap', 6) }} as float64))
                        )
                    )
                )
            )
            else cast(null as float64)
        end as s_backup_hist,

        case
            when eligible_hist then (
                100.0 * {{ bq_least() }}(
                    1.0,
                    {{ bq_greatest() }}(
                        0.0,
                        safe_divide(
                            {{ var('demand_r_max', 1.35) }} - (tsa_ratio_year + if(is_holiday_window, {{ var('demand_holiday_boost', 0.10) }}, 0.0)),
                            {{ var('demand_r_max', 1.35) }} - {{ var('demand_r_min', 0.65) }}
                        )
                    )
                )
            )
            else cast(null as float64)
        end as s_demand_hist,

        -- Planning sub-scores (leakage-free)
        case
            when eligible_plan then (
                {{ var('open_abs_weight', 0.50) }} * (
                    100.0 * {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(cast(est_open_plan_adj as float64), {{ var('open_abs_cap', 25.0) }})))
                )
                + (1.0 - {{ var('open_abs_weight', 0.50) }}) * (
                    100.0 * {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(safe_divide(cast(est_open_plan_adj as float64), cast(seats_config_plan as float64)), {{ var('open_pct_cap', 0.30) }})))
                )
            )
            else cast(null as float64)
        end as s_open_plan,

        case
            when eligible_plan then (
                100.0 * {{ bq_greatest() }}(
                    0.0,
                    {{ bq_least() }}(
                        1.0,
                        1.0 - safe_divide(
                            cast(prior_strict_scheduled_count as float64) * q_hat_plan * {{ var('disp_plan_beta', 0.65) }} + q_hat_plan,
                            {{ var('disp_plan_cap', 0.25) }}
                        )
                    )
                )
            )
            else cast(null as float64)
        end as s_disp_plan,

        case
            when eligible_plan then (
                100.0 * {{ bq_least() }}(
                    1.0,
                    {{ bq_greatest() }}(
                        0.0,
                        safe_divide(
                            ln(1.0 + cast(remaining_scheduled_backups as float64)),
                            ln(1.0 + cast({{ var('backup_cap', 6) }} as float64))
                        )
                    )
                )
            )
            else cast(null as float64)
        end as s_backup_plan,

        case
            when eligible_plan then (
                100.0 * {{ bq_least() }}(
                    1.0,
                    {{ bq_greatest() }}(
                        0.0,
                        safe_divide(
                            {{ var('demand_r_max', 1.35) }} - (r_tsa_year_plan + if(is_holiday_window, {{ var('demand_holiday_boost', 0.10) }}, 0.0)),
                            {{ var('demand_r_max', 1.35) }} - {{ var('demand_r_min', 0.65) }}
                        )
                    )
                )
            )
            else cast(null as float64)
        end as s_demand_plan
    from planning_open_seats
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
    est_load_factor,
    tsa_throughput,
    tsa_demand_multiplier,
    tsa_ratio_year,
    hour_block_multiplier,
    est_passengers,
    est_open_seats,
    est_lf_adjusted,
    est_passengers_adjusted,
    est_open_seats_adjusted,
    route_day_seq,
    prior_scheduled_count,
    prior_strict_scheduled_count,
    prior_disruption_count,
    weighted_displacement_hist,
    remaining_scheduled_backups,
    remaining_performed_backups,
    is_holiday_window,
    holiday_name,
    record_confidence_summary,
    s_open_hist,
    s_disp_hist,
    s_backup_hist,
    s_demand_hist,
    case
        when eligible_hist then round(
            {{ var('soi_weight_open', 0.50) }} * s_open_hist
            + {{ var('soi_weight_disp', 0.20) }} * s_disp_hist
            + {{ var('soi_weight_backup', 0.20) }} * s_backup_hist
            + {{ var('soi_weight_demand', 0.10) }} * s_demand_hist,
            2
        )
        else cast(null as float64)
    end as soi_historical,
    s_open_plan,
    s_disp_plan,
    s_backup_plan,
    s_demand_plan,
    case
        when eligible_plan then round(
            {{ var('soi_weight_open', 0.50) }} * s_open_plan
            + {{ var('soi_weight_disp', 0.20) }} * s_disp_plan
            + {{ var('soi_weight_backup', 0.20) }} * s_backup_plan
            + {{ var('soi_weight_demand', 0.10) }} * s_demand_plan,
            2
        )
        else cast(null as float64)
    end as soi_planning
from subscores
