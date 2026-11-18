# Technical Decisions & Assumptions Log (`docs/decisions.md`)

Per Ground Rule #2 (`ORIGINAL_REQUEST.md` §11, Line 180: *"Record every technical decision and assumption in `docs/decisions.md`"*), this document records the **13 baseline architectural decisions (`D-01` through `D-13`)**, the **5 Phase 0 empirical technical decisions (`D-14` through `D-18`)**, the **5 Phase 2 enrichment decisions (`D-19` through `D-23`)**, the **5 Phase 3 dbt modeling decisions (`D-24` through `D-28`)**, the **6 Phase 4 Standby Opportunity Index & validation decisions (`D-29` through `D-34`)**, the **4 Phase 5 Tableau delivery & documentation decisions (`D-35` through `D-38`)**, and the **live warehouse setup decision (`D-39`)**.

---

## Summary Table (`D-01` – `D-39`)

| ID | Category | Decision Title | Choice Made | Status |
|---|---|---|---|---|
| **D-01** | Domain Scope | Target Standby Traveler Type | Employee / non-rev standby (`SA`) | Approved (Baseline) |
| **D-02** | Time Scope | Historical Baseline & Analysis Window | `2019` baseline + `2023` → latest T-100 month (`2026-08`); retain post-T-100 OTP months with `t100_available = false` and `soi_* = NULL` | Approved (Baseline) |
| **D-03** | Data Acquisition | Scripted Download + Manual Guide + Checksummed Manifest | Scripted Python downloaders + documented manual steps in `docs/data_acquisition.md` + SHA-256 `data/manifest.csv` verification | Approved (Baseline) |
| **D-04** | International Scope | International Granularity (v1 vs. v2) | Route × month × carrier grain (`agg_route_month`) in v1 from T-100 Intl Segment; flight-level via OpenSky/FlightAware AeroAPI in v2 | Approved (Baseline) |
| **D-05** | Enrichment | Seat Configuration Granularity | Curated fleet-subtype lookup (`reference/seat_config.csv`, ~30 subtypes) + tail overrides (`reference/tail_overrides.csv`) with `source_url` & `as_of` | Approved (Baseline) |
| **D-06** | Index Design | Standby Opportunity Index Components & Weights | Open seats (`50%`), Displacement pressure (`20%`), Backup options (`20%`), Day/holiday demand (`10%`) across 6 departure hour blocks | Approved (Baseline) |
| **D-07** | Index Design | Leakage-Free Index Separation | Separate `soi_historical` (post-hoc realized signals) and `soi_planning` (leakage-free advance planning signals) | Approved (Baseline) |
| **D-08** | Warehouse | BigQuery Billing-Enabled Project & Partitioning | GCP project with billing enabled (free tier + `$1` budget alert), no partition expiry; partition `fct_flights` by `flight_date`, cluster by `origin, dest, op_carrier` | Approved (Baseline) |
| **D-09** | BI Delivery | Tableau Public Dashboard & Extracts | CSV extracts exported to `exports/tableau/` within Tableau Public row limits + dashboard spec/wireframe | Approved (Baseline) |
| **D-10** | Transformations | Hybrid Python + `dbt Core` Architecture | Python (`pandas`, `pandera`) for EL + enrichment + validation; `dbt Core` on BigQuery for `raw → staging → intermediate → marts` | Approved (Baseline) |
| **D-11** | Orchestration & CI | Single Entry Point & Secret-Free GitHub Actions CI | Single CLI (`python -m standby run` / `make all`) + GitHub Actions running `ruff`, `pytest` on `data/sample/`, and `dbt parse` without GCP secrets | Approved (Baseline) |
| **D-12** | Validation | Three-Pillar SOI Validation Suite | Time-based backtest (Spearman $\rho$, top-decile hit rate) + face-validity checks + weight sensitivity analysis + 5 automated sanity checks | Approved (Baseline) |
| **D-13** | Repository | Public GitHub Repo & Phased PR Workflow | Public GitHub repo `united-standby-analytics`, one PR per phase (`PR #1–#5` Phases 0–4; `PR #6–#8` intermediate/hotfix PRs; `PR #9` Phase 5), stopping at Gates 1, 2, and 3 | Approved (Baseline) |
| **D-14** | Data Source (Phase 0) | BTS Marketing Carrier OTP vs. Reporting Carrier OTP | Use `On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018` (`Marketing_Airline_Network = 'UA'`, `Duplicate = 'N'`) instead of Reporting Carrier OTP | Phase 0 Empirical |
| **D-15** | Schema Standardization (Phase 0) | `PREZIP` Column Stripping & Historical Express Partner Inclusion (`EV`, `AX`, `ZW`) | Strip `'Operating_Airline '` trailing space & empty 120th column; include pre-COVID 2019 partners `EV` (ExpressJet) and `AX` (Trans States) alongside `UA, OO, YX, YV, G7, C5, ZW` | Phase 0 Empirical |
| **D-16** | Enrichment (Phase 0) | Two-Tier FAA Registry Lookup (`MASTER.txt` → `DEREG.txt`) + Commercial Manufacturer Filter & `80`-Tail Overrides | Filter `MASTER.txt` & `DEREG.txt` to commercial airliner manufacturers (`BOEING`, `AIRBUS%`, `EMBRAER%`, `YABORA%`, `BOMBARDIER%`, `CANADAIR%`, `MHI RJ%`) + resolve `80` historical regional tails (`43` unmatched + `37` N-number-reused) in `tail_overrides.csv` | Phase 0 Empirical |
| **D-17** | Enrichment & Reconciliation (Phase 0) | Combined T-100 Segment (`FMG`) & 3-Tier `lf_source` Hierarchy + Multi-Brand Regional Handling | Use `T_T100_SEGMENT_ALL_CARRIER` (`CLASS = 'F'`), resolve LF via Tier 1 (`carrier_route_month_aircraft`) → Tier 2 (`carrier_route_month`) → Tier 3 (`route_month`), and flag shared multi-brand regional spokes (`OO`, `YX`) | Phase 0 Empirical |
| **D-18** | Data Hygiene (Phase 0) | Local `hhmm` Zero-Padding, `'2400'` Normalization & Conditional Null Constraints | Pad `hhmm` to 4 chars, normalize `'2400'` → `'0000'` (`late` hour block), enforce conditional non-null rules on `is_cancelled == 0`, and set `soi_historical = NULL` on cancelled/unmatched flights | Phase 0 Empirical |
| **D-19** | Enrichment (Phase 2) | Strict 3-Tier Aircraft Precedence, 1:1 Mapping & Stearman Seat Floor | Enforce `TAIL_OVERRIDE → FAA_MASTER → FAA_DEREG` 1:1 lookup with commercial manufacturer filter + `faa_tc_max_seats >= 20` (`9,263` collisions rejected) | Phase 2 Enrichment |
| **D-20** | Reference Tables (Phase 2) | Curated Seat Config, Crosswalk & Tail Overrides | Maintain `seat_config.csv` (`31` subtypes), `faa_t100_crosswalk.csv` (`56` rules), and `tail_overrides.csv` (`152` tails), achieving `100.0000%` performed flight resolution | Phase 2 Enrichment |
| **D-21** | Enrichment (Phase 2) | 3-Tier Domestic T-100 LF Hierarchy & Zero Silent Imputation | Join domestic T-100 via `CARRIER_ROUTE_MONTH_TYPE` → `CARRIER_ROUTE_MONTH` → `ROUTE_MONTH` and leave all `est_*` columns `NULL` on missing/unmatched T-100 | Phase 2 Enrichment |
| **D-22** | Enrichment (Phase 2) | Base Estimates vs. Modeled Demand Adjustments | Keep base estimates (`est_load_factor`, `est_passengers`, `est_open_seats`) separate from modeled TSA/hour-block adjustments (`est_*_adjusted`) | Phase 2 Enrichment |
| **D-23** | Validation (Phase 2) | Column Confidence-Tier Registry & Section 7 Sanity Checks (`SC-1`..`SC-5`) | Register every column in `COLUMN_CONFIDENCE_TIERS` and gate pipeline runs on `SC-1`..`SC-5` (`0` failures across `830,941` flights) | Phase 2 Enrichment |
| **D-24** | dbt CI & Tooling (Phase 3) | Offline BigQuery `oauth-secrets` CI Profile, Custom Schema Resolution & Zero-Dependency Tests | Use `method: oauth-secrets` placeholder in `target: ci` (`dev` keeps `oauth`), custom `generate_schema_name.sql` (`raw`/`staging`/`intermediate`/`marts`), and zero-dependency generic/singular tests (no `dbt_utils` / `packages.yml`) | Phase 3 dbt Marts |
| **D-25** | dbt Contracts (Phase 3) | Staging 31-Column Base Contract & `dbt/seeds/` Type Enforcement | `stg_otp_flights` reads strictly the 31 base standardized columns (`load-raw` & `run` compatible); `dbt/seeds/` mirrors `reference/*.csv` with explicit `+column_types` (`bts_aircraft_type: string`, `as_of: date`, `seats_*: int64`) | Phase 3 dbt Marts |
| **D-26** | Dimensional Modeling (Phase 3) | Conformed Dimension Primary Keys (`dim_airport`, `dim_carrier`, `dim_aircraft`) | Key `dim_airport` on `airport_code` (avoids `airport_id=14027` `PBI`/`DJT` collision), `dim_carrier` 1:1 on `op_carrier` (aggregating `L_CARRIER_HISTORY`), and `dim_aircraft` 1:1 on `tail_num` across multi-carrier regional tails | Phase 3 dbt Marts |
| **D-27** | Marts & Scope (Phase 3) | `fct_flights` Partitioning, `agg_route_month` Domestic+Intl Scope & Full-Refresh Tables | Materialize `fct_flights` (`DAY` partition on `flight_date`, clustered `origin, dest, op_carrier`) and `agg_route_month` (UA-family Domestic `DU/DF` + Intl `IU/IF` with `data_granularity`) as `table`s (offline `dbt compile` safe) | Phase 3 dbt Marts |
| **D-28** | Operational SQL Logic (Phase 3) | Deterministic Same-Day Tie-Breaking, Strict Backup Inequality & US Holiday Window Macro | Order route-day flights by `(crs_dep_time ASC, is_cancelled DESC, flight_num_mkt ASC, op_carrier ASC)`, use strict `crs_dep_time > current` for backups, and encode 6 deterministic US holiday windows in `holiday_calendar.sql` | Phase 3 dbt Marts |
| **D-29** | SOI Architecture (Phase 4) | 1:1 Python & dbt BigQuery SQL Scoring Parity | Implement identical `soi_historical`, `soi_planning`, and all 8 sub-scores in `src/standby/scoring/` and `dbt/models/intermediate/int_flights_soi_scored.sql` → `fct_flights`, verified via SQLite parity & singular tests | Phase 4 SOI & Validation |
| **D-30** | Leakage Prevention (Phase 4) | Expanding `train.year < eval.year` Walk-Forward Priors & `2019 = NULL` | Fit modal `(seats_config, bts_aircraft_type)` (4-tier), T-100 LF (5-tier), disruption rate $\hat{q}$ (4-tier, `min_obs=5`), and TSA ratios (4-tier) strictly on `year < eval_year`; leave `2019` `soi_planning = NULL` | Phase 4 SOI & Validation |
| **D-31** | Signal Calibration (Phase 4) | Dual TSA Normalization (`tsa_ratio_month` vs. `tsa_ratio_year`) & Year-Anchored Holiday Windows | Use `tsa_ratio_month` inside `m_tsa` (`S_open`), `tsa_ratio_year` inside `S_demand` (preserving cross-month holiday peaks), and year-anchored Thanksgiving Sunday (`+3d` without `month == 11`, capturing `2019-12-01` & `2024-12-01`) | Phase 4 SOI & Validation |
| **D-32** | Null & Eligibility Gates (Phase 4) | `SC-5` Zero Imputation, Cancelled `soi_historical = NULL`, and Planning Survivorship | Enforce `NULL` on both indices when T-100 is unavailable/unmatched (`SC-5`), `NULL` on `soi_historical` when `is_cancelled`, and score cancelled flights in `soi_planning` to avoid survivorship bias | Phase 4 SOI & Validation |
| **D-33** | Validation Suite (Phase 4) | Three-Pillar Section 6 Validation Gates (`standby validate-index`) | Gate full runs on Fixed Holdout + Expanding Walk-Forward ($\rho \ge 0.55$, lift $\ge 3.0\times$, coverage $\ge 95\%$), monotonic Face-Validity ordering + Welch/Mann-Whitney separation, and Weight Sensitivity ($\rho \ge 0.85$), outputting `docs/phase4_validation_summary.json` | Phase 4 SOI & Validation |
| **D-34** | Package Architecture (Phase 4) | Shared `DEFAULT_UA_FAMILY_CARRIERS` in `standby.config` & Lazy Confidence Tagging | Define `DEFAULT_UA_FAMILY_CARRIERS` in `standby.config` and lazy-import `tag_dataframe_confidence_tiers` in `score_flights()` to guarantee cycle-free imports across `scoring`, `validation`, and `enrichment` | Phase 4 SOI & Validation |
| **D-35** | BI Delivery (Phase 5) | Stratified `100k` Default Tableau Flight Extract & `< 50 MB` File Cap | Default `fct_flights_tableau.csv` to deterministic proportional stratified sampling (`100,000` rows across `year, month, op_carrier`, `seed=42`, `4`-decimal rounding) alongside `recent-12m` and gitignored `exports/tableau/full/` modes | Phase 5 Tableau & Docs |
| **D-36** | Geospatial BI (Phase 5) | Built-In Tableau `Airport` Geo Role via `route_map_paths.csv` | Export 2-point `route_map_paths.csv` (`path_order` 1=`ORD`, 2=`dest`, excluding `ORD -> ORD` ferry loops) using Tableau's built-in `Airport` geographic role with zero invented coordinates | Phase 5 Tableau & Docs |
| **D-37** | BI Modeling (Phase 5) | Parsed `dim_airport` City/Region & International `agg_route_month` Null Boundary | Parse `city_name` and `state_or_country` from `L_AIRPORT` in `dim_airport.csv` while preserving `otp_* = NULL` and `data_granularity = 'route_month_only_v1'` on international segments | Phase 5 Tableau & Docs |
| **D-38** | Quality & Docs (Phase 5) | Post-Export Invariant Gate & 157-Column Confidence-Tiered Data Dictionary | Block `export-tableau` on non-empty CSVs, unique PKs, `SC-5`, `[0,1]`/`[0,100]` bounds, zero PII, and `< 50 MB` file size; document all `157` BigQuery mart columns (`actual`/`derived`/`estimated`) | Phase 5 Tableau & Docs |
| **D-39** | Warehouse (Live) | Live Project `flight-standby-analytics` & `GCP_PROJECT_ID` Override | Point `config/standby.yaml` and the dbt profile/source defaults at the live billing-enabled project; `GCP_PROJECT_ID` overrides both. Offline dbt commands run with `--no-populate-cache` so they never call BigQuery | Live Warehouse Setup |

