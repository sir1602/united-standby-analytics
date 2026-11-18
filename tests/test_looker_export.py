"""Tests for Google Looker Studio pre-joined CSV extract generation (`standby.export.looker`)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner

from standby.cli import main
from standby.config import load_config
from standby.export.looker import (
    LOOKER_FLIGHTS_COLUMNS,
    LOOKER_ROUTE_MONTH_COLUMNS,
    LOOKER_ROUTE_SUMMARY_COLUMNS,
    _sha256_file,
    _validate_looker_tables,
    export_looker_extracts,
)
from standby.export.tableau import (
    VALIDATION_SUMMARY_COLUMNS,
    ExportValidationError,
    export_tableau_extracts,
)


def test_export_looker_extracts_sample_generates_all_4_csvs_and_metadata(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """`export_looker_extracts` produces all 4 denormalized Looker CSVs and valid metadata on sample data."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    out_dir = tmp_path / "looker_sample"

    result = export_looker_extracts(
        config=cfg,
        output_dir=out_dir,
        from_processed=False,
    )

    assert result.output_dir == out_dir.resolve()
    assert result.sample_mode is True
    assert result.flight_mode == "stratified"
    assert result.total_source_flights == 80
    assert result.exported_flights == 80
    assert result.metadata_path.is_file()

    expected_files = {
        "looker_flights.csv": LOOKER_FLIGHTS_COLUMNS,
        "looker_route_month.csv": LOOKER_ROUTE_MONTH_COLUMNS,
        "looker_route_summary.csv": LOOKER_ROUTE_SUMMARY_COLUMNS,
        "looker_validation_summary.csv": VALIDATION_SUMMARY_COLUMNS,
    }
    assert set(result.files.keys()) == set(expected_files.keys())

    meta = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert meta["bi_target"] == "looker_studio"
    assert meta["invariants_passed"] is True
    assert meta["total_source_flights"] == 80
    assert meta["exported_flights"] == 80

    for fname, expected_cols in expected_files.items():
        fpath = out_dir / fname
        assert fpath.is_file(), f"Missing exported Looker file: {fname}"
        df = pd.read_csv(fpath, low_memory=False)
        assert tuple(df.columns) == expected_cols
        assert len(df) == meta["files"][fname]["rows"]
        assert len(df.columns) == meta["files"][fname]["columns"]
        assert fpath.stat().st_size == meta["files"][fname]["bytes"]
        assert _sha256_file(fpath) == meta["files"][fname]["sha256"]

    # Check pre-joined Looker columns on looker_flights.csv
    flights_df = pd.read_csv(out_dir / "looker_flights.csv", low_memory=False)
    assert flights_df["route_label"].str.startswith("ORD -> ").all()
    assert flights_df["day_of_week_label"].notna().all()
    assert flights_df["hour_block_label"].notna().all()
    assert flights_df["carrier_name"].notna().all()
    assert set(flights_df["soi_planning_tier"].unique()) <= {
        "Favorable (70-100)",
        "Moderate (50-69.99)",
        "Tight (0-49.99)",
        "Unscored (NULL)",
    }

    # Check international rows in looker_route_month.csv and looker_route_summary.csv have NULL otp_*
    rm_df = pd.read_csv(out_dir / "looker_route_month.csv", low_memory=False)
    intl_rm = rm_df[rm_df["is_international"].astype(bool)]
    assert not intl_rm.empty
    otp_rm_cols = [c for c in rm_df.columns if c.startswith("otp_")]
    assert intl_rm[otp_rm_cols].isna().all().all()

    rs_df = pd.read_csv(out_dir / "looker_route_summary.csv", low_memory=False)
    assert rs_df["dest_geo_query"].str.contains("Airport").all()
    intl_rs = rs_df[rs_df["is_international"].astype(bool)]
    assert not intl_rs.empty
    otp_rs_cols = [c for c in rs_df.columns if c.startswith("otp_")]
    assert intl_rs[otp_rs_cols].isna().all().all()


