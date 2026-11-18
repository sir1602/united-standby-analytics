# Data Dictionary (`docs/data_dictionary.md`)

Complete column-level specification for all **6 BigQuery Analytics Marts (`157` total columns)** in `marts.*` and the **Tableau Public CSV extracts (`exports/tableau/*.csv`)**.

Every column carries an explicit **Confidence Tier** per `src/standby/enrichment/confidence.py` and `docs/decisions.md` (`D-23`):
- **`actual`**: Directly observed in BTS Marketing Carrier OTP (`PREZIP`), BTS T-100 Segment (`FMG`), BTS Lookups, or TSA daily checkpoint volumes.
- **`derived`**: Deterministic transformations, FAA Aircraft Registry (`MASTER` / `DEREG` / `ACFTREF`) lookups, curated `reference/` seat configurations (`seat_config.csv`, `tail_overrides.csv`, `faa_t100_crosswalk.csv`), and deterministic calendar/order metrics.
- **`estimated`**: Monthly T-100 load factors applied to individual flights, rounded passenger/open-seat proxies, modeled TSA/hour-block demand adjustments, and Phase 4 **Standby Opportunity Index (`soi_*`)** heuristic scores (**never a probability of boarding**).

---

## Summary Inventory (`157` BigQuery Mart Columns + Tableau Extract Projections)

| Table / Mart | Grain | Primary Key | Partition / Cluster | Columns | Source Model |
|---|---|---|---|---:|---|
| **`marts.fct_flights`** | 1 row per scheduled US domestic `ORD` `UA`-marketed flight (`830,941` rows) | `flight_id` | `DAY(flight_date)`, Cluster `(origin, dest, op_carrier)` | **75** | `dbt/models/marts/fct_flights.sql` |
| **`marts.agg_route_month`** | 1 row per `year × month × op_carrier × origin × dest × is_international` (`20,877` rows) | `route_month_id` | Cluster `(origin, dest, op_carrier)` | **30** | `dbt/models/marts/agg_route_month.sql` |
| **`marts.dim_aircraft`** | 1 row per observed `tail_num` (`1,908` rows) | `tail_num` | — | **22** | `dbt/models/marts/dim_aircraft.sql` |
| **`marts.dim_airport`** | 1 row per IATA airport station (`349` rows) | `airport_code` | — | **8** | `dbt/models/marts/dim_airport.sql` |
| **`marts.dim_carrier`** | 1 row per UA-family operating carrier (`9` rows) | `op_carrier` | — | **8** | `dbt/models/marts/dim_carrier.sql` |
| **`marts.dim_date`** | 1 row per calendar day in scope (`1,777` rows) | `date_day` | — | **14** | `dbt/models/marts/dim_date.sql` |
| **Total BigQuery Marts** | | | | **157** | |

---

## 1. `marts.fct_flights` (`75` Columns)

*Grain:* Scheduled U.S. domestic flights departing `ORD` marketed by `UA` (`Duplicate = 'N'`), covering `2019` and `2023-01`–`2026-08` (`830,941` rows).

