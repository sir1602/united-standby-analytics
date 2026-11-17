# United Airlines Standby Opportunity Analytics (`united-standby-analytics`)

> **Disclaimer:** This project is an independent, portfolio-grade data engineering and analytics engineering project built strictly from public U.S. government aviation datasets (BTS, FAA, TSA) and curated public aircraft reference data. The **Standby Opportunity Index (SOI)** is a transparent **0–100 relative heuristic**, **NOT a probability of boarding**. No public dataset contains actual gate standby lists or non-rev clearance outcomes. This project is **not affiliated with, endorsed by, or sponsored by United Airlines, Inc.**

---

## Overview

**United Airlines Standby Opportunity Analytics** estimates a **0–100 Standby Opportunity Index** for **employee / non-rev standby** travelers departing **Chicago O'Hare (`ORD`)** on **United Airlines mainline (`UA`)** and **United Express** regional partners (`OO`, `YX`, `YV`, `G7`, `C5`, `ZW`, plus pre-COVID 2019 partners `EV` and `AX`).

Non-revenue standby travelers board only after all revenue passengers — including revenue passengers reaccommodated from earlier cancelled or diverted flights — have been seated. Because flight-level passenger counts and gate standby lists are proprietary, this system combines five public aviation datasets with a curated fleet-subtype seat-configuration reference to model four components:

1. **Open Seats (`50%` weight):** Estimated open seats (`est_open_seats_adjusted`) and open-seat percentage derived by matching aircraft tail numbers (`TAIL_NUM`) to the FAA Aircraft Registry (`MASTER.txt` + `DEREG.txt` → `ACFTREF.txt`), resolving cabin seat configurations (`seats_config`), and applying hierarchical BTS T-100 monthly load factors (`lf_source`) modulated by TSA day-level and departure hour-block demand signals.
2. **Displacement Pressure (`20%` weight):** Same-day earlier cancellations and diversions on the same `ORD → DEST` route that roll displaced revenue passengers onto later departures ahead of non-rev standbys.
3. **Backup Options (`20%` weight):** Remaining same-day departures from `ORD` to the same destination after the target flight.
4. **Day / Holiday Demand (`10%` weight):** TSA checkpoint throughput relative to the yearly average, plus a 6-window US holiday calendar.

The index is published in two variants:

| Variant | Question it answers | Inputs | Coverage |
|---|---|---|---|
| **`soi_historical`** | "How good was this flight for standby, after the fact?" | Realized same-month T-100 load factors, actual aircraft, actual same-day cancellations/diversions, actual TSA volume | Performed flights; `NULL` for cancelled flights |
| **`soi_planning`** | "How good is this flight likely to be, planning ahead?" | Schedule and calendar keys only, plus lookup priors fitted strictly on earlier years (`train.year < eval.year`) | All scheduled flights from `2023`; `NULL` for `2019` (no earlier training year) |

Both variants are `NULL` whenever T-100 data is missing for the flight (`SC-5`, no silent imputation). The full specification is in [`docs/index_methodology.md`](docs/index_methodology.md).

---

## Project Status

| Phase | PR | Milestone & Deliverables | Status |
|---|---|---|---|
| **Phase 0** | #1 | Validation report + architecture proposal (`docs/phase0_validation_report.md`, `docs/data_acquisition.md`, `docs/decisions.md`, `notebooks/00_phase0_validation.ipynb`) — *Stop Gate 1* | Merged |
| **Phase 1** | #2 | Ingestion, checksummed `data/manifest.csv`, `data/sample/`, `pandera` schemas, BigQuery `raw` batch load, GitHub Actions CI | Merged |
| **Phase 2** | #3 | Flight-level enrichment, curated reference tables (`reference/`), Section 7 sanity checks — *Stop Gate 2* | Merged |
| **Phase 3** | #4 | dbt Core on BigQuery (`staging` → `intermediate` → `marts`), seeds, schema & singular tests, offline CI compile | Merged |
| **Phase 4** | #5 (fixes in #7) | Standby Opportunity Index (`soi_historical` + `soi_planning`), `score` / `validate-index` CLI, Section 6 validation suite — *Stop Gate 3* | Merged |
| **Phase 5** | — | Tableau Public extracts (`exports/tableau/`), dashboard spec, data dictionary | Planned |

---

## Scope (v1)

