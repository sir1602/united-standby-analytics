-- Staging model for FAA Aircraft Registry (MASTER + DEREG),
-- deduplicated per (n_number, registry_source) by latest cancel_date.

select
    upper(trim(n_number)) as n_number,
    trim(serial_number) as serial_number,
    trim(mfr_mdl_code) as mfr_mdl_code,
    nullif(trim(year_mfr), '') as year_mfr,
    lower(trim(registry_source)) as registry_source,
    nullif(trim(cancel_date), '') as cancel_date
from {{ source('raw', 'faa_aircraft_registry') }}
where nullif(trim(n_number), '') is not null
  and nullif(trim(mfr_mdl_code), '') is not null
qualify row_number() over (
    partition by upper(trim(n_number)), lower(trim(registry_source))
    order by coalesce(trim(cancel_date), '') desc, trim(serial_number) asc
) = 1