### 1.1 Core Flight Keys, Schedule & Operational Actuals (`31` Columns)

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 1 | `flight_id` | `STRING` | `derived` | NOT NULL (PK) | Deterministic surrogate key: `YYYYMMDD\|mkt_carrier\|flight_num_mkt\|origin\|dest\|crs_dep_time`. |
| 2 | `flight_date` | `DATE` | `actual` | NOT NULL (FK → `dim_date`) | Local scheduled departure date (`DAY` partition key, `NONE` expiry). |
| 3 | `year` | `INT64` | `actual` | NOT NULL | Calendar year (`2019`, `2023`, `2024`, `2025`, `2026`). |
| 4 | `quarter` | `INT64` | `actual` | NOT NULL | Calendar quarter (`1`–`4`). |
| 5 | `month` | `INT64` | `actual` | NOT NULL | Calendar month (`1`–`12`). |
| 6 | `day_of_month` | `INT64` | `actual` | NOT NULL | Calendar day of month (`1`–`31`). |
| 7 | `day_of_week` | `INT64` | `actual` | NOT NULL | ISO day of week (`1` = Monday .. `7` = Sunday). |
| 8 | `mkt_carrier` | `STRING` | `actual` | NOT NULL | BTS `Marketing_Airline_Network` (`'UA'`). |
| 9 | `op_carrier` | `STRING` | `actual` | NOT NULL (FK → `dim_carrier`) | BTS `'Operating_Airline '` (`UA`, `OO`, `YX`, `YV`, `G7`, `C5`, `ZW`, `EV`, `AX`). |
| 10 | `flight_num_mkt` | `STRING` | `actual` | NOT NULL | Marketing carrier flight number (`Flight_Number_Marketing_Airline`). |
| 11 | `flight_num_op` | `STRING` | `actual` | Nullable | Operating carrier flight number (`Flight_Number_Operating_Airline`). |
| 12 | `tail_num` | `STRING` | `actual` | NOT NULL if `not is_cancelled` (FK → `dim_aircraft`) | Normalized aircraft registration (`N`-prefixed, e.g., `'N37502'`). |
| 13 | `tail_num_clean` | `STRING` | `derived` | Nullable | `tail_num` with leading `'N'` stripped for FAA registry joins. |
| 14 | `origin` | `STRING` | `actual` | NOT NULL (FK → `dim_airport`) | Origin IATA airport code (`'ORD'`). Clustering key #1. |
| 15 | `origin_airport_id` | `INT64` | `actual` | NOT NULL | DOT `OriginAirportID` (`13930` for `ORD`). |
| 16 | `dest` | `STRING` | `actual` | NOT NULL (FK → `dim_airport`) | Destination IATA airport code (`191` domestic spokes). Clustering key #2. |
| 17 | `dest_airport_id` | `INT64` | `actual` | NOT NULL | DOT `DestAirportID`. |
| 18 | `crs_dep_time` | `STRING` | `actual` | NOT NULL | Scheduled local departure time, 4-char zero-padded `hhmm` (`'2400'` → `'0000'`). |
| 19 | `dep_time` | `STRING` | `actual` | NOT NULL if `not is_cancelled` | Actual local departure time (`hhmm`); may be populated on gate-return cancellations. |
| 20 | `crs_arr_time` | `STRING` | `actual` | NOT NULL | Scheduled local arrival time (`hhmm`). |
| 21 | `arr_time` | `STRING` | `actual` | NOT NULL if `not is_cancelled and not is_diverted` | Actual local arrival time (`hhmm`). |
| 22 | `dep_hour` | `INT64` | `derived` | NOT NULL (`0`–`23`) | Integer scheduled departure hour `FLOOR(CAST(crs_dep_time AS INT64) / 100) MOD 24`. |
| 23 | `hour_block` | `STRING` | `derived` | NOT NULL | 6-bucket departure bank: `early` (`05–08`), `morning` (`08–11`), `midday` (`11–14`), `afternoon` (`14–17`), `evening` (`17–20`), `late` (`20–05`). |
| 24 | `dep_delay_min` | `FLOAT64` | `actual` | NOT NULL if `not is_cancelled` | Departure delay in minutes (negative = early departure). |
| 25 | `arr_delay_min` | `FLOAT64` | `actual` | NOT NULL if `not is_cancelled and not is_diverted` | Arrival delay in minutes (negative = early arrival). |
| 26 | `is_cancelled` | `BOOL` | `actual` | NOT NULL | `TRUE` if flight was cancelled (`Cancelled = 1`). |
| 27 | `cancellation_code` | `STRING` | `actual` | Nullable | BTS cancellation reason (`'A'` Carrier, `'B'` Weather, `'C'` NAS, `'D'` Security). |
| 28 | `is_diverted` | `BOOL` | `actual` | NOT NULL | `TRUE` if flight diverted (`Diverted = 1`). |
| 29 | `crs_elapsed_time_min` | `FLOAT64` | `actual` | Nullable | Scheduled block time in minutes. |
| 30 | `actual_elapsed_time_min` | `FLOAT64` | `actual` | NOT NULL if `not is_cancelled and not is_diverted` | Actual block time in minutes. |
| 31 | `distance_miles` | `FLOAT64` | `actual` | NOT NULL | Great-circle airport-to-airport distance in statute miles. |

