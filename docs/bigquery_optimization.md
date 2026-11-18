# BigQuery Architecture, Partitioning, Clustering & Cost Optimization (`docs/bigquery_optimization.md`)

This document specifies the Google Cloud BigQuery warehouse architecture for `united-standby-analytics`, covering project billing requirements, dataset layout, partitioning and clustering design, cost estimation under the BigQuery Free Tier, and reproducible SQL query patterns.

---

## 1. Billing-Enabled Project vs. BigQuery Sandbox (`D-08`)

**BigQuery Sandbox is explicitly incompatible with this project** and must never be used:
- BigQuery Sandbox enforces a **mandatory 60-day table and partition expiration** anchored to the partition key (`flight_date`).
- Because our scope spans **`2019-01-01` through `2026-08-31`**, any partition older than 60 days from ingestion is **immediately deleted upon creation** by Sandbox — wiping out all `2019`, `2023`, `2024`, `2025`, and early `2026` partitions.

**Required Configuration (`config/standby.yaml` & `src/standby/load/bigquery.py`):**
- Use a **billing-enabled GCP project** (`project_id: flight-standby-analytics`, location `US`; override with the `GCP_PROJECT_ID` environment variable, which `dbt/profiles.yml` also reads) with a **`$1.00` monthly billing budget alert**.
- Explicitly enforce `partition_expiration_ms: null` (`NONE`) on both datasets and partitioned tables (`default_table_expiration_ms = None`, `default_partition_expiration_ms = None` in `src/standby/load/bigquery.py`).
- Operational cost remains **`$0.00/month`** because total storage and monthly query scan volume sit far below the BigQuery Free Tier (**10 GiB active storage/month** and **1 TiB on-demand query processing/month**).

---

## 2. Dataset & Table Layout

| Dataset | Purpose | Producer | Key Tables |
|---|---|---|---|
| **`raw`** | Standardized ingested source tables + dbt seed lookups | `python -m standby load-raw` / `run` + `dbt seed` | `otp_marketing_flights`, `t100_segments`, `faa_aircraft_registry`, `faa_acftref`, `tsa_daily_throughput`, `bts_lookups`, `seat_config`, `tail_overrides`, `faa_t100_crosswalk` |
| **`staging`** | Clean typed views (no duplicate storage cost) | `dbt` (`materialized='view'`) | `stg_otp_flights`, `stg_t100_segments`, `stg_faa_aircraft`, `stg_faa_acftref`, `stg_tsa_throughput`, `stg_bts_airports`, `stg_bts_carriers`, `stg_bts_aircraft_types` |
| **`intermediate`** | Multi-tier enrichment, T-100 LF resolution, displacement/backup prep, and SOI scoring | `dbt` (`materialized='ephemeral'` / `'view'`) | `int_aircraft_enriched`, `int_t100_load_factors`, `int_flight_estimates`, `int_flight_displacements_backups`, `int_flights_soi_scored` |
| **`marts`** | Conformed star-schema analytics marts & BI source tables | `dbt` (`materialized='table'`) | `fct_flights`, `agg_route_month`, `dim_airport`, `dim_aircraft`, `dim_carrier`, `dim_date` |

---

## 3. Partitioning & Clustering Strategy

### 3.1 `raw.otp_marketing_flights` & `marts.fct_flights`
- **Partitioning:** `DAY` time-unit partitioning on **`flight_date`** (`DATE`), `partition_expiration_ms = NULL`.
  - **Partition Count Verification:** Across `2019` (`365` days) + `2023-01-01..2026-08-31` (`1,339` days) = **`1,704` daily partitions** — comfortably below BigQuery's **`4,000` partitions per table** quota (`42.6%` utilization), leaving room for >6 additional years of daily data without switching to `MONTH` partitioning.
  - **Why `DAY` over `MONTH`:** Standby opportunity lookups, same-day IRROPS displacement audits (`Thanksgiving Sunday`, `2024-12-01`), and `recent-12m` / single-day operational queries prune directly to individual calendar days (`1 / 1,704` of the table ≈ `0.06%` bytes scanned).
- **Clustering:** `CLUSTER BY origin, dest, op_carrier` (in that exact column order).
  - `origin`: Primary hub selector (`ORD`, expandable to `DEN`/`IAH`/`EWR`/`SFO`/`IAD`/`LAX` in YAML/dbt vars).
  - `dest`: High-cardinality route spoke filter (`191` domestic spokes).
  - `op_carrier`: Mainline (`UA`) vs. Express partner (`OO`, `YX`, `G7`, `ZW`, `EV`, `AX`, `C5`) filter.

