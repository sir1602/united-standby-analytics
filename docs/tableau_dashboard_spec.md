# Tableau Public Dashboard Specification & Wireframe (`docs/tableau_dashboard_spec.md`)

**Workbook Title:** **United Standby Opportunity Explorer (`ORD` Hub)**  
**Target Platform:** Tableau Public (`.twbx` packaged workbook backed by `exports/tableau/*.csv` extracts)  
**Data Scope:** `2019` (pre-COVID baseline) & `2023-01` – `2026-08` (`56` months; `830,941` domestic `ORD` `UA`-marketed flights stratified to `100,000` BI rows in `fct_flights_tableau.csv` + `20,877` domestic & international route-month segments in `agg_route_month.csv`)

---

## 1. Mandatory Visible Disclaimers (Exact Copy for Dashboard Header & Footer)

Every published Tableau Public tab must display these three disclaimers prominently (header banner + persistent footer):

1. **Non-Probability Heuristic Disclaimer (Header Banner):**
   > **Standby Opportunity Index (0–100 Heuristic — NOT a Boarding Probability):** `soi_historical` and `soi_planning` are transparent 0–100 relative opportunity heuristics derived strictly from public U.S. government aviation datasets (BTS OTP, BTS T-100, FAA Aircraft Registry, TSA daily checkpoint throughput) and curated cabin seat references. **They do not represent a probability of clearing standby.** Public datasets do not contain United Airlines gate standby lists, pass priorities (`SA1P`/`SA2`/buddy pass), hire-date seniority, revenue standby/SDC counts, or weight-and-balance payload caps.

2. **Non-Affiliation Disclaimer (Persistent Footer):**
   > **Independent Project — Not Affiliated with United Airlines:** This analytics engineering portfolio project is an independent educational work and is **not affiliated with, endorsed by, or sponsored by United Airlines, Inc.** or its United Express regional partners.

3. **International Granularity & Multi-Brand Regional CPA Disclaimer (Map & Route Tables):**
   > **Scope & Granularity Notice:** Domestic `ORD` departures (`fct_flights_tableau.csv`) are modeled at the flight level. **International departures (`is_international = TRUE` in `agg_route_month.csv`) are reported strictly at the `route × month × operating carrier` grain (`data_granularity = 'route_month_only_v1'`)** because BTS On-Time Performance covers U.S. domestic flights only; flight-level international tracking via OpenSky Network / FlightAware AeroAPI is a documented v2 enhancement. On shared regional spokes (`is_shared_regional_spoke = TRUE`), `OO` (SkyWest), `YX` (Republic), and `2023 ZW` (Air Wisconsin) T-100 load factors reflect combined multi-brand regional operations across `UA`/`AA`/`DL`.

---

## 2. Extract Data Model & Tableau Relationships (`exports/tableau/`)

Connect Tableau Desktop / Tableau Public to the CSV files in `exports/tableau/` using Tableau's multi-table **Logical Layer Relationships** (avoiding physical SQL joins that could fan out dimensions):

```mermaid
flowchart LR
    FCT["fct_flights_tableau.csv<br/>(PK: flight_id | 100,000 stratified rows)"]
    DA["dim_airport.csv<br/>(PK: airport_code | 349 stations)<br/>Role: Destination"]
    DO["dim_airport.csv [Origin]<br/>(PK: airport_code)<br/>Role: Origin Hub"]
    DC["dim_carrier.csv<br/>(PK: op_carrier | 9 carriers)"]
    DAC["dim_aircraft.csv<br/>(PK: tail_num | 1,908 tails)"]
    DD["dim_date.csv<br/>(PK: date_day | 1,777 days)"]

    AGG["agg_route_month.csv<br/>(PK: route_month_id | 20,877 rows)<br/>Domestic + Intl T-100"]
    RMP["route_map_paths.csv<br/>(PK: path_id + path_order | 696 rows)<br/>Tableau Built-in Airport Geo"]
    VAL["validation_summary.csv<br/>(Standalone Validation Scorecard)"]

    FCT -->|"dest = airport_code"| DA
    FCT -->|"origin = airport_code"| DO
    FCT -->|"op_carrier = op_carrier"| DC
    FCT -->|"tail_num = tail_num"| DAC
    FCT -->|"flight_date = date_day"| DD
    AGG -->|"origin, dest, is_international"| RMP
    AGG -->|"dest = airport_code"| DA
    AGG -->|"op_carrier = op_carrier"| DC
```

