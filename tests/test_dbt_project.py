"""Tests for Phase 3 dbt BigQuery project: offline compile, manifest invariants, seed parity, and SQL parity."""

from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from standby.config import load_config
from standby.enrichment import enrich_flights
from standby.scoring import holiday_name, is_holiday_window, score_flights

SEED_FILES: tuple[str, ...] = (
    "seat_config.csv",
    "tail_overrides.csv",
    "faa_t100_crosswalk.csv",
)

EXPECTED_MART_MODELS: tuple[str, ...] = (
    "fct_flights",
    "agg_route_month",
    "dim_aircraft",
    "dim_airport",
    "dim_carrier",
    "dim_date",
)

EXPECTED_STAGING_MODELS: tuple[str, ...] = (
    "stg_otp_flights",
    "stg_t100_segments",
    "stg_faa_aircraft",
    "stg_faa_acftref",
    "stg_tsa_throughput",
    "stg_bts_airports",
    "stg_bts_carriers",
    "stg_bts_aircraft_types",
)

EXPECTED_INTERMEDIATE_MODELS: tuple[str, ...] = (
    "int_aircraft_enriched",
    "int_t100_load_factors",
    "int_flight_estimates",
    "int_flight_displacements_backups",
    "int_flights_soi_scored",
)


def _dbt_executable() -> str:
    """Resolve the `dbt` binary inside the active Python environment."""
    candidate = Path(sys.executable).parent / "dbt"
    if candidate.is_file():
        return str(candidate)
    return "dbt"


