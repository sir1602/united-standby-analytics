"""Ingestion and standardization for BTS Lookup tables (`L_*.csv`)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("standby.ingest.lookups")

LOOKUP_FILES: tuple[str, ...] = (
    "L_AIRCRAFT_TYPE.csv",
    "L_AIRPORT.csv",
    "L_AIRPORT_ID.csv",
    "L_CARRIER_HISTORY.csv",
    "L_UNIQUE_CARRIERS.csv",
)


def _read_lookup_csv(fp: Path) -> pd.DataFrame:
    """Read a BTS lookup CSV using UTF-8-SIG with CP1252/Latin-1 fallback."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return pd.read_csv(fp, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(
        fp,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        encoding_errors="replace",
    )


def ingest_lookups(data_dir: Path) -> pd.DataFrame:
    """Ingest all 5 BTS lookup CSVs under `data_dir / 'lookups'` into a unified DataFrame."""
    lookups_dir = data_dir / "lookups"
    frames: list[pd.DataFrame] = []

    for filename in LOOKUP_FILES:
        fp = lookups_dir / filename
        if not fp.is_file():
            raise FileNotFoundError(f"Missing BTS lookup file: {fp}")
        df_raw = _read_lookup_csv(fp)
        df_raw.columns = [c.strip() for c in df_raw.columns]
        table_name = fp.stem
        sub = pd.DataFrame(
            {
                "lookup_table": table_name,
                "code": df_raw["Code"].astype(str).str.strip(),
                "description": df_raw["Description"].astype(str).str.strip(),
                "confidence_tier": "actual",
            }
        )
        sub = sub[(sub["code"] != "") & (sub["description"] != "")].copy()
        frames.append(sub)

    combined = pd.concat(frames, ignore_index=True)
    logger.info(f"Ingested {len(combined):,} BTS lookup rows across {len(frames)} tables")
    return combined