---

## Detailed Decision Records (`D-01` – `D-39`)

### D-01: Target Standby Traveler Type (`Employee / Non-Rev Standby`)
- **Context:** Airline standby lists include both revenue standbys (same-day changes, missed connections, Premier upgrades) and non-revenue employee/pass-rider standbys (`SA`), who board only after every revenue and IRROPS-reaccommodated passenger is seated.
- **Decision:** Model the Standby Opportunity Index specifically for **employee / non-rev standby** travelers.
- **Rationale & Implications:** Because non-revs sit at the bottom of the boarding hierarchy, open physical seats (`est_open_seats_adjusted`), earlier same-day cancellations/diversions (`S_disp`), and remaining same-day backup departures (`S_backup`) are the decisive operational drivers.

### D-02: Time Horizon (`2019` Baseline + `2023` through `2026-08`)
- **Context:** Evaluating post-COVID standby trends requires a pre-pandemic baseline without including 2020–2022 pandemic schedule anomalies and middle-seat blocking. Additionally, BTS T-100 releases lag OTP in some release windows.
- **Decision:** Include full calendar year **`2019`** (`12` months) and **`2023` through `2026-08`** (`44` months = `56` months total, `830,941` `ORD` `UA` flights). Retain any OTP months without a matching T-100 month with `t100_available = false` and `soi_* = NULL`.
- **Rationale & Implications:** In our Phase 0 inspection, both OTP and T-100 are verified available through `2026-08`. Enforcing `soi_* = NULL` when `t100_available = false` satisfies Sanity Check #5 (never impute missing T-100 months silently).