def test_export_looker_fallback_from_verified_tableau_extracts(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """When processed Parquets and raw sources are absent, `export_looker_extracts` builds from verified Tableau extracts."""
    tableau_dir = tmp_path / "tableau_extracts"
    looker_dir = tmp_path / "looker_from_tableau"

    cfg_sample = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    export_tableau_extracts(config=cfg_sample, output_dir=tableau_dir, from_processed=False)

    # Point config at empty raw/sample/processed dirs but valid `exports_dir = tableau_dir`
    cfg_fallback = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    cfg_fallback.sample_dir = tmp_path / "nonexistent_sample"
    cfg_fallback.processed_dir = tmp_path / "nonexistent_processed"
    cfg_fallback.exports_dir = tableau_dir

    result = export_looker_extracts(
        config=cfg_fallback,
        output_dir=looker_dir,
        from_processed=True,
    )
    assert result.exported_flights == 80
    assert (looker_dir / "looker_flights.csv").is_file()
    assert (looker_dir / "looker_route_month.csv").is_file()
    assert (looker_dir / "looker_route_summary.csv").is_file()
    assert (looker_dir / "looker_validation_summary.csv").is_file()


def test_cli_export_looker_sample_and_invalid_years(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """`standby export-looker --sample` succeeds via CLI and rejects malformed `--years`."""
    runner = CliRunner()
    out_dir = tmp_path / "cli_looker"

    res = runner.invoke(
        main,
        [
            "export-looker",
            "--hub",
            "ORD",
            "--years",
            "2019,2023-2026",
            "--sample",
            "--output-dir",
            str(out_dir),
            "--recompute",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "LOOKER EXPORT SUCCEEDED:" in res.output
    assert (out_dir / "looker_flights.csv").is_file()
    assert (out_dir / "export_metadata.json").is_file()

    bad = runner.invoke(main, ["export-looker", "--years", "not-a-year", "--sample"])
    assert bad.exit_code != 0
    assert "--years" in bad.output


def test_validate_looker_tables_rejects_invariant_violations(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """`_validate_looker_tables` raises `ExportValidationError` on international OTP leakage or SC-5 violations."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    out_dir = tmp_path / "looker_inv"
    export_looker_extracts(config=cfg, output_dir=out_dir, from_processed=False)

    file_paths = {
        "looker_flights.csv": out_dir / "looker_flights.csv",
        "looker_route_month.csv": out_dir / "looker_route_month.csv",
        "looker_route_summary.csv": out_dir / "looker_route_summary.csv",
        "looker_validation_summary.csv": out_dir / "looker_validation_summary.csv",
    }
    base_tables = {
        "looker_flights": pd.read_csv(file_paths["looker_flights.csv"], low_memory=False),
        "looker_route_month.csv": pd.read_csv(
            file_paths["looker_route_month.csv"], low_memory=False
        ),
        "looker_route_summary.csv": pd.read_csv(
            file_paths["looker_route_summary.csv"], low_memory=False
        ),
        "looker_validation_summary.csv": pd.read_csv(
            file_paths["looker_validation_summary.csv"], low_memory=False
        ),
    }

    # 1. International row in looker_route_summary with non-null otp_avg_soi_planning must fail
    bad_rs = base_tables["looker_route_summary.csv"].copy()
    bad_rs.loc[bad_rs["is_international"].astype(bool), "otp_avg_soi_planning"] = 55.0
    with pytest.raises(ExportValidationError, match="International rows in looker_route_summary"):
        _validate_looker_tables(
            tables={**base_tables, "looker_route_summary.csv": bad_rs},
            file_paths=file_paths,
            flight_mode="stratified",
        )

    # 2. SC-5 violation in looker_flights must fail
    bad_fct = base_tables["looker_flights"].copy()
    bad_fct.loc[0, "t100_available"] = False
    bad_fct.loc[0, "lf_source"] = "UNMATCHED"
    bad_fct.loc[0, "soi_planning"] = 60.0
    with pytest.raises(ExportValidationError, match="SC-5 violated in looker_flights"):
        _validate_looker_tables(
            tables={**base_tables, "looker_flights": bad_fct},
            file_paths=file_paths,
            flight_mode="stratified",
        )


def test_committed_looker_extracts_match_metadata_and_invariants(repo_root: Path) -> None:
    """Verify committed `exports/looker/` CSV extracts match `export_metadata.json` SHA-256 and invariants."""
    looker_dir = repo_root / "exports" / "looker"
    meta_path = looker_dir / "export_metadata.json"
    assert meta_path.is_file(), "Missing exports/looker/export_metadata.json"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["bi_target"] == "looker_studio"
    assert meta["invariants_passed"] is True
    assert meta["total_source_flights"] == 830941
    assert meta["exported_flights"] == 100000

    file_paths: dict[str, Path] = {}
    for fname, info in meta["files"].items():
        fpath = looker_dir / fname
        assert fpath.is_file(), f"Missing committed Looker file: {fname}"
        assert fpath.stat().st_size == info["bytes"]
        assert _sha256_file(fpath) == info["sha256"]
        file_paths[fname] = fpath

    tables = {
        "looker_flights": pd.read_csv(looker_dir / "looker_flights.csv", low_memory=False),
        "looker_route_month.csv": pd.read_csv(
            looker_dir / "looker_route_month.csv", low_memory=False
        ),
        "looker_route_summary.csv": pd.read_csv(
            looker_dir / "looker_route_summary.csv", low_memory=False
        ),
        "looker_validation_summary.csv": pd.read_csv(
            looker_dir / "looker_validation_summary.csv", low_memory=False
        ),
    }
    _validate_looker_tables(
        tables=tables,
        file_paths=file_paths,
        flight_mode="stratified",
        max_file_mb=50.0,
    )
