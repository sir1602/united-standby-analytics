# United Airlines Standby Opportunity Analytics (`united-standby-analytics`)

> **Disclaimer:** This project is an independent, portfolio-grade data engineering and analytics engineering project built strictly from public U.S. government aviation datasets (BTS, FAA, TSA) and curated public aircraft reference data. The **Standby Opportunity Index (SOI)** is a transparent **0–100 relative heuristic**, **NOT a probability of boarding**. No public dataset contains actual gate standby lists or non-rev clearance outcomes. This project is **not affiliated with, endorsed by, or sponsored by United Airlines, Inc.**

---

## Overview

**United Airlines Standby Opportunity Analytics** estimates a **0–100 Standby Opportunity Index** (`soi_historical` and leakage-free `soi_planning`) for **employee / non-rev standby** travelers departing **Chicago O'Hare (`ORD`)** on **United Airlines mainline (`UA`)** and **United Express** regional partners (`OO`, `YX`, `YV`, `G7`, `C5`, `ZW`, plus pre-COVID 2019 partners `EV` and `AX`).

Non-revenue standby travelers board only after all revenue passengers — including revenue passengers reaccommodated from earlier cancelled or diverted flights — have been seated. Because flight-level passenger counts and gate standby lists are proprietary, this system combines five public aviation datasets with a curated fleet-subtype seat-configuration reference to model:
1. **Open Seats (`50%` weight):** Estimated open seats (`est_open_seats_adjusted`) and open-seat percentage derived by matching aircraft tail numbers (`TAIL_NUM`) to the FAA Aircraft Registry (`MASTER.txt` + `DEREG.txt` → `ACFTREF.txt`), resolving exact cabin seat configurations (`seats_config`), and applying hierarchical BTS T-100 monthly load factors (`lf_source`) modulated by day-of-week/holiday and departure hour-block demand signals.
2. **Displacement Pressure (`20%` weight):** Same-day earlier cancellations and diversions on the same `ORD → DEST` route that roll displaced revenue passengers onto subsequent departures ahead of non-rev standbys.
3. **Backup Options (`20%` weight):** Remaining same-day departures from `ORD` to the same destination after the target flight.
4. **Day / Holiday Demand (`10%` weight):** TSA checkpoint throughput day-of-week and holiday demand intensity.

---

## Phase 0 Deliverables (Stop-Gate 1 Review)

All actual raw files across all 5 sources (`56` monthly BTS Marketing Carrier OTP archives `2019-01..2019-12` & `2023-01..2026-08` covering `830,941` `ORD` `UA` flights out of `35,881,885` U.S. flights; `5` annual BTS T-100 Segment `FMG` archives covering `2,520,139` segment rows; `5` BTS Lookup tables; FAA `MASTER.txt` + `DEREG.txt` + `ACFTREF.txt`; and `1,777` TSA daily volume rows) have been empirically validated:

- **[`docs/phase0_validation_report.md`](docs/phase0_validation_report.md):** Complete Phase 0 Empirical Validation Report (Tables 1–5: dataset inventory & null rates, field verification & Reporting vs. Marketing OTP discrepancy proof, OTP vs. T-100 reconciliation across years/carriers, FAA tail match rate lift from `DEREG.txt`, and 6+3 data quirks with verbatim examples) + End-to-End Architecture & Methodology Proposal (Mermaid diagram, 7-step join keys & 3-tier `lf_source` hierarchy, `soi_historical` & `soi_planning` mathematical equations, strict leakage boundary, and 11 metric limitations).
- **[`docs/data_acquisition.md`](docs/data_acquisition.md):** Scripted acquisition methods and exact step-by-step manual browser download instructions (URLs, form selections, filters, checkboxes) for all 5 sources + `data/manifest.csv` checksum contract.
- **[`docs/decisions.md`](docs/decisions.md):** Technical Decisions & Assumptions Log (`D-01` through `D-18`), combining the 13 baseline decisions and 5 empirical Phase 0 technical decisions.
- **[`notebooks/00_phase0_validation.ipynb`](notebooks/00_phase0_validation.ipynb):** Executed Jupyter Notebook reproducing every table and empirical statistic in `docs/phase0_validation_report.md` from `data/raw/`.

---

## Scope (v1)

