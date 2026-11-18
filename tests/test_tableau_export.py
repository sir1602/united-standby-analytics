"""Tests for Phase 5 Tableau CSV export pipeline, post-export invariants, CLI, and docs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from standby.cli import main
from standby.config import StandbyConfig, load_config
from standby.export.tableau import (
    AGG_ROUTE_MONTH_COLUMNS,
    DIM_AIRCRAFT_COLUMNS,
    DIM_AIRPORT_COLUMNS,
    DIM_CARRIER_COLUMNS,
    DIM_DATE_COLUMNS,
    FCT_FLIGHTS_TABLEAU_COLUMNS,
    ROUTE_MAP_PATHS_COLUMNS,
    VALIDATION_SUMMARY_COLUMNS,
    ExportValidationError,
    _stratified_sample_flights,
    _validate_exported_tables,
    export_tableau_extracts,
)


def test_export_tableau_sample_end_to_end_and_invariants(
    sample_config: StandbyConfig,
    tmp_path: Path,
) -> None:
    """`export_tableau_extracts` produces all 8 mart-aligned CSVs + metadata from sample slice."""
    out_dir = tmp_path / "tableau_sample"
    res = export_tableau_extracts(
        config=sample_config,
        output_dir=out_dir,
        flight_mode="stratified",
        max_flight_rows=100,
        stratify_seed=42,
        from_processed=False,
    )

    assert res.sample_mode is True
    assert res.flight_mode == "stratified"
    assert res.total_source_flights == 80
    assert res.exported_flights == 80
    assert res.metadata_path.is_file()

    expected_files = {
        "agg_route_month.csv": AGG_ROUTE_MONTH_COLUMNS,
        "dim_airport.csv": DIM_AIRPORT_COLUMNS,
        "dim_aircraft.csv": DIM_AIRCRAFT_COLUMNS,
        "dim_carrier.csv": DIM_CARRIER_COLUMNS,
        "dim_date.csv": DIM_DATE_COLUMNS,
        "fct_flights_tableau.csv": FCT_FLIGHTS_TABLEAU_COLUMNS,
        "route_map_paths.csv": ROUTE_MAP_PATHS_COLUMNS,
        "validation_summary.csv": VALIDATION_SUMMARY_COLUMNS,
    }
    assert set(res.files.keys()) == set(expected_files.keys())

    frames: dict[str, pd.DataFrame] = {}
    for fname, expected_cols in expected_files.items():
        fpath = out_dir / fname
        assert fpath.is_file()
        assert fpath.stat().st_size > 0
        df = pd.read_csv(fpath)
        assert tuple(df.columns) == expected_cols
        assert len(df) > 0
        frames[fname] = df

    # Primary key uniqueness
    assert frames["agg_route_month.csv"]["route_month_id"].is_unique
    assert frames["dim_airport.csv"]["airport_code"].is_unique
    assert frames["dim_aircraft.csv"]["tail_num"].is_unique
    assert frames["dim_carrier.csv"]["op_carrier"].is_unique
    assert frames["dim_date.csv"]["date_day"].is_unique
    assert frames["fct_flights_tableau.csv"]["flight_id"].is_unique
    assert not frames["route_map_paths.csv"].duplicated(subset=["path_id", "path_order"]).any()

    # No invented lat/lon coordinates in route_map_paths or dim_airport
    for geo_df in (frames["route_map_paths.csv"], frames["dim_airport.csv"]):
        cols_lower = {c.lower() for c in geo_df.columns}
        assert not (cols_lower & {"lat", "lon", "latitude", "longitude"})

    # Every route_map_path has exact 2 rows (path_order 1=ORD, 2=dest)
    rmp = frames["route_map_paths.csv"]
    counts_per_path = rmp.groupby("path_id")["path_order"].apply(lambda s: sorted(s.tolist()))
    assert all(orders == [1, 2] for orders in counts_per_path)

    # International agg_route_month rows enforce D-04 OTP null boundary
    agg = frames["agg_route_month.csv"]
    intl = agg.loc[agg["is_international"].astype(bool)]
    assert not intl.empty
    assert (intl["data_granularity"] == "route_month_only_v1").all()
    otp_cols = [c for c in agg.columns if c.startswith("otp_")]
    assert intl[otp_cols].isna().all().all()

    # 2019 soi_planning is strictly NULL and cancelled soi_historical is strictly NULL
    fct = frames["fct_flights_tableau.csv"]
    assert fct.loc[fct["year"] == 2019, "soi_planning"].isna().all()
    assert fct.loc[fct["is_cancelled"].astype(bool), "soi_historical"].isna().all()


def test_stratified_sampling_deterministic_and_exact_budget() -> None:
    """Hamilton largest-remainder stratification hits exact budget deterministically across strata."""
    rows: list[dict[str, object]] = []
    idx = 0
    for year in (2019, 2023, 2024):
        for month in (1, 7):
            for carrier, n_rows in (("UA", 30), ("OO", 20), ("G7", 5)):
                for _ in range(n_rows):
                    idx += 1
                    rows.append(
                        {
                            "flight_id": f"{year}{month:02d}01|UA|{idx:04d}|ORD|DEN|0800",
                            "flight_date": f"{year}-{month:02d}-01",
                            "crs_dep_time": "0800",
                            "year": year,
                            "month": month,
                            "op_carrier": carrier,
                        }
                    )
    df = pd.DataFrame(rows)
    s1 = _stratified_sample_flights(df, max_rows=50, seed=42)
    s2 = _stratified_sample_flights(df, max_rows=50, seed=42)
    assert len(s1) == 50
    pd.testing.assert_frame_equal(s1, s2)
    # Every (year, month, op_carrier) stratum (18 total <= 50) must be represented
    assert s1.groupby(["year", "month", "op_carrier"]).ngroups == 18


def test_recent_12m_and_full_export_modes(
    sample_config: StandbyConfig,
    tmp_path: Path,
) -> None:
    """`recent-12m` filters to latest 12 year-months and `full` writes `*_full.csv`."""
    recent_dir = tmp_path / "recent"
    res_recent = export_tableau_extracts(
        config=sample_config,
        output_dir=recent_dir,
        flight_mode="recent-12m",
        from_processed=False,
    )
    assert res_recent.flight_mode == "recent-12m"
    recent_fct = pd.read_csv(recent_dir / "fct_flights_tableau.csv")
    assert recent_fct[["year", "month"]].drop_duplicates().shape[0] <= 12

    full_dir = tmp_path / "full"
    res_full = export_tableau_extracts(
        config=sample_config,
        output_dir=full_dir,
        flight_mode="full",
        from_processed=False,
    )
    assert res_full.flight_mode == "full"
    assert (full_dir / "fct_flights_tableau_full.csv").is_file()
    assert res_full.exported_flights == res_full.total_source_flights == 80


def test_post_export_validation_catches_sc5_pii_pk_and_size(tmp_path: Path) -> None:
    """Post-export invariant validator rejects SC-5 leakage, PII, duplicate PK, bounds, and oversized CSVs."""
    base_fct = pd.DataFrame(
        {
            "flight_id": ["20240101|UA|1|ORD|DEN|0800", "20240101|UA|2|ORD|EWR|0900"],
            "t100_available": [True, False],
            "lf_source": ["CARRIER_ROUTE_MONTH_TYPE", "UNMATCHED"],
            "est_load_factor": [0.80, np.nan],
            "est_passengers": [140, pd.NA],
            "est_open_seats": [26, pd.NA],
            "est_lf_adjusted": [0.81, np.nan],
            "est_passengers_adjusted": [142, pd.NA],
            "est_open_seats_adjusted": [24, pd.NA],
            "s_open_hist": [70.0, np.nan],
            "s_disp_hist": [100.0, np.nan],
            "s_backup_hist": [60.0, np.nan],
            "s_demand_hist": [50.0, np.nan],
            "soi_historical": [72.0, np.nan],
            "s_open_plan": [68.0, np.nan],
            "s_disp_plan": [95.0, np.nan],
            "s_backup_plan": [60.0, np.nan],
            "s_demand_plan": [52.0, np.nan],
            "soi_planning": [70.2, np.nan],
        }
    )
    base_agg = pd.DataFrame(
        {
            "route_month_id": ["2024-01|UA|ORD|DEN|DOM"],
            "is_international": [False],
            "t100_load_factor": [0.80],
            "otp_scheduled_flights": [100],
        }
    )
    valid_tables = {
        "agg_route_month.csv": base_agg,
        "dim_airport.csv": pd.DataFrame({"airport_code": ["ORD"]}),
        "dim_aircraft.csv": pd.DataFrame({"tail_num": ["N100UA"]}),
        "dim_carrier.csv": pd.DataFrame({"op_carrier": ["UA"]}),
        "dim_date.csv": pd.DataFrame({"date_day": ["2024-01-01"]}),
        "fct_flights": base_fct,
        "route_map_paths.csv": pd.DataFrame({"path_id": ["ORD-DEN-DOM"], "path_order": [1]}),
        "validation_summary.csv": pd.DataFrame({"pillar": ["P1"]}),
    }
    dummy_paths = {}
    for name in (
        "agg_route_month.csv",
        "dim_airport.csv",
        "dim_aircraft.csv",
        "dim_carrier.csv",
        "dim_date.csv",
        "fct_flights_tableau.csv",
        "route_map_paths.csv",
        "validation_summary.csv",
    ):
        p = tmp_path / name
        p.write_text("a\n1\n", encoding="utf-8")
        dummy_paths[name] = p

    # Valid baseline passes
    _validate_exported_tables(valid_tables, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 1. SC-5 violation
    bad_sc5 = {**valid_tables, "fct_flights": base_fct.copy()}
    bad_sc5["fct_flights"].loc[1, "soi_historical"] = 55.0
    with pytest.raises(ExportValidationError, match="SC-5"):
        _validate_exported_tables(bad_sc5, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 2. PII column
    bad_pii = {**valid_tables, "fct_flights": base_fct.assign(passenger_name="John Doe")}
    with pytest.raises(ExportValidationError, match="PII"):
        _validate_exported_tables(bad_pii, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 3. Duplicate PK
    bad_pk = {**valid_tables, "dim_airport.csv": pd.DataFrame({"airport_code": ["ORD", "ORD"]})}
    with pytest.raises(ExportValidationError, match="Duplicate primary keys"):
        _validate_exported_tables(bad_pk, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 4. Out-of-bounds SOI
    bad_bounds = {**valid_tables, "fct_flights": base_fct.copy()}
    bad_bounds["fct_flights"].loc[0, "soi_planning"] = 101.5
    with pytest.raises(ExportValidationError, match="outside \\[0, 100\\]"):
        _validate_exported_tables(
            bad_bounds, dummy_paths, flight_mode="stratified", max_file_mb=50.0
        )

    # 5. File size cap
    with pytest.raises(ExportValidationError, match="exceeds"):
        _validate_exported_tables(
            valid_tables, dummy_paths, flight_mode="stratified", max_file_mb=0.000001
        )


def test_cli_export_tableau_sample_and_committed_exports_integrity(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """CLI `export-tableau --sample` succeeds and committed `exports/tableau/` matches metadata."""
    runner = CliRunner()
    cli_out = tmp_path / "cli_tableau"
    res = runner.invoke(
        main,
        [
            "export-tableau",
            "--hub",
            "ORD",
            "--years",
            "2019,2023-2026",
            "--sample",
            "--recompute",
            "--output-dir",
            str(cli_out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "TABLEAU EXPORT SUCCEEDED: files=8" in res.output
    assert (cli_out / "export_metadata.json").is_file()

    # Verify committed full extracts in exports/tableau/
    exports_dir = repo_root / "exports" / "tableau"
    meta_path = exports_dir / "export_metadata.json"
    assert meta_path.is_file(), "Committed exports/tableau/export_metadata.json missing"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["invariants_passed"] is True
    assert meta["total_source_flights"] == 830941
    assert meta["exported_flights"] == 100000

    cfg = load_config(repo_root=repo_root)
    max_bytes = int(cfg.export.tableau_max_file_mb * 1024 * 1024)
    for fname, finfo in meta["files"].items():
        fpath = exports_dir / fname
        assert fpath.is_file(), f"Missing committed extract {fname}"
        actual_bytes = fpath.stat().st_size
        assert actual_bytes == finfo["bytes"]
        assert 0 < actual_bytes < max_bytes
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
        assert digest == finfo["sha256"]


def test_phase5_docs_exist_and_cover_157_mart_columns(repo_root: Path) -> None:
    """Phase 5 documentation deliverables exist and document all 157 BigQuery mart columns."""
    dd_path = repo_root / "docs" / "data_dictionary.md"
    spec_path = repo_root / "docs" / "tableau_dashboard_spec.md"
    bq_path = repo_root / "docs" / "bigquery_optimization.md"
    for p in (dd_path, spec_path, bq_path):
        assert p.is_file() and p.stat().st_size > 1000

    dd_text = dd_path.read_text(encoding="utf-8")
    assert "157" in dd_text
    for table_name in (
        "marts.fct_flights",
        "marts.agg_route_month",
        "marts.dim_aircraft",
        "marts.dim_airport",
        "marts.dim_carrier",
        "marts.dim_date",
    ):
        assert table_name in dd_text

    spec_text = spec_path.read_text(encoding="utf-8")
    assert "NOT a Boarding Probability" in spec_text
    assert "not affiliated with" in spec_text.lower()
    assert "route_month_only_v1" in spec_text
    assert "Airport" in spec_text

    bq_text = bq_path.read_text(encoding="utf-8")
    assert "Sandbox" in bq_text
    assert "flight_date" in bq_text
    assert "origin, dest, op_carrier" in bq_text
