"""Tests for Phase 5 Tableau CSV export pipeline, post-export invariants, CLI, and docs."""

from __future__ import annotations

import dataclasses
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
    _resolve_default_output_dir,
    _resolve_hub_set,
    _select_flights_for_export,
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
    """`recent-12m` filters strictly to latest 12 year-months and `full` writes `*_full.csv`."""
    recent_dir = tmp_path / "recent"
    res_recent = export_tableau_extracts(
        config=sample_config,
        output_dir=recent_dir,
        flight_mode="recent-12m",
        from_processed=False,
    )
    assert res_recent.flight_mode == "recent-12m"
    recent_fct = pd.read_csv(recent_dir / "fct_flights_tableau.csv")

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

    full_fct = pd.read_csv(full_dir / "fct_flights_tableau_full.csv")
    expected_latest = {
        tuple(r)
        for r in full_fct[["year", "month"]]
        .drop_duplicates()
        .sort_values(["year", "month"])
        .tail(12)
        .to_numpy()
    }
    actual_recent = {tuple(r) for r in recent_fct[["year", "month"]].drop_duplicates().to_numpy()}
    assert actual_recent == expected_latest

    # Switching modes in one directory leaves exactly one consistent run: the previous
    # `fct_flights_tableau.csv` is removed and metadata lists every CSV that remains.
    export_tableau_extracts(
        config=sample_config,
        output_dir=recent_dir,
        flight_mode="full",
        from_processed=False,
    )
    assert not (recent_dir / "fct_flights_tableau.csv").exists()
    assert (recent_dir / "fct_flights_tableau_full.csv").is_file()
    recent_meta = json.loads((recent_dir / "export_metadata.json").read_text(encoding="utf-8"))
    assert set(recent_meta["files"]) == {p.name for p in recent_dir.glob("*.csv")}

    # Multi-month synthetic check (> 12 months) confirms oldest months are dropped
    synth_rows = [
        {
            "flight_id": f"2024{m:02d}01|UA|{i}|ORD|DEN|0800",
            "flight_date": f"{2023 + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}-01",
            "crs_dep_time": "0800",
            "year": 2023 + (m - 1) // 12,
            "month": (m - 1) % 12 + 1,
        }
        for i, m in enumerate(range(1, 16), start=1)
    ]
    selected = _select_flights_for_export(
        pd.DataFrame(synth_rows),
        flight_mode="recent-12m",
        max_flight_rows=100,
        stratify_seed=42,
    )
    ym_selected = sorted({(int(r.year), int(r.month)) for r in selected.itertuples()})
    assert len(ym_selected) == 12
    assert ym_selected[0] == (2023, 4)
    assert ym_selected[-1] == (2024, 3)


def test_resolve_hub_set_respects_active_hub_override(repo_root: Path) -> None:
    """`--hub` override (`config.active_hub`) takes precedence over `config/standby.yaml` hubs."""
    cfg_default = load_config(repo_root=repo_root)
    assert _resolve_hub_set(cfg_default) == {"ORD"}

    cfg_den = load_config(repo_root=repo_root, hub="DEN")
    assert cfg_den.active_hub == "DEN"
    assert _resolve_hub_set(cfg_den) == {"DEN"}


