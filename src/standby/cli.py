"""Command-line interface (`python -m standby`) for United Standby Opportunity Analytics."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from standby.config import StandbyConfig, load_config, parse_years_arg
from standby.ingest import ingest_all_sources
from standby.load import load_all_raw_tables
from standby.logging_config import configure_logging
from standby.manifest import build_manifest, verify_manifest
from standby.schemas import validate_all_ingested

logger = logging.getLogger("standby.cli")


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
        ingested["otp_marketing_flights"] = enrichment_result.enriched_flights
        fm = enrichment_result.faa_match_metrics
        sr = enrichment_result.sanity_report
        logger.info(
            "Enrichment metrics & sanity summary",
            extra={
                "extra_fields": {
                    "raw_faa_unmatched_rate_non_null": fm.raw_faa_master_plus_dereg_commercial_unmatched_rate_non_null,
                    "post_override_unmatched_performed": fm.post_override_unmatched_performed,
                    "sanity_passed": sr.passed,
                    "sanity_warnings": sr.warnings_count,
                }
            },
        )
        enrichment_summary_msg = (
            f", enrichment_unmatched_performed={fm.post_override_unmatched_performed}, "
            f"sanity_warnings={sr.warnings_count}"
        )

    # 5. Batch-load standardized/enriched tables to BigQuery raw dataset (or local Parquet in dry-run)
    load_results = load_all_raw_tables(tables=ingested, config=cfg)

    click.echo(
        f"PIPELINE RUN SUCCEEDED: manifest_files={manifest_report.verified_entries}, "
        f"validated_tables={validated_counts}, loaded_tables={len(load_results)}"
        f"{enrichment_summary_msg}"
    )