def _run_dbt(args: list[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    dbt_dir = repo_root / "dbt"
    cmd = [
        _dbt_executable(),
        *args,
        "--project-dir",
        str(dbt_dir),
        "--profiles-dir",
        str(dbt_dir),
        "--no-send-anonymous-usage-stats",
    ]
    return subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def dbt_compiled_manifest(repo_root: Path) -> dict:
    """Run `dbt parse`, `dbt compile`, and `dbt docs generate --empty-catalog` offline."""
    for cmd in (
        ["parse"],
        ["compile"],
        ["docs", "generate", "--empty-catalog"],
    ):
        res = _run_dbt(cmd, repo_root)
        assert res.returncode == 0, (
            f"dbt {' '.join(cmd)} failed with exit code {res.returncode}:\n"
            f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        )

    manifest_path = repo_root / "dbt" / "target" / "manifest.json"
    catalog_path = repo_root / "dbt" / "target" / "catalog.json"
    assert manifest_path.is_file()
    assert catalog_path.is_file()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def test_dbt_seeds_byte_for_byte_parity_with_reference(repo_root: Path) -> None:
    """`dbt/seeds/*.csv` must remain byte-for-byte identical to `reference/*.csv`."""
    for filename in SEED_FILES:
        ref_bytes = (repo_root / "reference" / filename).read_bytes()
        seed_bytes = (repo_root / "dbt" / "seeds" / filename).read_bytes()
        assert seed_bytes == ref_bytes, f"Seed drift detected for {filename}"


def test_dbt_compile_custom_vars_override_sql(repo_root: Path, tmp_path: Path) -> None:
    """Compiling with explicit `--vars` dynamically updates BigQuery hub and year filters."""
    target_dir = tmp_path / "dbt_custom_target"
    res = _run_dbt(
        [
            "compile",
            "--vars",
            '{"hubs": ["ord", "den"], "years": [2024]}',
            "--target-path",
            str(target_dir),
        ],
        repo_root,
    )
    assert res.returncode == 0, f"Custom vars compile failed:\n{res.stdout}\n{res.stderr}"

    compiled_models = target_dir / "compiled" / "united_standby_analytics" / "models"
    for rel_path in (
        Path("staging") / "stg_otp_flights.sql",
        Path("staging") / "stg_t100_segments.sql",
        Path("marts") / "dim_airport.sql",
    ):
        compiled_file = compiled_models / rel_path
        assert compiled_file.is_file()
        sql_text = compiled_file.read_text(encoding="utf-8")
        assert "in ('ORD', 'DEN')" in sql_text

    stg_otp_sql = (compiled_models / "staging" / "stg_otp_flights.sql").read_text(encoding="utf-8")
    assert "in (2024)" in stg_otp_sql
    assert "2019, 2023" not in stg_otp_sql


def test_dbt_manifest_models_schemas_partitioning_and_tests(
    dbt_compiled_manifest: dict,
    repo_root: Path,
) -> None:
    """Verify schemas, BigQuery partition/cluster configs, descriptions, and tests in manifest.json."""
    nodes = dbt_compiled_manifest["nodes"]
    sources = dbt_compiled_manifest["sources"]

    # 1. Sources (6 raw tables)
    raw_sources = {s["name"] for s in sources.values() if s["source_name"] == "raw"}
    assert raw_sources == {
        "otp_marketing_flights",
        "t100_segments",
        "faa_aircraft_registry",
        "faa_acftref",
        "tsa_daily_throughput",
        "bts_lookups",
    }

    # 2. Seeds in `raw` schema with explicit string bts_aircraft_type
    for seed_name in ("seat_config", "tail_overrides", "faa_t100_crosswalk"):
        seed_node = nodes[f"seed.united_standby_analytics.{seed_name}"]
        assert seed_node["schema"] == "raw"
        assert seed_node["description"].strip() != ""
        if seed_name in {"tail_overrides", "faa_t100_crosswalk"}:
            assert seed_node["config"]["column_types"]["bts_aircraft_type"] == "string"

    # 3. Staging, Intermediate, Marts schemas & materializations
    for name in EXPECTED_STAGING_MODELS:
        node = nodes[f"model.united_standby_analytics.{name}"]
        assert node["schema"] == "staging"
        assert node["config"]["materialized"] == "view"
        assert node["description"].strip() != ""

    for name in EXPECTED_INTERMEDIATE_MODELS:
        node = nodes[f"model.united_standby_analytics.{name}"]
        assert node["schema"] == "intermediate"
        assert node["config"]["materialized"] == "view"
        assert node["description"].strip() != ""

    for name in EXPECTED_MART_MODELS:
        node = nodes[f"model.united_standby_analytics.{name}"]
        assert node["schema"] == "marts"
        assert node["config"]["materialized"] == "table"
        assert node["description"].strip() != ""
        assert len(node["columns"]) >= 3
        for col_meta in node["columns"].values():
            assert col_meta["description"].strip() != ""

    # 4. BigQuery partitioning & clustering on fct_flights and agg_route_month
    fct_node = nodes["model.united_standby_analytics.fct_flights"]
    assert fct_node["config"]["partition_by"] == {
        "field": "flight_date",
        "data_type": "date",
        "granularity": "day",
    }
    assert fct_node["config"]["cluster_by"] == ["origin", "dest", "op_carrier"]

    agg_node = nodes["model.united_standby_analytics.agg_route_month"]
    assert agg_node["config"]["cluster_by"] == ["origin", "dest", "op_carrier"]

    # 5. Schema tests exist for every mart and every seed, plus singular tests
    test_nodes = [n for n in nodes.values() if n["resource_type"] == "test"]
    assert len(test_nodes) >= 50

    for target_name in (
        *EXPECTED_MART_MODELS,
        "seat_config",
        "tail_overrides",
        "faa_t100_crosswalk",
    ):
        prefix = (
            "seed"
            if target_name in {"seat_config", "tail_overrides", "faa_t100_crosswalk"}
            else "model"
        )
        uid = f"{prefix}.united_standby_analytics.{target_name}"
        attached = [t for t in test_nodes if uid in t.get("depends_on", {}).get("nodes", [])]
        assert len(attached) >= 2, f"Expected schema tests attached to {uid}"

    singular_names = {n["name"] for n in test_nodes if n.get("test_metadata") is None}
    assert {
        "assert_sc1_passenger_bounds",
        "assert_sc5_no_silent_t100_imputation",
        "assert_fct_flights_grain_unique",
        "assert_soi_leakage_and_bounds",
    }.issubset(singular_names)

    # 6. Compiled SQL checks: stg_otp_flights selects only 31 base cols; fct_flights exposes Phase 4 SOI
    compiled_dir = repo_root / "dbt" / "target" / "compiled" / "united_standby_analytics" / "models"
    stg_otp_sql = (compiled_dir / "staging" / "stg_otp_flights.sql").read_text(encoding="utf-8")
    for enrichment_only_col in (
        "seats_config",
        "est_load_factor",
        "lf_source",
        "est_open_seats_adjusted",
    ):
        assert enrichment_only_col not in stg_otp_sql

    fct_sql = (compiled_dir / "marts" / "fct_flights.sql").read_text(encoding="utf-8").lower()
    assert "int_flights_soi_scored" in fct_sql
    for soi_col in (
        "s_open_hist",
        "s_disp_hist",
        "s_backup_hist",
        "s_demand_hist",
        "soi_historical",
        "s_open_plan",
        "s_disp_plan",
        "s_backup_plan",
        "s_demand_plan",
        "soi_planning",
    ):
        assert soi_col in fct_sql

    int_soi_sql = (
        (compiled_dir / "intermediate" / "int_flights_soi_scored.sql")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "f.year < ey.eval_year" in int_soi_sql
    assert "prior_strict_scheduled_count" in int_soi_sql


def _build_sample_sqlite_db(
    repo_root: Path, sample_ingested: dict[str, pd.DataFrame]
) -> sqlite3.Connection:
    """Load standardized sample inputs and dbt seeds into an in-memory SQLite DB for SQL parity tests."""
    conn = sqlite3.connect(":memory:")
    conn.create_function("LN", 1, math.log)
    conn.create_function("HOLIDAY_WINDOW", 1, lambda s: 1 if is_holiday_window(s) else 0)
    conn.create_function("HOLIDAY_NAME", 1, holiday_name)

    otp_base_cols = [
        "flight_date",
        "year",
        "quarter",
        "month",
        "day_of_month",
        "day_of_week",
        "mkt_carrier",
        "op_carrier",
        "flight_num_mkt",
        "flight_num_op",
        "tail_num",
        "tail_num_clean",
        "origin",
        "origin_airport_id",
        "dest",
        "dest_airport_id",
        "crs_dep_time",
        "dep_time",
        "crs_arr_time",
        "arr_time",
        "dep_hour",
        "hour_block",
        "dep_delay_min",
        "arr_delay_min",
        "is_cancelled",
        "cancellation_code",
        "is_diverted",
        "crs_elapsed_time_min",
        "actual_elapsed_time_min",
        "distance_miles",
        "confidence_tier",
    ]
    otp_df = sample_ingested["otp_marketing_flights"][otp_base_cols].copy()
    otp_df["flight_date"] = otp_df["flight_date"].astype(str).str.slice(0, 10)
    otp_df["is_cancelled"] = otp_df["is_cancelled"].astype(int)
    otp_df["is_diverted"] = otp_df["is_diverted"].astype(int)
    otp_df.to_sql("raw_otp_marketing_flights", conn, index=False)

    t100_df = sample_ingested["t100_segments"].copy()
    t100_df["is_international"] = t100_df["is_international"].astype(int)
    t100_df.to_sql("raw_t100_segments", conn, index=False)

    sample_ingested["faa_aircraft_registry"].to_sql("raw_faa_aircraft_registry", conn, index=False)
    faa_ref = sample_ingested["faa_acftref"].copy()
    faa_ref["is_commercial_airliner"] = faa_ref["is_commercial_airliner"].astype(int)
    faa_ref.to_sql("raw_faa_acftref", conn, index=False)

    tsa_df = sample_ingested["tsa_daily_throughput"].copy()
    tsa_df["date_day"] = tsa_df["date_day"].astype(str).str.slice(0, 10)
    tsa_df.to_sql("raw_tsa_daily_throughput", conn, index=False)

    sample_ingested["bts_lookups"].to_sql("raw_bts_lookups", conn, index=False)

    for seed_name in ("seat_config", "tail_overrides", "faa_t100_crosswalk"):
        seed_df = pd.read_csv(
            repo_root / "dbt" / "seeds" / f"{seed_name}.csv", dtype=str, keep_default_na=False
        )
        for col in ("seats_total", "seats_premium", "seats_economy"):
            if col in seed_df.columns:
                seed_df[col] = seed_df[col].astype(int)
        seed_df.to_sql(f"seed_{seed_name}", conn, index=False)

    # Create SQLite staging & intermediate views mirroring the dbt BigQuery models
    conn.executescript(
        """
        CREATE VIEW stg_otp_flights AS
        WITH base AS (
            SELECT
                flight_date,
                CAST(year AS INTEGER) AS year,
                CAST(quarter AS INTEGER) AS quarter,
                CAST(month AS INTEGER) AS month,
                CAST(day_of_month AS INTEGER) AS day_of_month,
                CAST(day_of_week AS INTEGER) AS day_of_week,
                UPPER(TRIM(mkt_carrier)) AS mkt_carrier,
                UPPER(TRIM(op_carrier)) AS op_carrier,
                TRIM(CAST(flight_num_mkt AS TEXT)) AS flight_num_mkt,
                NULLIF(TRIM(CAST(flight_num_op AS TEXT)), '') AS flight_num_op,
                CASE
                    WHEN tail_num IS NULL OR TRIM(tail_num) = '' OR UPPER(TRIM(tail_num)) IN ('NAN', 'NONE') THEN NULL
                    WHEN SUBSTR(UPPER(TRIM(tail_num)), 1, 1) = 'N' THEN UPPER(TRIM(tail_num))
                    ELSE 'N' || UPPER(TRIM(tail_num))
                END AS tail_num,
                UPPER(TRIM(origin)) AS origin,
                CAST(origin_airport_id AS INTEGER) AS origin_airport_id,
                UPPER(TRIM(dest)) AS dest,
                CAST(dest_airport_id AS INTEGER) AS dest_airport_id,
                CASE WHEN crs_dep_time = '2400' THEN '0000' ELSE crs_dep_time END AS crs_dep_time,
                CASE WHEN dep_time = '2400' THEN '0000' ELSE dep_time END AS dep_time,
                CASE WHEN crs_arr_time = '2400' THEN '0000' ELSE crs_arr_time END AS crs_arr_time,
                CASE WHEN arr_time = '2400' THEN '0000' ELSE arr_time END AS arr_time,
                dep_delay_min,
                arr_delay_min,
                is_cancelled,
                cancellation_code,
                is_diverted,
                crs_elapsed_time_min,
                actual_elapsed_time_min,
                distance_miles,
                confidence_tier
            FROM raw_otp_marketing_flights
            WHERE UPPER(TRIM(origin)) IN ('ORD')
              AND CAST(year AS INTEGER) IN (2019, 2023, 2024, 2025, 2026)
              AND UPPER(TRIM(mkt_carrier)) = 'UA'
        )
        SELECT
            REPLACE(flight_date, '-', '') || '|' || mkt_carrier || '|' || flight_num_mkt || '|' || origin || '|' || dest || '|' || crs_dep_time AS flight_id,
            *,
            CASE WHEN tail_num IS NULL THEN NULL ELSE SUBSTR(tail_num, 2) END AS tail_num_clean,
            CASE
                WHEN tail_num IS NULL THEN 0
                WHEN LENGTH(tail_num) BETWEEN 2 AND 6
                 AND SUBSTR(tail_num, 1, 1) = 'N'
                 AND SUBSTR(tail_num, 2, 1) BETWEEN '1' AND '9'
                 AND tail_num NOT GLOB '*[^A-Z0-9]*'
                THEN 1 ELSE 0
            END AS is_valid_us_n_number,
            (CAST(CAST(crs_dep_time AS INTEGER) / 100 AS INTEGER) % 24) AS dep_hour,
            CASE
                WHEN (CAST(CAST(crs_dep_time AS INTEGER) / 100 AS INTEGER) % 24) BETWEEN 5 AND 7 THEN 'early'
                WHEN (CAST(CAST(crs_dep_time AS INTEGER) / 100 AS INTEGER) % 24) BETWEEN 8 AND 10 THEN 'morning'
                WHEN (CAST(CAST(crs_dep_time AS INTEGER) / 100 AS INTEGER) % 24) BETWEEN 11 AND 13 THEN 'midday'
                WHEN (CAST(CAST(crs_dep_time AS INTEGER) / 100 AS INTEGER) % 24) BETWEEN 14 AND 16 THEN 'afternoon'
                WHEN (CAST(CAST(crs_dep_time AS INTEGER) / 100 AS INTEGER) % 24) BETWEEN 17 AND 19 THEN 'evening'
                ELSE 'late'
            END AS hour_block
        FROM base;

        CREATE VIEW stg_t100_segments AS
        WITH source AS (
            SELECT
                CAST(year AS INTEGER) AS year,
                CAST(quarter AS INTEGER) AS quarter,
                CAST(month AS INTEGER) AS month,
                UPPER(TRIM(op_carrier)) AS op_carrier,
                TRIM(carrier_name) AS carrier_name,
                UPPER(TRIM(origin)) AS origin,
                CAST(origin_airport_id AS INTEGER) AS origin_airport_id,
                UPPER(TRIM(origin_country)) AS origin_country,
                UPPER(TRIM(dest)) AS dest,
                CAST(dest_airport_id AS INTEGER) AS dest_airport_id,
                UPPER(TRIM(dest_country)) AS dest_country,
                TRIM(CAST(bts_aircraft_type AS TEXT)) AS bts_aircraft_type,
                CAST(aircraft_group AS INTEGER) AS aircraft_group,
                CAST(aircraft_config AS INTEGER) AS aircraft_config,
                UPPER(TRIM(service_class)) AS service_class,
                UPPER(TRIM(data_source)) AS data_source,
                is_international,
                CAST(departures_scheduled AS REAL) AS departures_scheduled,
                CAST(departures_performed AS REAL) AS departures_performed,
                CAST(seats AS REAL) AS seats,
                CAST(passengers AS REAL) AS passengers,
                CAST(distance_miles AS REAL) AS distance_miles,
                COALESCE(NULLIF(LOWER(TRIM(confidence_tier)), ''), 'actual') AS confidence_tier
            FROM raw_t100_segments
            WHERE UPPER(TRIM(service_class)) = 'F'
              AND UPPER(TRIM(origin)) IN ('ORD')
              AND CAST(year AS INTEGER) IN (2019, 2023, 2024, 2025, 2026)
        )
        SELECT
            year,
            MAX(quarter) AS quarter,
            month,
            op_carrier,
            MAX(carrier_name) AS carrier_name,
            origin,
            MAX(origin_airport_id) AS origin_airport_id,
            MAX(origin_country) AS origin_country,
            dest,
            MAX(dest_airport_id) AS dest_airport_id,
            MAX(dest_country) AS dest_country,
            bts_aircraft_type,
            MAX(aircraft_group) AS aircraft_group,
            MAX(aircraft_config) AS aircraft_config,
            MAX(service_class) AS service_class,
            data_source,
            MAX(is_international) AS is_international,
            MAX(CASE WHEN op_carrier IN ('UA','OO','YX','YV','G7','C5','ZW','EV','AX') THEN 1 ELSE 0 END) AS is_ua_family,
            SUM(departures_scheduled) AS departures_scheduled,
            SUM(departures_performed) AS departures_performed,
            SUM(seats) AS seats,
            SUM(passengers) AS passengers,
            MAX(distance_miles) AS distance_miles,
            CASE
                WHEN SUM(seats) > 0.0 THEN MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats)))
                ELSE NULL
            END AS load_factor,
            CASE
                WHEN SUM(departures_performed) > 0.0 THEN SUM(seats) * 1.0 / SUM(departures_performed)
                ELSE NULL
            END AS seats_per_departure,
            MAX(confidence_tier) AS confidence_tier
        FROM source
        GROUP BY year, month, op_carrier, origin, dest, bts_aircraft_type, data_source;

        CREATE VIEW stg_faa_aircraft AS
        SELECT n_number, serial_number, mfr_mdl_code, year_mfr, registry_source, cancel_date
        FROM (
            SELECT
                UPPER(TRIM(n_number)) AS n_number,
                TRIM(serial_number) AS serial_number,
                TRIM(mfr_mdl_code) AS mfr_mdl_code,
                year_mfr,
                LOWER(TRIM(registry_source)) AS registry_source,
                cancel_date,
                ROW_NUMBER() OVER (
                    PARTITION BY UPPER(TRIM(n_number)), LOWER(TRIM(registry_source))
                    ORDER BY COALESCE(TRIM(cancel_date), '') DESC, TRIM(serial_number) ASC
                ) AS rn
            FROM raw_faa_aircraft_registry
            WHERE NULLIF(TRIM(n_number), '') IS NOT NULL
              AND NULLIF(TRIM(mfr_mdl_code), '') IS NOT NULL
        )
        WHERE rn = 1;

        CREATE VIEW stg_faa_acftref AS
        SELECT
            TRIM(code) AS code,
            UPPER(TRIM(faa_manufacturer)) AS faa_manufacturer,
            UPPER(TRIM(faa_model)) AS faa_model,
            CAST(faa_tc_max_seats AS INTEGER) AS faa_tc_max_seats
        FROM raw_faa_acftref
        WHERE is_commercial_airliner = 1
          AND CAST(faa_tc_max_seats AS INTEGER) >= 20;

        CREATE VIEW stg_tsa_throughput AS
        WITH means AS (
            SELECT
                date_day,
                tsa_throughput,
                AVG(CAST(tsa_throughput AS REAL)) OVER (PARTITION BY SUBSTR(date_day, 1, 7)) AS tsa_month_mean,
                AVG(CAST(tsa_throughput AS REAL)) OVER (PARTITION BY SUBSTR(date_day, 1, 4)) AS tsa_year_mean
            FROM raw_tsa_daily_throughput
        )
        SELECT
            date_day,
            CAST(SUBSTR(date_day, 1, 4) AS INTEGER) AS year,
            CAST(SUBSTR(date_day, 6, 2) AS INTEGER) AS month,
            ((CAST(STRFTIME('%w', date_day) AS INTEGER) + 6) % 7) + 1 AS iso_dow,
            HOLIDAY_WINDOW(date_day) AS is_holiday_window,
            tsa_throughput,
            tsa_throughput * 1.0 / tsa_month_mean AS tsa_ratio_month,
            MIN(1.20, MAX(0.80, 1.0 + 0.35 * ((tsa_throughput * 1.0 / tsa_month_mean) - 1.0))) AS tsa_demand_multiplier,
            tsa_throughput * 1.0 / tsa_year_mean AS tsa_ratio_year
        FROM means;

        CREATE VIEW int_aircraft_enriched AS
        WITH faa_valid AS (
            SELECT reg.n_number, reg.registry_source, ref.faa_manufacturer, ref.faa_model, ref.faa_tc_max_seats
            FROM stg_faa_aircraft reg
            JOIN stg_faa_acftref ref ON reg.mfr_mdl_code = ref.code
        ),
        faa_master AS (
            SELECT n_number, faa_manufacturer, faa_model, faa_tc_max_seats FROM faa_valid WHERE registry_source = 'faa_master'
        ),
        faa_dereg AS (
            SELECT n_number, faa_manufacturer, faa_model, faa_tc_max_seats FROM faa_valid WHERE registry_source = 'faa_dereg'
        ),
        cw_carrier AS (
            SELECT cw.op_carrier, cw.faa_mfr, cw.faa_model, cw.subtype, cw.bts_aircraft_type, sc.seats_total, sc.seats_premium, sc.seats_economy
            FROM seed_faa_t100_crosswalk cw
            JOIN seed_seat_config sc ON cw.subtype = sc.subtype
            WHERE cw.op_carrier != '*'
        ),
        cw_generic AS (
            SELECT faa_mfr, faa_model, subtype, bts_aircraft_type, seats_total, seats_premium, seats_economy
            FROM (
                SELECT cw.faa_mfr, cw.faa_model, cw.subtype, cw.bts_aircraft_type, sc.seats_total, sc.seats_premium, sc.seats_economy,
                       ROW_NUMBER() OVER (PARTITION BY cw.faa_mfr, cw.faa_model ORDER BY cw.op_carrier, cw.subtype) AS rn
                FROM seed_faa_t100_crosswalk cw
                JOIN seed_seat_config sc ON cw.subtype = sc.subtype
            ) WHERE rn = 1
        )
        SELECT
            f.*,
            CASE
                WHEN f.tail_num IS NULL THEN CASE WHEN f.is_cancelled = 1 THEN 'UNASSIGNED_CANCELLED' ELSE 'UNMATCHED' END
                WHEN ov.tail_num IS NOT NULL THEN 'TAIL_OVERRIDE'
                WHEN f.is_valid_us_n_number = 0 THEN 'UNMATCHED_TYPO'
                WHEN COALESCE(mc_c.subtype, mc_g.subtype) IS NOT NULL THEN 'FAA_MASTER'
                WHEN COALESCE(dc_c.subtype, dc_g.subtype) IS NOT NULL THEN 'FAA_DEREG'
                ELSE 'UNMATCHED'
            END AS aircraft_source,
            CASE
                WHEN f.tail_num IS NULL THEN CASE WHEN f.is_cancelled = 1 THEN 'UNASSIGNED_CANCELLED' ELSE 'UNMATCHED' END
                WHEN ov.tail_num IS NOT NULL THEN 'TAIL_OVERRIDE'
                WHEN f.is_valid_us_n_number = 0 THEN 'UNMATCHED'
                WHEN COALESCE(mc_c.subtype, mc_g.subtype, dc_c.subtype, dc_g.subtype) IS NOT NULL THEN 'SUBTYPE_DEFAULT'
                ELSE 'UNMATCHED'
            END AS seat_config_source,
            CASE
                WHEN f.tail_num IS NULL THEN NULL
                WHEN ov.tail_num IS NOT NULL THEN ov.subtype
                WHEN f.is_valid_us_n_number = 0 THEN NULL
                ELSE COALESCE(mc_c.subtype, mc_g.subtype, dc_c.subtype, dc_g.subtype)
            END AS fleet_subtype,
            CASE
                WHEN f.tail_num IS NULL THEN NULL
                WHEN ov.tail_num IS NOT NULL THEN ov.bts_aircraft_type
                WHEN f.is_valid_us_n_number = 0 THEN NULL
                ELSE COALESCE(mc_c.bts_aircraft_type, mc_g.bts_aircraft_type, dc_c.bts_aircraft_type, dc_g.bts_aircraft_type)
            END AS bts_aircraft_type,
            CASE
                WHEN f.tail_num IS NULL THEN NULL
                WHEN ov.tail_num IS NOT NULL THEN ov.seats_total
                WHEN f.is_valid_us_n_number = 0 THEN NULL
                ELSE COALESCE(mc_c.seats_total, mc_g.seats_total, dc_c.seats_total, dc_g.seats_total)
            END AS seats_config,
            CASE
                WHEN f.tail_num IS NULL THEN NULL
                WHEN ov.tail_num IS NOT NULL THEN ov.seats_premium
                WHEN f.is_valid_us_n_number = 0 THEN NULL
                ELSE COALESCE(mc_c.seats_premium, mc_g.seats_premium, dc_c.seats_premium, dc_g.seats_premium)
            END AS seats_premium,
            CASE
                WHEN f.tail_num IS NULL THEN NULL
                WHEN ov.tail_num IS NOT NULL THEN ov.seats_economy
                WHEN f.is_valid_us_n_number = 0 THEN NULL
                ELSE COALESCE(mc_c.seats_economy, mc_g.seats_economy, dc_c.seats_economy, dc_g.seats_economy)
            END AS seats_economy
        FROM stg_otp_flights f
        LEFT JOIN seed_tail_overrides ov ON f.tail_num = ov.tail_num AND f.op_carrier = ov.op_carrier
        LEFT JOIN faa_master m ON f.is_valid_us_n_number = 1 AND f.tail_num_clean = m.n_number
        LEFT JOIN faa_dereg d ON f.is_valid_us_n_number = 1 AND m.n_number IS NULL AND f.tail_num_clean = d.n_number
        LEFT JOIN cw_carrier mc_c ON m.faa_manufacturer = mc_c.faa_mfr AND m.faa_model = mc_c.faa_model AND f.op_carrier = mc_c.op_carrier
        LEFT JOIN cw_generic mc_g ON m.faa_manufacturer = mc_g.faa_mfr AND m.faa_model = mc_g.faa_model
        LEFT JOIN cw_carrier dc_c ON d.faa_manufacturer = dc_c.faa_mfr AND d.faa_model = dc_c.faa_model AND f.op_carrier = dc_c.op_carrier
        LEFT JOIN cw_generic dc_g ON d.faa_manufacturer = dc_g.faa_mfr AND d.faa_model = dc_g.faa_model;

        CREATE VIEW int_t100_load_factors AS
        WITH dom AS (
            SELECT * FROM stg_t100_segments WHERE data_source IN ('DU', 'DF')
        ),
        avail AS (
            SELECT DISTINCT year, month FROM dom
        ),
        l1 AS (
            SELECT op_carrier, origin, dest, year, month, bts_aircraft_type,
                   MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf_l1
            FROM dom
            GROUP BY op_carrier, origin, dest, year, month, bts_aircraft_type
            HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        l2 AS (
            SELECT op_carrier, origin, dest, year, month,
                   SUM(departures_performed) AS t100_deps_l2,
                   CASE WHEN SUM(seats) > 0 AND SUM(departures_performed) > 0
                        THEN MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats)))
                        ELSE NULL END AS lf_l2
            FROM dom
            GROUP BY op_carrier, origin, dest, year, month
        ),
        l3 AS (
            SELECT origin, dest, year, month,
                   MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf_l3
            FROM dom
            WHERE is_ua_family = 1
            GROUP BY origin, dest, year, month
            HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        otp_cnt AS (
            SELECT op_carrier, origin, dest, year, month, COUNT(*) AS otp_performed
            FROM int_aircraft_enriched
            WHERE is_cancelled = 0
            GROUP BY op_carrier, origin, dest, year, month
        )
        SELECT
            f.*,
            CASE WHEN avail.year IS NOT NULL THEN 1 ELSE 0 END AS t100_available,
            CASE
                WHEN avail.year IS NULL THEN 'UNMATCHED'
                WHEN l1.lf_l1 IS NOT NULL THEN 'CARRIER_ROUTE_MONTH_TYPE'
                WHEN l2.lf_l2 IS NOT NULL THEN 'CARRIER_ROUTE_MONTH'
                WHEN l3.lf_l3 IS NOT NULL THEN 'ROUTE_MONTH'
                ELSE 'UNMATCHED'
            END AS lf_source,
            CASE
                WHEN avail.year IS NOT NULL
                 AND f.op_carrier IN ('OO','YX','YV','ZW')
                 AND NOT (f.op_carrier = 'ZW' AND f.year = 2019)
                 AND COALESCE(otp_cnt.otp_performed, 0) > 0
                 AND COALESCE(l2.t100_deps_l2, 0.0) > 1.10 * otp_cnt.otp_performed
                THEN 1 ELSE 0
            END AS is_shared_regional_spoke,
            CASE WHEN avail.year IS NULL THEN NULL ELSE COALESCE(l1.lf_l1, l2.lf_l2, l3.lf_l3) END AS est_load_factor
        FROM int_aircraft_enriched f
        LEFT JOIN avail ON f.year = avail.year AND f.month = avail.month
        LEFT JOIN l1 ON f.op_carrier = l1.op_carrier AND f.origin = l1.origin AND f.dest = l1.dest
                    AND f.year = l1.year AND f.month = l1.month AND f.bts_aircraft_type = l1.bts_aircraft_type
        LEFT JOIN l2 ON f.op_carrier = l2.op_carrier AND f.origin = l2.origin AND f.dest = l2.dest
                    AND f.year = l2.year AND f.month = l2.month
        LEFT JOIN l3 ON f.origin = l3.origin AND f.dest = l3.dest AND f.year = l3.year AND f.month = l3.month
        LEFT JOIN otp_cnt ON f.op_carrier = otp_cnt.op_carrier AND f.origin = otp_cnt.origin AND f.dest = otp_cnt.dest
                         AND f.year = otp_cnt.year AND f.month = otp_cnt.month;

        CREATE VIEW int_flight_estimates AS
        WITH base AS (
            SELECT
                f.*,
                tsa.tsa_throughput,
                tsa.tsa_demand_multiplier,
                CASE LOWER(TRIM(f.hour_block))
                    WHEN 'early' THEN 0.96
                    WHEN 'morning' THEN 1.03
                    WHEN 'midday' THEN 1.00
                    WHEN 'afternoon' THEN 1.02
                    WHEN 'evening' THEN 1.04
                    WHEN 'late' THEN 0.94
                    ELSE 1.00
                END AS hour_block_multiplier
            FROM int_t100_load_factors f
            LEFT JOIN stg_tsa_throughput tsa ON f.flight_date = tsa.date_day
        ),
        adj AS (
            SELECT
                *,
                CASE
                    WHEN t100_available = 1 AND lf_source != 'UNMATCHED' AND est_load_factor IS NOT NULL AND tsa_demand_multiplier IS NOT NULL
                    THEN MIN(1.0, MAX(0.0, est_load_factor * tsa_demand_multiplier * hour_block_multiplier))
                    ELSE NULL
                END AS est_lf_adjusted
            FROM base
        )
        SELECT
            *,
            CASE
                WHEN t100_available = 1 AND lf_source != 'UNMATCHED' AND est_load_factor IS NOT NULL AND seats_config IS NOT NULL
                THEN MIN(seats_config, MAX(0, CAST(ROUND(seats_config * est_load_factor) AS INTEGER)))
                ELSE NULL
            END AS est_passengers,
            CASE
                WHEN t100_available = 1 AND lf_source != 'UNMATCHED' AND est_load_factor IS NOT NULL AND seats_config IS NOT NULL
                THEN seats_config - MIN(seats_config, MAX(0, CAST(ROUND(seats_config * est_load_factor) AS INTEGER)))
                ELSE NULL
            END AS est_open_seats,
            CASE
                WHEN est_lf_adjusted IS NOT NULL AND seats_config IS NOT NULL
                THEN MIN(seats_config, MAX(0, CAST(ROUND(seats_config * est_lf_adjusted) AS INTEGER)))
                ELSE NULL
            END AS est_passengers_adjusted,
            CASE
                WHEN est_lf_adjusted IS NOT NULL AND seats_config IS NOT NULL
                THEN seats_config - MIN(seats_config, MAX(0, CAST(ROUND(seats_config * est_lf_adjusted) AS INTEGER)))
                ELSE NULL
            END AS est_open_seats_adjusted
        FROM adj;

        CREATE VIEW int_flight_displacements_backups AS
        WITH ordered AS (
            SELECT
                flight_id, flight_date, origin, dest, op_carrier, flight_num_mkt, crs_dep_time, is_cancelled, is_diverted,
                ROW_NUMBER() OVER (
                    PARTITION BY origin, dest, flight_date
                    ORDER BY crs_dep_time ASC, is_cancelled DESC, flight_num_mkt ASC, op_carrier ASC
                ) AS route_day_seq
            FROM stg_otp_flights
        )
        SELECT
            i.flight_id,
            i.route_day_seq,
            i.route_day_seq - 1 AS prior_scheduled_count,
            SUM(CASE WHEN j.crs_dep_time < i.crs_dep_time THEN 1 ELSE 0 END) AS prior_strict_scheduled_count,
            COALESCE(SUM(
                CASE
                    WHEN j.route_day_seq < i.route_day_seq AND (j.is_cancelled = 1 OR j.is_diverted = 1)
                    THEN (CASE WHEN j.is_cancelled = 1 THEN 1.0 ELSE 0.5 END) * POWER(0.75, i.route_day_seq - j.route_day_seq - 1)
                    ELSE 0.0
                END
            ), 0.0) AS weighted_displacement_hist,
            SUM(CASE WHEN j.crs_dep_time > i.crs_dep_time THEN 1 ELSE 0 END) AS remaining_scheduled_backups,
            SUM(CASE WHEN j.crs_dep_time > i.crs_dep_time AND j.is_cancelled = 0 THEN 1 ELSE 0 END) AS remaining_performed_backups
        FROM ordered i
        LEFT JOIN ordered j ON i.origin = j.origin AND i.dest = j.dest AND i.flight_date = j.flight_date
        GROUP BY i.flight_id, i.route_day_seq;

        CREATE VIEW int_flights_soi_scored AS
        WITH base_flights AS (
            SELECT
                e.*,
                db.route_day_seq,
                db.prior_scheduled_count,
                db.prior_strict_scheduled_count,
                db.weighted_displacement_hist,
                db.remaining_scheduled_backups,
                db.remaining_performed_backups,
                tsa.tsa_ratio_year,
                e.day_of_week AS iso_dow,
                HOLIDAY_NAME(e.flight_date) AS holiday_name,
                HOLIDAY_WINDOW(e.flight_date) AS is_holiday_window,
                CASE WHEN e.t100_available = 1 AND e.lf_source != 'UNMATCHED' THEN 1 ELSE 0 END AS has_t100_gate,
                CASE WHEN e.t100_available = 1 AND e.lf_source != 'UNMATCHED' AND e.est_load_factor IS NOT NULL THEN 1 ELSE 0 END AS has_valid_t100
            FROM int_flight_estimates e
            LEFT JOIN int_flight_displacements_backups db ON e.flight_id = db.flight_id
            LEFT JOIN stg_tsa_throughput tsa ON e.flight_date = tsa.date_day
        ),
        eval_years AS (
            SELECT DISTINCT year AS eval_year FROM base_flights
        ),
        modal_t1 AS (
            SELECT eval_year, op_carrier, origin, dest, month, hour_block, seats_config AS seats_config_plan, bts_aircraft_type AS bts_type_plan
            FROM (
                SELECT ey.eval_year, f.op_carrier, f.origin, f.dest, f.month, f.hour_block, f.seats_config, f.bts_aircraft_type,
                       ROW_NUMBER() OVER (PARTITION BY ey.eval_year, f.op_carrier, f.origin, f.dest, f.month, f.hour_block ORDER BY COUNT(*) DESC, f.seats_config ASC, f.bts_aircraft_type ASC) AS rn
                FROM base_flights f JOIN eval_years ey ON f.year < ey.eval_year
                WHERE f.seats_config IS NOT NULL AND f.seats_config > 0 AND f.bts_aircraft_type IS NOT NULL AND TRIM(f.bts_aircraft_type) != ''
                GROUP BY ey.eval_year, f.op_carrier, f.origin, f.dest, f.month, f.hour_block, f.seats_config, f.bts_aircraft_type
            ) WHERE rn = 1
        ),
        modal_t2 AS (
            SELECT eval_year, op_carrier, origin, dest, month, seats_config AS seats_config_plan, bts_aircraft_type AS bts_type_plan
            FROM (
                SELECT ey.eval_year, f.op_carrier, f.origin, f.dest, f.month, f.seats_config, f.bts_aircraft_type,
                       ROW_NUMBER() OVER (PARTITION BY ey.eval_year, f.op_carrier, f.origin, f.dest, f.month ORDER BY COUNT(*) DESC, f.seats_config ASC, f.bts_aircraft_type ASC) AS rn
                FROM base_flights f JOIN eval_years ey ON f.year < ey.eval_year
                WHERE f.seats_config IS NOT NULL AND f.seats_config > 0 AND f.bts_aircraft_type IS NOT NULL AND TRIM(f.bts_aircraft_type) != ''
                GROUP BY ey.eval_year, f.op_carrier, f.origin, f.dest, f.month, f.seats_config, f.bts_aircraft_type
            ) WHERE rn = 1
        ),
        modal_t3 AS (
            SELECT eval_year, op_carrier, origin, dest, seats_config AS seats_config_plan, bts_aircraft_type AS bts_type_plan
            FROM (
                SELECT ey.eval_year, f.op_carrier, f.origin, f.dest, f.seats_config, f.bts_aircraft_type,
                       ROW_NUMBER() OVER (PARTITION BY ey.eval_year, f.op_carrier, f.origin, f.dest ORDER BY COUNT(*) DESC, f.seats_config ASC, f.bts_aircraft_type ASC) AS rn
                FROM base_flights f JOIN eval_years ey ON f.year < ey.eval_year
                WHERE f.seats_config IS NOT NULL AND f.seats_config > 0 AND f.bts_aircraft_type IS NOT NULL AND TRIM(f.bts_aircraft_type) != ''
                GROUP BY ey.eval_year, f.op_carrier, f.origin, f.dest, f.seats_config, f.bts_aircraft_type
            ) WHERE rn = 1
        ),
        modal_t4 AS (
            SELECT eval_year, origin, dest, seats_config AS seats_config_plan, bts_aircraft_type AS bts_type_plan
            FROM (
                SELECT ey.eval_year, f.origin, f.dest, f.seats_config, f.bts_aircraft_type,
                       ROW_NUMBER() OVER (PARTITION BY ey.eval_year, f.origin, f.dest ORDER BY COUNT(*) DESC, f.seats_config ASC, f.bts_aircraft_type ASC) AS rn
                FROM base_flights f JOIN eval_years ey ON f.year < ey.eval_year
                WHERE f.seats_config IS NOT NULL AND f.seats_config > 0 AND f.bts_aircraft_type IS NOT NULL AND TRIM(f.bts_aircraft_type) != ''
                GROUP BY ey.eval_year, f.origin, f.dest, f.seats_config, f.bts_aircraft_type
            ) WHERE rn = 1
        ),
        dom_t100_train AS (
            SELECT ey.eval_year, t.op_carrier, t.origin, t.dest, t.month, t.bts_aircraft_type, t.is_ua_family, t.departures_performed, t.seats, t.passengers
            FROM stg_t100_segments t JOIN eval_years ey ON t.year < ey.eval_year
            WHERE t.data_source IN ('DU', 'DF')
        ),
        lf_t1 AS (
            SELECT eval_year, op_carrier, origin, dest, month, bts_aircraft_type, MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf
            FROM dom_t100_train WHERE bts_aircraft_type IS NOT NULL AND TRIM(bts_aircraft_type) != ''
            GROUP BY eval_year, op_carrier, origin, dest, month, bts_aircraft_type HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        lf_t2 AS (
            SELECT eval_year, op_carrier, origin, dest, month, MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf
            FROM dom_t100_train GROUP BY eval_year, op_carrier, origin, dest, month HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        lf_t3 AS (
            SELECT eval_year, origin, dest, month, MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf
            FROM dom_t100_train WHERE is_ua_family = 1 GROUP BY eval_year, origin, dest, month HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        lf_t4 AS (
            SELECT eval_year, op_carrier, origin, dest, MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf
            FROM dom_t100_train GROUP BY eval_year, op_carrier, origin, dest HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        lf_t5 AS (
            SELECT eval_year, origin, dest, MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) AS lf
            FROM dom_t100_train WHERE is_ua_family = 1 GROUP BY eval_year, origin, dest HAVING SUM(seats) > 0 AND SUM(departures_performed) > 0
        ),
        disr_train AS (
            SELECT ey.eval_year, f.origin, f.dest, f.month, f.hour_block,
                   CASE WHEN f.is_cancelled = 1 THEN 1.0 WHEN f.is_diverted = 1 THEN 0.5 ELSE 0.0 END AS w
            FROM base_flights f JOIN eval_years ey ON f.year < ey.eval_year
        ),
        q_t1 AS (SELECT eval_year, origin, dest, month, hour_block, AVG(w) AS q_hat FROM disr_train GROUP BY eval_year, origin, dest, month, hour_block HAVING COUNT(*) >= 5),
        q_t2 AS (SELECT eval_year, origin, dest, month, AVG(w) AS q_hat FROM disr_train GROUP BY eval_year, origin, dest, month HAVING COUNT(*) >= 5),
        q_t3 AS (SELECT eval_year, origin, dest, AVG(w) AS q_hat FROM disr_train GROUP BY eval_year, origin, dest),
        q_t4 AS (SELECT eval_year, AVG(w) AS q_hat FROM disr_train GROUP BY eval_year),
        tsa_train AS (
            SELECT ey.eval_year, t.month, t.iso_dow, t.is_holiday_window, t.tsa_ratio_month, t.tsa_ratio_year
            FROM stg_tsa_throughput t JOIN eval_years ey ON t.year < ey.eval_year
            WHERE t.tsa_ratio_month IS NOT NULL AND t.tsa_ratio_year IS NOT NULL
        ),
        tsa_t1 AS (SELECT eval_year, month, iso_dow, is_holiday_window, AVG(tsa_ratio_month) AS r_month, AVG(tsa_ratio_year) AS r_year FROM tsa_train GROUP BY eval_year, month, iso_dow, is_holiday_window),
        tsa_t2 AS (SELECT eval_year, month, iso_dow, AVG(tsa_ratio_month) AS r_month, AVG(tsa_ratio_year) AS r_year FROM tsa_train GROUP BY eval_year, month, iso_dow),
        tsa_t3 AS (SELECT eval_year, month, AVG(tsa_ratio_month) AS r_month, AVG(tsa_ratio_year) AS r_year FROM tsa_train GROUP BY eval_year, month),
        tsa_t4 AS (SELECT eval_year, AVG(tsa_ratio_month) AS r_month, AVG(tsa_ratio_year) AS r_year FROM tsa_train GROUP BY eval_year),
        pm AS (
            SELECT f.*,
                   CASE WHEN m1.seats_config_plan IS NOT NULL THEN m1.seats_config_plan WHEN m2.seats_config_plan IS NOT NULL THEN m2.seats_config_plan WHEN m3.seats_config_plan IS NOT NULL THEN m3.seats_config_plan ELSE m4.seats_config_plan END AS seats_config_plan,
                   CASE WHEN m1.seats_config_plan IS NOT NULL THEN m1.bts_type_plan WHEN m2.seats_config_plan IS NOT NULL THEN m2.bts_type_plan WHEN m3.seats_config_plan IS NOT NULL THEN m3.bts_type_plan ELSE m4.bts_type_plan END AS bts_type_plan,
                   COALESCE(q1.q_hat, q2.q_hat, q3.q_hat, q4.q_hat) AS q_hat_plan,
                   CASE WHEN tt1.r_month IS NOT NULL THEN tt1.r_month WHEN tt2.r_month IS NOT NULL THEN tt2.r_month WHEN tt3.r_month IS NOT NULL THEN tt3.r_month ELSE tt4.r_month END AS r_tsa_month_plan,
                   CASE WHEN tt1.r_year IS NOT NULL THEN tt1.r_year WHEN tt2.r_year IS NOT NULL THEN tt2.r_year WHEN tt3.r_year IS NOT NULL THEN tt3.r_year ELSE tt4.r_year END AS r_tsa_year_plan
            FROM base_flights f
            LEFT JOIN modal_t1 m1 ON f.year = m1.eval_year AND f.op_carrier = m1.op_carrier AND f.origin = m1.origin AND f.dest = m1.dest AND f.month = m1.month AND f.hour_block = m1.hour_block
            LEFT JOIN modal_t2 m2 ON f.year = m2.eval_year AND f.op_carrier = m2.op_carrier AND f.origin = m2.origin AND f.dest = m2.dest AND f.month = m2.month
            LEFT JOIN modal_t3 m3 ON f.year = m3.eval_year AND f.op_carrier = m3.op_carrier AND f.origin = m3.origin AND f.dest = m3.dest
            LEFT JOIN modal_t4 m4 ON f.year = m4.eval_year AND f.origin = m4.origin AND f.dest = m4.dest
            LEFT JOIN q_t1 q1 ON f.year = q1.eval_year AND f.origin = q1.origin AND f.dest = q1.dest AND f.month = q1.month AND f.hour_block = q1.hour_block
            LEFT JOIN q_t2 q2 ON f.year = q2.eval_year AND f.origin = q2.origin AND f.dest = q2.dest AND f.month = q2.month
            LEFT JOIN q_t3 q3 ON f.year = q3.eval_year AND f.origin = q3.origin AND f.dest = q3.dest
            LEFT JOIN q_t4 q4 ON f.year = q4.eval_year
            LEFT JOIN tsa_t1 tt1 ON f.year = tt1.eval_year AND f.month = tt1.month AND f.iso_dow = tt1.iso_dow AND f.is_holiday_window = tt1.is_holiday_window
            LEFT JOIN tsa_t2 tt2 ON f.year = tt2.eval_year AND f.month = tt2.month AND f.iso_dow = tt2.iso_dow
            LEFT JOIN tsa_t3 tt3 ON f.year = tt3.eval_year AND f.month = tt3.month
            LEFT JOIN tsa_t4 tt4 ON f.year = tt4.eval_year
        ),
        pi AS (
            SELECT pm.*, COALESCE(l1.lf, l2.lf, l3.lf, l4.lf, l5.lf) AS lf_plan
            FROM pm
            LEFT JOIN lf_t1 l1 ON pm.year = l1.eval_year AND pm.op_carrier = l1.op_carrier AND pm.origin = l1.origin AND pm.dest = l1.dest AND pm.month = l1.month AND pm.bts_type_plan = l1.bts_aircraft_type
            LEFT JOIN lf_t2 l2 ON pm.year = l2.eval_year AND pm.op_carrier = l2.op_carrier AND pm.origin = l2.origin AND pm.dest = l2.dest AND pm.month = l2.month
            LEFT JOIN lf_t3 l3 ON pm.year = l3.eval_year AND pm.origin = l3.origin AND pm.dest = l3.dest AND pm.month = l3.month
            LEFT JOIN lf_t4 l4 ON pm.year = l4.eval_year AND pm.op_carrier = l4.op_carrier AND pm.origin = l4.origin AND pm.dest = l4.dest
            LEFT JOIN lf_t5 l5 ON pm.year = l5.eval_year AND pm.origin = l5.origin AND pm.dest = l5.dest
        ),
        calc AS (
            SELECT *,
                   CASE WHEN has_valid_t100 = 1 AND is_cancelled = 0 AND seats_config IS NOT NULL AND seats_config > 0 AND est_open_seats_adjusted IS NOT NULL AND tsa_ratio_year IS NOT NULL THEN 1 ELSE 0 END AS eligible_hist,
                   CASE WHEN has_t100_gate = 1 AND seats_config_plan IS NOT NULL AND seats_config_plan > 0 AND lf_plan IS NOT NULL AND q_hat_plan IS NOT NULL AND r_tsa_month_plan IS NOT NULL AND r_tsa_year_plan IS NOT NULL THEN 1 ELSE 0 END AS eligible_plan,
                   CASE WHEN seats_config_plan IS NOT NULL AND seats_config_plan > 0 AND lf_plan IS NOT NULL AND r_tsa_month_plan IS NOT NULL
                        THEN seats_config_plan - MIN(seats_config_plan, MAX(0, CAST(ROUND(seats_config_plan * MIN(1.0, MAX(0.0, lf_plan * MIN(1.20, MAX(0.80, 1.0 + 0.35 * (r_tsa_month_plan - 1.0))) * hour_block_multiplier))) AS INTEGER)))
                        ELSE NULL END AS est_open_plan_adj
            FROM pi
        ),
        subs AS (
            SELECT *,
                   CASE WHEN eligible_hist = 1 THEN 0.50 * (100.0 * MIN(1.0, MAX(0.0, est_open_seats_adjusted * 1.0 / 25.0))) + 0.50 * (100.0 * MIN(1.0, MAX(0.0, (est_open_seats_adjusted * 1.0 / seats_config) / 0.30))) ELSE NULL END AS s_open_hist,
                   CASE WHEN eligible_hist = 1 THEN 100.0 * MAX(0.0, MIN(1.0, 1.0 - weighted_displacement_hist / 2.0)) ELSE NULL END AS s_disp_hist,
                   CASE WHEN eligible_hist = 1 THEN 100.0 * MIN(1.0, MAX(0.0, LN(1.0 + remaining_performed_backups) / LN(7.0))) ELSE NULL END AS s_backup_hist,
                   CASE WHEN eligible_hist = 1 THEN 100.0 * MIN(1.0, MAX(0.0, (1.35 - (tsa_ratio_year + CASE WHEN is_holiday_window = 1 THEN 0.10 ELSE 0.0 END)) / 0.70)) ELSE NULL END AS s_demand_hist,
                   CASE WHEN eligible_plan = 1 THEN 0.50 * (100.0 * MIN(1.0, MAX(0.0, est_open_plan_adj * 1.0 / 25.0))) + 0.50 * (100.0 * MIN(1.0, MAX(0.0, (est_open_plan_adj * 1.0 / seats_config_plan) / 0.30))) ELSE NULL END AS s_open_plan,
                   CASE WHEN eligible_plan = 1 THEN 100.0 * MAX(0.0, MIN(1.0, 1.0 - (prior_strict_scheduled_count * q_hat_plan * 0.65 + q_hat_plan) / 0.25)) ELSE NULL END AS s_disp_plan,
                   CASE WHEN eligible_plan = 1 THEN 100.0 * MIN(1.0, MAX(0.0, LN(1.0 + remaining_scheduled_backups) / LN(7.0))) ELSE NULL END AS s_backup_plan,
                   CASE WHEN eligible_plan = 1 THEN 100.0 * MIN(1.0, MAX(0.0, (1.35 - (r_tsa_year_plan + CASE WHEN is_holiday_window = 1 THEN 0.10 ELSE 0.0 END)) / 0.70)) ELSE NULL END AS s_demand_plan
            FROM calc
        )
        SELECT *,
               CASE WHEN eligible_hist = 1 THEN ROUND(0.50 * s_open_hist + 0.20 * s_disp_hist + 0.20 * s_backup_hist + 0.10 * s_demand_hist, 2) ELSE NULL END AS soi_historical,
               CASE WHEN eligible_plan = 1 THEN ROUND(0.50 * s_open_plan + 0.20 * s_disp_plan + 0.20 * s_backup_plan + 0.10 * s_demand_plan, 2) ELSE NULL END AS soi_planning
        FROM subs;

        CREATE VIEW fct_flights AS
        SELECT * FROM int_flights_soi_scored;

        CREATE VIEW agg_route_month AS
        WITH t100_agg AS (
            SELECT
                year, MIN(quarter) AS quarter, month, op_carrier, origin, dest, is_international,
                SUM(departures_scheduled) AS departures_scheduled,
                SUM(departures_performed) AS departures_performed,
                SUM(seats) AS seats,
                SUM(passengers) AS passengers,
                CASE WHEN SUM(seats) > 0 THEN MIN(1.0, MAX(0.0, SUM(passengers) * 1.0 / SUM(seats))) ELSE NULL END AS t100_load_factor
            FROM stg_t100_segments
            WHERE is_ua_family = 1
            GROUP BY year, month, op_carrier, origin, dest, is_international
        ),
        otp_m AS (
            SELECT year, month, op_carrier, origin, dest, 0 AS is_international,
                   COUNT(*) AS otp_scheduled_flights,
                   SUM(CASE WHEN is_cancelled = 0 THEN 1 ELSE 0 END) AS otp_performed_flights
            FROM fct_flights
            GROUP BY year, month, op_carrier, origin, dest
        )
        SELECT
            t.*,
            CASE WHEN t.is_international = 1 THEN 'route_month_only_v1' ELSE 'flight_and_route_month' END AS data_granularity,
            o.otp_scheduled_flights,
            o.otp_performed_flights
        FROM t100_agg t
        LEFT JOIN otp_m o
            ON t.year = o.year AND t.month = o.month AND t.op_carrier = o.op_carrier
           AND t.origin = o.origin AND t.dest = o.dest AND t.is_international = o.is_international;
        """
    )
    return conn


def test_sqlite_sql_parity_with_python_enrichment_and_mart_invariants(
    repo_root: Path,
    sample_ingested: dict[str, pd.DataFrame],
) -> None:
    """Verify SQLite SQL transformations match Python Phase 2/4 enrichment+SOI and all mart PK/FK invariants."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    py_result = enrich_flights(
        otp_df=sample_ingested["otp_marketing_flights"],
        t100_df=sample_ingested["t100_segments"],
        faa_registry_df=sample_ingested["faa_aircraft_registry"],
        faa_acftref_df=sample_ingested["faa_acftref"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        reference_dir=cfg.reference_dir,
        config=cfg,
        raise_on_sanity_failure=False,
    )
    py_df = score_flights(
        enriched_df=py_result.enriched_flights,
        t100_df=sample_ingested["t100_segments"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        config=cfg,
    )
    py_df["flight_date_str"] = py_df["flight_date"].astype(str).str.slice(0, 10)

    conn = _build_sample_sqlite_db(repo_root, sample_ingested)
    try:
        sql_fct = pd.read_sql_query("SELECT * FROM fct_flights", conn)
        assert len(sql_fct) == len(py_df) == 80
        assert sql_fct["flight_id"].is_unique

        # Cross-check against data/processed/sample/otp_marketing_flights.parquet when present on disk
        sample_parquet = (
            repo_root / "data" / "processed" / "sample" / "otp_marketing_flights.parquet"
        )
        if sample_parquet.is_file():
            pq_df = pd.read_parquet(sample_parquet)
            assert len(pq_df) == len(sql_fct)

        # Merge on natural flight grain and verify exact parity across aircraft, LF, estimates, and SOI
        merged = py_df.merge(
            sql_fct,
            left_on=[
                "flight_date_str",
                "mkt_carrier",
                "flight_num_mkt",
                "origin",
                "dest",
                "crs_dep_time",
            ],
            right_on=[
                "flight_date",
                "mkt_carrier",
                "flight_num_mkt",
                "origin",
                "dest",
                "crs_dep_time",
            ],
            suffixes=("_py", "_sql"),
            how="inner",
        )
        assert len(merged) == 80

        for col in (
            "aircraft_source",
            "seat_config_source",
            "fleet_subtype",
            "bts_aircraft_type",
            "lf_source",
            "holiday_name",
        ):
            assert (
                merged[f"{col}_py"].fillna("<NA>") == merged[f"{col}_sql"].fillna("<NA>")
            ).all(), f"Mismatch in {col}"

        assert (
            merged["seats_config_py"].fillna(-1).astype(int)
            == merged["seats_config_sql"].fillna(-1).astype(int)
        ).all()
        assert (
            merged["t100_available_py"].astype(int) == merged["t100_available_sql"].astype(int)
        ).all()
        assert (
            merged["is_shared_regional_spoke_py"].astype(int)
            == merged["is_shared_regional_spoke_sql"].astype(int)
        ).all()
        assert (
            merged["is_holiday_window_py"].astype(int)
            == merged["is_holiday_window_sql"].astype(int)
        ).all()
        assert (
            merged["prior_strict_scheduled_count_py"].astype(int)
            == merged["prior_strict_scheduled_count_sql"].astype(int)
        ).all()

        assert np.allclose(
            merged["est_load_factor_py"],
            merged["est_load_factor_sql"],
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )
        assert np.allclose(
            merged["tsa_demand_multiplier_py"],
            merged["tsa_demand_multiplier_sql"],
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )
        assert np.allclose(
            merged["tsa_ratio_year_py"],
            merged["tsa_ratio_year_sql"],
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )
        assert np.allclose(
            merged["hour_block_multiplier_py"],
            merged["hour_block_multiplier_sql"],
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )
        assert np.allclose(
            merged["est_lf_adjusted_py"],
            merged["est_lf_adjusted_sql"],
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )

        # Allow <= 1 passenger rounding tolerance only on exact 0.5 banker's-rounding vs SQL half-away-from-zero ties
        pax_diff = (
            (merged["est_passengers_py"].astype(float) - merged["est_passengers_sql"].astype(float))
            .abs()
            .max()
        )
        assert pax_diff <= 1
        has_seats = merged["seats_config_sql"].notna()
        assert has_seats.sum() == 76
        assert (
            merged.loc[has_seats, "est_passengers_sql"]
            + merged.loc[has_seats, "est_open_seats_sql"]
            == merged.loc[has_seats, "seats_config_sql"]
        ).all()
        assert (
            merged.loc[has_seats, "est_passengers_adjusted_sql"]
            + merged.loc[has_seats, "est_open_seats_adjusted_sql"]
            == merged.loc[has_seats, "seats_config_sql"]
        ).all()
        assert merged.loc[~has_seats, "est_passengers_sql"].isna().all()
        assert merged.loc[~has_seats, "est_open_seats_sql"].isna().all()

        # Phase 4 SOI Python <-> SQL parity (with small tolerance only for 0.5-seat rounding ties)
        for col in (
            "weighted_displacement_hist",
            "s_disp_hist",
            "s_backup_hist",
            "s_demand_hist",
            "s_disp_plan",
            "s_backup_plan",
            "s_demand_plan",
        ):
            assert np.allclose(
                merged[f"{col}_py"].astype(float),
                merged[f"{col}_sql"].astype(float),
                rtol=1e-7,
                atol=1e-6,
                equal_nan=True,
            ), f"Parity mismatch in {col}"

        for col in ("s_open_hist", "soi_historical", "s_open_plan", "soi_planning"):
            py_v = merged[f"{col}_py"].astype(float)
            sql_v = merged[f"{col}_sql"].astype(float)
            assert (py_v.isna() == sql_v.isna()).all(), f"Nullability mismatch in {col}"
            non_na = py_v.notna()
            if non_na.any():
                assert (py_v[non_na] - sql_v[non_na]).abs().max() <= 1.5

        assert sql_fct.loc[sql_fct["year"] == 2019, "soi_planning"].isna().all()
        assert sql_fct.loc[sql_fct["year"] >= 2023, "soi_planning"].notna().any()
        assert sql_fct.loc[sql_fct["is_cancelled"] == 0, "soi_historical"].notna().all()
        assert (
            sql_fct["remaining_scheduled_backups"] >= sql_fct["remaining_performed_backups"]
        ).all()
        assert (sql_fct["weighted_displacement_hist"] >= 0.0).all()

        # agg_route_month: covers both Domestic and International UA-family T-100 segments
        agg_df = pd.read_sql_query("SELECT * FROM agg_route_month", conn)
        assert not agg_df.empty
        assert set(agg_df["is_international"].unique()) == {0, 1}
        assert not agg_df.duplicated(
            subset=["year", "month", "op_carrier", "origin", "dest", "is_international"]
        ).any()
        intl_rows = agg_df[agg_df["is_international"] == 1]
        assert (intl_rows["data_granularity"] == "route_month_only_v1").all()
        assert intl_rows["otp_scheduled_flights"].isna().all()
        dom_rows = agg_df[agg_df["is_international"] == 0]
        assert (dom_rows["data_granularity"] == "flight_and_route_month").all()

        # Dimensions PK uniqueness & FK coverage
        dim_aircraft = pd.read_sql_query(
            """
            SELECT tail_num, COUNT(DISTINCT fleet_subtype) AS n_subtypes
            FROM int_aircraft_enriched
            WHERE tail_num IS NOT NULL
            GROUP BY tail_num
            """,
            conn,
        )
        assert dim_aircraft["tail_num"].is_unique
        assert (dim_aircraft["n_subtypes"] <= 1).all()

        dim_airport = pd.read_sql_query(
            """
            SELECT DISTINCT code AS airport_code FROM (
                SELECT origin AS code FROM stg_otp_flights
                UNION SELECT dest AS code FROM stg_otp_flights
                UNION SELECT origin AS code FROM stg_t100_segments WHERE is_ua_family = 1
                UNION SELECT dest AS code FROM stg_t100_segments WHERE is_ua_family = 1
            )
            """,
            conn,
        )
        assert dim_airport["airport_code"].is_unique
        assert set(sql_fct["origin"]).issubset(set(dim_airport["airport_code"]))
        assert set(sql_fct["dest"]).issubset(set(dim_airport["airport_code"]))
        assert set(agg_df["dest"]).issubset(set(dim_airport["airport_code"]))

        dim_carrier = pd.read_sql_query(
            """
            SELECT u.code AS op_carrier, u.description AS carrier_name,
                   GROUP_CONCAT(DISTINCT h.description) AS history_summary
            FROM raw_bts_lookups u
            LEFT JOIN raw_bts_lookups h
                ON u.code = h.code AND h.lookup_table = 'L_CARRIER_HISTORY'
            WHERE u.lookup_table = 'L_UNIQUE_CARRIERS'
              AND u.code IN ('UA', 'OO', 'YX', 'YV', 'G7', 'C5', 'ZW', 'EV', 'AX')
            GROUP BY u.code, u.description
            """,
            conn,
        )
        assert dim_carrier["op_carrier"].is_unique
        assert set(sql_fct["op_carrier"]).issubset(set(dim_carrier["op_carrier"]))
        assert set(agg_df["op_carrier"]).issubset(set(dim_carrier["op_carrier"]))

        dim_date = pd.read_sql_query(
            """
            SELECT d.date_day, t.tsa_throughput, t.tsa_demand_multiplier, t.tsa_ratio_year
            FROM (
                SELECT flight_date AS date_day FROM stg_otp_flights
                UNION
                SELECT date_day FROM stg_tsa_throughput
            ) d
            LEFT JOIN stg_tsa_throughput t ON d.date_day = t.date_day
            """,
            conn,
        )
        assert dim_date["date_day"].is_unique
        assert set(sql_fct["flight_date"]).issubset(set(dim_date["date_day"]))

        stg_t100 = pd.read_sql_query("SELECT * FROM stg_t100_segments", conn)
        assert not stg_t100.empty
        assert not stg_t100.duplicated(
            subset=[
                "year",
                "month",
                "op_carrier",
                "origin",
                "dest",
                "bts_aircraft_type",
                "data_source",
            ]
        ).any()
    finally:
        conn.close()


def test_stg_t100_segments_aggregates_duplicate_subsegments(
    repo_root: Path,
    sample_ingested: dict[str, pd.DataFrame],
) -> None:
    """Adversarial duplicate T-100 sub-segments collapse cleanly to 0 grain duplicates with exact sums."""
    mutated = {k: v.copy() for k, v in sample_ingested.items()}
    t100 = mutated["t100_segments"].copy()

    # Pick an existing UA DU row in sample_ingested and split it into scheduled + extra-section (0 sched) rows,
    # plus inject a KL IF multi-aircraft_config (1 vs 3) international pair matching real BTS T-100 quirks.
    ua_du_mask = (t100["op_carrier"] == "UA") & (t100["data_source"] == "DU")
    assert ua_du_mask.any(), "Expected at least one UA DU segment in sample_ingested"
    ua_template = t100[ua_du_mask].iloc[0].copy()

    ua_sched = ua_template.copy()
    ua_sched["departures_scheduled"] = 42.0
    ua_sched["departures_performed"] = 40.0
    ua_sched["seats"] = 8000.0
    ua_sched["passengers"] = 6400.0

    ua_extra = ua_template.copy()
    ua_extra["departures_scheduled"] = 0.0
    ua_extra["departures_performed"] = 2.0
    ua_extra["seats"] = 400.0
    ua_extra["passengers"] = 320.0

    # Replace all rows matching that exact 7-col key with the two sub-rows
    key_cols = [
        "year",
        "month",
        "op_carrier",
        "origin",
        "dest",
        "bts_aircraft_type",
        "data_source",
    ]
    same_ua_key = pd.Series(True, index=t100.index)
    for col in key_cols:
        same_ua_key &= t100[col] == ua_template[col]
    t100 = t100[~same_ua_key].copy()

    kl_cfg1 = pd.Series(
        {
            "year": 2019,
            "quarter": 1,
            "month": 1,
            "op_carrier": "KL",
            "carrier_name": "Klm Royal Dutch Airlines",
            "origin": "ORD",
            "origin_airport_id": 13930,
            "origin_country": "US",
            "dest": "AMS",
            "dest_airport_id": 10292,
            "dest_country": "NL",
            "bts_aircraft_type": "819",
            "aircraft_group": 8,
            "aircraft_config": 1,
            "service_class": "F",
            "data_source": "IF",
            "is_international": True,
            "departures_scheduled": 0.0,
            "departures_performed": 1.0,
            "seats": 408.0,
            "passengers": 261.0,
            "distance_miles": 4120.0,
            "confidence_tier": "actual",
        }
    )
    kl_cfg3 = kl_cfg1.copy()
    kl_cfg3["aircraft_config"] = 3
    kl_cfg3["departures_performed"] = 20.0
    kl_cfg3["seats"] = 5360.0
    kl_cfg3["passengers"] = 3852.0

    mutated["t100_segments"] = pd.concat(
        [t100, pd.DataFrame([ua_sched, ua_extra, kl_cfg1, kl_cfg3])],
        ignore_index=True,
    )

    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    py_result = enrich_flights(
        otp_df=mutated["otp_marketing_flights"],
        t100_df=mutated["t100_segments"],
        faa_registry_df=mutated["faa_aircraft_registry"],
        faa_acftref_df=mutated["faa_acftref"],
        tsa_df=mutated["tsa_daily_throughput"],
        reference_dir=cfg.reference_dir,
        config=cfg,
        raise_on_sanity_failure=False,
    )

    conn = _build_sample_sqlite_db(repo_root, mutated)
    try:
        stg_t100 = pd.read_sql_query("SELECT * FROM stg_t100_segments", conn)
        assert not stg_t100.duplicated(subset=key_cols).any(), (
            "stg_t100_segments must have 0 duplicate 7-col grain rows"
        )

        # Verify UA DU extra-section sub-rows aggregated with exact sums and recomputed metrics
        ua_row = stg_t100[
            (stg_t100["year"] == int(ua_template["year"]))
            & (stg_t100["month"] == int(ua_template["month"]))
            & (stg_t100["op_carrier"] == "UA")
            & (stg_t100["origin"] == str(ua_template["origin"]))
            & (stg_t100["dest"] == str(ua_template["dest"]))
            & (stg_t100["bts_aircraft_type"] == str(ua_template["bts_aircraft_type"]))
            & (stg_t100["data_source"] == "DU")
        ]
        assert len(ua_row) == 1
        assert float(ua_row.iloc[0]["departures_scheduled"]) == pytest.approx(42.0)
        assert float(ua_row.iloc[0]["departures_performed"]) == pytest.approx(42.0)
        assert float(ua_row.iloc[0]["seats"]) == pytest.approx(8400.0)
        assert float(ua_row.iloc[0]["passengers"]) == pytest.approx(6720.0)
        assert float(ua_row.iloc[0]["load_factor"]) == pytest.approx(6720.0 / 8400.0)
        assert float(ua_row.iloc[0]["seats_per_departure"]) == pytest.approx(8400.0 / 42.0)

        # Verify KL IF multi-aircraft_config sub-rows collapsed cleanly
        kl_row = stg_t100[
            (stg_t100["year"] == 2019)
            & (stg_t100["month"] == 1)
            & (stg_t100["op_carrier"] == "KL")
            & (stg_t100["dest"] == "AMS")
            & (stg_t100["bts_aircraft_type"] == "819")
            & (stg_t100["data_source"] == "IF")
        ]
        assert len(kl_row) == 1
        assert float(kl_row.iloc[0]["departures_performed"]) == pytest.approx(21.0)
        assert float(kl_row.iloc[0]["seats"]) == pytest.approx(5768.0)
        assert float(kl_row.iloc[0]["passengers"]) == pytest.approx(4113.0)
        assert float(kl_row.iloc[0]["load_factor"]) == pytest.approx(4113.0 / 5768.0)
        assert float(kl_row.iloc[0]["seats_per_departure"]) == pytest.approx(5768.0 / 21.0)

        # Verify downstream int_t100_load_factors and agg_route_month parity
        sql_fct = pd.read_sql_query("SELECT * FROM fct_flights", conn)
        assert len(sql_fct) == len(py_result.enriched_flights) == 80
        py_df = py_result.enriched_flights.copy()
        py_df["flight_date_str"] = py_df["flight_date"].astype(str).str.slice(0, 10)
        merged = py_df.merge(
            sql_fct,
            left_on=[
                "flight_date_str",
                "mkt_carrier",
                "flight_num_mkt",
                "origin",
                "dest",
                "crs_dep_time",
            ],
            right_on=[
                "flight_date",
                "mkt_carrier",
                "flight_num_mkt",
                "origin",
                "dest",
                "crs_dep_time",
            ],
            suffixes=("_py", "_sql"),
            how="inner",
        )
        assert len(merged) == 80
        assert np.allclose(
            merged["est_load_factor_py"],
            merged["est_load_factor_sql"],
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )

        agg_df = pd.read_sql_query("SELECT * FROM agg_route_month", conn)
        assert not agg_df.duplicated(
            subset=["year", "month", "op_carrier", "origin", "dest", "is_international"]
        ).any()
    finally:
        conn.close()


def test_sc1_singular_test_catches_null_open_seats(
    dbt_compiled_manifest: dict,
    repo_root: Path,
) -> None:
    """`assert_sc1_passenger_bounds.sql` catches rows where open_seats is NULL despite non-null passengers."""
    compiled_test = (
        repo_root
        / "dbt"
        / "target"
        / "compiled"
        / "united_standby_analytics"
        / "tests"
        / "assert_sc1_passenger_bounds.sql"
    )
    assert compiled_test.is_file()
    raw_sql = compiled_test.read_text(encoding="utf-8")
    sqlite_sql = raw_sql.replace("`united-standby-analytics`.`marts`.`fct_flights`", "fct_flights")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE fct_flights (
                flight_id TEXT,
                flight_date TEXT,
                op_carrier TEXT,
                origin TEXT,
                dest TEXT,
                seats_config INTEGER,
                est_load_factor REAL,
                est_passengers INTEGER,
                est_open_seats INTEGER,
                est_lf_adjusted REAL,
                est_passengers_adjusted INTEGER,
                est_open_seats_adjusted INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO fct_flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ok", "2024-01-01", "UA", "ORD", "DEN", 150, 0.80, 120, 30, 0.80, 120, 30),
                (
                    "null_base",
                    "2024-01-01",
                    "UA",
                    "ORD",
                    "DEN",
                    150,
                    0.80,
                    120,
                    None,
                    0.80,
                    120,
                    30,
                ),
                ("null_adj", "2024-01-01", "UA", "ORD", "DEN", 150, 0.80, 120, 30, 0.80, 120, None),
            ],
        )
        hits = pd.read_sql_query(sqlite_sql, conn)
        assert set(hits["flight_id"]) == {"null_base", "null_adj"}
    finally:
        conn.close()