### 3.2 `marts.agg_route_month`
- **Row Count:** `20,877` rows (`18,577` domestic + `2,300` international) — well below BigQuery's 64 MB block-pruning threshold, so partitioning is unnecessary (`table` without partitioning avoids tiny-partition metadata overhead).
- **Clustering:** `CLUSTER BY origin, dest, op_carrier` for fast route and carrier drill-down joins.

### 3.3 Conformed Dimensions (`dim_airport`, `dim_aircraft`, `dim_carrier`, `dim_date`)
- Materialized as compact unpartitioned `table`s (`349`, `1,908`, `9`, and `1,777` rows respectively) for sub-second star-schema joins and zero-overhead BI metadata introspection.

---

## 4. Storage & Query Cost Envelope (Analytical Sizing)

> **Note:** Exact BigQuery billed bytes depend on live BigQuery execution (`DRY RUN` / `INFORMATION_SCHEMA.JOBS`); the figures below are **structural size bounds** computed from actual local row counts (`830,941` flights, `75,863` T-100 rows, `702,892` FAA registry rows) and column schemas, not fabricated live job receipts.

| Asset / Workload | Row Count | Structural Uncompressed Footprint Estimate | Free-Tier Quota | Margin |
|---|---:|---:|---:|---|
| **`raw` tables combined** (`otp` + `t100` + `faa` + `tsa` + `lookups`) | `~1.71M` rows | `< 0.5 GiB` | `10 GiB` active storage/mo | `> 95%` headroom |
| **`marts` tables combined** (`fct_flights` 75 cols + `agg_route_month` + 4 dims) | `~856k` rows | `< 0.4 GiB` | `10 GiB` active storage/mo | `> 96%` headroom |
| **Full `dbt build` (rebuilding `marts`)** | Full scan of `raw` | `< 1 GiB` per build | `1 TiB` (`1,024 GiB`) queries/mo | `> 1,000` full rebuilds/mo within free tier |
| **Partition-pruned daily/route BI query** | `1`–`30` days × `1` spoke | `< 1 MiB` per query | `1 TiB` queries/mo | Effectively `$0.00` |

---

## 5. Unpartitioned vs. Partition-Filtered Benchmark SQL & `bq query --dry_run`

> **Transparency Note:** No GCP project or service-account credentials are attached to this repository's offline CI environment (`DISPATCH.md`). Accordingly, **zero live `totalBytesProcessed` byte counts are fabricated below**. Once you authenticate `gcloud auth login` against your billing-enabled GCP project (`flight-standby-analytics`), run the exact benchmark SQL pair and `bq query --dry_run` commands below to capture live byte-scan metrics.

### 5.1 Benchmark SQL Pair (Same Output, Unpartitioned / Non-Pruned vs. Partition-Pruned + Clustered)

#### Query A1 — Unpartitioned Baseline Table (Full Table Scan Anti-Pattern)
Create an unpartitioned, unclustered baseline copy (optional benchmark fixture) and scan all `830,941` rows across all `1,704` dates:
```sql
-- Optional unpartitioned baseline fixture (for 1:1 storage/scan comparison)
CREATE OR REPLACE TABLE `flight-standby-analytics.marts._bench_fct_flights_unpartitioned` AS
SELECT * FROM `flight-standby-analytics.marts.fct_flights`;

-- Query A1: Scans 100% of _bench_fct_flights_unpartitioned (no partition or cluster pruning)
SELECT
    flight_date,
    crs_dep_time,
    op_carrier,
    fleet_subtype,
    est_open_seats_adjusted,
    ROUND(soi_planning, 2) AS soi_planning
FROM `flight-standby-analytics.marts._bench_fct_flights_unpartitioned`
WHERE flight_date BETWEEN DATE '2025-07-01' AND DATE '2025-07-31'
  AND origin = 'ORD'
  AND dest = 'DEN'
  AND op_carrier = 'UA';
```

#### Query A2 — Non-Prunable Partition Expression Anti-Pattern on `marts.fct_flights`
Wrapping `flight_date` in `FORMAT_DATE(...)` prevents BigQuery partition elimination on `marts.fct_flights`, forcing a full scan across all `1,704` daily partitions:
```sql
-- Query A2: Non-prunable predicate prevents partition elimination on marts.fct_flights
SELECT
    flight_date,
    crs_dep_time,
    op_carrier,
    fleet_subtype,
    est_open_seats_adjusted,
    ROUND(soi_planning, 2) AS soi_planning
FROM `flight-standby-analytics.marts.fct_flights`
WHERE FORMAT_DATE('%Y-%m', flight_date) = '2025-07'
  AND origin = 'ORD'
  AND dest = 'DEN'
  AND op_carrier = 'UA';
```

