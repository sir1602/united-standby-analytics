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
| **Phase 0** | **PR #1** | **Phase 0 Validation Report + Architecture Proposal** (`docs/phase0_validation_report.md`, `docs/data_acquisition.md`, `docs/decisions.md`, `notebooks/00_phase0_validation.ipynb`) — *Stop Gate 1* | In Progress |
| **Phase 1** | PR #2 | Ingestion + Checksummed `data/manifest.csv` + `pandera` Schemas + BigQuery `raw` Batch Load | Planned |
| **Phase 2** | PR #3 | Flight-Level Enrichment + Curated Reference Tables (`reference/`) — *Stop Gate 2* | Planned |
| **Phase 3** | PR #4 | `dbt Core` (`staging` → `intermediate` → `marts`) + Schema Tests + GitHub Actions CI | Planned |
| **Phase 4** | PR #5 | Standby Opportunity Index (`soi_historical` + `soi_planning`) + Validation Suite — *Stop Gate 3* | Planned |
| **Phase 5** | PR #6 | Tableau Public Extracts (`exports/tableau/`) + Dashboard Spec + Final README & Data Dictionary | Planned |
