# Standby Opportunity Index Methodology & Section 6 Validation (`docs/index_methodology.md`)

> **Non-Probability Disclaimer:** The **Standby Opportunity Index (SOI)** (`soi_historical` and `soi_planning`) is a transparent, documented **0–100 relative opportunity heuristic** built strictly from public U.S. government aviation datasets (BTS On-Time Performance, BTS T-100 Segment, FAA Aircraft Registry, TSA daily checkpoint throughput) and curated cabin seat references. **It is NOT a probability of boarding.** No public dataset contains actual gate standby lists, non-rev pass priorities/seniority, revenue standby counts, or cleared/bumped outcomes. This project is **not affiliated with, endorsed by, or sponsored by United Airlines, Inc.**

---

## 1. Overview & Two-Variant Leakage Boundary

To support both post-hoc operational audits and leakage-free advance trip planning without target leakage, the pipeline computes two strictly separated `0–100` composite indices across all **`830,941` `ORD` `UA`-marketed flights** (`2019` and `2023–2026-08`):

1. **`soi_historical` (Post-Hoc Realized Audit):** Evaluates completed flights (`is_cancelled = FALSE`) using realized same-month BTS T-100 load factors, actual operated `tail_num` / `seats_config`, actual earlier same-day cancellations and diversions on the same route, actual remaining non-cancelled same-day departures, and actual `flight_date` TSA checkpoint volume. Cancelled flights strictly receive `soi_historical = NULL` because a cancelled flight cannot be boarded.
2. **`soi_planning` (Leakage-Free Advance Forecast):** Evaluates **all scheduled flights** (including flights that ultimately cancelled, avoiding survivorship bias) using **only** schedule/calendar keys (`op_carrier`, `origin`, `dest`, `month`, `hour_block`, `flight_date`, `crs_dep_time`) and lookup priors fitted strictly on prior calendar years (`train.year < eval.year`). Because no prior training year exists before `2019` in our scope, **`100%` of `2019` flights strictly receive `soi_planning = NULL`**.

### Strict Signal & Leakage Boundary Matrix

| Signal / Variable | `soi_historical` (Post-Hoc Audit) | `soi_planning` (Leakage-Free Advance Plan) | Leakage Prevention Mechanism |
|---|---|---|---|
| **Training / Prior Window** | Same-month & same-day realized signals | Strictly prior calendar years (`train.year < eval.year`) | Expanding walk-forward split (`2019` $\to$ `2023`; `2019,2023` $\to$ `2024`; etc.). `2019` = `NULL`. |
| **Cabin Seat Capacity & Aircraft Type** | Realized `seats_config` & `bts_aircraft_type` from actual `tail_num` | 4-tier modal `(seats_config, bts_aircraft_type)` from prior years | `score_planning_with_priors` never reads `seats_config` or `bts_aircraft_type` from `eval_flights_df`. |
| **Base Load Factor** | Same-month T-100 LF (`CARRIER_ROUTE_MONTH_TYPE` $\to$ `CARRIER_ROUTE_MONTH` $\to$ `ROUTE_MONTH`) | 5-tier T-100 LF from prior years keyed by modal `bts_type_plan` | Never reads same-year/same-month T-100 `passengers` or `seats`. |
| **TSA Demand Multiplier ($m_{\text{tsa}}$ & $S_{\text{demand}}$)** | Actual `flight_date` TSA ratios (`tsa_ratio_month`, `tsa_ratio_year`) | 4-tier prior mean TSA ratios keyed by `(month, iso_dow, is_holiday_window)` | Never reads actual `flight_date` `tsa_throughput`. |
| **Displacement Pressure ($S_{\text{disp}}$)** | Realized earlier same-day cancellations/diversions ($D_{\text{hist}}$) | Expected disruption $\hat{D}_{\text{plan}} = N_{\text{prior}}^{\text{sched}} \cdot \hat{q} \cdot 0.65 + \hat{q}$ | Never reads `is_cancelled` or `is_diverted` on `eval_flights_df`. |
| **Backup Options ($S_{\text{backup}}$)** | Remaining same-day **non-cancelled** departures (`crs_dep_time > current`) | Remaining same-day **scheduled** departures (`crs_dep_time > current`) | Ignores whether later flights on `flight_date` eventually cancel. |
| **Own Flight Cancellation** | `is_cancelled = TRUE` $\implies$ `soi_historical = NULL` | Scored for all scheduled flights (unless `SC-5` fails) | Eliminates post-hoc survivorship bias in planning scores. |
| **Missing T-100 Gate (`SC-5`)** | `NOT t100_available OR lf_source = 'UNMATCHED'` $\implies$ `NULL` | `NOT t100_available OR lf_source = 'UNMATCHED'` $\implies$ `NULL` | Zero silent imputation across both Python and BigQuery dbt SQL. |

