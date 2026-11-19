{{ config(partition_by={"field": "flight_date", "data_type": "date", "granularity": "day"}, cluster_by=["dest", "op_carrier", "hour_block"]) }}

-- Looker Studio flight-level source (all fct_flights rows, live, no 100k sample cap).
-- Same pre-joined labels as exports/looker/looker_flights.csv, so the dashboard spec
-- (docs/looker_studio_dashboard_spec.md) works unchanged against BigQuery.

select
    f.*,
    format('%d-%02d', f.year, f.month) as year_month,
    case f.day_of_week
        when 1 then '1 - Mon' when 2 then '2 - Tue' when 3 then '3 - Wed'
        when 4 then '4 - Thu' when 5 then '5 - Fri' when 6 then '6 - Sat'
        when 7 then '7 - Sun'
    end as day_of_week_label,
    f.day_of_week in (6, 7) as is_weekend,
    case lower(trim(f.hour_block))
        when 'early' then '1 - Early (05-07)'
        when 'morning' then '2 - Morning (08-10)'
        when 'midday' then '3 - Midday (11-13)'
        when 'afternoon' then '4 - Afternoon (14-16)'
        when 'evening' then '5 - Evening (17-19)'
        when 'late' then '6 - Late (20-04)'
        else f.hour_block
    end as hour_block_label,
    c.carrier_name,
    c.carrier_group,
    c.is_mainline,
    c.is_shared_cpa_carrier,
    concat(f.origin, ' -> ', f.dest) as route_label,
    g.city_name as dest_city_name,
    g.state_or_country as dest_state_or_country,
    g.country_code as dest_country_code,
    case
        when f.soi_historical is null then 'Unscored (NULL)'
        when f.soi_historical >= 70 then 'Favorable (70-100)'
        when f.soi_historical >= 50 then 'Moderate (50-69.99)'
        else 'Tight (0-49.99)'
    end as soi_historical_tier,
    case
        when f.soi_planning is null then 'Unscored (NULL)'
        when f.soi_planning >= 70 then 'Favorable (70-100)'
        when f.soi_planning >= 50 then 'Moderate (50-69.99)'
        else 'Tight (0-49.99)'
    end as soi_planning_tier
from {{ ref('fct_flights') }} as f
left join {{ ref('dim_carrier') }} as c
    on f.op_carrier = c.op_carrier
left join {{ ref('lk_airport_geo') }} as g
    on f.dest = g.airport_code
