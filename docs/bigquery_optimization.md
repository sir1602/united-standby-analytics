# BigQuery Architecture, Partitioning, Clustering & Cost Optimization (`docs/bigquery_optimization.md`)

This document specifies the Google Cloud BigQuery warehouse architecture for `united-standby-analytics`, covering project billing requirements, dataset layout, partitioning and clustering design, cost estimation under the BigQuery Free Tier, and reproducible SQL query patterns.

---

## 1. Billing-Enabled Project vs. BigQuery Sandbox (`D-08`)

**BigQuery Sandbox is explicitly incompatible with this project** and must never be used:
- BigQuery Sandbox enforces a **mandatory 60-day table and partition expiration** anchored to the partition key (`flight_date`).
- Because our scope spans **`2019-01-01` through `2026-08-31`**, any partition older than 60 days from ingestion is **immediately deleted upon creation** by Sandbox — wiping out all `2019`, `2023`, `2024`, `2025`, and early `2026` partitions.

**Required Configuration (`config/standby.yaml` & `src/standby/load/bigquery.py`):**
- Use a **billing-enabled GCP project** (`project_id: united-standby-analytics`, location `US`) with a **`$1.00` monthly billing budget alert**.
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

## 5. Partition- & Cluster-Pruning Query Examples

### 5.1 Prospective Route Planning Query (Prunes by `flight_date` Partition + `origin, dest` Cluster)
```sql
SELECT
    flight_date,
    crs_dep_time,
    hour_block,
    op_carrier,
    fleet_subtype,
    seats_config,
    est_open_seats_adjusted,
    remaining_scheduled_backups,
    ROUND(soi_planning, 2) AS soi_planning
FROM `united-standby-analytics.marts.fct_flights`
WHERE flight_date BETWEEN DATE '2026-07-01' AND DATE '2026-07-07'
  AND origin = 'ORD'
  AND dest = 'DEN'
  AND soi_planning IS NOT NULL
ORDER BY flight_date, soi_planning DESC;
```

### 5.2 Post-Hoc Thanksgiving Sunday Audit (Year-Anchored Holiday Window + Completed Flights)
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
FROM `united-standby-analytics.marts.fct_flights` AS f
WHERE f.flight_date IN (DATE '2024-12-01', DATE '2025-11-30')
  AND f.origin = 'ORD'
  AND NOT f.is_cancelled
  AND f.soi_historical IS NOT NULL
ORDER BY f.soi_historical ASC
LIMIT 50;
```

### 5.3 International Route-Month Lookup (`v1` Route-Month Grain)
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
FROM `united-standby-analytics.marts.agg_route_month`
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