### Exact File Inventory & Relationship Keys

| Extract File | Primary Key | Relationship Key(s) to Fact / Aggregate | Notes |
|---|---|---|---|
| `fct_flights_tableau.csv` | `flight_id` | `dest`, `origin`, `op_carrier`, `tail_num`, `flight_date` | `100,000` stratified rows across `(year, month, op_carrier)` (`seed=42`); 51 BI columns |
| `agg_route_month.csv` | `route_month_id` | `dest`, `op_carrier`, `(origin, dest, is_international)` | All `20,877` UA-family route-months (`18,577` domestic + `2,300` international) |
| `dim_airport.csv` | `airport_code` | `airport_code` | `349` airports with parsed `city_name`, `state_or_country`, `country_code`, `is_hub` |
| `dim_aircraft.csv` | `tail_num` | `tail_num` | `1,908` observed tails with `fleet_subtype`, `seats_config`, `source_url`, `as_of` |
| `dim_carrier.csv` | `op_carrier` | `op_carrier` | `9` UA-family carriers (`UA`, `OO`, `YX`, `YV`, `G7`, `C5`, `ZW`, `EV`, `AX`) |
| `dim_date.csv` | `date_day` | `date_day` | `1,777` days (`2019`, `2023–2026`) with `is_holiday_window`, `holiday_name`, TSA metrics |
| `route_map_paths.csv` | `(path_id, path_order)` | `(origin, dest, is_international)` | 2 rows per active `ORD` destination (`path_order`: `1=ORD`, `2=dest`); uses Tableau `Airport` geo role |
| `validation_summary.csv` | *(scorecard)* | Standalone data source | Summarizes Pillar 1 backtest, Pillar 2 face validity, Pillar 3 weight sensitivity, `SC-5` |

> **Important Null-Handling Rule (`2019` & `is_cancelled`):**
> - In `fct_flights_tableau.csv`, **`2019` flights strictly have `soi_planning = NULL`** (no prior training year in scope), and **cancelled flights (`is_cancelled = TRUE`) strictly have `soi_historical = NULL`**.
> - Tableau aggregations (`AVG`, `MEDIAN`) automatically ignore `NULL`s, but tooltips and summary cards must display `N/A (2019 baseline — no prior training year)` or `N/A (Cancelled flight)` rather than coalescing `NULL` to `0`.

---

## 3. Parameter & Tableau Calculated Fields

### 3.1 Parameter
- **`[Index Mode]`** (String List):
  - `'Planning (Leakage-Free)'` *(Default — for prospective trip planning across `2023–2026`)*
  - `'Historical (Post-Hoc)'` *(For retrospective audit across `2019` & `2023–2026` completed flights)*

### 3.2 Exact Calculated Fields (referencing exact CSV column names)

