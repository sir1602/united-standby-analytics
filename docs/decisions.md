# Technical Decisions & Assumptions Log (`docs/decisions.md`)

Per Ground Rule #2 (`ORIGINAL_REQUEST.md` §11, Line 180: *"Record every technical decision and assumption in `docs/decisions.md`"*), this document records the **13 baseline architectural decisions (`D-01` through `D-13`)**, the **5 Phase 0 empirical technical decisions (`D-14` through `D-18`)**, and the **5 Phase 2 enrichment decisions (`D-19` through `D-23`)**.

---

## Summary Table (`D-01` – `D-23`)

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
| **D-13** | Repository | Public GitHub Repo & Phased PR Workflow | Public GitHub repo `united-standby-analytics`, one PR per phase (PR #1–#6), stopping at Gates 1, 2, and 3 | Approved (Baseline) |
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

---

## Detailed Decision Records (`D-01` – `D-18`)

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
- **Decision:** Export optimized CSV extracts from the BigQuery `marts` tables (`fct_flights`, `agg_route_month`, `dim_*`) to `exports/tableau/` (~831k domestic flight rows + ~15k route-month rows, well within Tableau Public's 15M row limit), and deliver complete calculated-field formulas and a dashboard wireframe specification in `docs/tableau_dashboard_spec.md`.
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

### D-13: Public GitHub Repository & Six-PR Phased Workflow with Stop Gates
- **Context:** Structured code review across phases mirrors real-world engineering teams and enforces review gates after Phase 0, after Enrichment, and after Index Validation.
- **Decision:** Maintain a 1-PR-per-phase workflow (`PR #1` Phase 0 Validation + Architecture; `PR #2` Ingestion + Manifest + Raw Load; `PR #3` Enrichment + Reference Tables; `PR #4` dbt Staging → Marts + CI; `PR #5` SOI + Validation; `PR #6` Tableau Extracts + Dashboard Spec + Final README).
- **Rationale & Implications:** Keeps diffs focused and auditable at each stop gate.

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