---

## 2. Exact Mathematical Specification

Both Python (`src/standby/scoring/soi.py`, `src/standby/scoring/holidays.py`) and BigQuery dbt SQL (`dbt/models/intermediate/int_flight_displacements_backups.sql`, `dbt/models/intermediate/int_flights_soi_scored.sql`, `dbt/macros/holiday_calendar.sql`) implement identical equations and constants configured in `config/standby.yaml`.

### 2.1 Departure Hour Blocks & TSA Normalization
Every flight is assigned to one of 6 local scheduled departure hour blocks ($h = \lfloor \text{crs\_dep\_time} / 100 \rfloor \bmod 24$, normalizing `'2400'` $\to$ `'0000'`):

| `hour_block` | Local Time Range | Integer Hour $h$ | Intraday Multiplier $m_{\text{hour}}(b)$ |
|---|---|---|---:|
| `early` | `05:00`–`07:59` | $5 \le h < 8$ | `0.96` |
| `morning` | `08:00`–`10:59` | $8 \le h < 11$ | `1.03` |
| `midday` | `11:00`–`13:59` | $11 \le h < 14$ | `1.00` |
| `afternoon` | `14:00`–`16:59` | $14 \le h < 17$ | `1.02` |
| `evening` | `17:00`–`19:59` | $17 \le h < 20$ | `1.04` |
| `late` | `20:00`–`04:59` | $h \ge 20 \lor h < 5$ | `0.94` |

Daily TSA checkpoint throughput (`stg_tsa_throughput`) is deduplicated by `date_day` (keeping max `tsa_throughput`) and normalized into two distinct ratios:
- **Monthly-relative ratio (`tsa_ratio_month`):** $r_{\text{tsa, month}}(d) = \frac{\text{TSA}(d)}{\overline{\text{TSA}}_{\text{year-month}(d)}}$, used exclusively inside the damped open-seat adjustment multiplier:
  $$m_{\text{tsa}}(d) = \text{clip}\!\left(1.0 + 0.35 \cdot \left(r_{\text{tsa, month}}(d) - 1.0\right),\, 0.80,\, 1.20\right)$$
- **Annual-relative ratio (`tsa_ratio_year`):** $r_{\text{tsa, year}}(d) = \frac{\text{TSA}(d)}{\overline{\text{TSA}}_{\text{year}(d)}}$, used exclusively inside the macro demand sub-score $S_{\text{demand}}$ so cross-month holiday peaks (e.g., Thanksgiving vs. late January) retain their full seasonal separation.

### 2.2 Deterministic 6-Window US Holiday Calendar
`is_holiday_window` and `holiday_name` are evaluated deterministically in both `src/standby/scoring/holidays.py` and `dbt/macros/holiday_calendar.sql` across 6 windows:
1. **Christmas / New Year:** Dec 20 – Jan 3 (inclusive)
2. **Thanksgiving:** $\text{Thanksgiving}(y) - 1\text{ day}$ through $\text{Thanksgiving}(y) + 4\text{ days}$, where $\text{Thanksgiving}(y)$ is the 4th Thursday of November. Critically, the Sunday check is **year-anchored without a `month == 11` filter**, capturing December Thanksgiving Sundays (`2019-12-01` and `2024-12-01`).
3. **Memorial Day:** Last Monday of May $- 3\text{ days}$ (Fri) through $+ 1\text{ day}$ (Tue)
4. **Independence Day:** Jul 1 – Jul 7 (inclusive)
5. **Labor Day:** First Monday of September $- 3\text{ days}$ (Fri) through $+ 1\text{ day}$ (Tue)
6. **Spring Break:** Mar 10 – Apr 7 (inclusive)