| Calculated Field Name | Exact Tableau Formula | Purpose |
|---|---|---|
| **`Selected SOI`** | `IF [Index Mode] = "Planning (Leakage-Free)" THEN [soi_planning] ELSE [soi_historical] END` | Primary 0–100 score bound to `[Index Mode]` |
| **`Selected Open Seats Score`** | `IF [Index Mode] = "Planning (Leakage-Free)" THEN [s_open_plan] ELSE [s_open_hist] END` | $S_{\text{open}}$ (`50%` weight) |
| **`Selected Displacement Score`** | `IF [Index Mode] = "Planning (Leakage-Free)" THEN [s_disp_plan] ELSE [s_disp_hist] END` | $S_{\text{disp}}$ (`20%` weight) |
| **`Selected Backup Score`** | `IF [Index Mode] = "Planning (Leakage-Free)" THEN [s_backup_plan] ELSE [s_backup_hist] END` | $S_{\text{backup}}$ (`20%` weight) |
| **`Selected Demand Score`** | `IF [Index Mode] = "Planning (Leakage-Free)" THEN [s_demand_plan] ELSE [s_demand_hist] END` | $S_{\text{demand}}$ (`10%` weight) |
| **`SOI Opportunity Band`** | `IF ISNULL([Selected SOI]) THEN "Unscored (Cancelled / 2019 Plan)" ELSEIF [Selected SOI] >= 75 THEN "High Opportunity (75-100)" ELSEIF [Selected SOI] >= 55 THEN "Moderate Opportunity (55-74)" ELSEIF [Selected SOI] >= 35 THEN "Tight (35-54)" ELSE "Constrained (0-34)" END` | Color/categorical tiering (explicitly avoids calling any band a probability) |
| **`Hour Block Order`** | `CASE [hour_block] WHEN "early" THEN 1 WHEN "morning" THEN 2 WHEN "midday" THEN 3 WHEN "afternoon" THEN 4 WHEN "evening" THEN 5 WHEN "late" THEN 6 END` | Forces chronological sorting `05:00` → `04:59` |
| **`Hour Block Display`** | `CASE [hour_block] WHEN "early" THEN "Early (05:00-07:59)" WHEN "morning" THEN "Morning (08:00-10:59)" WHEN "midday" THEN "Midday (11:00-13:59)" WHEN "afternoon" THEN "Afternoon (14:00-16:59)" WHEN "evening" THEN "Evening (17:00-19:59)" WHEN "late" THEN "Late (20:00-04:59)" END` | Clean axis labels |
| **`Day of Week Name`** | `CASE [day_of_week] WHEN 1 THEN "Mon" WHEN 2 THEN "Tue" WHEN 3 THEN "Wed" WHEN 4 THEN "Thu" WHEN 5 THEN "Fri" WHEN 6 THEN "Sat" WHEN 7 THEN "Sun" END` | Maps ISO `1..7` to weekday labels |
| **`Adjusted Open Seat Pct`** | `IF [seats_config] > 0 THEN [est_open_seats_adjusted] / [seats_config] END` | Open cabin % for tooltips |
| **`Shared Spoke Warning`** | `IF [is_shared_regional_spoke] THEN "Shared CPA Spoke (OO/YX/2023 ZW multi-brand T-100)" ELSE "UA-Dedicated Segment" END` | Visual warning badge on regional routes |
| **`Route Granularity Label`** | `IF [is_international] THEN "International: Route-Month Only (v1)" ELSE "Domestic: Flight + Route-Month" END` | Enforces `D-04` transparency |
| **`Holiday Regime Label`** | `IF [is_holiday_window] THEN [holiday_name] ELSE "Non-Holiday Baseline" END` | Peak vs. off-peak grouping |

---

## 4. Dashboard Wireframe & 7 Core Views

### 4.1 Layout Wireframe (1400 × 950 px Desktop Canvas, 4 Story Tabs)

