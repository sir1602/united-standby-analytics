"""Tests for BigQuery batch LoadJobConfig, `flight_date` DATE partitioning, and CLI commands."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner
from google.auth.exceptions import DefaultCredentialsError

from standby.cli import main
from standby.config import StandbyConfig, load_config, parse_years_arg
from standby.load.bigquery import (
    BigQueryCredentialsError,
    build_load_job_config,
    infer_bigquery_schema,
    load_dataframe_to_bigquery,
    prepare_dataframe_for_bigquery,
)
from standby.manifest import build_manifest


def test_bigquery_schema_and_load_job_config_uses_flight_date_partitioning(
    sample_ingested: dict[str, pd.DataFrame],
) -> None:
    """`otp_marketing_flights` maps `flight_date` to `DATE`, partitions on `flight_date`, and clusters cleanly."""
    otp_df = sample_ingested["otp_marketing_flights"]
    schema = infer_bigquery_schema(otp_df, "otp_marketing_flights")
    schema_map = {f.name: f.field_type for f in schema}
    assert schema_map["flight_date"] == "DATE"

    job_config = build_load_job_config(
        otp_df, "otp_marketing_flights", partition_expiration_ms=None
    )
    assert str(job_config.write_disposition) == "WRITE_TRUNCATE"
    assert job_config.time_partitioning is not None
    assert str(job_config.time_partitioning.type_) == "DAY"
    assert job_config.time_partitioning.field == "flight_date"
    assert job_config.time_partitioning.expiration_ms is None
    assert list(job_config.clustering_fields) == ["origin", "dest", "op_carrier"]


def test_load_dataframe_to_bigquery_dry_run_and_live_mock_consistency(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
) -> None:
    """Both `--dry-run` Parquet and live BigQuery LoadJob convert `flight_date` to `DATE` and partition on it."""
    otp_df = sample_ingested["otp_marketing_flights"]

    # 1. Dry-run Parquet output
    dry_res = load_dataframe_to_bigquery(otp_df, "otp_marketing_flights", sample_config)
    assert dry_res.mode == "dry_run"
    assert dry_res.partition_field == "flight_date"
    assert dry_res.clustering_fields == ["origin", "dest", "op_carrier"]
    assert dry_res.local_artifact_path is not None and dry_res.local_artifact_path.is_file()
    arrow_schema = pq.read_schema(dry_res.local_artifact_path)
    assert str(arrow_schema.field("flight_date").type) == "date32[day]"

    # 2. Live mock BigQuery Client (dry_run=False)
    live_cfg = load_config(repo_root=sample_config.repo_root, sample_mode=True, dry_run=False)
    live_cfg.processed_dir = sample_config.processed_dir
    mock_client = MagicMock()
    mock_job = MagicMock()
    mock_client.load_table_from_dataframe.return_value = mock_job

    live_res = load_dataframe_to_bigquery(
        otp_df,
        "otp_marketing_flights",
        live_cfg,
        client=mock_client,
    )
    assert live_res.mode == "bigquery_batch_load"
    assert live_res.partition_field == "flight_date"
    assert live_res.clustering_fields == ["origin", "dest", "op_carrier"]
    mock_client.create_dataset.assert_called_once()
    dataset_arg = mock_client.create_dataset.call_args.args[0]
    assert dataset_arg.default_table_expiration_ms is None
    assert dataset_arg.default_partition_expiration_ms is None
    loaded_df = mock_client.load_table_from_dataframe.call_args.args[0]
    assert isinstance(loaded_df["flight_date"].iloc[0], datetime.date)
    load_cfg = mock_client.load_table_from_dataframe.call_args.kwargs["job_config"]
    assert load_cfg.time_partitioning.field == "flight_date"
    mock_job.result.assert_called_once()


def test_load_dataframe_to_bigquery_no_credentials_fails_fast(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A live load without GCP credentials raises instead of writing Parquet and reporting success."""
    live_cfg = load_config(repo_root=sample_config.repo_root, sample_mode=True, dry_run=False)
    live_cfg.processed_dir = tmp_path / "processed"

    def _raise_no_creds(*args, **kwargs):
        raise DefaultCredentialsError("No ADC configured")

    monkeypatch.setattr("standby.load.bigquery.bigquery.Client", _raise_no_creds)
    with pytest.raises(BigQueryCredentialsError, match="--dry-run"):
        load_dataframe_to_bigquery(
            sample_ingested["otp_marketing_flights"],
            "otp_marketing_flights",
            live_cfg,
        )
    assert not list(live_cfg.processed_dir.rglob("*.parquet"))


