-- Staging model for FAA Aircraft Registry (MASTER + DEREG): one row per registration record.
-- No per-n_number deduplication here: the "latest cancel_date" choice is made in
-- int_aircraft_enriched AFTER the stg_faa_acftref seat-floor join, so a later-cancelled
-- sub-20-seat record (e.g. a 2-seat Boeing Stearman) cannot shadow a commercial airliner (D-19).

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
