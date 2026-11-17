-- Intermediate model: Deterministic same-day displacement pressure and backup counts
-- per (origin, dest, flight_date) preparing Phase 4 SOI scoring.
-- Tie-breaker order: (crs_dep_time ASC, is_cancelled DESC, flight_num_mkt ASC, op_carrier ASC).
-- Backup counts use strict crs_dep_time > current so tied departures never self-count.

with ordered_flights as (
    select
        flight_id,
        flight_date,
        origin,
        dest,
        op_carrier,
        flight_num_mkt,
        crs_dep_time,
        is_cancelled,
        is_diverted,
        row_number() over (
            partition by origin, dest, flight_date
            order by crs_dep_time asc, is_cancelled desc, flight_num_mkt asc, op_carrier asc
        ) as route_day_seq
    from {{ ref('stg_otp_flights') }}
),

disrupted_flights as (
    select
        origin,
        dest,
        flight_date,
        route_day_seq,
        case
            when is_cancelled then 1.0
            when is_diverted then {{ var('disp_diversion_weight', 0.50) }}
            else 0.0
        end as disruption_weight
    from ordered_flights
    where is_cancelled or is_diverted
),

earlier_disruptions as (
    select
        i.flight_id,
        coalesce(
            sum(
                j.disruption_weight * pow(
                    {{ var('disp_hist_decay', 0.75) }},
                    cast(i.route_day_seq - j.route_day_seq - 1 as float64)
                )
            ),
            0.0
        ) as weighted_displacement_hist,
        count(j.route_day_seq) as prior_disruption_count
    from ordered_flights as i
    left join disrupted_flights as j
        on i.origin = j.origin
        and i.dest = j.dest
        and i.flight_date = j.flight_date
        and j.route_day_seq < i.route_day_seq
    group by i.flight_id
),

same_day_strict_counts as (
    select
        i.flight_id,
        countif(j.crs_dep_time < i.crs_dep_time) as prior_strict_scheduled_count,
        countif(j.crs_dep_time > i.crs_dep_time) as remaining_scheduled_backups,
        countif(j.crs_dep_time > i.crs_dep_time and not j.is_cancelled) as remaining_performed_backups
    from ordered_flights as i
    left join ordered_flights as j
        on i.origin = j.origin
        and i.dest = j.dest
        and i.flight_date = j.flight_date
    group by i.flight_id
)

select
    o.flight_id,
    o.flight_date,
    o.origin,
    o.dest,
    o.route_day_seq,
    o.route_day_seq - 1 as prior_scheduled_count,
    coalesce(lb.prior_strict_scheduled_count, 0) as prior_strict_scheduled_count,
    coalesce(ed.prior_disruption_count, 0) as prior_disruption_count,
    coalesce(ed.weighted_displacement_hist, 0.0) as weighted_displacement_hist,
    coalesce(lb.remaining_scheduled_backups, 0) as remaining_scheduled_backups,
    coalesce(lb.remaining_performed_backups, 0) as remaining_performed_backups
from ordered_flights as o
left join earlier_disruptions as ed
    on o.flight_id = ed.flight_id
left join same_day_strict_counts as lb
    on o.flight_id = lb.flight_id
