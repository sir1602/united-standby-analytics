-- Staging model for BTS Marketing Carrier On-Time Performance flights.
-- Selects ONLY the 31 base standardized columns so it works identically whether
-- `raw.otp_marketing_flights` was populated by `load-raw` (31 cols) or `run` (54+ cols).

with source as (
    select
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
        confidence_tier
    from {{ source('raw', 'otp_marketing_flights') }}
    where upper(trim(origin)) in ('{{ var("hubs") | join("', '") }}')
      and cast(year as int64) in ({{ var("years") | join(", ") }})
      and upper(trim(mkt_carrier)) = upper('{{ var("marketing_carrier") }}')
),

normalized as (
    select
        cast(flight_date as date) as flight_date,
        cast(year as int64) as year,
        cast(quarter as int64) as quarter,
        cast(month as int64) as month,
        cast(day_of_month as int64) as day_of_month,
        cast(day_of_week as int64) as day_of_week,
        upper(trim(mkt_carrier)) as mkt_carrier,
        upper(trim(op_carrier)) as op_carrier,
        trim(cast(flight_num_mkt as string)) as flight_num_mkt,
        nullif(trim(cast(flight_num_op as string)), '') as flight_num_op,
        case
            when nullif(upper(trim(cast(tail_num as string))), '') is null
              or upper(trim(cast(tail_num as string))) in ('NAN', 'NONE')
                then cast(null as string)
            when starts_with(upper(trim(cast(tail_num as string))), 'N')
                then upper(trim(cast(tail_num as string)))
            else concat('N', upper(trim(cast(tail_num as string))))
        end as tail_num_norm,
        upper(trim(origin)) as origin,
        cast(origin_airport_id as int64) as origin_airport_id,
        upper(trim(dest)) as dest,
        cast(dest_airport_id as int64) as dest_airport_id,
        case
            when nullif(trim(cast(crs_dep_time as string)), '') is null then cast(null as string)
            when lpad(regexp_replace(trim(cast(crs_dep_time as string)), r'\.0+$', ''), 4, '0') = '2400' then '0000'
            else lpad(regexp_replace(trim(cast(crs_dep_time as string)), r'\.0+$', ''), 4, '0')
        end as crs_dep_time,
        case
            when nullif(trim(cast(dep_time as string)), '') is null then cast(null as string)
            when lpad(regexp_replace(trim(cast(dep_time as string)), r'\.0+$', ''), 4, '0') = '2400' then '0000'
            else lpad(regexp_replace(trim(cast(dep_time as string)), r'\.0+$', ''), 4, '0')
        end as dep_time,
        case
            when nullif(trim(cast(crs_arr_time as string)), '') is null then cast(null as string)
            when lpad(regexp_replace(trim(cast(crs_arr_time as string)), r'\.0+$', ''), 4, '0') = '2400' then '0000'
            else lpad(regexp_replace(trim(cast(crs_arr_time as string)), r'\.0+$', ''), 4, '0')
        end as crs_arr_time,
        case
            when nullif(trim(cast(arr_time as string)), '') is null then cast(null as string)
            when lpad(regexp_replace(trim(cast(arr_time as string)), r'\.0+$', ''), 4, '0') = '2400' then '0000'
            else lpad(regexp_replace(trim(cast(arr_time as string)), r'\.0+$', ''), 4, '0')
        end as arr_time,
        cast(dep_delay_min as float64) as dep_delay_min,
        cast(arr_delay_min as float64) as arr_delay_min,
        cast(is_cancelled as bool) as is_cancelled,
        nullif(upper(trim(cast(cancellation_code as string))), '') as cancellation_code,
        cast(is_diverted as bool) as is_diverted,
        cast(crs_elapsed_time_min as float64) as crs_elapsed_time_min,
        cast(actual_elapsed_time_min as float64) as actual_elapsed_time_min,
        cast(distance_miles as float64) as distance_miles,
        coalesce(nullif(lower(trim(cast(confidence_tier as string))), ''), 'actual') as confidence_tier
    from source
),

with_derived_fields as (
    select
        concat(
            format_date('%Y%m%d', flight_date),
            '|', mkt_carrier,
            '|', flight_num_mkt,
            '|', origin,
            '|', dest,
            '|', crs_dep_time
        ) as flight_id,
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
        tail_num_norm as tail_num,
        case
            when tail_num_norm is null then cast(null as string)
            else substr(tail_num_norm, 2)
        end as tail_num_clean,
        case
            when tail_num_norm is null then false
            else regexp_contains(tail_num_norm, r'^N[1-9][0-9A-Z]{0,4}$')
        end as is_valid_us_n_number,
        origin,
        origin_airport_id,
        dest,
        dest_airport_id,
        crs_dep_time,
        dep_time,
        crs_arr_time,
        arr_time,
        mod(cast(floor(cast(crs_dep_time as int64) / 100) as int64), 24) as dep_hour,
        dep_delay_min,
        arr_delay_min,
        is_cancelled,
        cancellation_code,
        is_diverted,
        crs_elapsed_time_min,
        actual_elapsed_time_min,
        distance_miles,
        confidence_tier
    from normalized
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
    is_valid_us_n_number,
    origin,
    origin_airport_id,
    dest,
    dest_airport_id,
    crs_dep_time,
    dep_time,
    crs_arr_time,
    arr_time,
    dep_hour,
    {{ assign_hour_block('dep_hour') }} as hour_block,
    dep_delay_min,
    arr_delay_min,
    is_cancelled,
    cancellation_code,
    is_diverted,
    crs_elapsed_time_min,
    actual_elapsed_time_min,
    distance_miles,
    confidence_tier
from with_derived_fields