### 1.2 Derived Aircraft & Cabin Seat Configuration (`10` Columns)

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 32 | `aircraft_source` | `STRING` | `derived` | NOT NULL | Lookup precedence tier: `TAIL_OVERRIDE`, `FAA_MASTER`, `FAA_DEREG`, `UNASSIGNED_CANCELLED`, `UNMATCHED_TYPO`, or `UNMATCHED`. |
| 33 | `faa_manufacturer` | `STRING` | `derived` | Nullable | Commercial manufacturer from `ACFTREF` / reverse crosswalk (filtered against Stearman/GA/drone collisions). |
| 34 | `faa_model` | `STRING` | `derived` | Nullable | FAA aircraft model string (e.g., `'737-924ER'`, `'ERJ 170-200 LR'`). |
| 35 | `faa_tc_max_seats` | `INT64` | `derived` | Nullable | FAA Type Certificate exit limit (`NO-SEATS >= 20`); never used as cabin capacity. |
| 36 | `fleet_subtype` | `STRING` | `derived` | Nullable | Curated United/Express cabin subtype key in `reference/seat_config.csv` (31 subtypes). |
| 37 | `bts_aircraft_type` | `STRING` | `derived` | Nullable | 3-character BTS T-100 `AIRCRAFT_TYPE` code mapped via `faa_t100_crosswalk.csv`. |
| 38 | `seat_config_source` | `STRING` | `derived` | NOT NULL | Seat lookup tier: `TAIL_OVERRIDE`, `SUBTYPE_DEFAULT`, `UNASSIGNED_CANCELLED`, or `UNMATCHED`. |
| 39 | `seats_config` | `INT64` | `derived` | Nullable (`20`–`500`) | Resolved total physical cabin seats (`seats_premium + seats_economy`). |
| 40 | `seats_premium` | `INT64` | `derived` | Nullable | Physical First / Polaris / Premium Plus seats. |
| 41 | `seats_economy` | `INT64` | `derived` | Nullable | Physical Economy Plus + standard Economy seats. |

### 1.3 T-100 Provenance & Base Estimates (`6` Columns)

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 42 | `t100_available` | `BOOL` | `derived` | NOT NULL | `TRUE` if `(year, month)` exists in domestic T-100 (`DU`/`DF`, `CLASS='F'`). |
| 43 | `lf_source` | `STRING` | `derived` | NOT NULL | 3-tier T-100 fallback: `CARRIER_ROUTE_MONTH_TYPE`, `CARRIER_ROUTE_MONTH`, `ROUTE_MONTH`, or `UNMATCHED`. |
| 44 | `is_shared_regional_spoke` | `BOOL` | `derived` | NOT NULL | `TRUE` when `op_carrier` is a multi-brand CPA partner (`OO`, `YX`, `YV`, `2023 ZW`) and T-100 departures exceed OTP departures on that spoke-month. |
| 45 | `est_load_factor` | `FLOAT64` | `estimated` | Nullable (`[0, 1]`) | Base monthly T-100 `PASSENGERS / SEATS` clipped to `[0, 1]`; `NULL` on `SC-5` unmatched T-100. |
| 46 | `est_passengers` | `INT64` | `estimated` | Nullable | Base `ROUND(seats_config * est_load_factor)`; never overwritten by modeled adjustments. |
| 47 | `est_open_seats` | `INT64` | `estimated` | Nullable | Base `seats_config - est_passengers`. |

### 1.4 Modeled TSA & Hour-Block Demand Adjustments (`7` Columns)

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 48 | `tsa_throughput` | `INT64` | `actual` | Nullable | National daily TSA checkpoint screening count on `flight_date`. |
| 49 | `tsa_demand_multiplier` | `FLOAT64` | `estimated` | Nullable (`[0.80, 1.20]`) | Damped monthly-relative multiplier `CLIP(1.0 + 0.35 * (tsa_ratio_month - 1.0), 0.80, 1.20)`. |
| 50 | `tsa_ratio_year` | `FLOAT64` | `estimated` | Nullable | Annual-relative TSA ratio `tsa_throughput / mean_year(tsa_throughput)` used in `S_demand`. |
| 51 | `hour_block_multiplier` | `FLOAT64` | `estimated` | NOT NULL | Intraday bank multiplier from `config/standby.yaml` (`0.94`–`1.04`). |
| 52 | `est_lf_adjusted` | `FLOAT64` | `estimated` | Nullable (`[0, 1]`) | Modeled `CLIP(est_load_factor * tsa_demand_multiplier * hour_block_multiplier, 0.0, 1.0)`. |
| 53 | `est_passengers_adjusted` | `INT64` | `estimated` | Nullable | Modeled `ROUND(seats_config * est_lf_adjusted)`. |
| 54 | `est_open_seats_adjusted` | `INT64` | `estimated` | Nullable | Modeled `seats_config - est_passengers_adjusted` (input to `S_open`). |

