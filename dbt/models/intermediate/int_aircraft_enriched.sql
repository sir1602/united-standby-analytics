-- Intermediate model: 3-tier aircraft and seat-configuration resolution
-- matching `src/standby/enrichment/aircraft.py` (`TAIL_OVERRIDE -> FAA_MASTER -> FAA_DEREG`).

with flights as (
    select * from {{ ref('stg_otp_flights') }}
),

faa_valid as (
    select
        reg.n_number,
        reg.registry_source,
        reg.serial_number,
        reg.cancel_date,
        ref_acft.faa_manufacturer,
        ref_acft.faa_model,
        ref_acft.faa_tc_max_seats
    from {{ ref('stg_faa_aircraft') }} as reg
    inner join {{ ref('stg_faa_acftref') }} as ref_acft
        on reg.mfr_mdl_code = ref_acft.code
),

faa_master as (
    select
        n_number,
        faa_manufacturer,
        faa_model,
        faa_tc_max_seats
    from faa_valid
    where registry_source = 'faa_master'
    -- Dedup AFTER the seat-floor join: latest cancel_date, then lowest serial_number
    qualify row_number() over (
        partition by n_number
        order by coalesce(cancel_date, '') desc, coalesce(serial_number, '') asc
    ) = 1
),

faa_dereg as (
    select
        n_number,
        faa_manufacturer,
        faa_model,
        faa_tc_max_seats
    from faa_valid
    where registry_source = 'faa_dereg'
    -- Dedup AFTER the seat-floor join: latest cancel_date, then lowest serial_number
    qualify row_number() over (
        partition by n_number
        order by coalesce(cancel_date, '') desc, coalesce(serial_number, '') asc
    ) = 1
),

seat_cfg as (
    select
        trim(subtype) as subtype,
        cast(seats_total as int64) as seats_total,
        cast(seats_premium as int64) as seats_premium,
        cast(seats_economy as int64) as seats_economy
    from {{ ref('seat_config') }}
),

crosswalk_clean as (
    select
        upper(trim(faa_mfr)) as faa_mfr,
        upper(trim(faa_model)) as faa_model,
        upper(trim(op_carrier)) as op_carrier,
        trim(subtype) as subtype,
        trim(cast(bts_aircraft_type as string)) as bts_aircraft_type
    from {{ ref('faa_t100_crosswalk') }}
),

cw_carrier as (
    select
        cw.op_carrier,
        cw.faa_mfr,
        cw.faa_model,
        cw.subtype,
        cw.bts_aircraft_type,
        sc.seats_total,
        sc.seats_premium,
        sc.seats_economy
    from crosswalk_clean as cw
    inner join seat_cfg as sc
        on cw.subtype = sc.subtype
    where cw.op_carrier != '*'
    qualify row_number() over (
        partition by cw.op_carrier, cw.faa_mfr, cw.faa_model
        order by cw.subtype
    ) = 1
),

cw_generic as (
    select
        cw.faa_mfr,
        cw.faa_model,
        cw.subtype,
        cw.bts_aircraft_type,
        sc.seats_total,
        sc.seats_premium,
        sc.seats_economy
    from crosswalk_clean as cw
    inner join seat_cfg as sc
        on cw.subtype = sc.subtype
    qualify row_number() over (
        partition by cw.faa_mfr, cw.faa_model
        order by cw.op_carrier, cw.subtype
    ) = 1
),

rev_cw_carrier as (
    select
        op_carrier,
        subtype,
        faa_mfr,
        faa_model
    from crosswalk_clean
    where op_carrier != '*'
    qualify row_number() over (
        partition by op_carrier, subtype
        order by faa_mfr, faa_model
    ) = 1
),

rev_cw_generic as (
    select
        subtype,
        faa_mfr,
        faa_model
    from crosswalk_clean
    qualify row_number() over (
        partition by subtype
        order by op_carrier, faa_mfr, faa_model
    ) = 1
),