```text
+---------------------------------------------------------------------------------------------------+
| UNITED STANDBY OPPORTUNITY EXPLORER (ORD HUB)                                [Index Mode: Plan v] |
| [!] DISCLAIMER: 0-100 Relative Opportunity Heuristic — NOT a Boarding Probability. Not affiliated |
|     with United Airlines. Intl routes = Route-Month Only (v1); Shared CPA spokes flagged.         |
+---------------------------------------------------------------------------------------------------+
| GLOBAL FILTERS: [Dest/Region] [Carrier/Group] [Hour Block] [Year/Month/Holiday] [Fleet] [Scope]   |
+---------------------------------------------------------------------------------------------------+
| KPI STRIP (View 1):                                                                               |
|  Avg Selected SOI | Median Open Seats (Adj) | Avg T-100 LF (Adj) | Avg Backups | Validation Lift  |
|     58.1 / 100    |       19.2 seats        |       82.8%        |   3.4 flts  |  5.31x Top-Dec   |
+---------------------------------------------------+-----------------------------------------------+
| VIEW 2: ORD HUB-AND-SPOKE ROUTE MAP               | VIEW 3: HOUR-BLOCK x DAY-OF-WEEK HEATMAP      |
| (`route_map_paths.csv` + `agg_route_month.csv`)   | (`fct_flights_tableau.csv`)                   |
|                                                   |                                               |
|  * ORD hub -> 348 destinations (DOM + INTL)       |        Mon  Tue  Wed  Thu  Fri  Sat  Sun    |
|  * Color: Scope / Avg T-100 Load Factor           | Early  [72] [74] [73] [70] [66] [71] [69]    |
|  * Size: Departures Performed                     | Morn   [55] [59] [58] [54] [51] [60] [56]    |
|  * Click any spoke -> filters Views 3, 4, 5, 6    | Mid    [58] [62] [61] [57] [53] [61] [57]    |
|                                                   | Aftn   [54] [58] [57] [52] [49] [59] [53]    |
|                                                   | Eve    [50] [54] [53] [48] [45] [56] [48]    |
|                                                   | Late   [66] [69] [68] [64] [61] [68] [63]    |
+---------------------------------------------------+-----------------------------------------------+
| VIEW 4: ROUTE & CARRIER DRILL-DOWN TABLE          | VIEW 5: HOLIDAY & TSA SEASONAL DEMAND CURVE   |
|  Dest | Carrier | Subtype | Seats | Open | SOI    |  Monthly / Holiday SOI vs TSA Multiplier      |
|  DEN  | UA      | B737-9  |  179  |  24  | 64.2   |  Late Jan Midweek (77.0) >> Thanksgiving (46) |
|  LHR  | UA      | (INTL)  |  --   |  --  | v1 R-M |  Overlay: S_open, S_disp, S_backup, S_demand  |
+---------------------------------------------------+-----------------------------------------------+
| VIEW 6: FLEET SUBTYPE & CABIN CAPACITY EXPLORER   | VIEW 7: INTL ROUTE-MONTH & VALIDATION SCORECARD|
|  31 Subtypes: 777-200 HD (364) vs CRJ-550 (50)    |  agg_route_month (is_international=TRUE) +    |
|  Seats Config vs Est Open Seats & Aircraft Source |  validation_summary.csv (rho=0.653, SC-5=0)   |
+---------------------------------------------------+-----------------------------------------------+
| FOOTER: Independent Portfolio Project (BTS / FAA / TSA Public Data). Not affiliated w/ United.    |
+---------------------------------------------------------------------------------------------------+
```

### 4.2 Detailed Specification of the 7 Views

1. **View 1 — Executive KPI Strip & Validation Callout (`fct_flights_tableau.csv` + `validation_summary.csv`):**
   - Cards: `AVG([Selected SOI])`, `AVG([est_open_seats_adjusted])`, `AVG([est_lf_adjusted])`, `AVG([remaining_scheduled_backups])`, and fixed methodology validation metrics from `validation_summary.csv` (Fixed Holdout Spearman $\rho = 0.6530$, Top-Decile Lift $= 5.31\times$, `SC-5` Violations $= 0$).
2. **View 2 — `ORD` Hub-and-Spoke Route Map (`route_map_paths.csv` + `agg_route_month.csv`):**
   - Assign Tableau's built-in **`Airport`** geographic role to `route_map_paths.csv[airport_code]` (zero custom/invented coordinates).
   - Marks: **Line**; Path = `path_order` (Dimension); Detail = `path_id`, `dest`, `scope_label`, `data_granularity`; Color = `AVG([avg_t100_load_factor])` (diverging blue-amber palette) or `is_international`; Size = `SUM([total_departures_performed])`.
   - Action Filter: Clicking any destination filters Views 3, 4, 5, and 6 to `dest`.
3. **View 3 — Hour-Block × Day-of-Week Opportunity Matrix (`fct_flights_tableau.csv`):**
   - Rows: `[Hour Block Display]` sorted by `[Hour Block Order]`. Columns: `[Day of Week Name]` (`Mon`–`Sun`).
   - Text & Color: `AVG([Selected SOI])`, diverging green/gold/coral palette (`35`–`80`), tooltip showing all 4 component sub-scores (`S_open`, `S_disp`, `S_backup`, `S_demand`), `est_open_seats_adjusted`, and `remaining_scheduled_backups`.
4. **View 4 — Route & Carrier Drill-Down Table (`fct_flights_tableau.csv` + `dim_carrier.csv`):**
   - Grain: `(dest, op_carrier, fleet_subtype)` showing `COUNTD([flight_id])` (or sampled flight count), `seats_config`, `AVG([est_open_seats_adjusted])`, `AVG([soi_planning])`, `AVG([soi_historical])`, and `[Shared Spoke Warning]` icon when `is_shared_regional_spoke = TRUE`.