### 1.5 Same-Day Displacement, Backup & Holiday Signals (`9` Columns)

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 55 | `route_day_seq` | `INT64` | `derived` | NOT NULL | Deterministic 1-based sequence within `(origin, dest, flight_date)` ordered by `(crs_dep_time ASC, is_cancelled DESC, flight_num_mkt ASC, op_carrier ASC)`. |
| 56 | `prior_scheduled_count` | `INT64` | `derived` | NOT NULL | `route_day_seq - 1` (includes tied-minute peers). |
| 57 | `prior_strict_scheduled_count` | `INT64` | `derived` | NOT NULL | Strictly earlier scheduled route-day departures (`j.crs_dep_time < i.crs_dep_time`); used in leakage-free `S_disp_plan`. |
| 58 | `prior_disruption_count` | `INT64` | `derived` | NOT NULL | Unweighted count of earlier same-day cancellations/diversions on `(origin, dest, flight_date)`. |
| 59 | `weighted_displacement_hist` | `FLOAT64` | `derived` | NOT NULL | Exponentially decayed earlier disruption burden $D_{\text{hist}, i} = \sum w_j \cdot 0.75^{\text{seq}_i - \text{seq}_j - 1}$ ($w_j = 1.0$ cancel, $0.5$ divert). |
| 60 | `remaining_scheduled_backups` | `INT64` | `derived` | NOT NULL | Strictly later scheduled same-day departures (`j.crs_dep_time > i.crs_dep_time`); used in `S_backup_plan`. |
| 61 | `remaining_performed_backups` | `INT64` | `derived` | NOT NULL | Strictly later non-cancelled same-day departures (`j.crs_dep_time > i.crs_dep_time AND NOT j.is_cancelled`); used in `S_backup_hist`. |
| 62 | `is_holiday_window` | `BOOL` | `derived` | NOT NULL | `TRUE` if `flight_date` falls in one of 6 deterministic US holiday travel windows (`holiday_calendar.sql`). |
| 63 | `holiday_name` | `STRING` | `derived` | Nullable | Window name (`Christmas / New Year`, `Thanksgiving`, `Memorial Day`, `Independence Day`, `Labor Day`, `Spring Break`) or `NULL`. |

### 1.6 Confidence Metadata & Phase 4 Standby Opportunity Index (`12` Columns)

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 64 | `confidence_tier` | `STRING` | `derived` | NOT NULL | Column-level schema default tier (`'actual'` in base OTP; `'derived'`/`'estimated'` documented per column). |
| 65 | `record_confidence_summary` | `STRING` | `derived` | NOT NULL | Row provenance tag: `'actual+derived+estimated'` or `'actual+derived (unmatched_t100)'`. |
| 66 | `s_open_hist` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Historical open-seats sub-score (50/50 blend of abs open/25 and open%/30%); `NULL` if cancelled or `SC-5` unmatched. |
| 67 | `s_disp_hist` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Historical displacement sub-score `100 * CLIP(1 - D_hist / 2.0, 0, 1)`. |
| 68 | `s_backup_hist` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Historical backup options sub-score `100 * CLIP(LN(1 + remaining_performed_backups) / LN(7), 0, 1)`. |
| 69 | `s_demand_hist` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Historical macro demand sub-score from `tsa_ratio_year` + `0.10 * is_holiday_window`. |
| 70 | `soi_historical` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | **Post-hoc SOI heuristic** (`0.50*s_open + 0.20*s_disp + 0.20*s_backup + 0.10*s_demand`); `NULL` if cancelled or `SC-5` fails. |
| 71 | `s_open_plan` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Leakage-free open-seats sub-score from prior-year modal `seats_config`, 5-tier T-100 LF, and prior TSA ratios. `NULL` in `2019`. |
| 72 | `s_disp_plan` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Leakage-free displacement sub-score from prior-year 4-tier disruption rate $\hat{q}$ and `prior_strict_scheduled_count`. |
| 73 | `s_backup_plan` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Leakage-free backup sub-score `100 * CLIP(LN(1 + remaining_scheduled_backups) / LN(7), 0, 1)`. |
| 74 | `s_demand_plan` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | Leakage-free demand sub-score from prior-year mean TSA ratio by `(month, iso_dow, is_holiday_window)`. |
| 75 | `soi_planning` | `FLOAT64` | `estimated` | Nullable (`[0, 100]`) | **Leakage-free planning SOI heuristic** (fitted strictly on `train.year < eval.year`; `100% NULL` in `2019`; scores cancelled flights to avoid survivorship bias). |