def test_cli_verify_manifest_ingest_load_raw_and_run_sample(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """CLI commands `verify-manifest`, `ingest`, `load-raw`, and `run --sample --dry-run` all succeed."""
    runner = CliRunner()

    # Patch load_config in cli so `processed_dir` targets tmp_path rather than mutating repo workspace
    def _isolated_load_config(**kwargs):
        cfg = load_config(repo_root=repo_root, **kwargs)
        cfg.processed_dir = tmp_path / "processed"
        return cfg

    monkeypatch.setattr("standby.cli.load_config", _isolated_load_config)

    res_verify = runner.invoke(
        main,
        ["verify-manifest", "--manifest", str(repo_root / "data" / "sample" / "manifest.csv")],
    )
    assert res_verify.exit_code == 0, res_verify.output
    assert "MANIFEST VERIFIED: 13/13" in res_verify.output

    res_ingest = runner.invoke(main, ["ingest", "--sample"])
    assert res_ingest.exit_code == 0, res_ingest.output
    assert "INGEST & PANDERA VALIDATION PASSED" in res_ingest.output

    res_load_raw = runner.invoke(
        main,
        ["load-raw", "--hub", "ORD", "--years", "2019,2023-2026", "--sample", "--dry-run"],
    )
    assert res_load_raw.exit_code == 0, res_load_raw.output
    assert "RAW LOAD SUCCEEDED: manifest_files=13" in res_load_raw.output
    assert "loaded_tables=6" in res_load_raw.output

    res_enrich = runner.invoke(
        main,
        ["enrich", "--hub", "ORD", "--years", "2019,2023-2026", "--sample"],
    )
    assert res_enrich.exit_code == 0, res_enrich.output
    assert "ENRICHMENT PASSED: flights=80" in res_enrich.output
    assert "post_override_unmatched_performed=0" in res_enrich.output
    assert "sanity_failures=0" in res_enrich.output

    res_run = runner.invoke(
        main,
        ["run", "--hub", "ORD", "--years", "2019,2023-2026", "--sample", "--dry-run"],
    )
    assert res_run.exit_code == 0, res_run.output
    assert "PIPELINE RUN SUCCEEDED: manifest_files=13" in res_run.output
    assert "enrichment_unmatched_performed=0" in res_run.output
    otp_parquet = tmp_path / "processed" / "sample" / "otp_marketing_flights.parquet"
    assert otp_parquet.is_file()
    enriched_schema = pq.read_schema(otp_parquet)
    for col in (
        "aircraft_source",
        "fleet_subtype",
        "bts_aircraft_type",
        "seats_config",
        "t100_available",
        "lf_source",
        "est_load_factor",
        "est_passengers",
        "est_open_seats",
        "tsa_demand_multiplier",
        "hour_block_multiplier",
        "est_lf_adjusted",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
        "record_confidence_summary",
        "is_holiday_window",
        "holiday_name",
        "tsa_ratio_year",
        "prior_strict_scheduled_count",
        "weighted_displacement_hist",
        "remaining_scheduled_backups",
        "remaining_performed_backups",
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
        assert col in enriched_schema.names, f"Missing enriched/SOI column {col} in Parquet"
    assert len(enriched_schema.names) == 71
    assert str(enriched_schema.field("flight_date").type) == "date32[day]"

    res_score = runner.invoke(
        main,
        [
            "score",
            "--hub",
            "ORD",
            "--years",
            "2019,2023-2026",
            "--sample",
            "--from-processed",
        ],
    )
    assert res_score.exit_code == 0, res_score.output
    assert "SCORING PASSED: flights=80" in res_score.output

    val_summary = tmp_path / "sample_phase4_validation.json"
    res_validate = runner.invoke(
        main,
        [
            "validate-index",
            "--hub",
            "ORD",
            "--years",
            "2019,2023-2026",
            "--sample",
            "--summary-path",
            str(val_summary),
        ],
    )
    assert res_validate.exit_code == 0, res_validate.output
    assert "SOI VALIDATION PASSED: flights=80" in res_validate.output
    assert val_summary.is_file()


def test_parse_years_arg_and_load_config_rejects_invalid_and_empty_years(repo_root: Path) -> None:
    """`parse_years_arg` and `load_config` reject malformed, reversed, and empty year specs."""
    assert parse_years_arg(None, [2024, 2019]) == [2019, 2024]
    assert parse_years_arg("2023-2023", [2019]) == [2023]

    bad_inputs = (
        "2026-2023",
        ",,,",
        "   ",
        "",
        "-2023",
        "2023-",
        "2023-2024-2025",
        "abc",
        "2019,,2024",
    )
    for bad in bad_inputs:
        with pytest.raises(ValueError):
            parse_years_arg(bad, [2019])
        with pytest.raises(ValueError):
            load_config(repo_root=repo_root, years=bad)

    with pytest.raises(ValueError, match="must not be empty"):
        load_config(repo_root=repo_root, years=[])


@pytest.mark.parametrize(
    "cmd",
    [
        ["ingest", "--sample"],
        ["enrich", "--sample"],
        ["load-raw", "--sample", "--dry-run"],
        ["run", "--sample", "--dry-run"],
    ],
)
@pytest.mark.parametrize(
    "bad_years",
    ["2026-2023", ",,,", "   ", "", "-2023", "2023-", "2023-2024-2025", "abc"],
)
def test_cli_invalid_years_fails_with_bad_parameter_exit_code_2(
    cmd: list[str],
    bad_years: str,
) -> None:
    """CLI commands reject invalid `--years` expressions with Click exit_code=2."""
    runner = CliRunner()
    res = runner.invoke(main, [*cmd, "--years", bad_years])
    assert res.exit_code == 2, res.output
    assert "Invalid value for '--years'" in res.output


def test_cli_verify_manifest_external_path_and_repo_root(tmp_path: Path) -> None:
    """`verify-manifest` works on external manifests both via root inference and explicit `--repo-root`."""
    ext_root = tmp_path / "external_repo"
    otp_dir = ext_root / "data" / "sample" / "otp"
    otp_dir.mkdir(parents=True, exist_ok=True)
    (otp_dir / "otp_mkt_ord_ua_2019_01.csv").write_text("col_a,col_b\n1,2\n", encoding="utf-8")

    ext_manifest = ext_root / "data" / "sample" / "manifest.csv"
    build_manifest(
        data_dir=ext_root / "data" / "sample",
        output_manifest_path=ext_manifest,
        repo_root=ext_root,
        download_date="2026-11-13",
    )

    runner = CliRunner()
    res_inferred = runner.invoke(main, ["verify-manifest", "--manifest", str(ext_manifest)])
    assert res_inferred.exit_code == 0, res_inferred.output
    assert "MANIFEST VERIFIED: 1/1" in res_inferred.output

    res_explicit = runner.invoke(
        main,
        ["verify-manifest", "--manifest", str(ext_manifest), "--repo-root", str(ext_root)],
    )
    assert res_explicit.exit_code == 0, res_explicit.output
    assert "MANIFEST VERIFIED: 1/1" in res_explicit.output


def test_prepare_dataframe_for_bigquery_and_load_job_config_empty_flight_date_df(
    sample_ingested: dict[str, pd.DataFrame],
    tmp_path: Path,
) -> None:
    """Empty `flight_date` DataFrames preserve Arrow `date32[day]` and BigQuery `DATE` partitioning."""
    empty_otp = sample_ingested["otp_marketing_flights"].iloc[0:0].copy()
    prepared_otp = prepare_dataframe_for_bigquery(empty_otp, "otp_marketing_flights")
    assert "date32" in str(prepared_otp["flight_date"].dtype).lower()

    parquet_path = tmp_path / "empty_otp.parquet"
    prepared_otp.to_parquet(parquet_path, index=False)
    assert str(pq.read_schema(parquet_path).field("flight_date").type) == "date32[day]"

    schema_map = {
        f.name: f.field_type for f in infer_bigquery_schema(prepared_otp, "otp_marketing_flights")
    }
    assert schema_map["flight_date"] == "DATE"

    job_config = build_load_job_config(prepared_otp, "otp_marketing_flights")
    assert job_config.time_partitioning is not None
    assert job_config.time_partitioning.field == "flight_date"

    # Non-OTP table with `flight_date` column also normalizes cleanly
    empty_other = pd.DataFrame(
        {"flight_date": pd.Series([], dtype="object"), "val": pd.Series([], dtype="int64")}
    )
    prepared_other = prepare_dataframe_for_bigquery(empty_other, "other_table")
    assert "date32" in str(prepared_other["flight_date"].dtype).lower()
    other_schema = {
        f.name: f.field_type for f in infer_bigquery_schema(prepared_other, "other_table")
    }
    assert other_schema["flight_date"] == "DATE"


@pytest.mark.parametrize("empty_rows", [False, True])
def test_enriched_flights_bigquery_partitioning_clustering_and_parquet(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
    tmp_path: Path,
    empty_rows: bool,
) -> None:
    """`enriched_flights` sets `flight_date` DAY partitioning, clustering, and `date32[day]` Parquet for populated and 0-row DataFrames."""
    from google.cloud.bigquery import _pandas_helpers

    from standby.enrichment import enrich_flights

    res = enrich_flights(
        otp_df=sample_ingested["otp_marketing_flights"],
        t100_df=sample_ingested["t100_segments"],
        faa_registry_df=sample_ingested["faa_aircraft_registry"],
        faa_acftref_df=sample_ingested["faa_acftref"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        reference_dir=sample_config.reference_dir,
        config=sample_config,
    )
    enriched_df = (
        res.enriched_flights.iloc[0:0].copy() if empty_rows else res.enriched_flights.copy()
    )
    prepared = prepare_dataframe_for_bigquery(enriched_df, "enriched_flights")
    bq_schema = infer_bigquery_schema(prepared, "enriched_flights")
    schema_map = {f.name: f.field_type for f in bq_schema}
    assert schema_map["flight_date"] == "DATE"

    parquet_path = tmp_path / f"enriched_flights_{'empty' if empty_rows else 'populated'}.parquet"
    _pandas_helpers.dataframe_to_parquet(prepared, bq_schema, str(parquet_path))
    arrow_schema = pq.read_schema(parquet_path)
    assert str(arrow_schema.field("flight_date").type) == "date32[day]"

    job_config = build_load_job_config(prepared, "enriched_flights", partition_expiration_ms=None)
    assert str(job_config.write_disposition) == "WRITE_TRUNCATE"
    assert job_config.time_partitioning is not None
    assert str(job_config.time_partitioning.type_) == "DAY"
    assert job_config.time_partitioning.field == "flight_date"
    assert job_config.time_partitioning.expiration_ms is None
    assert list(job_config.clustering_fields) == ["origin", "dest", "op_carrier"]


def test_cli_score_and_validate_index_respect_hub_and_years_on_processed_parquet(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`score` / `validate-index` scope processed Parquets to `--hub` / `--years` and re-score by default."""
    import json

    runner = CliRunner()

    def _isolated_load_config(**kwargs):
        cfg = load_config(repo_root=repo_root, **kwargs)
        cfg.processed_dir = tmp_path / "processed"
        return cfg

    monkeypatch.setattr("standby.cli.load_config", _isolated_load_config)

    res_run = runner.invoke(
        main,
        ["run", "--hub", "ORD", "--years", "2019,2023-2026", "--sample", "--dry-run"],
    )
    assert res_run.exit_code == 0, res_run.output
    otp_parquet = tmp_path / "processed" / "sample" / "otp_marketing_flights.parquet"
    processed = pd.read_parquet(otp_parquet)
    n_2024 = int((processed["year"] == 2024).sum())
    assert 0 < n_2024 < len(processed)
    # Stored 2024 planning scores were trained on 2019, so re-scoring 2024 alone must change them.
    assert processed.loc[processed["year"] == 2024, "soi_planning"].notna().any()

    # `score` writes back to the processed Parquet, so it refuses to silently drop other years.
    res_score = runner.invoke(
        main, ["score", "--hub", "ORD", "--years", "2024", "--sample", "--from-processed"]
    )
    assert res_score.exit_code != 0
    assert "flights outside --hub ORD --years [2024]" in res_score.output
    assert len(pd.read_parquet(otp_parquet)) == len(processed)

    # `validate-index` validates only the requested years and re-scores them (2024 becomes the
    # minimum year, so its soi_planning must be NULL).
    summary = tmp_path / "val_2024.json"
    res_val = runner.invoke(
        main,
        [
            "validate-index",
            "--hub",
            "ORD",
            "--years",
            "2024",
            "--sample",
            "--summary-path",
            str(summary),
        ],
    )
    assert res_val.exit_code == 0, res_val.output
    assert f"SOI VALIDATION PASSED: flights={n_2024}" in res_val.output
    report = json.loads(summary.read_text(encoding="utf-8"))
    assert report["metadata"]["years"] == [2024]
    assert report["coverage_and_invariants"]["min_year_soi_planning_non_null"] == 0

    # `--reuse-scores` validates the stored (2019-trained) scores, which breaks the min-year invariant.
    res_reuse = runner.invoke(
        main, ["validate-index", "--hub", "ORD", "--years", "2024", "--sample", "--reuse-scores"]
    )
    assert res_reuse.exit_code != 0
    assert "Leakage invariant failed: min_year=2024" in str(res_reuse.exception)

    # A hub with no processed flights fails loudly instead of validating ORD data.
    res_hub = runner.invoke(main, ["validate-index", "--hub", "DEN", "--sample"])
    assert res_hub.exit_code != 0
    assert "has no flights for --hub DEN" in res_hub.output
