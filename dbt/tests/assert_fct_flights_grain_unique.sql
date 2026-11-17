-- Verify natural flight grain uniqueness in fct_flights:
-- (flight_date, mkt_carrier, flight_num_mkt, origin, dest, crs_dep_time) must be 1:1 with flight_id.
select
    flight_date,
    mkt_carrier,
    flight_num_mkt,
    origin,
    dest,
    crs_dep_time,
    count(*) as row_count
from {{ ref('fct_flights') }}
group by
    flight_date,
    mkt_carrier,
    flight_num_mkt,
    origin,
    dest,
    crs_dep_time
having count(*) > 1