#### Query B — Partition-Pruned + Clustered Query on `marts.fct_flights` (Recommended)
Direct `DATE` predicate on `flight_date` eliminates `1,673` of `1,704` partitions (`31 / 1,704 ≈ 1.82%` of partitions retained), and `origin = 'ORD' AND dest = 'DEN' AND op_carrier = 'UA'` aligns directly with `CLUSTER BY origin, dest, op_carrier`:
```sql
-- Query B: Partition-pruned (31 / 1,704 days) + clustered block pruning on (origin, dest, op_carrier)
SELECT
    flight_date,
    crs_dep_time,
    op_carrier,
    fleet_subtype,
    est_open_seats_adjusted,
    ROUND(soi_planning, 2) AS soi_planning
FROM `flight-standby-analytics.marts.fct_flights`
WHERE flight_date BETWEEN DATE '2025-07-01' AND DATE '2025-07-31'
  AND origin = 'ORD'
  AND dest = 'DEN'
  AND op_carrier = 'UA';
```

### 5.2 Reproducible `bq query --dry_run` CLI Verification Commands

Run both dry-run commands from a shell authenticated to your GCP project (`$0.00` cost — `--dry_run` estimates `.statistics.totalBytesProcessed` without executing or billing):

```bash
# 1. Baseline A1 (if _bench_fct_flights_unpartitioned exists) or A2 (non-prunable FORMAT_DATE on fct_flights)
bq query --use_legacy_sql=false --dry_run --format=prettyjson \
  "SELECT flight_date, crs_dep_time, op_carrier, fleet_subtype, est_open_seats_adjusted, ROUND(soi_planning, 2) AS soi_planning
   FROM \`flight-standby-analytics.marts._bench_fct_flights_unpartitioned\`
   WHERE flight_date BETWEEN DATE '2025-07-01' AND DATE '2025-07-31'
     AND origin = 'ORD' AND dest = 'DEN' AND op_carrier = 'UA'" \
  | jq -r '.statistics.totalBytesProcessed'

bq query --use_legacy_sql=false --dry_run --format=prettyjson \
  "SELECT flight_date, crs_dep_time, op_carrier, fleet_subtype, est_open_seats_adjusted, ROUND(soi_planning, 2) AS soi_planning
   FROM \`flight-standby-analytics.marts.fct_flights\`
   WHERE FORMAT_DATE('%Y-%m', flight_date) = '2025-07'
     AND origin = 'ORD' AND dest = 'DEN' AND op_carrier = 'UA'" \
  | jq -r '.statistics.totalBytesProcessed'

# 2. Partition-pruned + clustered Query B on marts.fct_flights
bq query --use_legacy_sql=false --dry_run --format=prettyjson \
  "SELECT flight_date, crs_dep_time, op_carrier, fleet_subtype, est_open_seats_adjusted, ROUND(soi_planning, 2) AS soi_planning
   FROM \`flight-standby-analytics.marts.fct_flights\`
   WHERE flight_date BETWEEN DATE '2025-07-01' AND DATE '2025-07-31'
     AND origin = 'ORD' AND dest = 'DEN' AND op_carrier = 'UA'" \
  | jq -r '.statistics.totalBytesProcessed'
```

- **Expected Structural Reduction:** Query B scans only the `31` daily partitions (`2025-07-01..2025-07-31`) out of `1,704` total partitions (`~98.2%` partition elimination before cluster block pruning on `(origin, dest, op_carrier)`), whereas A1 and A2 must scan all `1,704` days (`830,941` rows) for the selected 6 columns. *(Live `totalBytesProcessed` values must be captured on your GCP project; none are fabricated here.)*

### 5.3 Additional Production Query Patterns

#### Post-Hoc Thanksgiving Sunday Audit (Year-Anchored Holiday Window + Completed Flights)
```sql
SELECT
    f.flight_date,
    f.dest,
    f.op_carrier,
    f.hour_block,
    f.est_open_seats_adjusted,
    f.weighted_displacement_hist,
    f.remaining_performed_backups,
    f.soi_historical,
    f.soi_planning
FROM `flight-standby-analytics.marts.fct_flights` AS f
WHERE f.flight_date IN (DATE '2024-12-01', DATE '2025-11-30')
  AND f.origin = 'ORD'
  AND NOT f.is_cancelled
  AND f.soi_historical IS NOT NULL
ORDER BY f.soi_historical ASC
LIMIT 50;
```

#### International Route-Month Lookup (`v1` Route-Month Grain)
```sql
SELECT
    year,
    month,
    op_carrier,
    dest,
    dest_country,
    departures_performed,
    seats,
    passengers,
    ROUND(t100_load_factor, 4) AS t100_load_factor,
    data_granularity
FROM `flight-standby-analytics.marts.agg_route_month`
WHERE origin = 'ORD'
  AND is_international = TRUE
  AND year = 2025
ORDER BY t100_load_factor ASC, departures_performed DESC;
```

---

## 6. Operational Best Practices

