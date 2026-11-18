"""Run the real dbt project on DuckDB (the free local target) and check it against the Python pipeline.

Unlike the hand-written SQLite harness in `test_dbt_project.py`, this executes the actual model
SQL via `dbt build --target duckdb`, so formula drift between dbt and Python fails here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from pandas.api.types import is_bool_dtype, is_numeric_dtype

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("dbt.adapters.duckdb")

from standby.config import load_config  # noqa: E402
from standby.enrichment.pipeline import run_enrichment_pipeline  # noqa: E402
from standby.ingest import ingest_all_sources  # noqa: E402
from standby.load import load_all_raw_tables  # noqa: E402
from standby.scoring import score_flights  # noqa: E402
from standby.scoring.holidays import holiday_name  # noqa: E402

FLIGHT_KEYS = ["flight_date", "mkt_carrier", "flight_num_mkt", "origin", "dest", "crs_dep_time"]


def _dbt(repo_root: Path, tmp: Path, *args: str, db_path: Path) -> subprocess.CompletedProcess:
    """Invoke the venv's dbt CLI against the DuckDB target with isolated target/log paths."""
    dbt_bin = Path(sys.executable).with_name("dbt")
    env = {**os.environ, "STANDBY_DUCKDB_PATH": str(db_path)}
    return subprocess.run(
        [
            str(dbt_bin),
            *args,
            "--project-dir",
            str(repo_root / "dbt"),
            "--profiles-dir",
            str(repo_root / "dbt"),
            "--target",
            "duckdb",
            "--target-path",
            str(tmp / "target"),
            "--log-path",
            str(tmp / "logs"),
            "--no-send-anonymous-usage-stats",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture(scope="module")
def duckdb_sample_build(repo_root: Path, tmp_path_factory) -> dict[str, object]:
    """Write sample dry-run Parquet exactly like `standby run --sample --dry-run`, then dbt build."""
    tmp = tmp_path_factory.mktemp("dbt_duckdb")
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    cfg.processed_dir = tmp / "processed"

    ingested = ingest_all_sources(cfg)
    enrichment = run_enrichment_pipeline(ingested=ingested, config=cfg)
    scored = score_flights(
        enriched_df=enrichment.enriched_flights,
        t100_df=ingested["t100_segments"],
        tsa_df=ingested["tsa_daily_throughput"],
        config=cfg,
    )
    ingested["otp_marketing_flights"] = scored
    load_all_raw_tables(tables=ingested, config=cfg)
    parquet_dir = cfg.processed_dir / "sample"

    db_path = tmp / "sample.duckdb"
    proc = _dbt(
        repo_root,
        tmp,
        "build",
        "--vars",
        f"{{raw_parquet_dir: '{parquet_dir.as_posix()}'}}",
        db_path=db_path,
    )
    return {"proc": proc, "db_path": db_path, "scored": scored, "tmp": tmp}


def test_dbt_build_on_duckdb_passes_all_models_and_tests(duckdb_sample_build) -> None:
    """Every seed, model and data test in the dbt project passes on DuckDB."""
    proc = duckdb_sample_build["proc"]
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "ERROR=0" in proc.stdout and "SKIP=0" in proc.stdout


def test_duckdb_fct_flights_matches_python_pipeline(duckdb_sample_build) -> None:
    """dbt `fct_flights` on DuckDB equals the Python enrichment + SOI output on every shared column."""
    assert duckdb_sample_build["proc"].returncode == 0
    con = duckdb.connect(str(duckdb_sample_build["db_path"]), read_only=True)
    try:
        fct = con.execute("select * from marts.fct_flights").df()
    finally:
        con.close()
    py = duckdb_sample_build["scored"].copy()
    for key in FLIGHT_KEYS:
        fct[key] = fct[key].astype(str).str.slice(0, 10 if key == "flight_date" else None)
        py[key] = py[key].astype(str).str.slice(0, 10 if key == "flight_date" else None)

    merged = fct.merge(py, on=FLIGHT_KEYS, suffixes=("_dbt", "_py"), how="inner")
    assert len(fct) == len(py) == len(merged) == 80

    shared = [c for c in fct.columns if c in py.columns and c not in FLIGHT_KEYS]
    # The comparison must cover the headline outputs, not just pass-through columns.
    for col in ("seats_config", "est_passengers", "est_open_seats_adjusted", "soi_historical"):
        assert col in shared
        assert merged[f"{col}_dbt"].notna().any()
    assert "soi_planning" in shared and merged["soi_planning_dbt"].notna().any()

    mismatches: list[str] = []
    for col in shared:
        a, b = merged[f"{col}_dbt"], merged[f"{col}_py"]
        null_mismatch = int((a.isna().to_numpy() != b.isna().to_numpy()).sum())
        both = a.notna().to_numpy() & b.notna().to_numpy()
        if (
            is_numeric_dtype(a)
            and is_numeric_dtype(b)
            and not is_bool_dtype(a)
            and not is_bool_dtype(b)
        ):
            diff = np.abs(a.to_numpy(float)[both] - b.to_numpy(float)[both])
            worst = float(diff.max()) if both.any() else 0.0
            if null_mismatch or worst > 1e-6:
                mismatches.append(f"{col}: null_mismatch={null_mismatch} max_abs_diff={worst}")
        else:
            as_text = a.map(lambda v: str(bool(v)) if isinstance(v, (bool, np.bool_)) else str(v))
            bs_text = b.map(lambda v: str(bool(v)) if isinstance(v, (bool, np.bool_)) else str(v))
            differing = int((as_text[both] != bs_text[both]).sum())
            if null_mismatch or differing:
                mismatches.append(f"{col}: null_mismatch={null_mismatch} differing={differing}")
    assert not mismatches, "\n".join(mismatches)


def test_duckdb_shims_keep_bigquery_null_semantics(duckdb_sample_build) -> None:
    """DuckDB's own greatest/least skip NULLs; the registered shims must propagate them like BigQuery."""
    assert duckdb_sample_build["proc"].returncode == 0
    con = duckdb.connect(str(duckdb_sample_build["db_path"]), read_only=True)
    try:
        row = con.execute(
            """
            select
                least(1.0, greatest(0.0, safe_divide(5, nullif(0, 0)))) as plain_duckdb_clip,
                bq_least(1.0, bq_greatest(0.0, safe_divide(5, nullif(0, 0)))) as bq_clip,
                bq_greatest(0.2, 0.7) as bq_greatest_value,
                bq_least(0.2, 0.7) as bq_least_value,
                safe_divide(3, 0) as divide_by_zero,
                safe_divide(3, 2) as divide,
                logical_or(x) as any_true,
                format_date('%Y-%m', date '2024-03-05') as ym,
                safe.parse_date('%Y-%m-%d', 'not-a-date') as bad_date,
                regexp_contains('N123UA', '^N[1-9][0-9A-Z]{0,4}$') as valid_tail,
                typeof(cast(1 as float64)) as float64_type
            from (values (false), (true)) as t(x)
            """
        ).fetchone()
    finally:
        con.close()
    plain_clip, bq_clip, g, lst, div0, div, any_true, ym, bad_date, valid_tail, ftype = row
    assert plain_clip == 0.0  # the trap the shims exist for: NULL silently becomes 0.0
    assert bq_clip is None
    assert (float(g), float(lst)) == (0.7, 0.2)
    assert div0 is None and div == 1.5
    assert any_true is True
    assert ym == "2024-03"
    assert bad_date is None
    assert valid_tail is True
    assert ftype == "DOUBLE"


def test_duckdb_holiday_macro_matches_python_calendar_2019_2026(
    repo_root: Path, tmp_path: Path
) -> None:
    """The ported holiday macro (date arithmetic + day-of-week) equals Python for every day 2019-2026."""
    sql = (
        "select d, {{ holiday_name_for_date('d') }} as holiday_name from ("
        "select cast(unnest(generate_series(date '2019-01-01', date '2026-12-31', interval 1 day))"
        " as date) as d)"
    )
    proc = _dbt(
        repo_root, tmp_path, "compile", "--quiet", "--inline", sql, db_path=tmp_path / "h.duckdb"
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    compiled = proc.stdout.split("Compiled inline node is:", 1)[-1].strip()

    rows = duckdb.connect().execute(compiled).fetchall()
    assert len(rows) == (date(2026, 12, 31) - date(2019, 1, 1)).days + 1
    assert {d for d, _ in rows} == {date(2019, 1, 1) + timedelta(days=i) for i in range(len(rows))}
    mismatches = [(d, h, holiday_name(d)) for d, h in rows if h != holiday_name(d)]
    assert not mismatches, mismatches[:10]
    names = {h for _, h in rows}
    assert {"Thanksgiving", "Memorial Day", "Labor Day", "Christmas / New Year"} <= names