---

## 2. `marts.agg_route_month` (`30` Columns)

*Grain:* 1 row per `(year, month, op_carrier, origin, dest, is_international)` across Domestic (`DU`/`DF`) and International (`IU`/`IF`) BTS T-100 segments for the UA carrier family (`20,877` rows).

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 1 | `route_month_id` | `STRING` | `derived` | NOT NULL (PK) | `YYYY-MM\|op_carrier\|origin\|dest\|DOM` (or `INTL`). |
| 2 | `year` | `INT64` | `actual` | NOT NULL | Segment calendar year. |
| 3 | `quarter` | `INT64` | `actual` | NOT NULL | Segment calendar quarter (`1`–`4`). |
| 4 | `month` | `INT64` | `actual` | NOT NULL | Segment calendar month (`1`–`12`). |
| 5 | `op_carrier` | `STRING` | `actual` | NOT NULL (FK → `dim_carrier`) | Operating carrier IATA code. |
| 6 | `origin` | `STRING` | `actual` | NOT NULL (FK → `dim_airport`) | Origin IATA airport code (`ORD`). |
| 7 | `origin_country` | `STRING` | `actual` | NOT NULL | Origin ISO country code (`'US'`). |
| 8 | `dest` | `STRING` | `actual` | NOT NULL (FK → `dim_airport`) | Destination IATA airport code. |
| 9 | `dest_country` | `STRING` | `actual` | NOT NULL | Destination ISO country code (`'US'` or international country). |
| 10 | `is_international` | `BOOL` | `derived` | NOT NULL | `TRUE` for T-100 `IU`/`IF` segments; `FALSE` for `DU`/`DF`. |
| 11 | `scope_label` | `STRING` | `derived` | NOT NULL | `'International (T-100 Route-Month Only v1)'` vs. `'Domestic (OTP Flight + T-100)'`. |
| 12 | `data_granularity` | `STRING` | `derived` | NOT NULL | `'route_month_only_v1'` vs. `'flight_and_route_month'` (`D-04`). |
| 13 | `departures_scheduled` | `FLOAT64` | `actual` | NOT NULL | Sum of T-100 `DEPARTURES_SCHEDULED`. |
| 14 | `departures_performed` | `FLOAT64` | `actual` | NOT NULL | Sum of T-100 `DEPARTURES_PERFORMED`. |
| 15 | `seats` | `FLOAT64` | `actual` | NOT NULL | Sum of T-100 `SEATS`. |
| 16 | `passengers` | `FLOAT64` | `actual` | NOT NULL | Sum of T-100 `PASSENGERS`. |
| 17 | `distance_miles` | `FLOAT64` | `actual` | NOT NULL | Departure-weighted segment distance in miles. |
| 18 | `aircraft_type_count` | `INT64` | `derived` | NOT NULL | Distinct BTS `AIRCRAFT_TYPE` codes operated in that route-month. |
| 19 | `t100_load_factor` | `FLOAT64` | `derived` | Nullable (`[0, 1]`) | `CLIP(passengers / seats, 0.0, 1.0)` when `seats > 0`. |
| 20 | `t100_seats_per_departure` | `FLOAT64` | `derived` | Nullable | `seats / departures_performed` when `departures_performed > 0`. |
| 21 | `is_shared_regional_spoke` | `BOOL` | `derived` | NOT NULL | `TRUE` when domestic OTP flags multi-brand regional overlap (`OO`, `YX`, `2023 ZW`). |
| 22 | `otp_scheduled_flights` | `INT64` | `actual` | NULL if `is_international` | Count of domestic OTP scheduled flights. |
| 23 | `otp_performed_flights` | `INT64` | `actual` | NULL if `is_international` | Count of domestic OTP performed flights (`not is_cancelled`). |
| 24 | `otp_cancelled_flights` | `INT64` | `actual` | NULL if `is_international` | Count of domestic OTP cancelled flights. |
| 25 | `otp_diverted_flights` | `INT64` | `actual` | NULL if `is_international` | Count of domestic OTP diverted flights. |
| 26 | `otp_cancellation_rate` | `FLOAT64` | `derived` | NULL if `is_international` | `otp_cancelled_flights / otp_scheduled_flights`. |
| 27 | `otp_avg_dep_delay_min` | `FLOAT64` | `actual` | NULL if `is_international` | Mean departure delay across performed domestic flights. |
| 28 | `otp_avg_arr_delay_min` | `FLOAT64` | `actual` | NULL if `is_international` | Mean arrival delay across non-cancelled, non-diverted domestic flights. |
| 29 | `otp_avg_est_open_seats` | `FLOAT64` | `estimated` | NULL if `is_international` | Mean base `est_open_seats` across domestic OTP flights. |
| 30 | `otp_avg_est_open_seats_adjusted` | `FLOAT64` | `estimated` | NULL if `is_international` | Mean modeled `est_open_seats_adjusted` across domestic OTP flights. |

