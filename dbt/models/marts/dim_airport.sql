-- Conformed Airport Dimension (`dim_airport`).
-- Primary Key: `airport_code` (IATA), avoiding the DOT airport_id=14027 collision (PBI vs DJT).

with otp_airports as (
    select
        origin as airport_code,
        origin_airport_id as airport_id,
        'US' as country_code,
        true as has_domestic_service,
        false as has_international_service
    from {{ ref('stg_otp_flights') }}
    union all
    select
        dest as airport_code,
        dest_airport_id as airport_id,
        'US' as country_code,
        true as has_domestic_service,
        false as has_international_service
    from {{ ref('stg_otp_flights') }}
),

t100_ua_airports as (
    select
        origin as airport_code,
        origin_airport_id as airport_id,
        origin_country as country_code,
        not is_international as has_domestic_service,
        is_international as has_international_service
    from {{ ref('stg_t100_segments') }}
    where is_ua_family = true
    union all
    select
        dest as airport_code,
        dest_airport_id as airport_id,
        dest_country as country_code,
        not is_international as has_domestic_service,
        is_international as has_international_service
    from {{ ref('stg_t100_segments') }}
    where is_ua_family = true
),

combined_airports as (
    select
        airport_code,
        max(airport_id) as observed_airport_id,
        max(country_code) as country_code,
        logical_or(has_domestic_service) as has_domestic_service,
        logical_or(has_international_service) as has_international_service
    from (
        select * from otp_airports
        union all
        select * from t100_ua_airports
    )
    where airport_code is not null
    group by airport_code
)

select
    c.airport_code,
    coalesce(c.observed_airport_id, lkp.airport_id) as airport_id,
    lkp.airport_name,
    coalesce(c.country_code, 'US') as country_code,
    c.airport_code in ('{{ var("hubs") | join("', '") }}') as is_hub,
    c.has_domestic_service,
    c.has_international_service,
    'actual' as confidence_tier
from combined_airports as c
left join {{ ref('stg_bts_airports') }} as lkp
    on c.airport_code = lkp.airport_code
