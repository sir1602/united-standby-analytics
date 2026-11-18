-- Looker Studio route-month source: agg_route_month + carrier, destination geo,
-- and domestic monthly SOI rollups (NULL on international T-100-only rows).

with soi as (
    select
        year,
        month,
        op_carrier,
        origin,
        dest,
        avg(soi_historical) as otp_avg_soi_historical,
        avg(soi_planning) as otp_avg_soi_planning
    from {{ ref('fct_flights') }}
    group by 1, 2, 3, 4, 5
)

select
    r.*,
    format('%d-%02d', r.year, r.month) as year_month,
    concat(r.origin, ' -> ', r.dest) as route_label,
    c.carrier_name,
    c.carrier_group,
    c.is_mainline,
    c.is_shared_cpa_carrier,
    g.airport_name as dest_airport_name,
    g.city_name as dest_city_name,
    g.state_or_country as dest_state_or_country,
    g.geo_query as dest_geo_query,
    if(r.is_international, null, s.otp_avg_soi_historical) as otp_avg_soi_historical,
    if(r.is_international, null, s.otp_avg_soi_planning) as otp_avg_soi_planning
from {{ ref('agg_route_month') }} as r
left join {{ ref('dim_carrier') }} as c
    on r.op_carrier = c.op_carrier
left join {{ ref('lk_airport_geo') }} as g
    on r.dest = g.airport_code
left join soi as s
    on r.year = s.year
    and r.month = s.month
    and r.op_carrier = s.op_carrier
    and r.origin = s.origin
    and r.dest = s.dest
