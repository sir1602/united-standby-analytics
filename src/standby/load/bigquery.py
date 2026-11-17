"""BigQuery batch load jobs (`raw` dataset) with explicit schemas, partitioning, and offline dry-run."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery

from standby.config import StandbyConfig

logger = logging.getLogger("standby.load.bigquery")


@dataclass
class TableLoadResult:
    """Result metadata for a single BigQuery batch load or offline dry-run."""

    table_id: str
    row_count: int
    mode: str
    partition_field: str | None
    clustering_fields: list[str] | None
    local_artifact_path: Path | None = None


def prepare_dataframe_for_bigquery(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Normalize standardized columns for BigQuery batch loads (e.g., `flight_date` -> `datetime.date`)."""
    prepared = df.copy()
    if "flight_date" in prepared.columns:
        if prepared.empty:
            prepared["flight_date"] = pd.Series(
                pd.array([], dtype=pd.ArrowDtype(pa.date32())),
                index=prepared.index,
            )
        else:
            prepared["flight_date"] = pd.to_datetime(
                prepared["flight_date"], errors="raise"
            ).dt.date
    return prepared


def infer_bigquery_schema(df: pd.DataFrame, table_name: str) -> list[bigquery.SchemaField]:
    """Build explicit `bigquery.SchemaField` list from standardized DataFrame columns."""
    fields: list[bigquery.SchemaField] = []
    for col in df.columns:
        dtype_str = str(df[col].dtype).lower()
        if col == "flight_date" or "date32" in dtype_str:
            bq_type = "DATE"
        elif "datetime" in dtype_str:
            bq_type = "TIMESTAMP"
        elif "bool" in dtype_str:
            bq_type = "BOOL"
        elif "int" in dtype_str:
            bq_type = "INT64"
        elif "float" in dtype_str:
            bq_type = "FLOAT64"
        else:
            non_null = df[col].dropna()
            if (
                not non_null.empty
                and isinstance(non_null.iloc[0], date)
                and not isinstance(non_null.iloc[0], datetime)
            ):
                bq_type = "DATE"
            else:
                bq_type = "STRING"
        fields.append(bigquery.SchemaField(name=col, field_type=bq_type, mode="NULLABLE"))
    return fields


def build_load_job_config(
    df: pd.DataFrame,
    table_name: str,
    partition_expiration_ms: int | None = None,
) -> bigquery.LoadJobConfig:
    """Construct a genuine `bigquery.LoadJobConfig` with `WRITE_TRUNCATE`, explicit schema, and no partition expiry."""
    schema = infer_bigquery_schema(df, table_name)
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.PARQUET,
    )
    if table_name in {"otp_marketing_flights", "enriched_flights"} and "flight_date" in df.columns:
        # Fact flight table clustering & partition config (`expiration_ms=None` strictly enforced per D-08)
        job_config.clustering_fields = ["origin", "dest", "op_carrier"]
        job_config.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="flight_date",
            expiration_ms=partition_expiration_ms,
        )
    return job_config


def load_dataframe_to_bigquery(
    df: pd.DataFrame,
    table_name: str,
    config: StandbyConfig,
    client: bigquery.Client | None = None,
) -> TableLoadResult:
    """Batch-load a DataFrame to BigQuery `raw.<table_name>` or write local Parquet artifact in dry-run/offline mode."""
    dataset_id = config.bigquery.datasets.get("raw", "raw")
    bq_table_name = config.bigquery.tables.get(table_name, table_name)
    full_table_id = f"{config.bigquery.project_id}.{dataset_id}.{bq_table_name}"

    df_to_load = prepare_dataframe_for_bigquery(df, table_name)
    job_config = build_load_job_config(
        df=df_to_load,
        table_name=table_name,
        partition_expiration_ms=config.bigquery.partition_expiration_ms,
    )

    partition_field = (
        job_config.time_partitioning.field if job_config.time_partitioning is not None else None
    )
    clustering_fields = list(job_config.clustering_fields) if job_config.clustering_fields else None

    if config.dry_run:
        out_dir = config.processed_dir / ("sample" if config.sample_mode else "full")
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = out_dir / f"{bq_table_name}.parquet"
        df_to_load.to_parquet(artifact_path, index=False)
        logger.info(
            f"[DRY-RUN] Prepared LoadJobConfig for {full_table_id} ({len(df_to_load):,} rows) -> saved local Parquet to {artifact_path}"
        )
        return TableLoadResult(
            table_id=full_table_id,
            row_count=len(df_to_load),
            mode="dry_run",
            partition_field=partition_field,
            clustering_fields=clustering_fields,
            local_artifact_path=artifact_path,
        )

    try:
        bq_client = client or bigquery.Client(
            project=config.bigquery.project_id,
            location=config.bigquery.location,
        )
        dataset_ref = bigquery.Dataset(f"{config.bigquery.project_id}.{dataset_id}")
        dataset_ref.location = config.bigquery.location
        dataset_ref.default_table_expiration_ms = None
        dataset_ref.default_partition_expiration_ms = None
        bq_client.create_dataset(dataset_ref, exists_ok=True)

        load_job = bq_client.load_table_from_dataframe(
            df_to_load,
            full_table_id,
            job_config=job_config,
        )
        load_job.result()
        logger.info(f"Loaded {len(df_to_load):,} rows into BigQuery table {full_table_id}")
        return TableLoadResult(
            table_id=full_table_id,
            row_count=len(df_to_load),
            mode="bigquery_batch_load",
            partition_field=partition_field,
            clustering_fields=clustering_fields,
        )
    except DefaultCredentialsError:
        out_dir = config.processed_dir / ("sample" if config.sample_mode else "full")
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = out_dir / f"{bq_table_name}.parquet"
        df_to_load.to_parquet(artifact_path, index=False)
        logger.warning(
            f"No GCP credentials detected; wrote local batch artifact for {full_table_id} ({len(df_to_load):,} rows) to {artifact_path}"
        )
        return TableLoadResult(
            table_id=full_table_id,
            row_count=len(df_to_load),
            mode="local_fallback_no_credentials",
            partition_field=partition_field,
            clustering_fields=clustering_fields,
            local_artifact_path=artifact_path,
        )


def load_all_raw_tables(
    tables: dict[str, pd.DataFrame],
    config: StandbyConfig,
    client: bigquery.Client | None = None,
) -> dict[str, TableLoadResult]:
    """Batch-load all ingested/enriched tables to BigQuery `raw` dataset (or local artifacts in dry-run)."""
    results: dict[str, TableLoadResult] = {}
    for name, df in tables.items():
        results[name] = load_dataframe_to_bigquery(
            df=df,
            table_name=name,
            config=config,
            client=client,
        )
    return results
