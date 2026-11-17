-- Conformed Date Dimension (`dim_date`).
-- Primary Key: `date_day` (DATE) with US holiday window flags and daily TSA throughput signals.

with date_bounds as (
    select
        min(d) as min_date,
        max(d) as max_date
    from (
        select flight_date as d from {{ ref('stg_otp_flights') }}
        union all
        select date_day as d from {{ ref('stg_tsa_throughput') }}
    )
),

date_spine as (
    select date_day
    from date_bounds,
    unnest(generate_date_array(min_date, max_date, interval 1 day)) as date_day
    where extract(year from date_day) in ({{ var('years') | join(', ') }})
)

select
    s.date_day,
    extract(year from s.date_day) as year,
    extract(quarter from s.date_day) as quarter,
    extract(month from s.date_day) as month,
    extract(day from s.date_day) as day_of_month,
    -- ISO day of week (1=Monday .. 7=Sunday), matching BTS OTP DayOfWeek
    mod(extract(dayofweek from s.date_day) + 5, 7) + 1 as day_of_week,
    format_date('%A', s.date_day) as day_name,
    (mod(extract(dayofweek from s.date_day) + 5, 7) + 1) in (6, 7) as is_weekend,
    {{ is_holiday_window('s.date_day') }} as is_holiday_window,
    {{ holiday_name_for_date('s.date_day') }} as holiday_name,
    tsa.tsa_throughput,
    tsa.tsa_ratio_month,
    tsa.tsa_demand_multiplier,
    tsa.tsa_ratio_year
from date_spine as s
left join {{ ref('stg_tsa_throughput') }} as tsa
    on s.date_day = tsa.date_day