---

## 3. `marts.dim_aircraft` (`22` Columns)

*Grain:* 1 row per unique observed `tail_num` across `fct_flights` (`1,908` tails), deduplicated cleanly across multi-carrier regional operations (`D-26`).

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 1 | `tail_num` | `STRING` | `actual` | NOT NULL (PK) | Normalized US N-number (`'N37502'`). |
| 2 | `tail_num_clean` | `STRING` | `derived` | NOT NULL | N-number without leading `'N'`. |
| 3 | `aircraft_source` | `STRING` | `derived` | NOT NULL | Highest-precedence source (`TAIL_OVERRIDE` > `FAA_MASTER` > `FAA_DEREG` > `UNMATCHED_TYPO`). |
| 4 | `faa_manufacturer` | `STRING` | `derived` | Nullable | FAA commercial aircraft manufacturer. |
| 5 | `faa_model` | `STRING` | `derived` | Nullable | FAA aircraft model code. |
| 6 | `faa_tc_max_seats` | `INT64` | `derived` | Nullable | FAA Type Certificate exit capacity (`>= 20`). |
| 7 | `fleet_subtype` | `STRING` | `derived` | Nullable | United/Express cabin layout subtype (`reference/seat_config.csv`). |
| 8 | `bts_aircraft_type` | `STRING` | `derived` | Nullable | 3-character BTS T-100 `AIRCRAFT_TYPE` code. |
| 9 | `op_carrier_group` | `STRING` | `derived` | Nullable | `'MAINLINE'` or `'EXPRESS'` from `seat_config.csv`. |
| 10 | `seat_config_source` | `STRING` | `derived` | NOT NULL | `'TAIL_OVERRIDE'`, `'SUBTYPE_DEFAULT'`, or `'UNMATCHED'`. |
| 11 | `seats_config` | `INT64` | `derived` | Nullable | Physical cabin seat count (`20`–`500`). |
| 12 | `seats_premium` | `INT64` | `derived` | Nullable | Physical premium cabin seats. |
| 13 | `seats_economy` | `INT64` | `derived` | Nullable | Physical economy cabin seats. |
| 14 | `operating_carriers` | `STRING` | `actual` | NOT NULL | Comma-separated sorted list of `op_carrier`s observed operating this tail. |
| 15 | `operating_carrier_count` | `INT64` | `derived` | NOT NULL | Count of distinct `op_carrier`s operating this tail. |
| 16 | `observed_flights_count` | `INT64` | `derived` | NOT NULL | Total observed OTP flights with this `tail_num`. |
| 17 | `first_observed_flight_date` | `DATE` | `actual` | NOT NULL | Earliest `flight_date` observed for this tail. |
| 18 | `last_observed_flight_date` | `DATE` | `actual` | NOT NULL | Latest `flight_date` observed for this tail. |
| 19 | `source_url` | `STRING` | `derived` | Nullable | Public documentation URL (`united.com` / `aerolopa.com` / `skywest.com` / FAA). |
| 20 | `as_of` | `DATE` | `derived` | Nullable | Point-in-time curation date (`2026-08-31`). |
| 21 | `override_reason` | `STRING` | `derived` | Nullable | Reason string from `tail_overrides.csv` when overridden (`NULL` otherwise). |
| 22 | `confidence_tier` | `STRING` | `derived` | NOT NULL | `'derived'` when `seats_config IS NOT NULL`, else `'actual'`. |