5. **View 5 — Holiday & Seasonal Demand Curve (`dim_date.csv` + `fct_flights_tableau.csv`):**
   - Dual-axis / stacked breakdown comparing `is_holiday_window` / `holiday_name` regimes (`Thanksgiving`, `Christmas / New Year`, `Spring Break`, `Independence Day`, `Memorial Day`, `Labor Day`) against `Non-Holiday Baseline` and `Late Jan Midweek` trough, plotting `AVG([soi_historical])`, `AVG([soi_planning])`, and `AVG([tsa_demand_multiplier])`.
6. **View 6 — Aircraft Sub-Fleet & Cabin Capacity Explorer (`dim_aircraft.csv` + `fct_flights_tableau.csv`):**
   - Horizontal bar + boxplot across United's `31` curated fleet subtypes (`B777-200_HD` `364` seats down to `CRJ-200` / `CRJ-550` / `ERJ-145` `50` seats), comparing `seats_config`, `AVG([est_open_seats_adjusted])`, `AVG([Selected SOI])`, and `aircraft_source` provenance (`FAA_MASTER`, `FAA_DEREG`, `TAIL_OVERRIDE`).
7. **View 7 — International Route-Month Explorer (v1 Scope) (`agg_route_month.csv`):**
   - Filtered strictly to `is_international = TRUE` (`data_granularity = 'route_month_only_v1'`), listing `dest`, `dest_country`, `op_carrier`, `departures_performed`, `seats`, `passengers`, `t100_load_factor`, and `t100_seats_per_departure`, with an inline callout noting flight-level international tracking is deferred to v2 (OpenSky / FlightAware AeroAPI).

---

## 5. Interactive Filters & Controls (All 6 Required)

| # | Filter Control | Source Column(s) | Tableau Widget Type | Applies To |
|---|---|---|---|---|
| **1** | **Destination Airport / Region** | `dest`, `dim_airport.state_or_country`, `dim_airport.country_code` | Multi-select dropdown + map click action | Views 1–7 |
| **2** | **Operating Carrier** | `op_carrier`, `dim_carrier.carrier_group` (`MAINLINE` / `EXPRESS`) | Multi-select checklist | Views 1–7 |
| **3** | **Departure Hour Block** | `hour_block` (`early`, `morning`, `midday`, `afternoon`, `evening`, `late`) | Multi-select pills (chronological) | Views 1, 3–6 |
| **4** | **Year / Month / Holiday Window** | `year`, `month`, `is_holiday_window`, `holiday_name` | Compact dropdowns + Holiday toggle | Views 1–7 |
| **5** | **Fleet Subtype** | `fleet_subtype` (`dim_aircraft.fleet_subtype`) | Searchable multi-select dropdown | Views 1, 3, 4, 6 |
| **6** | **Scope (Domestic vs. International)** | `agg_route_month.is_international`, `scope_label` | Single/multi toggle (`DOM` / `INTL`) | Views 1, 2, 7 |

---

## 6. Tableau Public Publishing Checklist

1. Open Tableau Desktop / Tableau Public Desktop and connect to `exports/tableau/fct_flights_tableau.csv` + dimensions (`dim_airport.csv`, `dim_carrier.csv`, `dim_aircraft.csv`, `dim_date.csv`), plus a second logical connection for `agg_route_month.csv` + `route_map_paths.csv` and a third for `validation_summary.csv`.
2. Right-click `route_map_paths.csv[airport_code]` → **Geographic Role** → **Airport** (and set `path_order` to **Dimension** / Continuous Integer on the **Path** mark).
3. Verify `year`, `month`, `day_of_week`, and `dep_hour` are treated cleanly as Dimensions where used for slicing.
4. Create the `[Index Mode]` parameter and all calculated fields from §3.2.
5. Verify all 3 mandatory disclaimers (§1) appear visibly on the canvas and tooltips never display `NULL` planning (`2019`) or `NULL` historical (cancelled) scores as `0`.
6. Create Extract (`.hyper`), save as packaged workbook (`.twbx`), and publish to Tableau Public with data-source attribution (`BTS OTP`, `BTS T-100`, `FAA Registry`, `TSA Checkpoint`).