### 2.3 Deterministic Route-Day Ordering & Tie-Breakers
Within each `(origin, dest, flight_date)` partition, flights are ordered deterministically by:
$$\text{ORDER BY } \text{crs\_dep\_time ASC},\ \text{is\_cancelled DESC},\ \text{flight\_num\_mkt ASC},\ \text{op\_carrier ASC}$$
- **Strict Backup & Prior Counts:** `prior_strict_scheduled_count` counts $j$ with `j.crs_dep_time < i.crs_dep_time`; `remaining_scheduled_backups` counts $j$ with `j.crs_dep_time > i.crs_dep_time`; `remaining_performed_backups` counts $j$ with `j.crs_dep_time > i.crs_dep_time AND NOT j.is_cancelled`. Strict inequality prevents tied-minute self/peer inflation.
- **Modal Tie-Breaker (`_fit_modal_tier`):** When resolving modal `(seats_config, bts_aircraft_type)` in `soi_planning`, ties on frequency `COUNT(*) DESC` break deterministically by `seats_config ASC, bts_aircraft_type ASC`.

### 2.4 Planning Prior Hierarchies (`train.year < eval.year`)
1. **4-Tier Modal `(seats_config_plan, bts_type_plan)`:**
   1. `(op_carrier, origin, dest, month, hour_block)`
   2. `(op_carrier, origin, dest, month)`
   3. `(op_carrier, origin, dest)`
   4. `(origin, dest)`
2. **5-Tier Planning T-100 Load Factor (`lf_plan`, keyed by `bts_type_plan`):**
   1. `(op_carrier, origin, dest, month, bts_type_plan)` (domestic `DU`/`DF`)
   2. `(op_carrier, origin, dest, month)`
   3. `(origin, dest, month)` across UA-family carriers
   4. `(op_carrier, origin, dest)`
   5. `(origin, dest)` across UA-family carriers
3. **4-Tier Expected Disruption Rate ($\hat{q}_{\text{plan}}$, `disp_plan_min_obs = 5`):**
   Each prior flight receives weight $w_j = 1.0$ if cancelled, $0.5$ if diverted, $0.0$ otherwise:
   1. `(origin, dest, month, hour_block)` requiring $\ge 5$ training flights
   2. `(origin, dest, month)` requiring $\ge 5$ training flights
   3. `(origin, dest)` requiring $\ge 1$ training flight
   4. Global training mean $\bar{w}_{\text{train}}$
4. **4-Tier Planning TSA Ratios (`r_tsa_month_plan`, `r_tsa_year_plan`):**
   1. `(month, iso_dow, is_holiday_window)`
   2. `(month, iso_dow)`
   3. `(month)`
   4. Global training mean

### 2.5 Sub-Score & Composite Equations
For `soi_planning`, adjusted planning open seats are derived from `seats_config_plan`, `lf_plan`, `r_tsa_month_plan`, and `hour_block_multiplier`:
$$\text{lf}_{\text{plan, adj}} = \text{clip}\!\left(\text{lf}_{\text{plan}} \cdot m_{\text{tsa, plan}} \cdot m_{\text{hour}},\, 0.0,\, 1.0\right), \quad \text{open}_{\text{plan, adj}} = \text{seats\_config\_plan} - \text{round}\!\left(\text{seats\_config\_plan} \cdot \text{lf}_{\text{plan, adj}}\right)$$

Each sub-score lies strictly in $[0.0, 100.0]$:

| Sub-Score | Starting Weight | Equation |
|---|---:|---|
| **$S_{\text{open}}$** | `50%` | $0.50 \cdot \left(100 \cdot \text{clip}\!\left(\frac{\text{open}_{\text{adj}}}{25.0}, 0, 1\right)\right) + 0.50 \cdot \left(100 \cdot \text{clip}\!\left(\frac{\text{open}_{\text{adj}} / \text{seats}}{0.30}, 0, 1\right)\right)$ |
| **$S_{\text{disp, hist}}$** | `20%` | $D_{\text{hist}, i} = \sum_{j: \text{seq}_j < \text{seq}_i} w_j \cdot (0.75)^{\text{seq}_i - \text{seq}_j - 1}, \qquad S_{\text{disp, hist}, i} = 100 \cdot \text{clip}\!\left(1.0 - \frac{D_{\text{hist}, i}}{2.0}, 0, 1\right)$ |
| **$S_{\text{disp, plan}}$** | `20%` | $\hat{D}_{\text{plan}, i} = N_{\text{prior, strict}, i}^{\text{sched}} \cdot \hat{q}_{\text{plan}, i} \cdot 0.65 + \hat{q}_{\text{plan}, i}, \qquad S_{\text{disp, plan}, i} = 100 \cdot \text{clip}\!\left(1.0 - \frac{\hat{D}_{\text{plan}, i}}{0.25}, 0, 1\right)$ |
| **$S_{\text{backup}}$** | `20%` | $100 \cdot \text{clip}\!\left(\frac{\ln(1 + N_{\text{after}, i})}{\ln(1 + 6)}, 0, 1\right)$ using $N_{\text{after}}^{\text{perf}}$ (`hist`) or $N_{\text{after}}^{\text{sched}}$ (`plan`) |
| **$S_{\text{demand}}$** | `10%` | $I_{\text{demand}, i} = r_{\text{tsa, year}} + 0.10 \cdot \mathbb{I}(\text{is\_holiday\_window}_i), \qquad S_{\text{demand}, i} = 100 \cdot \text{clip}\!\left(\frac{1.35 - I_{\text{demand}, i}}{1.35 - 0.65}, 0, 1\right)$ |

