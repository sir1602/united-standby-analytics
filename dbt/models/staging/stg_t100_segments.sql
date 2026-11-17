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
      and upper(trim(origin)) in ('{{ var("hubs") | join("', '") }}')
      and cast(year as int64) in ({{ var("years") | join(", ") }})
)

select
    year,
    quarter,
    month,
    op_carrier,
    carrier_name,
    origin,
    origin_airport_id,
    origin_country,
    dest,
    dest_airport_id,
    dest_country,
    bts_aircraft_type,
    aircraft_group,
    aircraft_config,
    service_class,
    data_source,
    is_international,
    op_carrier in ('{{ var("ua_family_carriers") | join("', '") }}') as is_ua_family,
    departures_scheduled,
    departures_performed,
    seats,
    passengers,
    distance_miles,
    least(1.0, greatest(0.0, safe_divide(passengers, nullif(seats, 0.0)))) as load_factor,
    safe_divide(seats, nullif(departures_performed, 0.0)) as seats_per_departure,
    confidence_tier
from source
