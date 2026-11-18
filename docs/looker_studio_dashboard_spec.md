# Google Looker Studio Dashboard Specification (`ORD` United Standby Analytics)

This specification provides a step-by-step build guide for the **Google Looker Studio** (`lookerstudio.google.com`) interactive portfolio dashboard, offered as a zero-install, browser-native alternative alongside the Tableau Public specification ([docs/tableau_dashboard_spec.md](file:///home/antigravity/sir1602/united-standby-analytics/docs/tableau_dashboard_spec.md)).

---

## 1. Data Connection Options

Looker Studio supports two zero-cost connection paths for this project:

### Option A — Direct CSV Upload (`exports/looker/`, Zero Blends Required)
Generated via `standby export-looker` ([src/standby/export/looker.py](file:///home/antigravity/sir1602/united-standby-analytics/src/standby/export/looker.py)), the `exports/looker/` directory contains **4 pre-joined (denormalized) CSV files** totaling **`53.91 MiB`** (well below Looker Studio's `100 MB` per-dataset limit and GitHub's `50 MiB` per-file cap):

| Extract File | Rows | Cols | Size | Primary Key | Looker Studio Page / Purpose |
|---|---:|---:|---:|---|---|
| [looker_route_summary.csv](file:///home/antigravity/sir1602/united-standby-analytics/exports/looker/looker_route_summary.csv) | `348` | `29` | `0.09 MiB` | `route_id` | **Page 1:** ORD Route Network Google Maps Bubble Map & Destination Leaderboard (Domestic + International) |
| [looker_route_month.csv](file:///home/antigravity/sir1602/united-standby-analytics/exports/looker/looker_route_month.csv) | `20,877` | `42` | `7.14 MiB` | `route_month_id` | **Page 1 & Page 4:** Monthly Route Trends, Regional CPA Comparison & International Scope Toggle |
| [looker_flights.csv](file:///home/antigravity/sir1602/united-standby-analytics/exports/looker/looker_flights.csv) | `100,000` | `67` | `46.68 MiB` | `flight_id` | **Page 2 & Page 3:** Day-of-Week × Hour-Block Standby Heatmap & Flight-Level Standby Scorer |
| [looker_validation_summary.csv](file:///home/antigravity/sir1602/united-standby-analytics/exports/looker/looker_validation_summary.csv) | `45` | `7` | `0.01 MiB` | `(pillar, metric_group, metric_name)` | **Page 4:** Section 6 Empirical Validation Scorecard & Methodology Guardrails |

Because dimensions (`dim_airport`, `dim_carrier`, `dim_aircraft`, `dim_date`) are already pre-joined onto `looker_flights.csv`, `looker_route_month.csv`, and `looker_route_summary.csv`, **you do not need to configure any Data Blends in Looker Studio**.

**How to upload in Looker Studio:**
1. Open `https://lookerstudio.google.com` → **Create** → **Data source** → **File Upload**.
2. Upload each of the 4 CSV files in `exports/looker/` as its own Data Source (`ORD_Looker_Route_Summary`, `ORD_Looker_Route_Month`, `ORD_Looker_Flights`, `ORD_Looker_Validation_Summary`).
3. Verify the semantic field types listed in Section 2 below, then click **Connect**.

### Option B — Live BigQuery Connection (`marts.*`)
If you have loaded the warehouse into **BigQuery Sandbox** or a GCP project (`flight-standby-analytics` by default; see `docs/bigquery_optimization.md` §7):
1. In Looker Studio, click **Create** → **Data source** → **BigQuery**.
2. Select project `flight-standby-analytics` → dataset `marts` → connect `fct_flights` (all `830,941` flights, DAY-partitioned on `flight_date` and clustered by `origin, dest, op_carrier`) and `agg_route_month` (`20,877` route-months).

---

## 2. Field Type & Geo Configuration in Looker Studio

After uploading the CSV data sources, adjust these field types in the Looker Studio Data Source schema editor:

| Data Source | Field Name | Set Looker Studio Type To | Default Aggregation | Purpose |
|---|---|---|---|---|
| `looker_route_summary.csv` & `looker_route_month.csv` | `dest_geo_query` | **Geo → Address** | None | Plots 100% of all 348 domestic & international destination airports on Looker Studio Google Maps (`"DEN Airport, Denver, CO, US"`, `"CDG Airport, Paris, France, FR"`) without synthetic coordinates |
| `looker_route_summary.csv` & `looker_route_month.csv` | `dest_country_code` / `dest_country` | **Geo → Country** | None | Country filter / world map drill-down |
| `looker_route_month.csv` & `looker_flights.csv` | `year_month` | **Date & Time → Year Month (`YYYY-MM`)** | None | Chronological monthly time-series axis (`2019-01` .. `2026-08`) |
| `looker_flights.csv` | `flight_date` | **Date & Time → Date (`YYYY-MM-DD`)** | None | Date range filter control |
| `looker_flights.csv` | `day_of_week_label` | **Text** | None | Pre-numbered `"1 - Mon"` .. `"7 - Sun"` so Pivot Tables sort Monday → Sunday automatically |
| `looker_flights.csv` | `hour_block_label` | **Text** | None | Pre-numbered `"1 - Early (05-07)"` .. `"6 - Late (20-04)"` so heatmaps sort chronologically |
| All sources | `*_load_factor`, `otp_cancellation_rate` | **Numeric → Percent** | **Average** | Renders `[0.0, 1.0]` ratios as `0.0%`–`100.0%` |
| All sources | `soi_*`, `s_*`, `otp_avg_soi_*`, `est_open_seats*` | **Numeric → Number** | **Average** | Prevents Looker Studio from defaulting to `SUM` on index scores or average open seats |

---

## 3. Parameters & Calculated Fields (`looker_flights.csv`)

To enable the interactive **Planning (Ex-Ante, Leakage-Free) vs. Historical (Post-Hoc)** toggle on Pages 2 and 3, create 1 Parameter and 5 Calculated Fields in the `ORD_Looker_Flights` data source:

### 3.1 Parameter: `SOI Mode`
- **Parameter Name:** `SOI Mode`
- **Parameter ID:** `soi_mode`
- **Data Type:** `Text`
- **Permitted Values:** `List of values`
  - `Planning (Ex-Ante, Leakage-Free)` *(Default)*
  - `Historical (Post-Hoc Actuals)`

### 3.2 Calculated Fields

1. **`Active SOI Score`** *(Default Aggregation: Average)*
   ```sql
   CASE
     WHEN @soi_mode = "Historical (Post-Hoc Actuals)" THEN soi_historical
     ELSE soi_planning
   END
   ```
2. **`Active SOI Tier`** *(Dimension)*
   ```sql
   CASE
     WHEN @soi_mode = "Historical (Post-Hoc Actuals)" THEN soi_historical_tier
     ELSE soi_planning_tier
   END
   ```
3. **`Active Open Seats Sub-Score (50% wt)`** *(Default Aggregation: Average)*
   ```sql
   CASE
     WHEN @soi_mode = "Historical (Post-Hoc Actuals)" THEN s_open_hist
     ELSE s_open_plan
   END
   ```
4. **`Active Displacement Sub-Score (20% wt)`** *(Default Aggregation: Average)*
   ```sql
   CASE
     WHEN @soi_mode = "Historical (Post-Hoc Actuals)" THEN s_disp_hist
     ELSE s_disp_plan
   END
   ```
5. **`Active Backup Options Sub-Score (20% wt)`** *(Default Aggregation: Average)*
   ```sql
   CASE
     WHEN @soi_mode = "Historical (Post-Hoc Actuals)" THEN s_backup_hist
     ELSE s_backup_plan
   END
   ```
6. **`Active Macro Demand Sub-Score (10% wt)`** *(Default Aggregation: Average)*
   ```sql
   CASE
     WHEN @soi_mode = "Historical (Post-Hoc Actuals)" THEN s_demand_hist
     ELSE s_demand_plan
   END
   ```

---

## 4. Four-Page Looker Studio Dashboard Layout

### Page 1 — ORD Route Network & International Scope Explorer
- **Primary Data Source:** `looker_route_summary.csv` (top map & leaderboard) and `looker_route_month.csv` (bottom monthly trend).
- **Top Filter Bar:**
  - Drop-down control: `scope_label` (`Domestic (OTP Flight + T-100)` vs. `International (T-100 Route-Month Only v1)`)
  - Drop-down control: `dest_country_code`
  - Drop-down control: `dest` (`route_label`)
  - Checkbox control: `is_shared_regional_spoke`
- **Visual 1A — Destination Network Map (Google Maps → Bubble Map):**
  - **Location:** `dest_geo_query` (`Geo -> Address`)
  - **Tooltip:** `route_label`
  - **Size:** `total_departures_performed` (or `total_seats`)
  - **Color Metric:** `avg_t100_load_factor` (Diverging palette: Blue/Teal low load factor = easier standby → Amber/Red high load factor = tighter standby)
- **Visual 1B — Destination Route Leaderboard (Table with Bars/Heatmap):**
  - **Dimensions:** `route_label`, `dest_city_name`, `dest_state_or_country`, `scope_label`
  - **Metrics:** `total_departures_performed`, `avg_t100_load_factor`, `otp_avg_est_open_seats_adjusted`, `otp_avg_soi_planning`, `otp_avg_soi_historical`
  - **Note Banner:** *"International routes (`is_international = TRUE`) are analyzed at T-100 route-month granularity (`route_month_only_v1`); domestic OTP flight-level metrics (`otp_*`) are intentionally NULL on international segments."*
- **Visual 1C — Monthly Route Load Factor & Open-Seat Trend (Time Series / Combo Chart from `looker_route_month.csv`):**
  - **Dimension:** `year_month`
  - **Metrics:** `AVG(t100_load_factor)` (Line, Left Axis) and `AVG(otp_avg_est_open_seats_adjusted)` (Bars, Right Axis)

---

### Page 2 — Day-of-Week × Departure Hour-Block Standby Heatmap
- **Primary Data Source:** `looker_flights.csv`
- **Top Filter Bar:**
  - Parameter Control: `SOI Mode` (`Planning (Ex-Ante, Leakage-Free)` vs. `Historical (Post-Hoc Actuals)`)
  - Drop-down controls: `dest` (`route_label`), `year`, `month`, `carrier_group` (`MAINLINE` vs. `EXPRESS`), `holiday_name`, `is_holiday_window`
- **Visual 2A — Day × Hour-Block Standby Opportunity Matrix (Pivot Table with Heatmap):**
  - **Row Dimension:** `day_of_week_label` (Sorted **Ascending** `"1 - Mon"` → `"7 - Sun"`)
  - **Column Dimension:** `hour_block_label` (Sorted **Ascending** `"1 - Early (05-07)"` → `"6 - Late (20-04)"`)
  - **Metrics:**
    1. `AVG(Active SOI Score)` (Heatmap color scale: Red `< 50` → Yellow `50–69` → Green/Blue `>= 70`)
    2. `AVG(est_open_seats_adjusted)`
    3. `Record Count` (Flight volume)
- **Visual 2B — Holiday Window vs. Baseline Comparison (Grouped Bar Chart):**
  - **Dimension:** `holiday_name` (with `NULL` labeled `"Non-Holiday Baseline"`)
  - **Metrics:** `AVG(Active SOI Score)` and `AVG(est_open_seats_adjusted)`

---

### Page 3 — Flight-Level Standby Scorer & Component Breakdown
- **Primary Data Source:** `looker_flights.csv`
- **Top Filter Bar:**
  - Parameter Control: `SOI Mode`
  - Drop-down controls: `route_label`, `op_carrier` (`carrier_name`), `year_month`, `hour_block_label`, `Active SOI Tier`, `fleet_subtype`
- **Visual 3A — KPI Summary Tiles (4 Scorecards):**
  1. `AVG(Active SOI Score)` (`0–100`)
  2. `AVG(est_open_seats_adjusted)` (Modeled open seats)
  3. `AVG(est_load_factor)` (Base T-100 monthly load factor)
  4. `AVG(remaining_scheduled_backups)` (Same-day later backup departures)
- **Visual 3B — SOI Component Sub-Score Decomposition (Stacked / Grouped Horizontal Bar by `hour_block_label` or `route_label`):**
  - **Dimension:** `hour_block_label`
  - **Metrics:** `AVG(Active Open Seats Sub-Score (50% wt))`, `AVG(Active Displacement Sub-Score (20% wt))`, `AVG(Active Backup Options Sub-Score (20% wt))`, `AVG(Active Macro Demand Sub-Score (10% wt))`
- **Visual 3C — Individual Flight Schedule & Standby Explorer Table:**
  - **Dimensions:** `flight_date`, `crs_dep_time`, `flight_num_mkt`, `op_carrier`, `route_label`, `fleet_subtype`, `seats_config`, `lf_source`, `record_confidence_summary`
  - **Metrics:** `Active SOI Score`, `est_open_seats_adjusted`, `est_open_seats`, `remaining_scheduled_backups`, `prior_strict_scheduled_count`
  - **Sort:** `Active SOI Score` **Descending**

---

### Page 4 — Fleet, Regional CPA & Empirical Validation Scorecard
- **Primary Data Sources:** `looker_validation_summary.csv` and `looker_route_month.csv`
- **Visual 4A — Section 6 Empirical Validation Scorecard (Table / KPI Cards from `looker_validation_summary.csv`):**
  - Displays Pillar 1 walk-forward Spearman $\rho = 0.8073$ (`n = 515,017`) and 2025–2026 holdout $\rho = 0.8027$ (`n = 269,759`), Pillar 2 face-validity slices (`5/5` passed), Pillar 3 weight-sensitivity stability (`min \rho = 0.8588`), and `SC-5` zero silent imputation (`0` violations across `28,159` lag-month flights).
- **Visual 4B — Mainline vs. Dedicated Express vs. Multi-Brand Shared CPA Spokes (`looker_route_month.csv`):**
  - **Dimensions:** `carrier_name`, `is_shared_regional_spoke`
  - **Metrics:** `SUM(departures_performed)`, `SUM(otp_performed_flights)`, `AVG(t100_load_factor)`, `AVG(otp_avg_est_open_seats_adjusted)`
- **Visual 4C — Mandatory Methodology & Scope Disclaimer Banner:**
  - *"Estimates & heuristic Standby Opportunity Index (`SOI`, 0–100) derived from public BTS OTP, BTS T-100 monthly segment aggregates, FAA aircraft registry, and national TSA checkpoint throughput. Does NOT reflect proprietary United Airlines PNR booking curves, real-time cabin inventory, revenue standby / upgrade lists, or employee pass-rider seniority."*
