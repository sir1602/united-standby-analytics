-- Staging model for daily national TSA checkpoint screening volumes.
-- Computes monthly-relative TSA demand multiplier M_TSA(d) (alpha=0.35, bounds [0.80, 1.20])
-- and annual-relative TSA ratio r_tsa_year(d) for macro demand signaling.

with parsed as (
    select
        coalesce(
            safe.parse_date('%Y-%m-%d', trim(cast(date_day as string))),
            {{ safe_cast_as('date_day', 'date') }}
        ) as date_day,
        cast(tsa_throughput as int64) as tsa_throughput,
        trim(cast(source_page_year as string)) as source_page_year,
        trim(cast(source_url as string)) as source_url,
        coalesce(nullif(lower(trim(confidence_tier)), ''), 'actual') as confidence_tier
    from {{ source('raw', 'tsa_daily_throughput') }}
    qualify row_number() over (
        partition by coalesce(
            safe.parse_date('%Y-%m-%d', trim(cast(date_day as string))),
            {{ safe_cast_as('date_day', 'date') }}
        )
        order by cast(tsa_throughput as int64) desc
    ) = 1
),

with_means as (
    select
        date_day,
        extract(year from date_day) as year,
        extract(month from date_day) as month,
        tsa_throughput,
        avg(cast(tsa_throughput as float64)) over (
            partition by format_date('%Y-%m', date_day)
        ) as tsa_month_mean,
        avg(cast(tsa_throughput as float64)) over (
            partition by extract(year from date_day)
        ) as tsa_year_mean,
        source_page_year,
        source_url,
        confidence_tier
    from parsed
    where date_day is not null
      and extract(year from date_day) in ({{ var("years") | join(", ") }})
),

with_ratios as (
    select
        date_day,
        year,
        month,
        tsa_throughput,
        safe_divide(cast(tsa_throughput as float64), nullif(tsa_month_mean, 0.0)) as tsa_ratio_month,
        safe_divide(cast(tsa_throughput as float64), nullif(tsa_year_mean, 0.0)) as tsa_ratio_year,
        source_page_year,
        source_url,
        confidence_tier
    from with_means
)

select
    date_day,
    year,
    month,
    mod({{ bq_dayofweek('date_day') }} + 5, 7) + 1 as iso_dow,
    {{ is_holiday_window('date_day') }} as is_holiday_window,
    tsa_throughput,
    tsa_ratio_month,
    {{ bq_least() }}(
        {{ var('tsa_multiplier_max', 1.20) }},
        {{ bq_greatest() }}(
            {{ var('tsa_multiplier_min', 0.80) }},
            1.0 + {{ var('tsa_damping_alpha', 0.35) }} * (tsa_ratio_month - 1.0)
        )
    ) as tsa_demand_multiplier,
    tsa_ratio_year,
    source_page_year,
    source_url,
    confidence_tier
from with_ratios
