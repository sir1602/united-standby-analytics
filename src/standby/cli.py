"""Command-line interface (`python -m standby`) for United Standby Opportunity Analytics."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

from standby.config import StandbyConfig, load_config, parse_years_arg
from standby.ingest import ingest_all_sources
from standby.load import load_all_raw_tables
from standby.logging_config import configure_logging
from standby.manifest import build_manifest, verify_manifest
from standby.schemas import validate_all_ingested

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger("standby.cli")

PROCESSED_OTP_FILE = "otp_marketing_flights.parquet"
PROCESSED_T100_FILE = "t100_segments.parquet"
PROCESSED_TSA_FILE = "tsa_daily_throughput.parquet"


def _validate_years_option(
    ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> str | None:
    """Validate `--years` syntax cleanly via Click so invalid expressions exit with code 2."""
    if value is None:
        return None
    try:
        parse_years_arg(value, default_years=[2019])
    except ValueError as exc:
        raise click.BadParameter(str(exc), ctx=ctx, param=param) from exc
    return value


def _load_cli_config(
    hub: str | None,
    years: str | None,
    sample_mode: bool,
    dry_run: bool,
) -> StandbyConfig:
    """Load `StandbyConfig` for CLI commands, translating invalid `--years` `ValueError` to `BadParameter`."""
    try:
        return load_config(hub=hub, years=years, sample_mode=sample_mode, dry_run=dry_run)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="'--years'") from exc


def _processed_inputs_exist(mode_dir: Path) -> bool:
    return all(
        (mode_dir / name).is_file()
        for name in (PROCESSED_OTP_FILE, PROCESSED_T100_FILE, PROCESSED_TSA_FILE)
    )


@dataclass(frozen=True)
class _ProcessedInputs:
    """Processed Parquet inputs scoped to the requested `--hub` / `--years`."""

    flights: pd.DataFrame
    t100: pd.DataFrame
    tsa: pd.DataFrame
    out_of_scope_flights: int
    file_hubs: list[str]
    file_years: list[int]
    absent_years: list[int]


def _read_processed_inputs(cfg: StandbyConfig, mode_dir: Path) -> _ProcessedInputs:
    """
    Read processed Parquets and scope them to `cfg.active_hub` and `cfg.years`, mirroring
    `ingest_all_sources` (OTP & T-100: `origin == hub` and `year in years`; TSA: `year in years`).
    """
    import pandas as pd

    flights_all = pd.read_parquet(mode_dir / PROCESSED_OTP_FILE)
    t100_all = pd.read_parquet(mode_dir / PROCESSED_T100_FILE)
    tsa_all = pd.read_parquet(mode_dir / PROCESSED_TSA_FILE)

    hub = cfg.active_hub.strip().upper()
    years = {int(y) for y in cfg.years}

    def _hub_year_mask(df: pd.DataFrame) -> pd.Series:
        return df["origin"].astype(str).str.strip().str.upper().eq(hub) & df["year"].astype(
            "int64"
        ).isin(years)

    flight_mask = _hub_year_mask(flights_all)
    tsa_years = pd.to_datetime(tsa_all["date_day"], errors="coerce").dt.year

    flights = flights_all.loc[flight_mask].reset_index(drop=True)
    present_years = {int(y) for y in flights["year"].unique()}
    return _ProcessedInputs(
        flights=flights,
        t100=t100_all.loc[_hub_year_mask(t100_all)].reset_index(drop=True),
        tsa=tsa_all.loc[tsa_years.isin(years)].reset_index(drop=True),
        out_of_scope_flights=int((~flight_mask).sum()),
        file_hubs=sorted(flights_all["origin"].astype(str).str.strip().str.upper().unique()),
        file_years=sorted(int(y) for y in flights_all["year"].unique()),
        absent_years=sorted(years - present_years),
    )


@click.group()
@click.option("--log-level", default="INFO", show_default=True, help="Logging verbosity level.")
@click.option("--json-logs", is_flag=True, default=False, help="Emit structured JSON log lines.")
def main(log_level: str, json_logs: bool) -> None:
    """United Airlines Standby Opportunity Analytics CLI."""
    configure_logging(level=log_level, json_output=json_logs)


@main.command("verify-manifest")
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("data/manifest.csv"),
    show_default=True,
    help="Path to manifest CSV file to verify.",
)
@click.option(
    "--subset-glob",
    default=None,
    help="Optional glob pattern to filter manifest entries.",
)
@click.option(
    "--repo-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Optional repository root path (defaults to inferring from --manifest).",
)
def verify_manifest_cmd(
    manifest_path: Path,
    subset_glob: str | None,
    repo_root: Path | None,
) -> None:
    """Verify SHA-256 checksums and data row counts for all files in a manifest."""
    report = verify_manifest(
        manifest_path=manifest_path,
        repo_root=repo_root,
        subset_glob=subset_glob,
    )
    click.echo(
        f"MANIFEST VERIFIED: {report.verified_entries}/{report.total_entries} files passed ({manifest_path})"
    )


@main.command("build-manifest")
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("data/raw"),
    show_default=True,
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("data/manifest.csv"),
    show_default=True,
)
def build_manifest_cmd(data_dir: Path, output_path: Path) -> None:
    """Build or refresh a checksum manifest for `data/raw` or `data/sample`."""
    cfg = load_config()
    entries = build_manifest(
        data_dir=data_dir.resolve(),
        output_manifest_path=output_path.resolve(),
        repo_root=cfg.repo_root,
    )
    click.echo(f"Built manifest with {len(entries)} entries at {output_path}")


@main.command("ingest")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to ingest (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option(
    "--sample", is_flag=True, default=False, help="Ingest from data/sample/ instead of data/raw/."
)
def ingest_cmd(hub: str | None, years: str | None, sample: bool) -> None:
    """Verify manifest, ingest all sources, and run Pandera schema validation."""
    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=True)
    verify_manifest(cfg.active_manifest_path, repo_root=cfg.repo_root)
    ingested = ingest_all_sources(cfg)
    counts = validate_all_ingested(ingested, config=cfg)
    click.echo(f"INGEST & PANDERA VALIDATION PASSED: {counts}")


@main.command("load-raw")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to load (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option("--sample", is_flag=True, default=False, help="Load from data/sample/ for fast CI.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Skip remote BigQuery load and write local Parquet artifacts.",
)
def load_raw_cmd(hub: str | None, years: str | None, sample: bool, dry_run: bool) -> None:
    """Verify manifest, ingest & validate raw sources, and batch-load to BigQuery `raw` dataset."""
    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=dry_run)
    manifest_report = verify_manifest(cfg.active_manifest_path, repo_root=cfg.repo_root)
    ingested = ingest_all_sources(cfg)
    validated_counts = validate_all_ingested(ingested, config=cfg)
    load_results = load_all_raw_tables(tables=ingested, config=cfg)
    click.echo(
        f"RAW LOAD SUCCEEDED: manifest_files={manifest_report.verified_entries}, "
        f"validated_tables={validated_counts}, loaded_tables={len(load_results)}"
    )


@main.command("enrich")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to process (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option("--sample", is_flag=True, default=False, help="Run against data/sample/ for fast CI.")
@click.option(
    "--summary-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional path to write Phase 2 enrichment summary JSON.",
)
def enrich_cmd(
    hub: str | None,
    years: str | None,
    sample: bool,
    summary_path: Path | None,
) -> None:
    """Verify manifest, ingest & validate sources, and run Phase 2 enrichment + sanity checks."""
    from standby.enrichment.pipeline import run_enrichment_pipeline

    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=True)
    verify_manifest(cfg.active_manifest_path, repo_root=cfg.repo_root)
    ingested = ingest_all_sources(cfg)
    validate_all_ingested(ingested, config=cfg)
    enrichment_result = run_enrichment_pipeline(
        ingested=ingested,
        config=cfg,
        summary_path=summary_path,
    )
    fm = enrichment_result.faa_match_metrics
    sr = enrichment_result.sanity_report
    click.echo(
        f"ENRICHMENT PASSED: flights={len(enrichment_result.enriched_flights):,}, "
        f"raw_faa_unmatched={fm.raw_faa_master_plus_dereg_commercial_unmatched_rate_non_null:.4%}, "
        f"post_override_unmatched_performed={fm.post_override_unmatched_performed}, "
        f"sanity_warnings={sr.warnings_count}, sanity_failures={sr.failures_count}"
    )


@main.command("run")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to process (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option("--sample", is_flag=True, default=False, help="Run against data/sample/ for fast CI.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Skip remote BigQuery load and write local Parquet artifacts.",
)
@click.option(
    "--summary-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional path to write Phase 2 enrichment summary JSON.",
)
def run_cmd(
    hub: str | None,
    years: str | None,
    sample: bool,
    dry_run: bool,
    summary_path: Path | None,
) -> None:
    """End-to-end pipeline entry point: manifest gate -> ingest -> validate -> enrich -> load."""
    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=dry_run)
    logger.info(
        f"Starting pipeline run (hub={cfg.active_hub}, years={cfg.years}, sample={cfg.sample_mode}, dry_run={cfg.dry_run})"
    )

    # 1. Verify checksum manifest before running
    manifest_report = verify_manifest(cfg.active_manifest_path, repo_root=cfg.repo_root)

    # 2. Ingest & standardize all 5 aviation data sources
    ingested = ingest_all_sources(cfg)

    # 3. Validate all ingested DataFrames against Pandera schemas
    validated_counts = validate_all_ingested(ingested, config=cfg)

    # 4. Run Phase 2 flight-level enrichment & sanity checks if reference tables are present
    enrichment_summary_msg = ""
    enrichment_pkg = Path(__file__).parent / "enrichment" / "pipeline.py"
    if enrichment_pkg.is_file() and (cfg.reference_dir / "seat_config.csv").is_file():
        from standby.enrichment.pipeline import run_enrichment_pipeline

        enrichment_result = run_enrichment_pipeline(
            ingested=ingested,
            config=cfg,
            summary_path=summary_path,
        )

        from standby.scoring import score_flights
        from standby.validation.sanity import (
            SanityCheckError,
            check_sc5_missing_t100_null_invariant,
            check_soi_bounds,
        )

        scored_df = score_flights(
            enriched_df=enrichment_result.enriched_flights,
            t100_df=ingested["t100_segments"],
            tsa_df=ingested["tsa_daily_throughput"],
            config=cfg,
        )
        post_sc5 = check_sc5_missing_t100_null_invariant(scored_df)
        post_bounds = check_soi_bounds(scored_df)
        if post_sc5.status == "FAIL":
            raise SanityCheckError(f"Post-SOI SC-5 failed: {post_sc5.message}")
        if post_bounds.status == "FAIL":
            raise SanityCheckError(f"Post-SOI bounds failed: {post_bounds.message}")

        ingested["otp_marketing_flights"] = scored_df
        fm = enrichment_result.faa_match_metrics
        sr = enrichment_result.sanity_report
        logger.info(
            "Enrichment & Phase 4 SOI scoring summary",
            extra={
                "extra_fields": {
                    "raw_faa_unmatched_rate_non_null": fm.raw_faa_master_plus_dereg_commercial_unmatched_rate_non_null,
                    "post_override_unmatched_performed": fm.post_override_unmatched_performed,
                    "sanity_passed": sr.passed,
                    "sanity_warnings": sr.warnings_count,
                    "soi_historical_scored": int(scored_df["soi_historical"].notna().sum()),
                    "soi_planning_scored": int(scored_df["soi_planning"].notna().sum()),
                }
            },
        )
        enrichment_summary_msg = (
            f", enrichment_unmatched_performed={fm.post_override_unmatched_performed}, "
            f"sanity_warnings={sr.warnings_count}, "
            f"soi_hist_scored={int(scored_df['soi_historical'].notna().sum())}, "
            f"soi_plan_scored={int(scored_df['soi_planning'].notna().sum())}"
        )

    # 5. Batch-load standardized/enriched/scored tables to BigQuery raw dataset (or local Parquet in dry-run)
    load_results = load_all_raw_tables(tables=ingested, config=cfg)

    click.echo(
        f"PIPELINE RUN SUCCEEDED: manifest_files={manifest_report.verified_entries}, "
        f"validated_tables={validated_counts}, loaded_tables={len(load_results)}"
        f"{enrichment_summary_msg}"
    )


@main.command("score")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to score (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option("--sample", is_flag=True, default=False, help="Score data/sample/ for fast CI.")
@click.option(
    "--from-processed/--recompute",
    default=True,
    show_default=True,
    help="Read existing processed Parquets when present, or recompute from raw sources.",
)
@click.option(
    "--summary-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Optional path to write JSON scoring summary.",
)
def score_cmd(
    hub: str | None,
    years: str | None,
    sample: bool,
    from_processed: bool,
    summary_path: Path | None,
) -> None:
    """Compute Phase 4 `soi_historical` and `soi_planning` and write scored Parquet."""
    import json

    from standby.load.bigquery import prepare_dataframe_for_bigquery
    from standby.scoring import score_flights
    from standby.validation.sanity import (
        SanityCheckError,
        check_sc5_missing_t100_null_invariant,
        check_soi_bounds,
    )

    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=True)
    mode_dir = cfg.processed_dir / ("sample" if cfg.sample_mode else "full")
    otp_pq = mode_dir / PROCESSED_OTP_FILE

    if from_processed and _processed_inputs_exist(mode_dir):
        inputs = _read_processed_inputs(cfg, mode_dir)
        if inputs.out_of_scope_flights:
            # Scoring writes back to `otp_pq`, so silently dropping out-of-scope rows would lose data.
            raise click.ClickException(
                f"{otp_pq} holds {inputs.out_of_scope_flights:,} flights outside "
                f"--hub {cfg.active_hub} --years {cfg.years} (file covers hubs {inputs.file_hubs}, "
                f"years {inputs.file_years}). Pass a matching --hub/--years, or use --recompute "
                "to rebuild from raw sources."
            )
        if inputs.flights.empty:
            raise click.ClickException(
                f"{otp_pq} has no flights for --hub {cfg.active_hub} --years {cfg.years}."
            )
        enriched_df = inputs.flights
        t100_df = inputs.t100
        tsa_df = inputs.tsa
    else:
        from standby.enrichment.pipeline import run_enrichment_pipeline

        verify_manifest(cfg.active_manifest_path, repo_root=cfg.repo_root)
        ingested = ingest_all_sources(cfg)
        validate_all_ingested(ingested, config=cfg)
        enrichment_result = run_enrichment_pipeline(ingested=ingested, config=cfg)
        enriched_df = enrichment_result.enriched_flights
        t100_df = ingested["t100_segments"]
        tsa_df = ingested["tsa_daily_throughput"]

    scored_df = score_flights(
        enriched_df=enriched_df,
        t100_df=t100_df,
        tsa_df=tsa_df,
        config=cfg,
    )
    sc5 = check_sc5_missing_t100_null_invariant(scored_df)
    bounds = check_soi_bounds(scored_df)
    if sc5.status == "FAIL":
        raise SanityCheckError(f"SC-5 failed after scoring: {sc5.message}")
    if bounds.status == "FAIL":
        raise SanityCheckError(f"SOI bounds failed after scoring: {bounds.message}")

    mode_dir.mkdir(parents=True, exist_ok=True)
    prepared = prepare_dataframe_for_bigquery(scored_df, "otp_marketing_flights")
    prepared.to_parquet(otp_pq, index=False)

    hist_cnt = int(scored_df["soi_historical"].notna().sum())
    plan_cnt = int(scored_df["soi_planning"].notna().sum())
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "total_flights": int(len(scored_df)),
                    "soi_historical_scored": hist_cnt,
                    "soi_planning_scored": plan_cnt,
                    "sc5": sc5.to_dict(),
                    "bounds": bounds.to_dict(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    click.echo(
        f"SCORING PASSED: flights={len(scored_df):,}, "
        f"soi_historical_scored={hist_cnt:,}, soi_planning_scored={plan_cnt:,}, "
        f"output={otp_pq}"
    )


@main.command("validate-index")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to validate (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option("--sample", is_flag=True, default=False, help="Validate data/sample/ for fast CI.")
@click.option(
    "--summary-path",
    "--output",
    "summary_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to write Phase 4 validation JSON summary.",
)
@click.option(
    "--reuse-scores",
    is_flag=True,
    default=False,
    help=(
        "Validate SOI scores already stored in the processed Parquet instead of re-scoring "
        "with the current config (faster, but scores may be stale)."
    ),
)
def validate_index_cmd(
    hub: str | None,
    years: str | None,
    sample: bool,
    summary_path: Path | None,
    reuse_scores: bool,
) -> None:
    """Run Section 6 validation suite (backtest, face-validity, weight sensitivity, SC-5)."""
    from standby.scoring import SOI_OUTPUT_COLUMNS
    from standby.validation.soi_validation import run_soi_validation

    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=True)
    mode_dir = cfg.processed_dir / ("sample" if cfg.sample_mode else "full")
    resolved_summary = summary_path or (
        mode_dir / "phase4_validation_summary.json"
        if cfg.sample_mode
        else cfg.repo_root / "docs" / "phase4_validation_summary.json"
    )

    if _processed_inputs_exist(mode_dir):
        inputs = _read_processed_inputs(cfg, mode_dir)
        if inputs.flights.empty:
            raise click.ClickException(
                f"{mode_dir / PROCESSED_OTP_FILE} has no flights for --hub {cfg.active_hub} "
                f"--years {cfg.years} (file covers hubs {inputs.file_hubs}, "
                f"years {inputs.file_years})."
            )
        if inputs.out_of_scope_flights:
            logger.info(
                f"Excluded {inputs.out_of_scope_flights:,} processed flights outside "
                f"hub={cfg.active_hub} years={cfg.years}"
            )
        if inputs.absent_years:
            logger.warning(
                f"Requested years {inputs.absent_years} have no flights in the processed Parquet; "
                "re-run `standby run` for those years to include them"
            )
        flights_df = inputs.flights
        t100_df = inputs.t100
        tsa_df = inputs.tsa
        if not reuse_scores:
            # Drop stored SOI outputs so `run_soi_validation` re-scores the scoped flights with the
            # current config (stored scores may come from other years, hubs, or weights).
            flights_df = flights_df.drop(
                columns=[c for c in SOI_OUTPUT_COLUMNS if c in flights_df.columns]
            )
    else:
        from standby.enrichment.pipeline import run_enrichment_pipeline
        from standby.scoring import score_flights

        verify_manifest(cfg.active_manifest_path, repo_root=cfg.repo_root)
        ingested = ingest_all_sources(cfg)
        validate_all_ingested(ingested, config=cfg)
        enrichment_result = run_enrichment_pipeline(ingested=ingested, config=cfg)
        t100_df = ingested["t100_segments"]
        tsa_df = ingested["tsa_daily_throughput"]
        flights_df = score_flights(
            enriched_df=enrichment_result.enriched_flights,
            t100_df=t100_df,
            tsa_df=tsa_df,
            config=cfg,
        )

    report = run_soi_validation(
        flights_df=flights_df,
        t100_df=t100_df,
        tsa_df=tsa_df,
        config=cfg,
        sample_mode=cfg.sample_mode,
        summary_path=resolved_summary,
        raise_on_failure=True,
    )
    holdout_rho = report.pillar_1_time_based_backtest["fixed_holdout"].get("spearman_rho")
    wf_rho = report.pillar_1_time_based_backtest["expanding_walk_forward"]["combined"].get(
        "spearman_rho"
    )
    click.echo(
        f"SOI VALIDATION PASSED: flights={report.metadata['total_flights']:,}, "
        f"sample_mode={report.metadata['sample_mode']}, "
        f"holdout_rho={holdout_rho}, walk_forward_rho={wf_rho}, "
        f"summary={resolved_summary}"
    )


@main.command("export-tableau")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to export (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option(
    "--sample",
    is_flag=True,
    default=False,
    help="Export data/sample/ for fast CI (writes to gitignored `data/processed/sample/tableau/`).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Output directory for Tableau CSV extracts (defaults to `exports/tableau`; "
        "`--sample` runs default to `data/processed/sample/tableau` and `--flight-mode full` "
        "runs to `exports/tableau/full`, both gitignored)."
    ),
)
@click.option(
    "--flight-mode",
    type=click.Choice(["stratified", "recent-12m", "full"], case_sensitive=False),
    default=None,
    help="Flight extract row selection mode (defaults to config `export.tableau_flight_mode`).",
)
@click.option(
    "--max-flight-rows",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum rows for stratified/recent-12m `fct_flights_tableau.csv` (defaults to 100,000).",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Deterministic random seed for stratified sampling (defaults to 42).",
)
@click.option(
    "--from-processed/--recompute",
    default=True,
    show_default=True,
    help="Read existing processed Parquets when present, or recompute from raw sources.",
)
def export_tableau_cmd(
    hub: str | None,
    years: str | None,
    sample: bool,
    output_dir: Path | None,
    flight_mode: str | None,
    max_flight_rows: int | None,
    seed: int | None,
    from_processed: bool,
) -> None:
    """Export mart-aligned CSV extracts for Tableau Public and validate post-export invariants."""
    from standby.export.tableau import export_tableau_extracts

    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=True)
    result = export_tableau_extracts(
        config=cfg,
        output_dir=output_dir,
        flight_mode=flight_mode,
        max_flight_rows=max_flight_rows,
        stratify_seed=seed,
        from_processed=from_processed,
    )
    total_bytes = sum(info.bytes for info in result.files.values())
    click.echo(
        f"TABLEAU EXPORT SUCCEEDED: files={len(result.files)}, "
        f"flight_mode={result.flight_mode}, exported_flights={result.exported_flights:,}/"
        f"{result.total_source_flights:,}, total_mb={total_bytes / (1024 * 1024):.2f}, "
        f"output_dir={result.output_dir}"
    )


@main.command("export-looker")
@click.option(
    "--hub",
    default=None,
    help="Origin hub IATA code (defaults to `config/standby.yaml` default_hub).",
)
@click.option(
    "--years",
    default=None,
    callback=_validate_years_option,
    help="Years to export (comma/range syntax, e.g. 2019,2023-2026; defaults to YAML config).",
)
@click.option(
    "--sample",
    is_flag=True,
    default=False,
    help="Export data/sample/ for fast CI (writes to gitignored `data/processed/sample/looker/`).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Output directory for Looker Studio CSV extracts (defaults to `exports/looker`; "
        "`--sample` runs default to `data/processed/sample/looker` and `--flight-mode full` "
        "runs to `exports/looker/full`, both gitignored)."
    ),
)
@click.option(
    "--flight-mode",
    type=click.Choice(["stratified", "recent-12m", "full"], case_sensitive=False),
    default=None,
    help="Flight extract row selection mode (defaults to config `export.tableau_flight_mode`).",
)
@click.option(
    "--max-flight-rows",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum rows for stratified/recent-12m `looker_flights.csv` (defaults to 100,000).",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Deterministic random seed for stratified sampling (defaults to 42).",
)
@click.option(
    "--from-processed/--recompute",
    default=True,
    show_default=True,
    help="Read existing processed Parquets (or verified Tableau extracts) when present, or recompute from raw sources.",
)
def export_looker_cmd(
    hub: str | None,
    years: str | None,
    sample: bool,
    output_dir: Path | None,
    flight_mode: str | None,
    max_flight_rows: int | None,
    seed: int | None,
    from_processed: bool,
) -> None:
    """Export pre-joined (denormalized) CSV extracts for Google Looker Studio and validate invariants."""
    from standby.export.looker import export_looker_extracts

    cfg = _load_cli_config(hub=hub, years=years, sample_mode=sample, dry_run=True)
    result = export_looker_extracts(
        config=cfg,
        output_dir=output_dir,
        flight_mode=flight_mode,
        max_flight_rows=max_flight_rows,
        stratify_seed=seed,
        from_processed=from_processed,
    )
    total_bytes = sum(info.bytes for info in result.files.values())
    click.echo(
        f"LOOKER EXPORT SUCCEEDED: files={len(result.files)}, "
        f"flight_mode={result.flight_mode}, exported_flights={result.exported_flights:,}/"
        f"{result.total_source_flights:,}, total_mb={total_bytes / (1024 * 1024):.2f}, "
        f"output_dir={result.output_dir}"
    )