### D-03: Data Acquisition & Checksummed Manifest (`data/manifest.csv`)
- **Context:** Public government datasets (`transtats.bts.gov`, `registry.faa.gov`, `tsa.gov`) mix static archives, ASP.NET postback forms, and HTML tables.
- **Decision:** Provide 100% scripted Python downloaders for all 5 sources alongside step-by-step manual browser instructions in `docs/data_acquisition.md`, and enforce SHA-256 + row-count verification via `data/manifest.csv`.
- **Rationale & Implications:** Guarantees cryptographic reproducibility while keeping raw files out of Git (`data/raw/` is git-ignored).

### D-04: International Scope (`v1` Route/Month vs. `v2` Flight-Level)
- **Context:** BTS On-Time Performance (`OTP`) only covers U.S. domestic flights, whereas BTS T-100 International Segment (`DATA_SOURCE IN ('IU', 'IF')`) covers international routes at the monthly segment grain (`51,673` `ORD` `UA` international departures across our 56 months).
- **Decision:** Analyze international routes at the **route × month × operating carrier** grain in `agg_route_month` (`is_international = true`) in `v1`, clearly labeled in all outputs. Document flight-level international tracking via OpenSky Network / FlightAware AeroAPI as a `v2` enhancement.
- **Rationale & Implications:** Respects Ground Rule #1 (never fabricate synthetic daily flights and present them as actual flight-level records).

### D-05: Seat Configuration Granularity (`reference/seat_config.csv` + `reference/tail_overrides.csv`)
- **Context:** FAA `ACFTREF.txt` `NO-SEATS` reports regulatory maximum exit limits (e.g., `222` on a 737-900ER, `550` on a 777-200, `182` on an A320), which overstate United's actual cabin capacities (`179`, `364`/`276`, `150`) by 15–50%. Meanwhile, certain United sub-fleets (777-200 Domestic HD 364 vs. Intl Polaris 276, 757-200 176 vs. 169, 767-300ER 167 vs. 199/203, E175 76 vs. 70 seats) have multiple cabin layouts under the same FAA model code.
- **Decision:** Curate a manual fleet-subtype reference table (`reference/seat_config.csv`, ~30 UA + Express subtypes) and a tail-level override table (`reference/tail_overrides.csv`), each with a verifiable `source_url` and `as_of` date.
- **Rationale & Implications:** Produces high-confidence `seats_config` (`derived` tier) without prohibited bulk scraping of United.com seat maps.

### D-06: Standby Opportunity Index Components, Weights & Hour Blocks
- **Context:** A single monthly load factor cannot distinguish a 6:00 AM Tuesday flight with 6 remaining same-day backups from a 9:30 PM Thanksgiving Sunday flight following two earlier weather cancellations.
- **Decision:** Compute a composite `0–100` heuristic from 4 sub-scores with starting weights: **Open seats (`50%`)**, **Displacement pressure (`20%`)**, **Backup options (`20%`)**, and **Day/holiday demand (`10%`)**, evaluated across 6 departure hour blocks (`early 05–08`, `morning 08–11`, `midday 11–14`, `afternoon 14–17`, `evening 17–20`, `late 20–05`).Keep base estimates (`est_load_factor`, `est_passengers`, `est_open_seats`) separate from modeled adjustments (`est_lf_adjusted`, `est_open_seats_adjusted`).
- **Rationale & Implications:** Balances physical seat supply (50%) with operational disruption and stranding risk (40%) and macro calendar demand (10%). Starting weights will be stress-tested via weight sensitivity analysis in Phase 4.

### D-07: Strict Separation of `soi_historical` and `soi_planning` (Leakage Prevention)
- **Context:** Using same-day realized cancellations, day-of-departure tail swaps, or same-month realized T-100 load factors when scoring flights in advance constitutes target/future leakage.
- **Decision:** Maintain two strictly separated index columns:
  1. **`soi_historical`:** Post-hoc score for completed flights (`is_cancelled == 0`) using realized same-month T-100 LF, actual tail/seat config, actual earlier same-day cancellations/diversions, actual remaining non-cancelled departures, and actual daily TSA volume.
  2. **`soi_planning`:** Advance planning score using only historical training-window T-100 LF averages, historical cancellation rates, scheduled remaining departures, and historical TSA day-of-week/holiday profiles.
- **Rationale & Implications:** Enables rigorous out-of-sample time-based backtesting (`soi_planning` vs. `soi_historical`).