def test_post_export_validation_catches_sc5_pii_pk_and_size(tmp_path: Path) -> None:
    """Post-export invariant validator rejects SC-5, PII, PK, bounds, intl OTP, null rules, self-loops, and oversized CSVs."""
    base_fct = pd.DataFrame(
        {
            "flight_id": ["20240101|UA|1|ORD|DEN|0800", "20240101|UA|2|ORD|EWR|0900"],
            "year": [2024, 2019],
            "is_cancelled": [False, False],
            "seats_config": [166, 166],
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
            "route_month_id": ["2024-01|UA|ORD|DEN|DOM", "2024-01|UA|ORD|LHR|INTL"],
            "is_international": [False, True],
            "t100_load_factor": [0.80, 0.85],
            "otp_scheduled_flights": pd.array([100, pd.NA], dtype="Int64"),
        }
    )
    valid_tables = {
        "agg_route_month.csv": base_agg,
        "dim_airport.csv": pd.DataFrame({"airport_code": ["ORD"]}),
        "dim_aircraft.csv": pd.DataFrame({"tail_num": ["N100UA"]}),
        "dim_carrier.csv": pd.DataFrame({"op_carrier": ["UA"]}),
        "dim_date.csv": pd.DataFrame({"date_day": ["2024-01-01"]}),
        "fct_flights": base_fct,
        "route_map_paths.csv": pd.DataFrame(
            {
                "path_id": ["ORD-DEN-DOM", "ORD-DEN-DOM"],
                "origin": ["ORD", "ORD"],
                "dest": ["DEN", "DEN"],
                "path_order": [1, 2],
            }
        ),
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

    # 5. International non-null otp_* rejection
    bad_intl = {**valid_tables, "agg_route_month.csv": base_agg.copy()}
    bad_intl["agg_route_month.csv"].loc[1, "otp_scheduled_flights"] = 25
    with pytest.raises(ExportValidationError, match="International rows"):
        _validate_exported_tables(bad_intl, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 6. est_passengers > seats_config rejection
    bad_seats = {**valid_tables, "fct_flights": base_fct.copy()}
    bad_seats["fct_flights"].loc[0, "est_passengers"] = 180
    with pytest.raises(ExportValidationError, match="exceeds seats_config"):
        _validate_exported_tables(
            bad_seats, dummy_paths, flight_mode="stratified", max_file_mb=50.0
        )

    # 7. 2019 soi_planning non-null rejection
    bad_2019 = {**valid_tables, "fct_flights": base_fct.copy()}
    bad_2019["fct_flights"].loc[1, "t100_available"] = True
    bad_2019["fct_flights"].loc[1, "lf_source"] = "CARRIER_ROUTE_MONTH"
    bad_2019["fct_flights"].loc[1, "soi_planning"] = 50.0
    with pytest.raises(ExportValidationError, match="2019 rows must have NULL soi_planning"):
        _validate_exported_tables(bad_2019, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 8. Cancelled soi_historical non-null rejection
    bad_canc = {**valid_tables, "fct_flights": base_fct.copy()}
    bad_canc["fct_flights"].loc[0, "is_cancelled"] = True
    with pytest.raises(ExportValidationError, match="cancelled rows must have NULL soi_historical"):
        _validate_exported_tables(bad_canc, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 9. route_map_paths self-loop rejection
    bad_loop = {
        **valid_tables,
        "route_map_paths.csv": pd.DataFrame(
            {
                "path_id": ["ORD-ORD-DOM"],
                "origin": ["ORD"],
                "dest": ["ORD"],
                "path_order": [1],
            }
        ),
    }
    with pytest.raises(ExportValidationError, match="self-loop"):
        _validate_exported_tables(bad_loop, dummy_paths, flight_mode="stratified", max_file_mb=50.0)

    # 10. File size cap
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
    assert meta["sample_mode"] is False, "Committed extracts were overwritten by a --sample run"
    assert meta["flight_mode"] in {"stratified", "recent-12m"}
    assert "fct_flights_tableau.csv" in meta["files"]
    assert meta["exported_flights"] == meta["files"]["fct_flights_tableau.csv"]["rows"]
    assert meta["exported_flights"] == min(meta["flight_row_budget"], meta["total_source_flights"])

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
    """Phase 5 documentation deliverables cover 157 mart columns, Tableau specs, BigQuery dry-run SQL, and README contracts."""
    dd_path = repo_root / "docs" / "data_dictionary.md"
    spec_path = repo_root / "docs" / "tableau_dashboard_spec.md"
    bq_path = repo_root / "docs" / "bigquery_optimization.md"
    readme_path = repo_root / "README.md"
    for p in (dd_path, spec_path, bq_path, readme_path):
        assert p.is_file() and p.stat().st_size > 1000

    # 1. Data dictionary: 157 columns, all 6 marts, consistent tsa_ratio_year tier
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
    assert "| 14 | `tsa_ratio_year` | `FLOAT64` | `estimated` |" in dd_text

    # 2. Tableau dashboard spec: 4 disclaimers, KPI formulas, 2019 baseline, flight detail table, grounded numbers
    spec_text = spec_path.read_text(encoding="utf-8")
    for required_spec in (
        "NOT a Boarding Probability",
        "not affiliated with",
        "NOT from observed flight-level loads",
        "route_month_only_v1",
        "KPI Load Factor (Adj)",
        "KPI Delay Rate (Dep 15+)",
        "KPI Cancellation Rate",
        "KPI Avg Open Seats (Adj)",
        "SOI 2019 Baseline (Historical)",
        "SOI vs 2019 Baseline Delta",
        "confidence_tier",
        "record_confidence_summary",
        "lf_source",
        "is_shared_regional_spoke",
        "Airport",
        "MAKELINE",
    ):
        assert required_spec in spec_text, f"Missing {required_spec!r} in tableau_dashboard_spec.md"

    # 3. BigQuery optimization: Sandbox rationale, unpartitioned vs pruned SQL, bq query --dry_run, zero fake bytes
    bq_text = bq_path.read_text(encoding="utf-8")
    for required_bq in (
        "Sandbox",
        "flight_date",
        "origin, dest, op_carrier",
        "_bench_fct_flights_unpartitioned",
        "FORMAT_DATE",
        "BETWEEN DATE '2025-07-01' AND DATE '2025-07-31'",
        "bq query --use_legacy_sql=false --dry_run --format=prettyjson",
        "totalBytesProcessed",
    ):
        assert required_bq in bq_text, f"Missing {required_bq!r} in bigquery_optimization.md"

    # 4. README: CI badge, methodology, consistency caveat, BQ summary, Tableau placeholder/wireframe, limitations/v2
    readme_text = readme_path.read_text(encoding="utf-8")
    for required_readme in (
        "actions/workflows/ci.yml/badge.svg",
        "Data Sources & Acquisition",
        "Standby Opportunity Index Formula",
        "Consistency vs. Ground Truth Caveat",
        "BigQuery Architecture & Partitioning Optimization",
        "bq query --dry_run",
        "Tableau Public Dashboard",
        "Assumptions, Limitations & `v2` Roadmap",
    ):
        assert required_readme in readme_text, f"Missing {required_readme!r} in README.md"


def test_default_output_dirs_never_target_committed_exports(
    sample_config: StandbyConfig,
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """`--sample` and `--flight-mode full` default to gitignored dirs, not `exports/tableau/`."""
    full_cfg = load_config(repo_root=repo_root)
    assert _resolve_default_output_dir(full_cfg, "stratified") == full_cfg.exports_dir
    assert _resolve_default_output_dir(full_cfg, "recent-12m") == full_cfg.exports_dir
    assert _resolve_default_output_dir(full_cfg, "full") == full_cfg.exports_dir / "full"
    for mode in ("stratified", "recent-12m", "full"):
        assert _resolve_default_output_dir(sample_config, mode) == (
            sample_config.processed_dir / "sample" / "tableau"
        )

    # End to end with no `output_dir`: the (isolated) exports dir is never created or touched.
    isolated_cfg = dataclasses.replace(sample_config, exports_dir=tmp_path / "exports" / "tableau")
    res = export_tableau_extracts(config=isolated_cfg, from_processed=False)
    assert res.output_dir == (isolated_cfg.processed_dir / "sample" / "tableau").resolve()
    assert (res.output_dir / "fct_flights_tableau.csv").is_file()
    assert not isolated_cfg.exports_dir.exists()


def test_failed_validation_leaves_existing_exports_untouched(
    sample_config: StandbyConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing invariant must not overwrite existing extracts or their metadata."""
    out_dir = tmp_path / "existing"
    out_dir.mkdir()
    previous = {
        "fct_flights_tableau.csv": "flight_id\nprevious-run\n",
        "agg_route_month.csv": "route_month_id\nprevious-run\n",
        "export_metadata.json": '{"invariants_passed": true}\n',
    }
    for name, text in previous.items():
        (out_dir / name).write_text(text, encoding="utf-8")

    def _always_fail(**_kwargs: object) -> None:
        raise ExportValidationError("forced failure")

    monkeypatch.setattr("standby.export.tableau._validate_exported_tables", _always_fail)
    with pytest.raises(ExportValidationError, match="forced failure"):
        export_tableau_extracts(config=sample_config, output_dir=out_dir, from_processed=False)

    assert sorted(p.name for p in out_dir.iterdir()) == sorted(previous)
    for name, text in previous.items():
        assert (out_dir / name).read_text(encoding="utf-8") == text


def test_max_flight_rows_must_be_positive(
    sample_config: StandbyConfig,
    tmp_path: Path,
) -> None:
    """A zero/negative row limit is rejected the same way for every sampled mode."""
    tiny = pd.DataFrame(
        {
            "flight_id": ["a"],
            "flight_date": ["2024-01-01"],
            "crs_dep_time": ["0800"],
            "year": [2024],
            "month": [1],
            "op_carrier": ["UA"],
        }
    )
    with pytest.raises(ValueError, match="max_rows must be positive"):
        _stratified_sample_flights(tiny, max_rows=0, seed=42)
    with pytest.raises(ValueError, match="max_rows must be positive"):
        _select_flights_for_export(
            tiny, flight_mode="recent-12m", max_flight_rows=0, stratify_seed=42
        )

    for mode in ("stratified", "recent-12m"):
        with pytest.raises(ValueError, match="max_flight_rows must be >= 1"):
            export_tableau_extracts(
                config=sample_config,
                output_dir=tmp_path / mode,
                flight_mode=mode,
                max_flight_rows=0,
            )

    res = CliRunner().invoke(
        main,
        ["export-tableau", "--sample", "--max-flight-rows", "0", "--output-dir", str(tmp_path)],
    )
    assert res.exit_code == 2
    assert "--max-flight-rows" in res.output