overrides as (
    select
        upper(trim(tail_num)) as tail_num,
        upper(trim(op_carrier)) as op_carrier,
        trim(subtype) as subtype,
        trim(cast(bts_aircraft_type as string)) as bts_aircraft_type,
        cast(seats_total as int64) as seats_total,
        cast(seats_premium as int64) as seats_premium,
        cast(seats_economy as int64) as seats_economy
    from {{ ref('tail_overrides') }}
),

joined as (
    select
        f.*,
        -- Exact or wildcard tail override (prefer carrier-specific over wildcard)
        coalesce(ov_c.subtype, ov_w.subtype) as ov_subtype,
        coalesce(ov_c.bts_aircraft_type, ov_w.bts_aircraft_type) as ov_bts_type,
        coalesce(ov_c.seats_total, ov_w.seats_total) as ov_seats_total,
        coalesce(ov_c.seats_premium, ov_w.seats_premium) as ov_seats_premium,
        coalesce(ov_c.seats_economy, ov_w.seats_economy) as ov_seats_economy,
        (ov_c.tail_num is not null or ov_w.tail_num is not null) as has_override,

        -- FAA MASTER & DEREG raw hits
        m.faa_manufacturer as m_faa_mfr,
        m.faa_model as m_faa_model,
        m.faa_tc_max_seats as m_tc_seats,
        d.faa_manufacturer as d_faa_mfr,
        d.faa_model as d_faa_model,
        d.faa_tc_max_seats as d_tc_seats,

        -- Crosswalk hits for MASTER
        coalesce(mc_c.subtype, mc_g.subtype) as m_subtype,
        coalesce(mc_c.bts_aircraft_type, mc_g.bts_aircraft_type) as m_bts_type,
        coalesce(mc_c.seats_total, mc_g.seats_total) as m_seats_total,
        coalesce(mc_c.seats_premium, mc_g.seats_premium) as m_seats_premium,
        coalesce(mc_c.seats_economy, mc_g.seats_economy) as m_seats_economy,

        -- Crosswalk hits for DEREG
        coalesce(dc_c.subtype, dc_g.subtype) as d_subtype,
        coalesce(dc_c.bts_aircraft_type, dc_g.bts_aircraft_type) as d_bts_type,
        coalesce(dc_c.seats_total, dc_g.seats_total) as d_seats_total,
        coalesce(dc_c.seats_premium, dc_g.seats_premium) as d_seats_premium,
        coalesce(dc_c.seats_economy, dc_g.seats_economy) as d_seats_economy,

        -- Reverse crosswalk fallback for overrides without FAA match
        coalesce(rc_c.faa_mfr, rc_g.faa_mfr) as ov_rev_mfr,
        coalesce(rc_c.faa_model, rc_g.faa_model) as ov_rev_model
    from flights as f
    left join overrides as ov_c
        on f.tail_num = ov_c.tail_num
        and f.op_carrier = ov_c.op_carrier
    left join overrides as ov_w
        on f.tail_num = ov_w.tail_num
        and ov_w.op_carrier = '*'
    left join faa_master as m
        on f.is_valid_us_n_number
        and f.tail_num_clean = m.n_number
    left join faa_dereg as d
        on f.is_valid_us_n_number
        and m.n_number is null
        and f.tail_num_clean = d.n_number
    left join cw_carrier as mc_c
        on m.faa_manufacturer = mc_c.faa_mfr
        and m.faa_model = mc_c.faa_model
        and f.op_carrier = mc_c.op_carrier
    left join cw_generic as mc_g
        on m.faa_manufacturer = mc_g.faa_mfr
        and m.faa_model = mc_g.faa_model
    left join cw_carrier as dc_c
        on d.faa_manufacturer = dc_c.faa_mfr
        and d.faa_model = dc_c.faa_model
        and f.op_carrier = dc_c.op_carrier
    left join cw_generic as dc_g
        on d.faa_manufacturer = dc_g.faa_mfr
        and d.faa_model = dc_g.faa_model
    left join rev_cw_carrier as rc_c
        on coalesce(ov_c.subtype, ov_w.subtype) = rc_c.subtype
        and f.op_carrier = rc_c.op_carrier
    left join rev_cw_generic as rc_g
        on coalesce(ov_c.subtype, ov_w.subtype) = rc_g.subtype
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

    case
        when tail_num is null then if(is_cancelled, 'UNASSIGNED_CANCELLED', 'UNMATCHED')
        when has_override then 'TAIL_OVERRIDE'
        when not is_valid_us_n_number then 'UNMATCHED_TYPO'
        when m_subtype is not null then 'FAA_MASTER'
        when d_subtype is not null then 'FAA_DEREG'
        else 'UNMATCHED'
    end as aircraft_source,

    case
        when tail_num is null then cast(null as string)
        when has_override then coalesce(m_faa_mfr, d_faa_mfr, ov_rev_mfr)
        when not is_valid_us_n_number then cast(null as string)
        when m_subtype is not null then m_faa_mfr
        when d_subtype is not null then d_faa_mfr
        else coalesce(m_faa_mfr, d_faa_mfr)
    end as faa_manufacturer,

    case
        when tail_num is null then cast(null as string)
        when has_override then coalesce(m_faa_model, d_faa_model, ov_rev_model)
        when not is_valid_us_n_number then cast(null as string)
        when m_subtype is not null then m_faa_model
        when d_subtype is not null then d_faa_model
        else coalesce(m_faa_model, d_faa_model)
    end as faa_model,

    case
        when tail_num is null then cast(null as int64)
        when has_override then coalesce(m_tc_seats, d_tc_seats)
        when not is_valid_us_n_number then cast(null as int64)
        when m_subtype is not null then m_tc_seats
        when d_subtype is not null then d_tc_seats
        else coalesce(m_tc_seats, d_tc_seats)
    end as faa_tc_max_seats,

    case
        when tail_num is null then cast(null as string)
        when has_override then ov_subtype
        when not is_valid_us_n_number then cast(null as string)
        when m_subtype is not null then m_subtype
        when d_subtype is not null then d_subtype
        else cast(null as string)
    end as fleet_subtype,

    case
        when tail_num is null then cast(null as string)
        when has_override then ov_bts_type
        when not is_valid_us_n_number then cast(null as string)
        when m_subtype is not null then m_bts_type
        when d_subtype is not null then d_bts_type
        else cast(null as string)
    end as bts_aircraft_type,

    case
        when tail_num is null then if(is_cancelled, 'UNASSIGNED_CANCELLED', 'UNMATCHED')
        when has_override then 'TAIL_OVERRIDE'
        when not is_valid_us_n_number then 'UNMATCHED'
        when m_subtype is not null or d_subtype is not null then 'SUBTYPE_DEFAULT'
        else 'UNMATCHED'
    end as seat_config_source,

    case
        when tail_num is null then cast(null as int64)
        when has_override then ov_seats_total
        when not is_valid_us_n_number then cast(null as int64)
        when m_subtype is not null then m_seats_total
        when d_subtype is not null then d_seats_total
        else cast(null as int64)
    end as seats_config,

    case
        when tail_num is null then cast(null as int64)
        when has_override then ov_seats_premium
        when not is_valid_us_n_number then cast(null as int64)
        when m_subtype is not null then m_seats_premium
        when d_subtype is not null then d_seats_premium
        else cast(null as int64)
    end as seats_premium,

    case
        when tail_num is null then cast(null as int64)
        when has_override then ov_seats_economy
        when not is_valid_us_n_number then cast(null as int64)
        when m_subtype is not null then m_seats_economy
        when d_subtype is not null then d_seats_economy
        else cast(null as int64)
    end as seats_economy
from joined