- **Hub:** `ORD` (Chicago O'Hare International Airport) departures. Hubs, carriers, and years are config-driven (`YAML`) so expanding to `DEN`, `IAH`, `EWR`, `SFO`, `IAD`, or `LAX` requires zero code changes.
- **Carriers:** United Airlines mainline (`UA`) + United Express capacity purchase partners marketed by `UA` (`OO` SkyWest, `YX` Republic, `YV` Mesa, `G7` GoJet, `C5` CommutAir, `ZW` Air Wisconsin, `EV` ExpressJet, `AX` Trans States).
- **Time Horizon:** `2019` (pre-COVID structural baseline) and `2023` through the latest available BTS T-100 month (`2026-08`).
- **Granularity:**
  - **Domestic (`v1`):** Flight-level analysis (`fct_flights`, partitioned by `flight_date` and clustered by `origin, dest, op_carrier`).
  - **International (`v1`):** Route × month × operating carrier level (`agg_route_month`) from BTS T-100 International Segment (`CLASS = 'F'`). Flight-level international tracking via OpenSky Network / FlightAware AeroAPI is documented as a `v2` enhancement.

---

## Repository Structure

```text
united-standby-analytics/
├── config/                  # YAML pipeline configuration (hubs, carriers, years, SOI weights)
├── data/
│   ├── raw/                 # Git-ignored raw BTS, FAA, and TSA files
│   └── sample/              # Anonymized-by-design sample slice committed for CI
├── dbt/                     # dbt Core project on BigQuery (raw -> staging -> intermediate -> marts)
├── docs/                    # Phase 0 validation report, data acquisition guide, decisions log, specs
├── exports/                 # Tableau Public CSV extracts (exports/tableau/)
├── notebooks/               # Exploratory Data Analysis (EDA) notebooks only
├── reference/               # Curated fleet seat configs, tail overrides, and FAA->T-100 crosswalk
├── src/
│   └── standby/             # Python package (ingestion, manifest verification, pandera, enrichment)
└── tests/                   # pytest suite running against data/sample/
```

---

## Project Phasing & Roadmap (One PR per Phase)

| Phase | PR | Milestone & Deliverables | Status |
|---|---|---|---|
| **Phase 0** | **PR #1** | **Phase 0 Validation Report + Architecture Proposal** (`docs/phase0_validation_report.md`, `docs/data_acquisition.md`, `docs/decisions.md`, `notebooks/00_phase0_validation.ipynb`) — *Stop Gate 1* | **Merged (`main`)** |
| **Phase 1** | **PR #2** | **Ingestion + Checksummed `data/manifest.csv` + `data/sample/` + `pandera` Schemas + BigQuery `raw` Batch Load + GitHub Actions CI + `dbt parse` Stub** | **Complete (PR #2 Open)** |
| **Phase 2** | PR #3 | Flight-Level Enrichment + Curated Reference Tables (`reference/`) — *Stop Gate 2* | Planned |
| **Phase 3** | PR #4 | `dbt Core` (`staging` → `intermediate` → `marts`) + Schema Tests | Planned |
| **Phase 4** | PR #5 | Standby Opportunity Index (`soi_historical` + `soi_planning`) + Validation Suite — *Stop Gate 3* | Planned |
| **Phase 5** | PR #6 | Tableau Public Extracts (`exports/tableau/`) + Dashboard Spec + Final README & Data Dictionary | Planned |

---

## Quickstart (Local & CI Reproduction)

```bash
# 1. Create pinned virtual environment from uv.lock
make setup

# 2. Verify raw & sample checksum manifests
make manifest
# or directly:
.venv/bin/python -m standby verify-manifest --manifest data/sample/manifest.csv
.venv/bin/python -m standby verify-manifest --manifest data/manifest.csv

# 3. Run code quality, unit tests (against data/sample/), and offline dbt parse
make lint
make format-check
make test
make dbt-parse

# 4. Run end-to-end sample dry-run (CI parity, <5s, no GCP credentials required)
make ingest-sample
# or directly:
.venv/bin/python -m standby run --hub ORD --years 2019,2023-2026 --sample --dry-run

# 5. Run full pipeline against data/raw/ (auto-falls back to data/processed/full/ Parquet if ADC absent)
.venv/bin/python -m standby run --hub ORD --years 2019,2023-2026
# Other CLI subcommands:
.venv/bin/python -m standby ingest --hub ORD --years 2019,2023-2026 --sample
.venv/bin/python -m standby load-raw --hub ORD --years 2019,2023-2026 --sample --dry-run
```
