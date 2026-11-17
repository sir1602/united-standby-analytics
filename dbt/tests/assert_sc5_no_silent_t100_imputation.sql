-- Section 7 Sanity Check #5 (SC-5): Zero Silent T-100 Imputation.
-- Fails if any flight without a valid T-100 match (not t100_available or lf_source = 'UNMATCHED')
-- has non-null estimated loads, passenger/open-seat estimates, or SOI values.
select
    flight_id,
    flight_date,
    op_carrier,
    origin,
    dest,
    t100_available,
    lf_source,
    is_cancelled,
    est_load_factor,
    est_passengers,
    est_open_seats,
    est_lf_adjusted,
    est_passengers_adjusted,
    est_open_seats_adjusted,
    soi_historical,
    soi_planning
from {{ ref('fct_flights') }}
where (
    (
        not t100_available
        or lf_source = 'UNMATCHED'
    )
    and (
        est_load_factor is not null
        or est_passengers is not null
        or est_open_seats is not null
        or est_lf_adjusted is not null
        or est_passengers_adjusted is not null
        or est_open_seats_adjusted is not null
        or soi_historical is not null
        or soi_planning is not null
    )
)
or (
    is_cancelled
    and soi_historical is not null
)
