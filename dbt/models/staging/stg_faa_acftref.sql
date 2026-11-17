-- Staging model for FAA ACFTREF aircraft reference table,
-- filtered to commercial airliner manufacturers with faa_tc_max_seats >= 20 (D-19).

select
    trim(code) as code,
    upper(trim(faa_manufacturer)) as faa_manufacturer,
    upper(trim(faa_model)) as faa_model,
    cast(faa_tc_max_seats as int64) as faa_tc_max_seats,
    cast(is_commercial_airliner as bool) as is_commercial_airliner
from {{ source('raw', 'faa_acftref') }}
where cast(is_commercial_airliner as bool) = true
  and cast(faa_tc_max_seats as int64) >= {{ var('min_faa_tc_max_seats', 20) }}
qualify row_number() over (partition by trim(code) order by trim(code)) = 1