1. **Always Filter on `flight_date`:** Include explicit `flight_date` predicates (`BETWEEN` / `IN` / `>=`) rather than wrapping `flight_date` in functions like `FORMAT_DATE(...)` or `EXTRACT(YEAR FROM flight_date)` so BigQuery triggers partition elimination.
2. **Respect `require_partition_filter` Option:** Once interactive BI extracts are cached in Tableau Public, enabling `require_partition_filter = true` on `fct_flights` prevents accidental full-table scans by ad-hoc SQL consumers.
3. **Avoid `SELECT *` on `fct_flights`:** Select only required columns (75 columns total) — BigQuery's Capacitor columnar storage bills strictly on referenced columns.
4. **Refresh Cadence:** BTS releases monthly OTP and T-100 batches monthly; re-running `python -m standby run && dbt build` refreshes the full `~831k`-row `marts` layer cleanly in `WRITE_TRUNCATE` / full-refresh mode well inside the monthly free tier.

---

## 7. Live Warehouse: `flight-standby-analytics`

**Status (2026-11-18): set up and loaded with the committed sample slice, not the full dataset.**

| Item | State |
|---|---|
| GCP project | `flight-standby-analytics` (billing enabled; datasets show no Sandbox-imposed 60-day default expiration) |
| Datasets | `raw`, `staging`, `intermediate`, `marts`, all in `US`, no default table or partition expiration |
| `raw` tables | `otp_marketing_flights` (80 rows; `DAY`-partitioned on `flight_date`, clustered by `origin, dest, op_carrier`), `t100_segments` (1,472), `bts_lookups` (810), `faa_acftref` (19), `faa_aircraft_registry` (17), `tsa_daily_throughput` (62), plus the dbt seeds `seat_config` (31), `tail_overrides` (152), `faa_t100_crosswalk` (56) |
| `staging` / `intermediate` | All 8 staging and 5 intermediate dbt models, as views |
| `marts` | `fct_flights` (80 rows; `DAY`-partitioned on `flight_date`, clustered by `origin, dest, op_carrier`), `agg_route_month`, `dim_aircraft`, `dim_airport`, `dim_carrier`, `dim_date`, as tables |

### How the sample was loaded

The cloud session that set this up had no direct GCP credentials, only a SQL-capable BigQuery connector. So the load ran as SQL instead of through `standby run` and `dbt build`:

1. `python -m standby load-raw --sample --dry-run` produced the raw tables. Each table's schema, partitioning and clustering came from `standby.load.bigquery.build_load_job_config`, the same function the live loader uses. The rows were inserted with `CREATE TABLE` + `INSERT`. The OTP table therefore has the 31-column `load-raw` shape, which `stg_otp_flights` reads identically to the enriched `run` shape.
2. The dbt seeds were loaded into `raw` using the `+column_types` declared in `dbt/dbt_project.yml`.
3. Every model was built from dbt's own compiled SQL (`dbt compile`), in dependency order. Views and tables follow each model's `materialized`, `partition_by` and `cluster_by` config.

### Verification

- **Raw layer:** all 108 per-column aggregates match the local source frames exactly (row counts, non-null counts, numeric sums, MD5-sum fingerprints of string and date columns) across all 9 raw tables.
- **SOI parity:** `marts.fct_flights` matches the Python pipeline's scored output for the same sample on 24 metrics, within 1e-9 relative tolerance. These are flight-ID-hash-weighted sums of `soi_historical`, `soi_planning`, all 8 sub-scores, load factors, open seats, `seats_config`, displacement, backups and TSA ratio, plus the `lf_source`, `aircraft_source`, `seat_config_source`, `fleet_subtype` and `hour_block` distributions. Counts: 70 `soi_historical` and 20 `soi_planning` scored, as in Python.
- **dbt tests:** all **231 of 231** tests in the project pass against the live warehouse, with 0 failing rows. That covers 117 `not_null`, 57 `accepted_range`, 21 `unique`, 12 `accepted_values`, 10 `relationships`, 7 `not_null_where` and 3 `unique_combination_of_columns`, plus the 4 singular tests (`fct_flights` grain, `SC-1` passenger bounds, `SC-5` no silent T-100 imputation, SOI leakage & bounds). Each test ran its exact dbt-compiled SQL.

### Loading the full dataset

Run these from a machine with Application Default Credentials for the project, and with `data/raw/` populated per [`docs/data_acquisition.md`](data_acquisition.md):

```bash
gcloud auth application-default login
make bq-load     # standby run without --dry-run: WRITE_TRUNCATE loads into raw.*
make dbt-build   # dbt seed + run + test against the dev target (oauth / ADC)
```

`make bq-load` replaces each raw table, and `make dbt-build` recreates the seeds, views and marts. Nothing from the sample load needs to be removed first. Set `GCP_PROJECT_ID` to point both the loader and dbt at a different project.