Composite scores are rounded to 2 decimal places:
$$\text{soi\_*} = \text{round}\!\left(0.50 \cdot S_{\text{open}} + 0.20 \cdot S_{\text{disp}} + 0.20 \cdot S_{\text{backup}} + 0.10 \cdot S_{\text{demand}},\, 2\right)$$

**Python vs. BigQuery SQL Parity:** Both engines implement identical formulas. The only expected numerical difference occurs on exact `.5` passenger rounding ties (`np.round` half-to-even vs. BigQuery/SQLite `ROUND` half-away-from-zero), producing at most a $\pm 1$ seat difference on rare `.5` ties (`tests/test_dbt_project.py` verifies parity across all 80 sample flights).

---

## 3. Empirical Section 6 Validation Results (`830,941` Flights)

Full-dataset validation was executed on **`830,941` `ORD` `UA` flights** (`814,847` performed, `16,094` cancelled) via `python -m standby validate-index --hub ORD --years 2019,2023-2026 --summary-path docs/phase4_validation_summary.json`. All automated gates **PASSED (`passed: true`, `gate_failures: []`)**.

### 3.1 Coverage, Distribution & Invariant Summary

| Year | Total Flights | Performed | Cancelled | `soi_historical` Scored | `soi_planning` Scored | Planning Coverage |
|---|---:|---:|---:|---:|---:|---:|
| **2019** | 199,882 | 194,127 | 5,755 | 194,127 (100.0% perf.) | **0** (`100% NULL`) | 0.00% (no prior year) |
| **2023** | 151,447 | 149,486 | 1,961 | 149,486 (100.0% perf.) | 149,077 | 98.44% |
| **2024** | 161,074 | 158,869 | 2,205 | 158,869 (100.0% perf.) | 160,806 | 99.83% |
| **2025** | 181,927 | 179,482 | 2,445 | 179,482 (100.0% perf.) | 180,881 | 99.43% |
| **2026** *(Jan–Aug)* | 136,611 | 132,883 | 3,728 | 132,883 (100.0% perf.) | 136,074 | 99.61% |
| **Total** | **830,941** | **814,847** | **16,094** | **814,847** | **626,838** | **99.33%** *(2023–2026)* |

- **`soi_historical` Distribution ($n=814,847$):** Min `4.58`, P10 `40.53`, P25 `49.62`, Median `61.79`, Mean `61.73`, P75 `74.03`, P90 `83.02`, Max `100.00`, Std `15.99`.
- **`soi_planning` Distribution ($n=626,838$):** Min `2.04`, P10 `37.61`, P25 `46.77`, Median `57.97`, Mean `58.09`, P75 `69.74`, P90 `78.97`, Max `98.20`, Std `15.65`.
- **Invariants:** `SC-5` (`0` silent imputation violations), `SOI-BOUNDS` (`0` out-of-bounds values across all 10 score/sub-score columns), `min_year_soi_planning_non_null = 0`, `is_cancelled` `soi_historical` non-null `= 0`.

### 3.2 Pillar 1: Time-Based Backtest (`soi_planning` vs. `soi_historical`)

> **Consistency Caveat:** Both `soi_planning` and `soi_historical` use BTS T-100 monthly load factors as their underlying capacity baseline because public datasets do not contain flight-level gate standby clearance outcomes. Therefore, time-based backtesting measures **temporal consistency** between advance-planning and post-hoc proxies rather than ground-truth non-rev boarding accuracy.

