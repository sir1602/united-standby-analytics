# Data Acquisition & Manifest Specification (`docs/data_acquisition.md`)

This document specifies **both** the automated/scripted acquisition recipes and the exact step-by-step manual browser download instructions (URLs, form selections, filters, and checkboxes/fields selected) for all five public aviation data sources used in **United Airlines Standby Opportunity Analytics**, along with the `data/manifest.csv` checksum contract and repository `.gitignore` rules.

---

## 1. Storage Layout, Git-Ignore Policy & Checksum Manifest

### 1.1 Directory Policy
- **Raw Data (`data/raw/`):** Strictly **git-ignored** via `.gitignore` (`data/raw/`, `*.zip`, `*.parquet`). Raw files are never committed to Git.
- **CI Sample (`data/sample/`):** Only a small, anonymized-by-design sample slice (`data/sample/`) is committed in Phase 1 (PR #2) so GitHub Actions CI (`ruff`, `pytest`, `dbt parse`) can run deterministically without downloading multi-gigabyte archives or requiring GCP credentials.
- **Manifest (`data/manifest.csv`):** Tracks every raw file consumed by the pipeline. Before running ingestion or enrichment, the pipeline verifies that each file in `data/manifest.csv` exists and matches its recorded `sha256_checksum` and `row_count`.

### 1.2 `data/manifest.csv` Schema Contract
| Column | Type | Description | Example |
|---|---|---|---|
| `file` | `string` | Relative path from repository root | `data/raw/t100/t100_segment_all_2024.csv` |
| `source_url` | `string` | Canonical upstream download URL | `https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4` |
| `download_date` | `string` (`YYYY-MM-DD`) | UTC date the file was acquired | `2026-11-13` |
| `period_covered` | `string` | Year, year-month, or date range covered | `2024-01..2024-12` |
| `row_count` | `integer` | Data row count (excluding header line) | `549799` |
| `sha256_checksum` | `string` (64 hex chars) | Lowercase SHA-256 hex digest of the file | `c430bcfae3ade4ba455942ec25f5c1bad9b3e964d7b8d5f20a8a888430085b60` |

---

## 2. Source 1: BTS On-Time Performance — Marketing Carrier (`PREZIP` & TranStats `FGK`)

### 2.1 Why Marketing Carrier OTP (`FGK`) Instead of Reporting Carrier OTP (`OTP`)
As proved empirically in `docs/phase0_validation_report.md` (Section 2.2), the legacy **Reporting Carrier On-Time Performance (`1987–present`)** dataset lacks `MKT_UNIQUE_CARRIER` (`Marketing_Airline_Network`) and `OP_UNIQUE_CARRIER` (`'Operating_Airline '`), completely omits non-reporting United Express partner **GoJet Airlines (`G7`)**, and cannot separate SkyWest (`OO`) or Republic (`YX`) flights operated for United Express (`UA`) from those operated for American Eagle (`AA`) or Delta Connection (`DL`) at `ORD`. Therefore, this project uses **Marketing Carrier On-Time Performance (Beginning January 2018)**.

### 2.2 Scripted Acquisition (Direct `PREZIP` Monthly Archives)
- **URL Pattern:**
  ```text
  https://transtats.bts.gov/PREZIP/On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{year}_{month}.zip
  ```
  - `{year}`: `2019`, `2023`, `2024`, `2025`, `2026`
  - `{month}`: Unpadded integer `1` through `12` (`1` through `8` for `2026`)
  - **Total files:** `56` monthly `.zip` archives (`1,830.3 MB` total compressed; `35,881,885` total U.S. flight rows).
- **HTTP & Parsing Notes:**
  - `transtats.bts.gov` throttles aggregate bandwidth per IP to `~1.7 MB/s`; use `ThreadPoolExecutor(max_workers=10)` in batches of 8–12 months.
  - Inside each `.zip`, extract the `.csv` entry (`On_Time_Marketing_Carrier_On_Time_Performance_(Beginning_January_2018)_{year}_{month}.csv`).
  - **Header Quirk:** Column 16 in the raw `PREZIP` CSV header has a literal trailing space (`'Operating_Airline '`), and column 120 is a trailing empty column (`''`). Always strip header whitespace (`[h.strip() for h in header]`) and drop the trailing empty column.
  - **Filter Criteria:**
    ```python
    row["Origin"] == "ORD"
    and row["Marketing_Airline_Network"] == "UA"
    and row["Duplicate"] == "N"
    ```

### 2.3 Manual Browser Download Instructions (TranStats Web Form `FGK`)
If downloading via the TranStats web interface instead of `PREZIP`:
1. Navigate to:
   `https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FGK&QO_fu146_anzr=b0-gvzr`
2. Set dropdown filters at the top of the page:
   - **Filter Geography:** `Illinois` (or `All`)
   - **Filter Year:** `2019` (repeat for `2023`, `2024`, `2025`, `2026`)
   - **Filter Period:** `January` through `December` (`January` through `August` for `2026`)
3. Check the following required field checkboxes:
   - **Time Period:** `YEAR`, `QUARTER`, `MONTH`, `DAY_OF_MONTH`, `DAY_OF_WEEK`, `FL_DATE`
   - **Airline:** `MKT_UNIQUE_CARRIER`, `MKT_CARRIER`, `MKT_CARRIER_FL_NUM`, `OP_UNIQUE_CARRIER`, `OP_CARRIER`, `TAIL_NUM`, `OP_CARRIER_FL_NUM`
   - **Origin:** `ORIGIN_AIRPORT_ID`, `ORIGIN_AIRPORT_SEQ_ID`, `ORIGIN_CITY_MARKET_ID`, `ORIGIN`, `ORIGIN_CITY_NAME`, `ORIGIN_STATE_ABR`
   - **Destination:** `DEST_AIRPORT_ID`, `DEST_AIRPORT_SEQ_ID`, `DEST_CITY_MARKET_ID`, `DEST`, `DEST_CITY_NAME`, `DEST_STATE_ABR`
   - **Departure Performance:** `CRS_DEP_TIME`, `DEP_TIME`, `DEP_DELAY`, `DEP_DELAY_NEW`, `DEP_DEL15`, `DEP_TIME_BLK`, `TAXI_OUT`, `WHEELS_OFF`
   - **Arrival Performance:** `WHEELS_ON`, `TAXI_IN`, `CRS_ARR_TIME`, `ARR_TIME`, `ARR_DELAY`, `ARR_DELAY_NEW`, `ARR_DEL15`, `ARR_TIME_BLK`
   - **Cancellations and Diversions:** `CANCELLED`, `CANCELLATION_CODE`, `DIVERTED`
   - **Flight Summaries:** `CRS_ELAPSED_TIME`, `ACTUAL_ELAPSED_TIME`, `AIR_TIME`, `FLIGHTS`, `DISTANCE`, `DISTANCE_GROUP`
   - **Cause of Delay:** `CARRIER_DELAY`, `WEATHER_DELAY`, `NAS_DELAY`, `SECURITY_DELAY`, `LATE_AIRCRAFT_DELAY`
   - **Duplicate Indicator:** `DUPLICATE`
4. Click **Download** (`btnDownload`) and extract the resulting `.csv` into `data/raw/otp/`.

---

## 3. Source 2: BTS T-100 Segment — All Carriers, Domestic & International (`FMG`)

### 3.1 Table Selection (`FMG` vs. `GEE` / `FJE`)
BTS TranStats provides three T-100 Segment tables (`All Carriers`):
- **Combined Domestic + International (`gnoyr_VQ=FMG`):** `T_T100_SEGMENT_ALL_CARRIER.csv` — contains both U.S. Domestic segments (`DATA_SOURCE IN ('DU', 'DF')`) and International segments (`DATA_SOURCE IN ('IU', 'IF')`) in a single unified 50-column table.
- **Domestic Only (`gnoyr_VQ=GEE`):** `T_T100D_SEGMENT_ALL_CARRIER.csv`
- **International Only (`gnoyr_VQ=FJE`):** `T_T100I_SEGMENT_ALL_CARRIER.csv`

We use the combined **`FMG` (`T_T100_SEGMENT_ALL_CARRIER.csv`)** table so a single annual export per year (`2019`, `2023`, `2024`, `2025`, `2026`) provides 100% of Domestic (`fct_flights`, `agg_route_month`) and International (`agg_route_month`) segment records.

### 3.2 Scripted Acquisition (ASP.NET Stateful Form `POST`)
- **Form URL:**
  ```text
  https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4
  ```
- **Automated `GET` → `POST` Protocol:**
  1. Perform an HTTP `GET` request with `http.cookiejar.CookieJar()` and `User-Agent: Mozilla/5.0`.
  2. Parse the 7 hidden ASP.NET form inputs from the HTML response:
     - `affiliate`, `__EVENTTARGET`, `__EVENTARGUMENT`, `__LASTFOCUS`, `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`
  3. Parse all `<input type="checkbox" name="...">` elements and include **only the 50 data column checkboxes** (`not name.startswith("chk")`).
     - **Critical Pitfall:** Do **NOT** include `chkAllVars`, `chkAllGroups`, or `chkDownloadZip` in the `POST` body — those control checkboxes trigger ASP.NET `__doPostBack` handlers that cause the server to hang if submitted alongside `btnDownload=Download`.
  4. Set dropdown parameters:
     - `cboGeography = "All"`
     - `cboYear = str(year)` (`"2019"`, `"2023"`, `"2024"`, `"2025"`, `"2026"`)
     - `cboPeriod = "All"`
     - `btnDownload = "Download"`
  5. Submit **sequentially** (one year at a time to avoid ASP.NET session locks) and extract `T_T100_SEGMENT_ALL_CARRIER.csv` from the returned `.zip` archive to `data/raw/t100/t100_segment_all_{year}.csv`.

### 3.3 Manual Browser Download Instructions
1. Open `https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4` in a browser.
2. Set dropdowns:
   - **Filter Geography:** `All`
   - **Filter Year:** `2019` (repeat for `2023`, `2024`, `2025`, `2026`)
   - **Filter Period:** `All`
3. Check **Select All Fields** (`chkAllVars`) in the browser (wait 2 seconds for the page postback to check all 50 data columns), or manually check:
   - `DEPARTURES_SCHEDULED`, `DEPARTURES_PERFORMED`, `PAYLOAD`, `SEATS`, `PASSENGERS`, `FREIGHT`, `MAIL`, `DISTANCE`, `RAMP_TO_RAMP`, `AIR_TIME`, `UNIQUE_CARRIER`, `AIRLINE_ID`, `UNIQUE_CARRIER_NAME`, `CARRIER`, `CARRIER_NAME`, `ORIGIN_AIRPORT_ID`, `ORIGIN`, `ORIGIN_CITY_NAME`, `ORIGIN_STATE_ABR`, `ORIGIN_COUNTRY`, `DEST_AIRPORT_ID`, `DEST`, `DEST_CITY_NAME`, `DEST_STATE_ABR`, `DEST_COUNTRY`, `AIRCRAFT_GROUP`, `AIRCRAFT_TYPE`, `AIRCRAFT_CONFIG`, `YEAR`, `QUARTER`, `MONTH`, `CLASS`, `DATA_SOURCE`.
4. Click **Download**, unzip the downloaded `T_T100_SEGMENT_ALL_CARRIER_*.zip`, and save the `.csv` as `data/raw/t100/t100_segment_all_{year}.csv`.

---

## 4. Source 3: BTS Lookup Tables (`L_AIRCRAFT_TYPE`, `L_AIRPORT_ID`, `L_CARRIER_HISTORY`, `L_AIRPORT`, `L_UNIQUE_CARRIERS`)

### 4.1 Scripted Direct Download URLs (ROT13 Parameter `Y11x72`)
TranStats serves its lookup CSVs directly via `Download_Lookup.asp?Y11x72=<ROT13_TABLE_NAME>`:

| Target File in `data/raw/lookups/` | Direct Scriptable URL | Verified Rows (`data/raw/lookups/`) | SHA-256 Checksum (`2026-11-13`) |
|---|---|---:|---|
| `L_AIRCRAFT_TYPE.csv` | `https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVePeNSg_glcR` | `453` | `4fdb4909c671582f0267a730b7a486cab218d2405076d95c344e4d4b2f77d4c4` |
| `L_AIRPORT_ID.csv` | `https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVecbeg_VQ` | `6,918` | `cff27be77b3da705cdce87fbfe6c07d93087468d5aa0d139bb816f637e97137d` |
| `L_CARRIER_HISTORY.csv` | `https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_PNeeVRe_UVfgbel` | `2,077` | `bd6514a8e0f0ba143a322b956e196b22ff8aae1cd10b8994e5b09eab9a4baf7b` |
| `L_AIRPORT.csv` | `https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVecbeg` | `6,942` | `7a3c2a0bbcdc6951c2ae6eefe1e5739cfeb86f5016006cec49e146bb54b9466d` |
| `L_UNIQUE_CARRIERS.csv` | `https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_haVdhR_PNeeVRef` | `1,780` | `2ed6201cb689e77fc653bb9fce091fde2571c3d04dac35fa7e345987f7e0790b` |

### 4.2 Manual Browser Download Instructions
1. Open the T-100 Segment page (`https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4`).
2. Next to `AIRCRAFT_TYPE`, `ORIGIN_AIRPORT_ID`, `ORIGIN`, `UNIQUE_CARRIER`, and `CARRIER`, click the **"Download Lookup Table"** link in the right-hand column.
3. Save each resulting `.csv` into `data/raw/lookups/`.

---

## 5. Source 4: FAA Aircraft Registry (`ReleasableAircraft.zip`)

### 5.1 Scripted Download & Extraction
- **URL:**
  ```text
  https://registry.faa.gov/database/ReleasableAircraft.zip
  ```
- **HTTP Requirements:**
  - Must use an HTTP **`GET`** request with a browser `User-Agent` header (`User-Agent: Mozilla/5.0 ...`).
  - Do **not** issue an HTTP `HEAD` probe without browser headers (`registry.faa.gov` Akamai WAF drops bare `HEAD` requests).
- **Extracted Files (`data/raw/faa/`):**
  1. `MASTER.txt` (`319,135` active aircraft registrations; `195,949,290` bytes)
  2. `DEREG.txt` (`383,757` deregistered/retired aircraft records covering `301,318` unique N-numbers; `278,224,346` bytes)
  3. `ACFTREF.txt` (`94,210` manufacturer/model reference records; `14,885,302` bytes)
- **Parsing Rules:**
  - Read with `encoding="utf-8-sig"` to strip the 3-byte UTF-8 BOM (`\xef\xbb\xbf`).
  - Strip whitespace from all column names and cell values, and drop the trailing empty column (`''`).

### 5.2 Manual Browser Download Instructions
1. Navigate to `https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download` in a web browser.
2. Click **Aircraft Registration Master File (`ReleasableAircraft.zip`)** (`~66 MB`).
3. Extract `MASTER.txt`, `DEREG.txt`, and `ACFTREF.txt` into `data/raw/faa/`.

---

## 6. Source 5: TSA Daily Checkpoint Volumes (`2019` and `2023–2026`)

### 6.1 Scripted HTML Table Extraction
- **URLs:**
  - `2026` (Current year): `https://www.tsa.gov/travel/passenger-volumes` (`316` days through `2026-11-12`)
  - `2025`: `https://www.tsa.gov/travel/passenger-volumes/2025` (`365` days)
  - `2024`: `https://www.tsa.gov/travel/passenger-volumes/2024` (`366` days, leap year)
  - `2023`: `https://www.tsa.gov/travel/passenger-volumes/2023` (`365` days)
  - `2019`: `https://www.tsa.gov/travel/passenger-volumes/2019` (`365` days)
- **Parsing Rules:**
  - Fetch each page via `GET` with `User-Agent: Mozilla/5.0`.
  - Extract all `<tr><td>Date</td><td>Numbers</td></tr>` rows.
  - Convert `M/D/YYYY` to ISO `YYYY-MM-DD`, strip commas from `Numbers` (`"2,908,785"` → `2908785`), deduplicate by `date`, sort ascending (`2019-01-01` → `2026-11-12`), and write to `data/raw/tsa/tsa_checkpoint_volumes.csv` (`1,777` rows).

### 6.2 Manual Browser Download Instructions
1. Visit each of the five URLs listed in Section 6.1.
2. Copy the two-column HTML table (`Date`, `Numbers` / `Throughput`) into `data/raw/tsa/tsa_checkpoint_volumes.csv` standardized to `date,tsa_throughput`.
