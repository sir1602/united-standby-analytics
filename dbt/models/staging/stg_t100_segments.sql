-- Staging model for BTS T-100 Segment (All Carriers) domestic and international segments.

with source as (
    select
        cast(year as int64) as year,
        cast(quarter as int64) as quarter,
        cast(month as int64) as month,
        upper(trim(op_carrier)) as op_carrier,
        trim(carrier_name) as carrier_name,
        upper(trim(origin)) as origin,
        cast(origin_airport_id as int64) as origin_airport_id,
        upper(trim(origin_country)) as origin_country,
        upper(trim(dest)) as dest,
        cast(dest_airport_id as int64) as dest_airport_id,
        upper(trim(dest_country)) as dest_country,
        trim(cast(bts_aircraft_type as string)) as bts_aircraft_type,
        cast(aircraft_group as int64) as aircraft_group,
        cast(aircraft_config as int64) as aircraft_config,
        upper(trim(service_class)) as service_class,
        upper(trim(data_source)) as data_source,
        cast(is_international as bool) as is_international,
        cast(departures_scheduled as float64) as departures_scheduled,
        cast(departures_performed as float64) as departures_performed,
        cast(seats as float64) as seats,
        cast(passengers as float64) as passengers,
        cast(distance_miles as float64) as distance_miles,
        coalesce(nullif(lower(trim(confidence_tier)), ''), 'actual') as confidence_tier
    from {{ source('raw', 't100_segments') }}
    where upper(trim(service_class)) = 'F'
      and upper(trim(origin)) in ('{{ var("hubs") | map("upper") | join("', '") }}')
      and cast(year as int64) in ({{ var("years") | join(", ") }})
)

select
    year,
    max(quarter) as quarter,
    month,
    op_carrier,
    max(carrier_name) as carrier_name,
    origin,
    max(origin_airport_id) as origin_airport_id,
    max(origin_country) as origin_country,
    dest,
    max(dest_airport_id) as dest_airport_id,
    max(dest_country) as dest_country,
    bts_aircraft_type,
    max(aircraft_group) as aircraft_group,
    max(aircraft_config) as aircraft_config,
    max(service_class) as service_class,
    data_source,
    max(is_international) as is_international,
    max(op_carrier in ('{{ var("ua_family_carriers") | join("', '") }}')) as is_ua_family,
    sum(departures_scheduled) as departures_scheduled,
    sum(departures_performed) as departures_performed,
    sum(seats) as seats,
    sum(passengers) as passengers,
    max(distance_miles) as distance_miles,
    {{ bq_least() }}(1.0, {{ bq_greatest() }}(0.0, safe_divide(sum(passengers), nullif(sum(seats), 0.0)))) as load_factor,
    safe_divide(sum(seats), nullif(sum(departures_performed), 0.0)) as seats_per_departure,
    max(confidence_tier) as confidence_tier
from source
group by
    year,
    month,
    op_carrier,
    origin,
    dest,
    bts_aircraft_type,
    data_source