| Backtest Regime | Train Years | Eval Years | Paired Flights | Planning Coverage | Spearman $\rho$ | Top-Decile Hit Rate (Lift) | Bottom-Decile Hit Rate (Lift) | MAE | RMSE |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| **Fixed Holdout** | `2019, 2023, 2024` | `2025–2026` | 309,930 | 99.22% | **0.6530** | **53.12% (5.31x)** | 38.91% (3.89x) | 11.46 | 14.68 |
| **Walk-Forward: 2023** | `2019` | `2023` | 147,149 | 98.44% | 0.5645 | 51.55% (5.16x) | 34.68% (3.47x) | 11.29 | 14.61 |
| **Walk-Forward: 2024** | `2019, 2023` | `2024` | 158,605 | 99.83% | 0.6610 | 56.93% (5.69x) | 40.92% (4.09x) | 9.98 | 13.10 |
| **Walk-Forward: 2025** | `2019, 2023, 2024` | `2025` | 178,453 | 99.43% | 0.6877 | 55.33% (5.53x) | 42.08% (4.21x) | 10.38 | 13.44 |
| **Walk-Forward: 2026** | `2019, 2023–2025` | `2026` | 132,357 | 99.61% | 0.6543 | 54.63% (5.46x) | 37.80% (3.78x) | 12.01 | 15.21 |
| **Walk-Forward Combined** | *Expanding* | `2023–2026` | **616,564** | **99.33%** | **0.6306** | **53.11% (5.31x)** | 38.37% (3.84x) | 10.84 | 14.04 |

All gates pass comfortably: Fixed Holdout $\rho = 0.6530 \ge 0.55$, Walk-Forward Combined $\rho = 0.6306 \ge 0.55$, Top-Decile Lift $= 5.31\times \ge 3.0\times$, and Coverage $= 99.22\% \ge 95\%$. Notice that 2023 ($\rho = 0.5645$) is lowest because it relies solely on pre-COVID 2019 priors, jumping immediately to $\rho = 0.6610$–$0.6877$ once post-COVID 2023–2024 network structure enters the training window.

### 3.3 Pillar 2: Face-Validity Checks (Peak Holidays vs. Off-Peak Troughs)

| Travel Window | `soi_historical` $n$ | `soi_historical` Mean (Med) | `soi_planning` $n$ | `soi_planning` Mean (Med) | Key Driver & Empirical Insight |
|---|---:|---:|---:|---:|---|
| **`late_jan_midweek`** *(Jan 15–31 Tue/Wed)* | 10,109 | **76.99** (78.12) | 7,936 | **67.33** (68.69) | Deepest off-peak trough ($S_{\text{open, hist}} = 80.93$, $S_{\text{demand, hist}} = 89.16$). |
| **`all_flights_baseline`** | 814,847 | **61.73** (61.79) | 626,838 | **58.09** (57.97) | System-wide 56-month baseline. |
| **`spring_break`** *(Mar 15–31)* | 40,393 | **59.32** (59.56) | 31,724 | **57.54** (57.39) | Spring Break surge ($S_{\text{demand, hist}} = 31.10$). |
| **`christmas_peak`** *(Dec 20–30)* | 20,278 | **51.78** (50.69) | 14,514 | **53.70** (52.99) | Strong peak suppression ($S_{\text{open, hist}} = 41.94$, $S_{\text{demand, hist}} = 24.06$). |
| **`thanksgiving_sunday`** *(Year-anchored)* | 2,109 | **46.39** (43.33) | 1,547 | **55.19** (55.07) | Captures `2019-12-01` & `2024-12-01`; `soi_historical` drops $-30.60$ pts below Jan trough ($S_{\text{demand, hist}} = 0.79$). |
| *Sub-slice:* `christmas_eve_and_day` *(Dec 24–25)* | 3,216 | **65.21** (65.71) | 2,322 | **53.15** (52.60) | **Bimodal holiday effect:** Dec 24–25 flies lighter loads ($S_{\text{open, hist}} = 63.99$) than Dec 20–23 & 26–30 (`49.25`). |
| *Sub-slice:* `christmas_peak_excl_eve_day` | 17,062 | **49.25** (47.83) | 12,192 | **53.80** (53.03) | Excluding Dec 24–25 reveals true holiday peak compression (`49.25`). |

