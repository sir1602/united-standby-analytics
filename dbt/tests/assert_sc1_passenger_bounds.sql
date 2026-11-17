-- Section 7 Sanity Check #1 (SC-1): Passenger & Load Factor Bounds on fct_flights.
-- Fails if any row has est_passengers outside [0, seats_config], est_load_factor outside [0, 1],
-- or adjusted estimates violating cabin capacity bounds.
select
    flight_id,
    flight_date,
    op_carrier,
    origin,
    dest,
    seats_config,
    est_load_factor,
    est_passengers,
    est_open_seats,
    est_lf_adjusted,
    est_passengers_adjusted,
    est_open_seats_adjusted
from {{ ref('fct_flights') }}
where (
    est_load_factor is not null
    and (est_load_factor < 0.0 or est_load_factor > 1.0)
)
or (
    est_lf_adjusted is not null
    and (est_lf_adjusted < 0.0 or est_lf_adjusted > 1.0)
)
or (
    seats_config is not null
    and est_passengers is not null
    and (
        est_open_seats is null
        or est_passengers < 0
        or est_passengers > seats_config
        or est_open_seats < 0
        or est_open_seats > seats_config
        or est_passengers + est_open_seats != seats_config
    )
)
or (
    seats_config is not null
    and est_passengers_adjusted is not null
    and (
        est_open_seats_adjusted is null
        or est_passengers_adjusted < 0
        or est_passengers_adjusted > seats_config
        or est_open_seats_adjusted < 0
        or est_open_seats_adjusted > seats_config
        or est_passengers_adjusted + est_open_seats_adjusted != seats_config
    )
)