---

## 4. `marts.dim_airport` (`8` BigQuery Columns + `2` Tableau Parsed Columns)

*Grain:* 1 row per unique IATA station appearing in domestic OTP or UA-family T-100 (`349` stations), keyed on `airport_code` to avoid the DOT `airport_id = 14027` (`PBI`/`DJT`) collision (`D-26`).

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 1 | `airport_code` | `STRING` | `actual` | NOT NULL (PK) | 3-character IATA/FAA station code. |
| 2 | `airport_id` | `INT64` | `actual` | Nullable | DOT 5-digit `AirportID` (`L_AIRPORT_ID.csv`). |
| 3 | `airport_name` | `STRING` | `actual` | Nullable | Official BTS `L_AIRPORT` description (`"City, Region: Airport Name"`). |
| 4 | `country_code` | `STRING` | `actual` | NOT NULL | ISO country code (`'US'` or international). |
| 5 | `is_hub` | `BOOL` | `derived` | NOT NULL | `TRUE` if `airport_code` is in configured `hubs` (`ORD`). |
| 6 | `has_domestic_service` | `BOOL` | `derived` | NOT NULL | `TRUE` if served in domestic OTP or domestic T-100 (`DU`/`DF`). |
| 7 | `has_international_service` | `BOOL` | `derived` | NOT NULL | `TRUE` if served in international T-100 (`IU`/`IF`). |
| 8 | `confidence_tier` | `STRING` | `actual` | NOT NULL | SQL value literal `'actual'` (source BTS station master records; note the schema metadata column itself is registered `derived` in `COLUMN_CONFIDENCE_TIERS`). |
| *T1* | `city_name` *(Tableau CSV only)* | `STRING` | `derived` | Nullable | City parsed from `L_AIRPORT` (`split(':')[0].rsplit(',')[0]`) in `exports/tableau/dim_airport.csv`. |
| *T2* | `state_or_country` *(Tableau CSV only)* | `STRING` | `derived` | Nullable | State/country parsed from `L_AIRPORT` (`split(':')[0].rsplit(',')[1]`) in `exports/tableau/dim_airport.csv`. |

---

## 5. `marts.dim_carrier` (`8` Columns)

*Grain:* 1 row per UA-family operating carrier (`9` rows: `UA`, `OO`, `YX`, `YV`, `G7`, `C5`, `ZW`, `EV`, `AX`).

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 1 | `op_carrier` | `STRING` | `actual` | NOT NULL (PK) | Operating carrier IATA code. |
| 2 | `carrier_name` | `STRING` | `actual` | Nullable | Official BTS carrier name from `L_UNIQUE_CARRIERS.csv`. |
| 3 | `carrier_group` | `STRING` | `derived` | NOT NULL | `'MAINLINE'` (`UA`) or `'EXPRESS'`. |
| 4 | `is_mainline` | `BOOL` | `derived` | NOT NULL | `TRUE` for `UA`. |
| 5 | `is_shared_cpa_carrier` | `BOOL` | `derived` | NOT NULL | `TRUE` for multi-brand regional CPA carriers (`OO`, `YX`, `YV`, `ZW`). |
| 6 | `history_summary` | `STRING` | `actual` | Nullable | Pipe-delimited distinct historical carrier eras from `L_CARRIER_HISTORY.csv`. |
| 7 | `history_era_count` | `INT64` | `derived` | NOT NULL | Number of `L_CARRIER_HISTORY` rows aggregated for this carrier (`0`+). |
| 8 | `confidence_tier` | `STRING` | `actual` | NOT NULL | SQL value literal `'actual'` (source BTS carrier master records; note the schema metadata column itself is registered `derived` in `COLUMN_CONFIDENCE_TIERS`). |

---

## 6. `marts.dim_date` (`14` Columns)

*Grain:* 1 row per calendar day across `2019` and `2023-01-01`–`2026-11-12` (`1,777` days).

