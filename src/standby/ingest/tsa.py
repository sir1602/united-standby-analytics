"""Ingestion and standardization for TSA Daily Checkpoint Volumes (`tsa_checkpoint_volumes.csv`)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("standby.ingest.tsa")


def ingest_tsa(data_dir: Path, years: list[int] | None = None) -> pd.DataFrame:
    """Read and standardize `data_dir / 'tsa' / 'tsa_checkpoint_volumes.csv'`."""
    tsa_path = data_dir / "tsa" / "tsa_checkpoint_volumes.csv"
    if not tsa_path.is_file():
        raise FileNotFoundError(f"Missing TSA checkpoint volumes file: {tsa_path}")

    df_raw = pd.read_csv(tsa_path, dtype=str, keep_default_na=False)
    df_raw.columns = [c.strip() for c in df_raw.columns]

    date_col = "date" if "date" in df_raw.columns else "date_day"
    out = pd.DataFrame(
        {
            "date_day": df_raw[date_col].astype(str).str.strip(),
            "tsa_throughput": pd.to_numeric(
                df_raw["tsa_throughput"].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            )
            .fillna(0)
            .astype("int64"),
            "source_page_year": df_raw.get(
                "source_page_year", pd.Series("unknown", index=df_raw.index)
            )
            .astype(str)
            .str.strip(),
            "source_url": df_raw.get(
                "source_url",
                pd.Series("https://www.tsa.gov/travel/passenger-volumes", index=df_raw.index),
            )
            .astype(str)
            .str.strip(),
            "confidence_tier": "actual",
        }
    )
    if years is not None:
        allowed_prefixes = tuple(f"{y}-" for y in years)
        out = out[out["date_day"].str.startswith(allowed_prefixes)].copy()

    # Duplicate dates (overlapping TSA year pages): keep the highest throughput, matching
    # dbt `stg_tsa_throughput` (`qualify row_number() ... order by tsa_throughput desc`).
    out = (
        out.sort_values(["date_day", "tsa_throughput"], ascending=[True, False], kind="mergesort")
        .drop_duplicates(subset=["date_day"], keep="first")
        .reset_index(drop=True)
    )
    logger.info(f"Ingested {len(out):,} TSA daily checkpoint rows from {tsa_path}")
    return out