- **Statistical Separation (`late_jan_midweek` vs. `thanksgiving_sunday`):**
  - `soi_historical`: $\Delta \text{mean} = +30.60$, Welch $t = 86.31$ ($p \approx 0.0$), Mann-Whitney $U$ $p \approx 0.0$.
  - `soi_planning`: $\Delta \text{mean} = +12.13$, Welch $t = 28.68$ ($p = 1.66 \times 10^{-152}$), Mann-Whitney $U$ $p = 6.99 \times 10^{-165}$.
- **Why `soi_planning` Trough-vs-Peak Separation Narrows:**
  1. In `soi_planning`, the TSA `(month, iso_dow, is_holiday_window)` prior blends `Dec 1` Thanksgiving Sunday (`2019-12-01`, `2024-12-01`) with `Dec 22/29` holiday Sundays, and blends `Jan 15–31` troughs (`is_holiday_window = False`) with `Jan 1–14` vs. non-holiday Jan dynamics while historical November/December T-100 monthly LFs average Dec 24–25 lighter travel with Dec 20–23 peak days.
  2. Furthermore, `soi_planning` retains `late_jan_midweek` > `all_flights_baseline` > `spring_break` > `christmas_peak` strictly monotonically, and separates `late_jan_midweek` from `thanksgiving_sunday` by `+12.13` points ($p < 10^{-150}$).

### 3.4 Pillar 3: Weight Sensitivity Analysis ($n = 814,847$)

| Weighting Scheme | Weights `(open / disp / backup / demand)` | Mean Score | Std | Spearman $\rho$ vs. Baseline | Top-10% Overlap | Bottom-10% Overlap |
|---|---|---:|---:|---:|---:|---:|
| **`baseline`** | `0.50 / 0.20 / 0.20 / 0.10` | 61.73 | 15.99 | **1.0000** | 100.00% | 100.00% |
| **`seat_heavy`** | `0.65 / 0.15 / 0.15 / 0.05` | 60.68 | 18.98 | **0.9833** | 87.46% | 87.34% |
| **`disruption_heavy`** | `0.40 / 0.30 / 0.20 / 0.10` | 65.99 | 13.62 | **0.9940** | 94.28% | 92.36% |
| **`backup_heavy`** | `0.40 / 0.15 / 0.35 / 0.10` | 57.87 | 16.50 | **0.9222** | 80.70% | 72.99% |
| **`equal_weights`** | `0.25 / 0.25 / 0.25 / 0.25` | 61.74 | 12.74 | **0.9010** | 77.02% | 72.45% |
| **`demand_heavy`** | `0.40 / 0.15 / 0.15 / 0.30` | 58.12 | 14.94 | **0.9665** | 75.37% | 84.28% |

Every alternative weighting achieves $\rho \ge 0.9010$ (well above the `0.85` stability gate), demonstrating that flight opportunity rankings are structurally robust to reasonable weight perturbations.

---

## 4. Comprehensive Metric & Domain Limitations (11 Items)

Per `docs/phase0_validation_report.md` §3.5, every consumer of the Standby Opportunity Index must account for these 11 limitations:

1. **Heuristic Score, Not a Boarding Probability (`soi_*`):** Public datasets lack actual United Airlines gate standby lists, cleared/bumped counts, and revenue no-show rates. Both `soi_historical` and `soi_planning` are 0–100 heuristics; backtesting measures temporal consistency between proxies, not ground-truth clearance accuracy.
2. **T-100 Monthly Aggregation Masks Flight-Level Variance ($S_{\text{open}}$):** BTS T-100 reports monthly `PASSENGERS` and `SEATS` per `(carrier, origin, dest, aircraft_type)`. Intraday and day-of-week variation in `est_lf_adjusted` comes strictly from modeled TSA and hour-block multipliers ($m_{\text{tsa}} \times m_{\text{hour}}$), never observed flight-level loads.
3. **Regional Carrier CPA Ambiguity (`OO`, `YX`, `2023 ZW`):** Regional operators report under `UNIQUE_CARRIER` without `MKT_UNIQUE_CARRIER`. On overlapping ORD spokes (`76,446` flights flagged `is_shared_regional_spoke`), T-100 LF reflects combined `OO`/`YX` operations across `UA`/`AA`/`DL`.
4. **Non-Rev Pass Priority & Seniority Blindness:** Boarding priority depends on pass classification (`SA1P`, `SA2`, buddy pass) and company seniority date. The index measures physical seat and operational supply, independent of a traveler's position on the standby list.
5. **Revenue Standby, Same-Day Confirmed & Upgrade Dynamics:** Revenue standbys/SDC clear ahead of all non-revs, while Premier upgrades open Economy seats from First/Polaris. `est_open_seats` evaluates total cabin capacity (`seats_config`) since Economy fills last.
6. **Weight & Balance, Payload & Fuel Restrictions:** Flights with physical open seats can still deny non-revs due to winter headwinds, summer heat/runway limits, cargo, or alternate fuel (especially 50-seat CRJ-200/ERJ-145s).
7. **Displacement Pressure Proxy Scope ($S_{\text{disp}}$):** Captures earlier same-day cancellations/diversions on the direct `ORD → DEST` route, but cannot observe multi-day rebookings, alternative-hub reroutes (`DEN`, `IAH`, `EWR`), or inbound connecting misconnects rolling onto `ORD → DEST`.
8. **Backup Options Proxy Scope ($S_{\text{backup}}$):** Measures remaining departure frequency, not remaining open seats across those later flights. Analysts should inspect both `S_backup` and later hour-block `soi_*` scores.
9. **TSA National Checkpoint Volume Scope ($S_{\text{demand}}$):** TSA reports **national** daily screening totals across all U.S. airports, capturing macro holiday/day-of-week peaks while missing localized city-specific events (e.g., conventions in `LAS`/`MCO` or Chicago festivals).
10. **Point-in-Time Seat Configuration (`seats_config`):** `reference/seat_config.csv` and `reference/tail_overrides.csv` use curated `as_of` cabin layouts. Multi-year cabin retrofits ("United Next") are guarded via Sanity Check #3 (`T-100 seats/dep` vs. `seats_config`, max diff `5.68%`).
11. **International Scope in v1 (`agg_route_month`):** BTS OTP covers U.S. domestic flights only. All `51,673` `ORD` `UA` international departures (`2019`, `2023–2026-08`) are analyzed at the `route × month × carrier` grain in `agg_route_month` (`is_international = true`, `data_granularity = 'route_month_only_v1'`). Flight-level international tracking via OpenSky Network / FlightAware AeroAPI is a documented **v2 enhancement**.

---

## 5. Day-Based Standby Seat Likelihood Predictor Extension (`D-41`)

Building directly on the validated `soi_historical` / `soi_planning` pipeline without replacing its non-probability heuristic guarantee, the repository includes a **Day-Based Standby Seat Likelihood Predictor** (`src/standby/prediction/`, `dbt/models/marts/agg_predictor_priors.sql`, `standby predict`, and Tab 1 of the live dashboard):

- **4-Tier Hierarchical Empirical Bayes Priors:** Fits open-seat means/standard deviations, seat capacities, cancellation rates, and scheduled departure-frequency distributions across all `348` ORD outbound destinations (`277` domestic hour-block routes + `71` international `route_month_only_v1` routes), with shrinkage from `(dest, month, dow, hour_block)` $\to$ `(dest, month, hour_block)` $\to$ `(dest, month)` $\to$ `(month, hour_block)`.
- **Correlated Day-Level Monte Carlo (`Mulberry32`, $N = 2{,}000$):** Combines shifted Log-Normal open-seat draws with shared ORD hub IRROPS (`0.35`) and same-day route demand (`0.58`) correlation factors, cancellation spillover (`18.0` seats/cancel with `0.50` decay into later banks), and `40` outer epistemic batches for an `80%` confidence interval. Implemented with bit-for-bit parity between Python (`src/standby/prediction/simulator.py`) and browser JS (`dashboard/predictor.js`).
- **Analytical Honesty Guardrail:** Every prediction strictly separates **empirically calibrated open-seat availability** ($P(\text{open seats} \ge \text{party size})$ validated via expanding walk-forward Brier/BSS/ECE/AUC in `src/standby/prediction/calibration.py`) from **pass-tier queue clearance** (`SA1P` through `BUDDY`), unless the traveler supplies live **EmployeeRES** counts (`open_seats`, `standbys_ahead`, `hours_until_departure`). See `docs/decisions.md` (`D-41`) for full specification.

