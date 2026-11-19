-- Looker Studio airport geo lookup: city / region parsed from the BTS L_AIRPORT
-- 'City, ST: Airport Name' text, plus a Google Maps `geo_query` (Geo -> Address).
-- Mirrors `_parse_airport_location` / `_build_dest_geo_query` in src/standby/export/.

with parsed as (
    select
        airport_code,
        airport_name,
        country_code,
        trim(split(airport_name, ':')[safe_offset(0)]) as loc_part
    from {{ ref('dim_airport') }}
),

split_loc as (
    select
        airport_code,
        airport_name,
        country_code,
        if(strpos(loc_part, ',') > 0, nullif(trim(regexp_extract(loc_part, r'^(.*),[^,]*$')), ''), nullif(loc_part, '')) as city_name,
        if(strpos(loc_part, ',') > 0, nullif(trim(regexp_extract(loc_part, r',([^,]*)$')), ''), null) as state_or_country
    from parsed
)

select
    airport_code,
    airport_name,
    city_name,
    state_or_country,
    upper(coalesce(country_code, 'US')) as country_code,
    array_to_string(
        array_concat(
            [concat(airport_code, ' Airport')],
            if(city_name is not null, [city_name], []),
            if(state_or_country is not null and lower(state_or_country) != lower(coalesce(city_name, '')), [state_or_country], []),
            if(lower(upper(coalesce(country_code, 'US'))) != lower(coalesce(state_or_country, '')), [upper(coalesce(country_code, 'US'))], [])
        ),
        ', '
    ) as geo_query
from split_loc