| # | Column Name | BigQuery Type | Confidence Tier | Nullable / Condition | Description & Provenance |
|---:|---|---|---|---|---|
| 1 | `date_day` | `DATE` | `derived` | NOT NULL (PK) | Calendar date (`YYYY-MM-DD`). |
| 2 | `year` | `INT64` | `derived` | NOT NULL | Calendar year. |
| 3 | `quarter` | `INT64` | `derived` | NOT NULL | Calendar quarter (`1`–`4`). |
| 4 | `month` | `INT64` | `derived` | NOT NULL | Calendar month (`1`–`12`). |
| 5 | `day_of_month` | `INT64` | `derived` | NOT NULL | Day of month (`1`–`31`). |
| 6 | `day_of_week` | `INT64` | `derived` | NOT NULL | ISO day of week (`1` = Monday .. `7` = Sunday). |
| 7 | `day_name` | `STRING` | `derived` | NOT NULL | Weekday name (`'Monday'`..`'Sunday'`). |
| 8 | `is_weekend` | `BOOL` | `derived` | NOT NULL | `TRUE` on Saturday (`6`) and Sunday (`7`). |
| 9 | `is_holiday_window` | `BOOL` | `derived` | NOT NULL | `TRUE` when `date_day` falls in one of 6 deterministic US holiday windows. |
| 10 | `holiday_name` | `STRING` | `derived` | Nullable | Matching holiday window name or `NULL`. |
| 11 | `tsa_throughput` | `INT64` | `actual` | Nullable | Deduplicated daily national TSA checkpoint passenger count. |
| 12 | `tsa_ratio_month` | `FLOAT64` | `derived` | Nullable | `tsa_throughput / mean_year_month(tsa_throughput)`. |
| 13 | `tsa_demand_multiplier` | `FLOAT64` | `estimated` | Nullable (`[0.80, 1.20]`) | Damped monthly-relative TSA multiplier (`alpha = 0.35`, clipped `[0.80, 1.20]`). |
| 14 | `tsa_ratio_year` | `FLOAT64` | `estimated` | Nullable | `tsa_throughput / mean_year(tsa_throughput)` used in `S_demand` (matches `marts.fct_flights.tsa_ratio_year` and `COLUMN_CONFIDENCE_TIERS`). |

---

## 7. Tableau Extract Additions (`exports/tableau/`)

1. **`fct_flights_tableau.csv` (`51` Columns, `100,000` Stratified Rows):** Projects the 51 BI-relevant columns of `marts.fct_flights` (`flight_id`, `flight_date`, `year`, `month`, `day_of_week`, `op_carrier`, `flight_num_mkt`, `tail_num`, `origin`, `dest`, `crs_dep_time`, `dep_hour`, `hour_block`, `dep_delay_min`, `arr_delay_min`, `is_cancelled`, `cancellation_code`, `is_diverted`, `distance_miles`, `fleet_subtype`, `bts_aircraft_type`, `seats_config`, `t100_available`, `lf_source`, `is_shared_regional_spoke`, `est_load_factor`, `est_passengers`, `est_open_seats`, `tsa_demand_multiplier`, `hour_block_multiplier`, `est_lf_adjusted`, `est_passengers_adjusted`, `est_open_seats_adjusted`, `prior_strict_scheduled_count`, `weighted_displacement_hist`, `remaining_scheduled_backups`, `remaining_performed_backups`, `is_holiday_window`, `holiday_name`, `confidence_tier`, `record_confidence_summary`, `s_open_hist`, `s_disp_hist`, `s_backup_hist`, `s_demand_hist`, `soi_historical`, `s_open_plan`, `s_disp_plan`, `s_backup_plan`, `s_demand_plan`, `soi_planning`) via deterministic proportional stratification across `(year, month, op_carrier)` (`seed=42`).
2. **`route_map_paths.csv` (`14` Columns, `696` Rows):** Two rows per active `ORD` destination (`path_order` `1 = ORD`, `2 = dest`) with columns `path_id`, `origin`, `dest`, `path_order`, `airport_code`, `route_label`, `is_international`, `scope_label`, `data_granularity`, `total_departures_performed`, `total_seats`, `total_passengers`, `avg_t100_load_factor`, `months_active`. Uses Tableau's built-in `Airport` geographic role without synthetic coordinates.
3. **`validation_summary.csv` (`7` Columns, `45` Rows):** Flattened Section 6 validation scorecard (`pillar`, `metric_group`, `metric_name`, `metric_value`, `sample_n`, `status_or_gate`, `notes`) sourced from `docs/phase4_validation_summary.json`.
4. **`export_metadata.json`:** Machine-readable export provenance, row/column counts, byte sizes, SHA-256 digests, and `invariants_passed: true`.
