-- Phase 4 Leakage & Bounds Singular Test:
-- 1. The minimum analysis year (e.g. 2019, which has no prior training years) must NEVER receive
--    non-null `soi_planning` or `s_*_plan` scores.
-- 2. All non-null SOI composite and sub-score columns must lie strictly within [0.0, 100.0].
with bounds_check as (
    select
        min(year) as min_year
    from {{ ref('fct_flights') }}
)

select
    f.flight_id,
    f.year,
    f.soi_historical,
    f.soi_planning,
    f.s_open_hist,
    f.s_disp_hist,
    f.s_backup_hist,
    f.s_demand_hist,
    f.s_open_plan,
    f.s_disp_plan,
    f.s_backup_plan,
    f.s_demand_plan
from {{ ref('fct_flights') }} as f
cross join bounds_check as b
where (
    f.year = b.min_year
    and (
        f.soi_planning is not null
        or f.s_open_plan is not null
        or f.s_disp_plan is not null
        or f.s_backup_plan is not null
        or f.s_demand_plan is not null
    )
)
or f.soi_historical < 0.0 or f.soi_historical > 100.0
or f.soi_planning < 0.0 or f.soi_planning > 100.0
or f.s_open_hist < 0.0 or f.s_open_hist > 100.0
or f.s_disp_hist < 0.0 or f.s_disp_hist > 100.0
or f.s_backup_hist < 0.0 or f.s_backup_hist > 100.0
or f.s_demand_hist < 0.0 or f.s_demand_hist > 100.0
or f.s_open_plan < 0.0 or f.s_open_plan > 100.0
or f.s_disp_plan < 0.0 or f.s_disp_plan > 100.0
or f.s_backup_plan < 0.0 or f.s_backup_plan > 100.0
or f.s_demand_plan < 0.0 or f.s_demand_plan > 100.0