### D-08: BigQuery Billing-Enabled Project, Partitioning & Clustering
- **Context:** BigQuery Sandbox enforces a mandatory 60-day partition expiration based on the partition date (`flight_date`), which immediately deletes 2019 and historical 2023–2025 partitions upon creation.
- **Decision:** Require a **GCP project with billing enabled** (operating at `~$0` within the BigQuery free tier of 10 GiB storage and 1 TiB/month queries, protected by a `$1` budget alert), set dataset/table partition expiration to `NONE`, and partition `fct_flights` by `DATE(flight_date)` (`~1,704` daily partitions across 56 months, well under BigQuery's `4,000` partition limit) and cluster by `origin, dest, op_carrier`.
- **Rationale & Implications:** Preserves all `2019` and `2023–2026` partitions indefinitely and maximizes query pruning on date, route, and carrier filters.

### D-09: Tableau Public Extracts & Dashboard Specification
- **Context:** Tableau Public does not support live authenticated connections to BigQuery and enforces row limits on packaged extracts.
- **Decision:** Export optimized CSV extracts from the BigQuery `marts` tables (`fct_flights`, `agg_route_month`, `dim_*`) to `exports/tableau/` (baseline estimate `~831k` domestic flight rows + `~15k` route-month rows; empirically `830,941` domestic flights stratified to `100,000` Tableau rows per `D-35` + `20,877` route-month rows, well within Tableau Public's 15M row limit), and deliver complete calculated-field formulas and a dashboard wireframe specification in `docs/tableau_dashboard_spec.md`.
- **Rationale & Implications:** Ensures fast dashboard rendering and self-contained reproducibility.

### D-10: Hybrid Python + `dbt Core` Transformation Split
- **Context:** File-level checksum verification, fixed-width FAA parsing, HTML TSA scraping, and multi-tier tail/crosswalk enrichment are naturally suited to Python (`pandas`, `pandera`), whereas warehouse dimensional modeling, relational testing, and lineage documentation are best expressed in `dbt Core` SQL.
- **Decision:** Execute extraction, manifest verification, `pandera` schema validation, flight-level enrichment, and BigQuery `raw` batch Load Jobs in Python (`src/standby/`), and execute `staging → intermediate → marts` transformations, seed loading (`reference/*.csv`), schema tests, and lineage generation in `dbt Core` (`dbt/`).
- **Rationale & Implications:** Demonstrates production-grade proficiency in both Python data engineering and SQL analytics engineering.

### D-11: Single CLI Entry Point & Secret-Free GitHub Actions CI
- **Context:** Public portfolio repositories should never require cloud credentials in GitHub Actions PR checks, yet must still test Python logic, schemas, and dbt compilation on every push.
- **Decision:** Provide a unified CLI (`python -m standby run --hub ORD --years 2019,2023-2026` and `make all`) and configure `.github/workflows/ci.yml` to run `ruff check`, `pytest` against `data/sample/`, and `dbt parse` without GCP secrets. Full `dbt build` runs locally against BigQuery.
- **Rationale & Implications:** Fast, zero-secret CI on every PR.

### D-12: Three-Pillar Validation & Five Automated Sanity Checks
- **Context:** Because ground-truth gate standby outcomes do not exist in public data, the heuristic must be validated for internal consistency, directional face validity, and mathematical bounds.
- **Decision:** Implement:
  1. **5 Automated Sanity Checks** (`est_passengers <= seats_config` & `0 <= LF <= 1`; T-100 vs. OTP departure reconciliation; T-100 seats/dep vs. `seats_config` by aircraft type; system-wide UA LF ~80–85%; missing T-100 match $\implies$ `NULL` index).
  2. **Time-Based Backtest:** Spearman rank correlation ($\rho$) and top-decile hit rate between `soi_planning` (trained on earlier months) and `soi_historical` (evaluated on later months).
  3. **Face-Validity & Weight Sensitivity:** Verify peak holidays (Thanksgiving Sunday, Christmas, Spring Break) score low vs. late-January mid-week troughs scoring high, and quantify ranking stability across 5 alternative weightings.
- **Rationale & Implications:** Transparently documents that backtesting measures consistency between planning and historical proxies, not ground-truth clearance accuracy.

### D-13: Public GitHub Repository & Phased PR Workflow with Stop Gates
- **Context:** Structured code review across phases mirrors real-world engineering teams and enforces review gates after Phase 0, after Enrichment, and after Index Validation.
- **Decision:** Maintain a 1-PR-per-phase workflow (`PR #1` Phase 0 Validation + Architecture; `PR #2` Ingestion + Manifest + Raw Load; `PR #3` Enrichment + Reference Tables; `PR #4` dbt Staging → Marts + CI; `PR #5` SOI + Validation; `PR #9` Phase 5 Tableau Extracts + Dashboard Spec + Final README). *Note: Intermediate GitHub PRs `#6`–`#8` were Phase 4 post-review hotfix/documentation PRs from branch `claude/sharp-planck-0w5dly`, shifting the Phase 5 PR number from `#6` to `#9`.*
- **Rationale & Implications:** Keeps diffs focused and auditable at each stop gate while maintaining 1:1 traceability to actual GitHub PR history.

---

### D-14: Use BTS Marketing Carrier OTP (`FGK`) Instead of Reporting Carrier OTP (`OTP`) *(Phase 0 Empirical Decision)*
- **Context:** `ORIGINAL_REQUEST.md` §2 mentions *"BTS On-Time Performance (Reporting Carrier): filter `ORIGIN = 'ORD'` and `MKT_UNIQUE_CARRIER = 'UA'`; keep `OP_UNIQUE_CARRIER`..."*
- **Empirical Observation:** Inspecting `data/raw/otp/otp_rep_ord_2024_01.csv` (`On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip`) proved that the Reporting Carrier dataset (`110` columns) **does not contain `MKT_UNIQUE_CARRIER` or `OP_UNIQUE_CARRIER`**, omits non-reporting United Express carrier **GoJet (`G7`, `0` rows vs. `672` actual ORD flights in Jan 2024 and `64,744` performed ORD flights across 2019–2026)**, and mixes `OO` (`+524` AA/DL flights in Jan 2024) and `YX` (`+600` AA/DL flights in Jan 2024) regional operations at ORD.
- **Decision:** Ingest **BTS Marketing Carrier On-Time Performance (`On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{year}_{month}.zip`)** (`120` raw columns), filtering on `Origin == 'ORD'`, `Marketing_Airline_Network == 'UA'`, and `Duplicate == 'N'`.
- **Rationale & Implications:** Captures 100% of United mainline (`UA`) and United Express (`OO, YX, G7, ZW, EV, AX, C5`) flights marketed by `UA` with zero contamination from American Eagle or Delta Connection flights at ORD.

### D-15: Strip `PREZIP` `'Operating_Airline '` Trailing Space & Include Historical 2019 Partners `EV` and `AX` *(Phase 0 Empirical Decision)*
- **Context:** Inspecting all 56 monthly `PREZIP` archives (`2019-01..2026-08`) revealed two critical schema/domain facts:
  1. Column 16 in every `PREZIP` CSV header is literally `'Operating_Airline '` (with a trailing space), and column 120 is an empty trailing column `''`.
  2. In `2019`, `ORD` `UA`-marketed flights included **ExpressJet (`EV`, `13,073` scheduled / `12,408` performed flights)** and **Trans States Airlines (`AX`, `12,774` scheduled / `12,138` performed flights)** — together accounting for `12.9%` of all 2019 ORD UA departures — before both carriers ceased operations in 2020. Additionally, **Air Wisconsin (`ZW`)** operated `38,321` UA flights in 2019 and `8,464` in H1 2023 before switching to American Eagle (`AA`) in mid-2023.
- **Decision:**
  1. Strip whitespace on all raw `PREZIP` headers and map `Marketing_Airline_Network` → `mkt_carrier`, `'Operating_Airline '` → `op_carrier`, `Tail_Number` → `tail_num`, `FlightDate` → `flight_date`.
  2. Include `EV` and `AX` in `config/standby.yaml`, `dim_carrier`, `reference/seat_config.csv`, and `reference/faa_t100_crosswalk.csv` alongside `UA, OO, YX, YV, G7, C5, ZW`.
- **Rationale & Implications:** Prevents `KeyError: 'Operating_Airline'` during ingestion and ensures `100.00%` of 2019 baseline United Express flights are enriched and scored.

### D-16: Two-Tier FAA Registry Lookup (`MASTER.txt` → `DEREG.txt`) + Commercial Manufacturer Filter & `80`-Tail Overrides *(Phase 0 Empirical Decision)*
- **Context:** Matching all `822,719` non-null `ORD` `UA` flight tails against active `MASTER.txt` alone matched `95.80%` (`788,180` flights), leaving `34,539` flights unmatched (including `14.25%` of `2019` flights, `45.44%` of `EV` flights, `32.81%` of `AX` flights, and `7,234` retired `UA` mainline A320/757 flights).
- **Empirical Observation:**
  1. Adding `DEREG.txt` as a Tier-2 fallback recovered **`+21,206` flights (`+2.58%` overall; `+8.46%` in 2019; `+44.70%` on `EV`; `+32.81%` on `AX`)**, bringing `UA` mainline (`412,093` flights), `AX` (`12,152` flights), and `C5` (`85` flights) to **`100.00%` match**, `OO` (`215,077` flights) to **`99.9995%`**, and overall match to **`98.38%` (`99.91%` in 2024, `100.00%` in 2025–2026)**.
  2. The `13,333` unmatched flights across all 56 months come from just **`44` unique tail numbers** (`23` GoJet `N152GJ..N175GJ` 2019 CRJ-700s re-registered as CRJ-550s `N5xxGJ`; `18` Republic `N631RW..N859RW` E170/E175s; `1` `ZW` `N461AW` CRJ-200; `1` `EV` `N14902` ERJ-145; and `1` `OO` dispatcher typo `NTCR2`).
  3. Furthermore, **`43` retired 2019 regional tails (`5,496` flights)** matched an `N-NUMBER` in `MASTER.txt` or `DEREG.txt` that was subsequently **reassigned to a general aviation aircraft or agricultural drone** (e.g., `N151GJ` → `HYLIO INC AG-272` drone; `N884AS` → `EA VISION EA J100` drone; `41` `EV`/`AX`/`G7` tails colliding with 1940s–1970s Piper/Cessna records in `DEREG.txt`). Filtering `MASTER.txt` and `DEREG.txt` via `ACFTREF.txt` to commercial airliner manufacturers (`BOEING`, `AIRBUS%`, `EMBRAER%`, `YABORA%`, `BOMBARDIER%`, `CANADAIR%`, `MHI RJ%`) prevents GA/drone collisions and recovers the true commercial jet registration in `DEREG.txt` for `6` of those tails (`1,037` flights), leaving `37` N-number-reused 2019 regional tails (`4,459` flights).
- **Decision:** Implement a 3-step tail resolution pipeline:
  1. Filter `MASTER.txt` (Tier 1) and `DEREG.txt` (Tier 2, deduplicated by latest `CANCEL-DATE`) via `ACFTREF.txt` to commercial airliner manufacturers (`BOEING`, `AIRBUS%`, `EMBRAER%`, `YABORA%`, `BOMBARDIER%`, `CANADAIR%`, `MHI RJ%`).
  2. Resolve the `80` historical regional tails (`43` unmatched + `37` N-number-reused) alongside multi-config sub-fleet tails (`777-222` HD vs. `777-224` Intl, `757-224`, `767-322`) via Tier 3 `reference/tail_overrides.csv`.
- **Rationale & Implications:** Achieves **99.9999% (`814,846 / 814,847`)** exact tail and cabin seat resolution across all performed flights in 2019 and 2023–2026 with zero GA/drone collision risk.

### D-17: Combined T-100 Segment (`FMG`), 3-Tier `lf_source` Hierarchy & Multi-Brand Regional Reconciliation *(Phase 0 Empirical Decision)*
- **Context:** In T-100 (`CLASS = 'F'`), **`88.44%` (`4,254 / 4,810`)** of `UA` mainline route-months at `ORD` operate multiple `AIRCRAFT_TYPE` codes in the same month (up to 14 types on `ORD → EWR`). Meanwhile, regional carriers `OO` and `YX` report all departures under `UNIQUE_CARRIER` without a marketing carrier column, so on shared ORD spokes where `OO` or `YX` flies for both `UA` and `AA`/`DL`, T-100 `DEPARTURES_PERFORMED` exceeds UA-only OTP performed flights.
- **Decision:**
  1. Ingest the combined `T_T100_SEGMENT_ALL_CARRIER.csv` (`gnoyr_VQ=FMG`, `CLASS = 'F'`), using `DATA_SOURCE` (`DU`/`DF` vs. `IU`/`IF`) to separate Domestic and International segments.
  2. Resolve flight-level load factors via a 3-tier hierarchy recorded in `lf_source`:
     - **Tier 1 (`'carrier_route_month_aircraft'`):** `(op_carrier, origin, dest, year, month, bts_aircraft_type)`
     - **Tier 2 (`'carrier_route_month'`):** `(op_carrier, origin, dest, year, month)`
     - **Tier 3 (`'route_month'`):** `(origin, dest, year, month)` across the UA carrier family
     - **Unmatched (`'unmatched'` / `'t100_month_unavailable'`):** `est_load_factor = NULL` and `soi_* = NULL`.
  3. In Sanity Check #2 (`±10%` OTP vs. T-100 reconciliation), enforce strict two-sided `|diff| <= 10%` on dedicated carriers (`UA`, `G7`, `EV`, `AX`, `C5`, `2019 ZW` — verified `99.49%` and `98.35%` compliant) and one-sided `T-100 >= 0.90 * OTP` on multi-brand CPA carriers (`OO`, `YX`, `2023 ZW`), flagging shared spokes via `is_shared_regional_spoke`.
- **Rationale & Implications:** Prevents false alarms on shared `OO`/`YX` ORD spokes while catching any genuine missing T-100 data.

### D-18: Local `hhmm` Zero-Padding, `'2400'` Normalization & Conditional Null Constraints *(Phase 0 Empirical Decision)*
- **Context:** Across `830,941` `ORD` `UA` flights, `201,918` (`24.30%`) have leading-zero `CRSDepTime` strings (`'0001'`–`'0959'`), `129` time fields have the literal value `'2400'`, `16,094` cancelled flights have expected nulls on `ArrTime`/`ArrDelay` (`819` pushed back and recorded a `DepTime` before cancelling!), and `2,389 / 2,390` diverted flights have null `ArrDelay` (`355` also have null `ArrTime`).
- **Decision:**
  1. Pad all `hhmm` time strings to 4 characters and normalize `'2400'` → `'0000'` (integer hour `0`, `late (20–05)` hour block).
  2. Condition `pandera` and `dbt` `not_null` tests for `dep_time`, `dep_delay_min`, and `tail_num` on `is_cancelled == 0`, and for `arr_time`, `arr_delay_min`, and `actual_elapsed_time_min` on `is_cancelled == 0 AND is_diverted == 0`.
  3. Set `soi_historical = NULL` whenever `is_cancelled == 1`.
- **Rationale & Implications:** Eliminates false schema-validation failures on cancelled and diverted flights while maintaining `100.0000%` completeness checks on performed non-diverted flights (`812,457` rows).

---

## Phase 2 Enrichment Decisions (`D-19` – `D-23`)

### D-19: Strict 3-Tier Aircraft Precedence, 1:1 Mapping & Stearman Seat-Floor Guard (`faa_tc_max_seats >= 20`)
- **Context:** Inspecting `MASTER.txt` and `DEREG.txt` revealed `49` dual-registry N-numbers present in both tables, including `16` retired 1940s–1950s `BOEING A75N1` / `E75` Stearman 2-seat biplanes in `DEREG.txt` whose N-numbers were later reassigned to modern United mainline Boeing/Airbus jets in `MASTER.txt`. A naive `MASTER + DEREG` merge would produce Cartesian duplicates (`>830,941` rows) or resolve modern jets to 2-seat biplanes.
- **Decision:** Enforce strict 1:1 dictionary-keyed lookup precedence **`TAIL_OVERRIDE` → `FAA_MASTER` → `FAA_DEREG`** (latest `cancel_date`) with a commercial manufacturer prefix filter **plus** `faa_tc_max_seats >= 20` (`config.enrichment.min_faa_tc_max_seats`).
- **Rationale & Implications:** Preserves exact `830,941` row cardinality and rejects `9,263` low-seat registry collisions with zero Stearman or drone contamination.

### D-20: Curated Reference Tables (`31` Subtypes, `56` Crosswalk Rules, `152` Tail Overrides)
- **Context:** Raw FAA `MASTER` + `DEREG` leaves `2.1626%` of non-null flights (`2.1458%` of performed flights) unmatched due to historical 2019 regional N-number reassignments/purges, and FAA `ACFTREF` lacks sub-fleet cabin splits (`777-222` Domestic HD `364` vs. Intl Polaris `276`, `767-300ER` High-J `167` vs. `203`, `757-200` `169` vs. `176`).
- **Decision:** Maintain `reference/seat_config.csv` (`31` subtypes: `21` mainline + `10` express), `reference/faa_t100_crosswalk.csv` (`56` `(faa_mfr, faa_model, op_carrier)` rules verified 100% against `L_AIRCRAFT_TYPE.csv`), and `reference/tail_overrides.csv` (`152` tails: `72` mainline multi-config + `80` historical regional tails).
- **Rationale & Implications:** Resolves **`814,847 / 814,847` (`100.0000%`)** of performed flights (`0` unmatched performed; only `1` cancelled `OO` dispatcher typo `NTCR2` across all `822,719` non-null tail rows).

### D-21: 3-Tier Domestic T-100 Load Factor Hierarchy & Zero Silent Imputation
- **Context:** T-100 reports monthly carrier/route/aircraft-type traffic, with occasional carrier/type splits or post-T-100 OTP months.
- **Decision:** Join domestic T-100 (`CLASS = 'F'`, `DATA_SOURCE IN ('DU', 'DF')`) via `CARRIER_ROUTE_MONTH_TYPE` (`814,497` flights / `98.02%`) → `CARRIER_ROUTE_MONTH` (`16,430` / `1.98%`) → `ROUTE_MONTH` (`14` / `0.002%`), flag shared multi-brand regional spokes (`is_shared_regional_spoke`, `76,446` flights), and strictly leave `est_load_factor` and all `est_*` columns `NULL` whenever `t100_available == False` or `lf_source == 'UNMATCHED'`.
- **Rationale & Implications:** Eliminates silent imputation bias and preserves exact provenance in `lf_source`.

### D-22: Separation of Base Estimates vs. Modeled Demand Adjustments
- **Context:** Monthly T-100 load factors smooth out daily holiday surges and time-of-day bank peaks.
- **Decision:** Keep base estimates (`est_load_factor`, `est_passengers`, `est_open_seats`) strictly untouched alongside separate modeled adjustments (`tsa_demand_multiplier` with YAML-driven $\alpha=0.35$ and $[0.80, 1.20]$ bounds, `hour_block_multiplier`, `est_lf_adjusted`, `est_passengers_adjusted`, `est_open_seats_adjusted`).
- **Rationale & Implications:** Allows downstream analysts and `dbt` marts to inspect both raw T-100 monthly baselines and demand-adjusted estimates independently.

### D-23: Explicit Column Confidence-Tier Registry & Blocking Section 7 Sanity Checks (`SC-1`..`SC-5`)
- **Context:** Portfolio transparency requires unambiguous separation of observed facts from derived lookups and modeled estimates, plus automated data quality gates.
- **Decision:** Register every column in `COLUMN_CONFIDENCE_TIERS` (`actual`, `derived`, `estimated`), populate `record_confidence_summary`, and gate `python -m standby run` / `enrich` on all 5 Section 7 sanity checks (`SC-1`..`SC-5`), adapting `SC-2` and `SC-4` gracefully in `--sample` slice mode while enforcing strict thresholds on full runs.
- **Rationale & Implications:** Verified on all `830,941` flights (`0` failures across `SC-1`..`SC-5`; `SC-4` system UA LF = `82.8945%`; `SC-3` max seat/dep diff = `5.68%`; `docs/phase2_enrichment_summary.json`).

---

## Phase 3 dbt Marts Decisions (`D-24` – `D-28`)

### D-24: Offline BigQuery `oauth-secrets` CI Profile, Custom Schema Resolution & Zero-Dependency Tests
- **Context:** `dbt-bigquery` `method: oauth` invokes `google.auth.default()` during `dbt compile`, which fails in secret-free GitHub Actions CI. Additionally, `dbt deps` / `dbt_utils` requires external network access to `hub.getdbt.com`, and dbt's default `generate_schema_name` concatenates `marts_staging` instead of targeting `staging`.
- **Decision:** Configure `target: ci` in `dbt/profiles.yml` with `method: oauth-secrets` and `token: "{{ env_var('DBT_BQ_TOKEN', 'offline-ci-placeholder') }}"` (retaining `method: oauth` on `dev` for live BigQuery `dbt build`), implement `dbt/macros/generate_schema_name.sql` so datasets resolve directly to `raw`, `staging`, `intermediate`, and `marts`, and implement custom zero-dependency generic tests (`accepted_range`, `not_null_where`, `unique_combination_of_columns`) in `dbt/tests/generic/` without `packages.yml`.
- **Rationale & Implications:** Enables `dbt parse`, `dbt compile`, and `dbt docs generate --empty-catalog` to run 100% offline in CI with zero GCP secrets or network package downloads.

### D-25: Staging 31-Column Base Contract & `dbt/seeds/` Type Enforcement
- **Context:** `python -m standby load-raw` loads 31 base standardized columns to `raw.otp_marketing_flights`, whereas `python -m standby run` writes 54+ enriched columns. Meanwhile, `dbt-core` requires seed paths inside `dbt/`, and purely numeric BTS aircraft type codes (`'627'`, `'530'`, `'888'`) in `tail_overrides.csv` and `faa_t100_crosswalk.csv` would default to `INT64` without explicit schema hints.
- **Decision:** Have `stg_otp_flights.sql` select strictly the **31 base standardized columns** from `raw.otp_marketing_flights` (performing aircraft, T-100 LF, and modeled estimate enrichment inside `dbt/models/intermediate/`), copy `reference/*.csv` into `dbt/seeds/` with explicit `+column_types` (`bts_aircraft_type: string`, `as_of: date`, `seats_*: int64`) in `dbt/dbt_project.yml`, and enforce byte-for-byte parity between `dbt/seeds/*.csv` and `reference/*.csv` in `pytest`.
- **Rationale & Implications:** Guarantees identical dbt execution whether `raw` is populated via `load-raw` or `run`, and prevents `INT64` vs. `STRING` join mismatches on `bts_aircraft_type`.

### D-26: Conformed Dimension Primary Keys (`dim_airport`, `dim_carrier`, `dim_aircraft`)
- **Context:** Auditing the full `830,941`-flight dataset uncovered three warehouse cardinality hazards: (1) DOT `airport_id = 14027` maps to both `PBI` and `DJT` in `L_AIRPORT`; (2) `L_CARRIER_HISTORY` contains `208` duplicate carrier codes (`250` extra historical-era rows); and (3) `76` regional aircraft records across `38` tails operated under multiple `op_carrier`s over `2019–2026`, whereas `tail_num` maps `1:1` to `(faa_manufacturer, faa_model, fleet_subtype, bts_aircraft_type, seats_config)`.
- **Decision:** Key `dim_airport` on IATA `airport_code` (not `airport_id`), source `dim_carrier` `1:1` per `op_carrier` from `L_UNIQUE_CARRIERS` with `STRING_AGG` summary of `L_CARRIER_HISTORY`, and key `dim_aircraft` `1:1` on `tail_num` with aggregated `operating_carriers` and curated `source_url`/`as_of` provenance.
- **Rationale & Implications:** Eliminates Cartesian fan-out and guarantees `0` duplicate primary keys across all 4 conformed dimensions (`dim_aircraft`, `dim_airport`, `dim_carrier`, `dim_date`).

### D-27: `fct_flights` BigQuery Partitioning, `agg_route_month` Domestic + International Scope & Full-Refresh Tables
- **Context:** `ORIGINAL_REQUEST.md` §8 requires `flight_date` partitioning and `origin, dest, op_carrier` clustering on `fct_flights`, plus domestic and international route/month visibility in `agg_route_month`. Additionally, `materialized='incremental'` triggers `adapter.get_relation(...)` BigQuery network calls during `dbt compile`.
- **Decision:** Materialize `fct_flights` (`~831k` rows) as a full-refresh `table` partitioned by `DAY` on `flight_date` (expiry `NONE`) and clustered by `['origin', 'dest', 'op_carrier']` (exposing typed `CAST(NULL AS FLOAT64)` Phase 3 placeholders for `soi_historical`/`soi_planning`), and materialize `agg_route_month` (`~21k` rows) as a `table` clustered by `['origin', 'dest', 'op_carrier']` covering UA-family (`var('ua_family_carriers')`) across both Domestic (`DU`/`DF`) and International (`IU`/`IF`) T-100 segments with explicit `is_international` and `data_granularity` (`'flight_and_route_month'` vs. `'route_month_only_v1'`) labels.
- **Rationale & Implications:** Preserves offline `dbt compile` safety, aligns with `load/bigquery.py`, and keeps international route-month boundaries transparent per `D-04`.

### D-28: Deterministic Same-Day Tie-Breaking, Strict Backup Inequality & US Holiday Window Macro
- **Context:** Eight `(origin, dest, flight_date, crs_dep_time)` cells (`18` rows) in the full dataset contain multiple departures scheduled at the exact same minute (including cancelled + recovery flights), and Phase 4 SOI requires shared, leakage-free displacement/backup prep metrics and holiday calendar definitions across Python and BigQuery SQL.
- **Decision:** Order route-day flights deterministically by `(crs_dep_time ASC, is_cancelled DESC, flight_num_mkt ASC, op_carrier ASC)`, compute `weighted_displacement_hist` with intermediate-flight absorption $0.75^{\Delta k}$ ($\Delta k = \text{seq}_i - \text{seq}_j - 1$), count `remaining_scheduled_backups` and `remaining_performed_backups` using strict `j.crs_dep_time > i.crs_dep_time` (no self-count on tied times), and define 6 deterministic US holiday travel windows in `dbt/macros/holiday_calendar.sql` (`Christmas / New Year` Dec 20–Jan 3, `Thanksgiving` Wed–Mon, `Memorial Day` Fri–Tue, `Independence Day` Jul 1–7, `Labor Day` Fri–Tue, `Spring Break` Mar 10–Apr 7).
- **Rationale & Implications:** Guarantees bit-for-bit parity between Phase 3 SQL marts and Phase 4 Python SOI scoring.

---

## Phase 4 Standby Opportunity Index & Validation Decisions (`D-29` – `D-34`)

### D-29: 1:1 Python & dbt BigQuery SQL Scoring Parity
- **Context:** Phase 4 requires both Python (`src/standby/scoring/`) and BigQuery `dbt` (`int_flights_soi_scored.sql` → `fct_flights.sql`) to expose `soi_historical`, `soi_planning`, and all 8 sub-scores (`s_open_*`, `s_disp_*`, `s_backup_*`, `s_demand_*`) with identical business rules.
- **Decision:** Mirror the Python scoring equations, constants (`config/standby.yaml` / `dbt/dbt_project.yml` vars), deterministic tie-breakers, and `SC-5` eligibility gates 1:1 in `dbt/models/intermediate/int_flights_soi_scored.sql`, and verify equivalence in `tests/test_dbt_project.py` (SQLite parity test) and `dbt/tests/assert_soi_leakage_and_bounds.sql`.
- **Rationale & Implications:** Ensures BI extracts from BigQuery marts match Python validation metrics (`docs/phase4_validation_summary.json`) up to the documented $\pm 1$ seat banker's vs. half-away-from-zero rounding tolerance on exact `.5` passenger ties.

### D-30: Expanding `train.year < eval.year` Walk-Forward Priors & `2019 = NULL`
- **Context:** Evaluating `soi_planning` without future/same-year leakage requires strict temporal isolation of aircraft seat configurations, T-100 load factors, disruption rates, and TSA demand ratios.
- **Decision:** Fit all 4 planning prior hierarchies (`4-tier` modal `(seats_config, bts_aircraft_type)` with `seats_config ASC, bts_aircraft_type ASC` tie-breaker; `5-tier` T-100 LF; `4-tier` disruption rate $\hat{q}$ with `disp_plan_min_obs = 5` on Tiers 1–2; `4-tier` TSA ratios) strictly over `train.year < eval.year`. Leave `2019` (`199,882` flights, the minimum year) strictly `NULL` on `soi_planning`.
- **Rationale & Implications:** Verified via adversarial mutation testing (`test_soi_planning_strict_zero_future_leakage_adversarial`) and SQLite/dbt compilation checks (`f.year < ey.eval_year`), achieving `99.33%` planning coverage across `2023–2026`.

### D-31: Dual TSA Normalization (`tsa_ratio_month` vs. `tsa_ratio_year`) & Year-Anchored Holiday Windows
- **Context:** `m_tsa` in Phase 2 uses `tsa_ratio_month` to capture intraday/day-of-week variance within a month, whereas `S_demand` must distinguish peak holiday months (Thanksgiving/Christmas) from off-peak months (late January). Additionally, Thanksgiving Sunday falls on December 1 in both `2019` (`2019-12-01`) and `2024` (`2024-12-01`).
- **Decision:** Keep `tsa_ratio_month` for `m_tsa` (`est_lf_adjusted` / `S_open`), use annual `tsa_ratio_year` for `S_demand`, and compute the Thanksgiving window strictly as `thanksgiving_date(year) - 1 day .. + 4 days` without a `month == 11` filter in both `src/standby/scoring/holidays.py` and `dbt/macros/holiday_calendar.sql`.
- **Rationale & Implications:** Preserves full seasonal separation (`late_jan_midweek` `76.99` vs. `thanksgiving_sunday` `46.39` on `soi_historical`, $\Delta = +30.60$, $p \approx 0.0$) and captures all 2019/2024 December Thanksgiving Sunday flights (`n = 2,109`).

### D-32: Null & Eligibility Gates (`SC-5`, Cancelled `soi_historical = NULL`, Planning Survivorship)
- **Context:** A cancelled flight cannot be boarded post-hoc, whereas an advance planner does not know in advance which scheduled flights will cancel. Meanwhile, `SC-5` forbids silent imputation when T-100 is missing.
- **Decision:** Enforce `NULL` on both `soi_historical` and `soi_planning` whenever `NOT t100_available OR lf_source = 'UNMATCHED'` (`SC-5`), enforce `NULL` on `soi_historical` whenever `is_cancelled = TRUE` (`814,847 / 814,847` performed flights scored), and score cancelled flights in `soi_planning` (`626,838` flights scored across `2023–2026`).
- **Rationale & Implications:** Prevents survivorship bias in planning evaluations and guarantees `0` `SC-5` or `[0, 100]` bounds violations.

### D-33: Three-Pillar Section 6 Validation Gates (`standby validate-index`)
- **Context:** Because ground-truth gate standby clearance outcomes are unavailable in public data, index quality must be established via temporal consistency, domain face validity, and weight stability.
- **Decision:** Implement `run_soi_validation` (`python -m standby validate-index`) gating full runs on Fixed Holdout + Expanding Walk-Forward ($\rho \ge 0.55$, top-decile lift $\ge 3.0\times$, coverage $\ge 95\%$), Face-Validity monotonic peak/trough ordering + Welch $p < 10^{-10}$, and Weight Sensitivity ($\rho \ge 0.85$ across core alternative schemes), persisting full metrics to `docs/phase4_validation_summary.json` and documenting all findings and limitations in `docs/index_methodology.md`.
- **Rationale & Implications:** Full `830,941`-flight validation passes every gate (`holdout_rho = 0.6530`, `walk_forward_rho = 0.6306`, `top_decile_lift = 5.31x`, `min sensitivity rho = 0.9010`).

### D-34: Cycle-Free Package Imports (`DEFAULT_UA_FAMILY_CARRIERS` in `standby.config` & Lazy Confidence Tagging)
- **Context:** `standby.validation.sanity` and `standby.scoring.soi` both referenced `DEFAULT_UA_FAMILY_CARRIERS` from `standby.enrichment.load_factor`, while `standby.enrichment.pipeline` imported `standby.validation.sanity` and `standby.validation.soi_validation` imported `standby.scoring.soi` — creating an import-order circular dependency.
- **Decision:** Define `DEFAULT_UA_FAMILY_CARRIERS` in `src/standby/config.py` (re-exported by `load_factor.py` for compatibility) and lazy-import `tag_dataframe_confidence_tiers` inside `score_flights()`.
- **Rationale & Implications:** Guarantees `standby.scoring`, `standby.validation`, and `standby.enrichment` can be imported in any order in a fresh Python interpreter.

---

## Phase 5 Tableau Public Delivery & Portfolio Documentation (`D-35` – `D-38`)

### D-35: Stratified `100,000`-Row Default Tableau Flight Extract & `< 50 MB` File Cap
- **Context:** Full `fct_flights` spans `830,941` rows across 75 columns (`>250 MB` as uncompressed CSV), exceeding GitHub's recommended 50 MB file warning threshold and slowing Tableau Public browser rendering.
- **Decision:** Default `python -m standby export-tableau` (`config.export.tableau_flight_mode: stratified`) to a deterministic Hamilton largest-remainder stratified sample (`100,000` rows across `(year, month, op_carrier)` with `seed = 42`, `51` BI columns, `4`-decimal float precision = `30.4 MiB`), while supporting `--flight-mode recent-12m` and gitignored `--flight-mode full` (`exports/tableau/full/fct_flights_tableau_full.csv`). `--sample` exports default to gitignored `data/processed/sample/tableau/`, and every export is staged and validated before it replaces any existing file, so neither a sample run nor a failed run can overwrite the committed `exports/tableau/` set. Switching flight modes in one directory removes the other mode's flight file only when the previous `export_metadata.json` there lists it; unlisted files (e.g. a legacy `exports/tableau/fct_flights_tableau_full.csv`) are kept with a warning. An explicit `--output-dir exports/tableau` on a `--sample` or `full` run does replace the committed set (restore with `git checkout -- exports/tableau`).
- **Rationale & Implications:** Keeps the entire committed `exports/tableau/` suite at `34.91 MiB` total while preserving proportional representation across all 56 months and all 9 carriers.

### D-36: Built-In Tableau `Airport` Geo Role via `route_map_paths.csv`
- **Context:** Plotting an `ORD` hub-and-spoke route map in Tableau Public requires origin-destination path rows, and neither BTS OTP nor T-100 ships airport latitude/longitude coordinates.
- **Decision:** Export `exports/tableau/route_map_paths.csv` with 2 rows per active non-self `ORD` destination (`path_order` `1 = ORD`, `2 = dest`, excluding `147` zero-passenger `ORD -> ORD` T-100 reposition rows), exposing `airport_code` for Tableau's built-in **`Airport`** geographic role without inventing coordinates.
- **Rationale & Implications:** Complies strictly with Ground Rule #1 (no synthetic coordinates) while supporting a domestic + international spoke map (`348` destinations, `696` path rows).

### D-37: Parsed `dim_airport` City/Region & International `agg_route_month` Null Boundary
- **Context:** BTS `L_AIRPORT` encodes city and region inside a single string (`"Chicago, IL: Chicago O'Hare International"`), and international T-100 rows (`is_international = true`) lack OTP flight-level metrics (`D-04`).
- **Decision:** Parse `city_name` and `state_or_country` deterministically in `exports/tableau/dim_airport.csv` for BI region filtering while keeping `marts.dim_airport` strictly aligned to 8 columns, and enforce `otp_* = NULL` alongside `data_granularity = 'route_month_only_v1'` on all `2,300` international rows in `agg_route_month.csv`.
- **Rationale & Implications:** Delivers intuitive BI geography filters without altering BigQuery mart schemas or conflating domestic and international granularity.

### D-38: Post-Export Invariant Gate & 157-Column Confidence-Tiered Data Dictionary
- **Context:** Publishing CSV extracts alongside portfolio documentation creates risk of schema drift, silent `SC-5` leakage, PII exposure, or oversized commits.
- **Decision:** Gate `export_tableau_extracts()` on automated post-export validation (non-empty CSVs, unique PKs, `SC-5` null invariant on unmatched T-100 rows, `[0, 1]` LF & `[0, 100]` SOI bounds, zero forbidden PII columns, and `< 50 MB` file cap) and document all `157` BigQuery mart columns (`actual`/`derived`/`estimated`) in `docs/data_dictionary.md`.
- **Rationale & Implications:** Guarantees every published extract and documentation table reconciles 1:1 with the dbt marts (`dbt/models/marts/`) and Python validation outputs.

---

## Live Warehouse Setup (`D-39`)

### D-39: Live Project `flight-standby-analytics`, `GCP_PROJECT_ID` Override & Offline-Only dbt Compilation
- **Context:** `config/standby.yaml` and `dbt/profiles.yml` defaulted to a placeholder project ID (`united-standby-analytics`) that does not exist. The live warehouse lives in the billing-enabled project `flight-standby-analytics`. The Python loader read the project only from YAML, while dbt read `GCP_PROJECT_ID`, so the two could target different projects. Offline `dbt compile` also passed only because the placeholder project returned 404 during cache population. Against a real project, the placeholder CI token gets a 401, and `dbt compile` fails with a `RefreshError`.
- **Decision:** Set `bigquery.project_id: flight-standby-analytics` and use it as the default in `dbt/profiles.yml` and `dbt/models/staging/_sources.yml`. Let `GCP_PROJECT_ID` override the YAML value in `load_config()` so that one variable steers both the loader and dbt. Add `--no-populate-cache` to the offline `dbt parse`/`compile`/`docs generate` calls in the Makefile, the CI workflow and the dbt test fixture. Add `make bq-load` and `make dbt-build` for the live path; they are kept out of `make all`.
- **Rationale & Implications:** CI stays fully offline and deterministic whichever project ID is configured. Pointing the pipeline at another project means setting one environment variable, with no code or YAML edits. The current contents of the live warehouse (the sample slice) and how they were verified are recorded in `docs/bigquery_optimization.md` §7.