- **Hub:** `ORD` (Chicago O'Hare International Airport) departures. Hubs, carriers, years, and SOI weights are config-driven ([`config/standby.yaml`](config/standby.yaml)), so expanding to `DEN`, `IAH`, `EWR`, `SFO`, `IAD`, or `LAX` requires no code changes.
- **Carriers:** United Airlines mainline (`UA`) + United Express capacity purchase partners marketed by `UA` (`OO` SkyWest, `YX` Republic, `YV` Mesa, `G7` GoJet, `C5` CommutAir, `ZW` Air Wisconsin, `EV` ExpressJet, `AX` Trans States).
- **Time Horizon:** `2019` (pre-COVID structural baseline) and `2023` through the latest available BTS month (`2026-08`).
- **Granularity:**
  - **Domestic:** Flight-level analysis (`fct_flights`, partitioned by `flight_date` and clustered by `origin, dest, op_carrier`).
  - **International:** Route × month × operating carrier level (`agg_route_month`) from BTS T-100 International Segment (`CLASS = 'F'`). Flight-level international tracking is a `v2` enhancement.

---

## Architecture

```text
data/raw/ (BTS OTP, T-100, lookups, FAA registry, TSA)      data/sample/ (CI slice)
        │
        ▼  verify-manifest (SHA-256 + row counts)
Python package `standby`
  ingest ──► pandera schemas ──► enrichment ──► SOI scoring ──► sanity / SC-5 / bounds checks
  (ingest/)   (schemas/)         (enrichment/)   (scoring/)      (validation/)
        │
        ▼  load (BigQuery `raw`, or local Parquet in data/processed/ on --dry-run)
dbt Core on BigQuery
  staging (views) ──► intermediate (views) ──► marts (tables)
                                                fct_flights, agg_route_month, dim_*
        │
        ▼
exports/tableau/ (Phase 5)
```

The SOI is implemented twice — in Python (`src/standby/scoring/`) and in dbt SQL (`int_flights_soi_scored`) — with identical equations and constants. A parity test in `tests/test_dbt_project.py` compares the Python output against a SQLite port of the dbt SQL on the sample data.

---

## Repository Structure

```text
united-standby-analytics/
├── config/standby.yaml      # Hubs, carriers, years, hour blocks, SOI weights & scoring hyperparameters
├── data/
│   ├── manifest.csv         # SHA-256 checksum contract for data/raw/
│   ├── raw/                 # Git-ignored raw BTS, FAA, and TSA files
│   ├── processed/           # Git-ignored Parquet outputs (sample/ and full/)
│   └── sample/              # Anonymized-by-design sample slice committed for CI
├── dbt/                     # dbt Core project on BigQuery
│   ├── models/              # staging/ (8 views), intermediate/ (5 views), marts/ (6 tables)
│   ├── macros/              # holiday_calendar, hour_block, generate_schema_name
│   ├── seeds/               # seat_config, tail_overrides, faa_t100_crosswalk
│   └── tests/               # Singular tests + generic tests (accepted_range, not_null_where, ...)
├── docs/                    # Validation reports, methodology, data acquisition guide, decisions log
├── exports/                 # Tableau Public CSV extracts (Phase 5)
├── notebooks/               # Exploratory Data Analysis (EDA) notebooks only
├── reference/               # Curated fleet seat configs, tail overrides, and FAA -> T-100 crosswalk
├── src/standby/
│   ├── ingest/              # Source readers (OTP, T-100, lookups, FAA, TSA)
│   ├── schemas/             # pandera schemas for every ingested table
│   ├── enrichment/          # Aircraft resolution, T-100 load factors, estimates, confidence tiers
│   ├── scoring/             # SOI scoring (soi.py) and the US holiday calendar (holidays.py)
│   ├── validation/          # Section 7 sanity checks and Section 6 SOI validation suite
│   ├── load/                # BigQuery batch loader (with local Parquet fallback)
│   └── cli.py               # `python -m standby` command-line interface
└── tests/                   # pytest suite running against data/sample/
```

---

## Phase Results

### Phase 0 — Data Validation (Stop Gate 1)

All raw files across the 5 sources were empirically validated: `56` monthly BTS Marketing Carrier OTP archives (`2019-01..2019-12` and `2023-01..2026-08`) covering `830,941` `ORD` `UA` flights out of `35,881,885` U.S. flights; `5` annual BTS T-100 Segment archives (`2,520,139` segment rows); `5` BTS lookup tables; FAA `MASTER.txt` + `DEREG.txt` + `ACFTREF.txt`; and `1,777` TSA daily volume rows.

- **[`docs/phase0_validation_report.md`](docs/phase0_validation_report.md):** Dataset inventory and null rates, field verification, OTP vs. T-100 reconciliation, FAA tail match-rate lift from `DEREG.txt`, data quirks with verbatim examples, plus the end-to-end architecture and methodology proposal.
- **[`docs/data_acquisition.md`](docs/data_acquisition.md):** Scripted and manual download instructions for all 5 sources, and the `data/manifest.csv` checksum contract.
- **[`notebooks/00_phase0_validation.ipynb`](notebooks/00_phase0_validation.ipynb):** Executed notebook reproducing every Phase 0 table and statistic from `data/raw/`.

### Phase 2 — Enrichment (Stop Gate 2)

Verified across all **`830,941` `ORD` `UA` flights** (`814,847` performed, `16,094` cancelled; full output in [`docs/phase2_enrichment_summary.json`](docs/phase2_enrichment_summary.json)):

- **Curated reference tables (`reference/`):** `seat_config.csv` (`31` mainline and Express cabin layouts with `source_url` and `as_of`), `faa_t100_crosswalk.csv` (`56` rules mapped to `L_AIRCRAFT_TYPE.csv`), and `tail_overrides.csv` (`152` tail-level overrides).
- **3-tier aircraft resolution:** `TAIL_OVERRIDE → FAA_MASTER → FAA_DEREG`, with a `faa_tc_max_seats >= 20` collision guard (`9,263` low-seat registry collisions rejected), resolving **`100%` of performed flights** (up from `97.85%` with the raw FAA registry alone).
- **3-tier T-100 load factors:** `98.02%` resolved at `CARRIER_ROUTE_MONTH_TYPE`, `1.98%` at `CARRIER_ROUTE_MONTH`, and `14` flights at `ROUTE_MONTH`. Base estimates are kept separate from modeled TSA / hour-block adjustments (`alpha = 0.35`, `[0.80, 1.20]` bounds), and stay `NULL` when T-100 is unavailable.
- **Section 7 sanity checks:** All 5 checks (`SC-1`..`SC-5`) pass on the full dataset (`SC-3` max T-100 seats/departure difference `5.68%`; `SC-4` system-wide UA load factor `82.89%`).

### Phase 3 — dbt Marts

- **19 models:** 8 staging views, 5 intermediate views (aircraft enrichment, T-100 load factors, flight estimates, displacement/backup counts, SOI scoring), and 6 mart tables (`fct_flights`, `agg_route_month`, `dim_aircraft`, `dim_airport`, `dim_carrier`, `dim_date`).
- **232 data tests**, including 3 custom generic tests (`accepted_range`, `not_null_where`, `unique_combination_of_columns`) and 4 singular tests (grain uniqueness, `SC-1` passenger bounds, `SC-5` no silent T-100 imputation, SOI leakage & bounds).
- **`fct_flights`** is partitioned by `DAY` on `flight_date` and clustered by `origin, dest, op_carrier`; `agg_route_month` covers UA-family domestic and international T-100 segments.
- **Offline CI:** `dbt parse`, `dbt compile`, and `dbt docs generate --empty-catalog` run against a credential-free `ci` target, so CI never needs GCP access.

### Phase 4 — Standby Opportunity Index (Stop Gate 3)

Full results are in [`docs/phase4_validation_summary.json`](docs/phase4_validation_summary.json) and explained in [`docs/index_methodology.md`](docs/index_methodology.md).

- **Coverage:** `soi_historical` scores all `814,847` performed flights; `soi_planning` scores `626,838` flights across `2023–2026` (all `199,882` flights in `2019` are `NULL` by design). `0` `SC-5` violations and `0` out-of-range scores.
- **Pillar 1 — time-based backtest** (`soi_planning` vs. `soi_historical`):

  | Evaluation | Spearman ρ | Top-decile lift | Planning coverage |
  |---|---:|---:|---:|
  | Fixed holdout (train `2019, 2023, 2024` → evaluate `2025–2026`) | `0.653` | `5.31×` | `99.22%` |
  | Expanding walk-forward, `2023–2026` combined | `0.631` | `5.31×` | `99.33%` |

  Per-year walk-forward ρ ranges from `0.565` (`2023`) to `0.688` (`2025`). Gates: ρ ≥ `0.55`, lift ≥ `3.0×`, coverage ≥ `95%`.
- **Pillar 2 — face validity:** Mean `soi_historical` falls in the expected order from off-peak to peak: late-January midweek `76.99` > all flights `61.73` > spring break `59.32` > Christmas peak `51.78` > Thanksgiving Sunday `46.39`. `soi_planning` separates late-January midweek (`67.33`) from Thanksgiving Sunday (`55.19`) with Welch p ≈ `1.7e-152`.
- **Pillar 3 — weight sensitivity:** Rankings stay stable under alternative weightings (Spearman ρ vs. baseline ≥ `0.901` across all schemes; gate ≥ `0.85`).

> **Caveat:** Both variants use T-100 monthly load factors as their capacity baseline, so the backtest measures consistency between the planning and post-hoc proxies — not accuracy against real standby outcomes, which no public dataset contains.

---

## Quickstart

```bash
# 1. Create the pinned virtual environment from uv.lock
make setup

# 2. Verify raw and sample checksum manifests
make manifest

# 3. Code quality, unit tests (against data/sample/), and offline dbt checks
make lint
make format-check
make test
make dbt-parse
make dbt-compile

# 4. End-to-end sample dry-run (CI parity, no GCP credentials required)
make ingest-sample            # standby run --hub ORD --years 2019,2023-2026 --sample --dry-run

# 5. Score and validate the SOI on the sample slice
make score-sample             # standby score ... --sample --recompute
make validate-index-sample    # standby validate-index ... --sample

# 6. Full pipeline against data/raw/ (falls back to data/processed/full/ Parquet if GCP credentials are absent)
make run                      # standby run --hub ORD --years 2019,2023-2026
make validate-index           # re-scores and writes docs/phase4_validation_summary.json
```

### CLI Reference (`python -m standby <command>`)

| Command | What it does |
|---|---|
| `verify-manifest` | Verify SHA-256 checksums and row counts for every file in a manifest |
| `build-manifest` | Build or refresh a checksum manifest for `data/raw` or `data/sample` |
| `ingest` | Verify the manifest, ingest all sources, and run `pandera` validation |
| `load-raw` | Ingest, validate, and batch-load the raw tables to BigQuery (or local Parquet with `--dry-run`) |
| `enrich` | Ingest, validate, and run Phase 2 enrichment with sanity checks |
| `run` | End-to-end: manifest gate → ingest → validate → enrich → score → load |
| `score` | Compute `soi_historical` and `soi_planning` and write the scored Parquet |
| `validate-index` | Run the Section 6 validation suite and write the JSON summary |

Every command except the two manifest commands accepts `--hub`, `--years` (e.g. `2019,2023-2026`), and `--sample`; `load-raw` and `run` also accept `--dry-run`. Notes on the scoring commands:

- `score` and `validate-index` read existing Parquets from `data/processed/` when present, scoped to the requested `--hub` and `--years`.
- `score` refuses to run if the processed Parquet holds flights outside the requested scope, because it writes its results back to that file. Pass matching flags, or `--recompute` to rebuild from raw sources.
- `validate-index` re-scores with the current config by default. `--reuse-scores` validates the scores already stored in the Parquet instead (faster, but they may be stale).

---

## Continuous Integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pushes to `main` and `phase-*` branches, and on pull requests into `main`:

1. `ruff check` and `ruff format --check`
2. Sample manifest verification
3. `pytest` against `data/sample/` (112 tests, including Python ↔ SQL parity and SOI leakage tests)
4. End-to-end sample dry-run (`standby run --sample --dry-run`)
5. Offline `dbt parse`, `dbt compile`, and `dbt docs generate --empty-catalog`

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/index_methodology.md`](docs/index_methodology.md) | SOI equations, leakage boundary, planning priors, validation results, and 11 limitations |
| [`docs/decisions.md`](docs/decisions.md) | Technical decisions and assumptions log (`D-01` – `D-34`) |
| [`docs/phase0_validation_report.md`](docs/phase0_validation_report.md) | Phase 0 empirical validation and architecture proposal |
| [`docs/data_acquisition.md`](docs/data_acquisition.md) | How to download every source and the manifest contract |
| [`docs/phase2_enrichment_summary.json`](docs/phase2_enrichment_summary.json) | Full-dataset enrichment metrics |
| [`docs/phase4_validation_summary.json`](docs/phase4_validation_summary.json) | Full-dataset SOI validation metrics |

---

## Limitations

The SOI is a relative heuristic, not a boarding probability. Key limitations include monthly (not flight-level) T-100 load factors, no visibility into non-rev pass priority or seniority, revenue standby and upgrade dynamics, weight-and-balance restrictions, national (not airport-level) TSA volumes, and point-in-time seat configurations. See [`docs/index_methodology.md` §4](docs/index_methodology.md#4-comprehensive-metric--domain-limitations-11-items) for all 11.
