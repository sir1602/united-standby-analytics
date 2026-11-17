"""Ingestion and standardization for BTS T-100 Segment (`FMG`) files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

logger = logging.getLogger("standby.ingest.t100")

T100_USECOLS: set[str] = {
    "YEAR",
    "QUARTER",
    "MONTH",
    "UNIQUE_CARRIER",
    "UNIQUE_CARRIER_NAME",
    "ORIGIN_AIRPORT_ID",
    "ORIGIN",
    "ORIGIN_COUNTRY",
    "DEST_AIRPORT_ID",
    "DEST",
    "DEST_COUNTRY",
    "AIRCRAFT_GROUP",
    "AIRCRAFT_TYPE",
    "AIRCRAFT_CONFIG",
    "CLASS",
    "DATA_SOURCE",
    "DEPARTURES_SCHEDULED",
    "DEPARTURES_PERFORMED",
    "SEATS",
    "PASSENGERS",
    "DISTANCE",
}


def ingest_single_t100_csv(
    file_path: Path,
    hub: str | None = "ORD",
    service_class: str = "F",
    allowed_years: set[int] | None = None,
) -> pd.DataFrame:
    """Read and standardize a single annual BTS T-100 Segment CSV file."""
    df_raw = pd.read_csv(
        file_path,
        dtype=str,
        keep_default_na=False,
        low_memory=False,
        usecols=lambda c: c.strip() in T100_USECOLS,
    )
    df_raw.columns = [c.strip() for c in df_raw.columns]

    hub_norm = hub.strip().upper() if hub is not None else None
    if hub_norm == "":
        raise ValueError("hub must not be empty")
    service_class_norm = service_class.strip().upper()
    mask = df_raw["CLASS"].str.strip().str.upper() == service_class_norm
    if hub_norm is not None:
        mask = mask & (df_raw["ORIGIN"].str.strip().str.upper() == hub_norm)
    df_raw = df_raw.loc[mask].copy()
    if df_raw.empty:
        return pd.DataFrame()

    years_int = df_raw["YEAR"].astype("int64")
    if allowed_years is not None:
        df_raw = df_raw.loc[years_int.isin(allowed_years)].copy()
        if df_raw.empty:
            return pd.DataFrame()
        years_int = df_raw["YEAR"].astype("int64")

    data_source = df_raw["DATA_SOURCE"].astype(str).str.strip()
    is_intl = data_source.isin(["IU", "IF"])

    out = pd.DataFrame(
        {
            "year": years_int,
            "quarter": df_raw["QUARTER"].astype("int64"),
            "month": df_raw["MONTH"].astype("int64"),
            "op_carrier": df_raw["UNIQUE_CARRIER"].astype(str).str.strip(),
            "carrier_name": df_raw["UNIQUE_CARRIER_NAME"].astype(str).str.strip(),
            "origin": df_raw["ORIGIN"].astype(str).str.strip(),
            "origin_airport_id": df_raw["ORIGIN_AIRPORT_ID"].astype("int64"),
            "origin_country": df_raw["ORIGIN_COUNTRY"].astype(str).str.strip(),
            "dest": df_raw["DEST"].astype(str).str.strip(),
            "dest_airport_id": df_raw["DEST_AIRPORT_ID"].astype("int64"),
            "dest_country": df_raw["DEST_COUNTRY"].astype(str).str.strip(),
            "bts_aircraft_type": df_raw["AIRCRAFT_TYPE"].astype(str).str.strip(),
            "aircraft_group": pd.to_numeric(df_raw["AIRCRAFT_GROUP"], errors="coerce")
            .fillna(0)
            .astype("int64"),
            "aircraft_config": pd.to_numeric(df_raw["AIRCRAFT_CONFIG"], errors="coerce")
            .fillna(0)
            .astype("int64"),
            "service_class": df_raw["CLASS"].astype(str).str.strip(),
            "data_source": data_source,
            "is_international": is_intl.astype(bool),
            "departures_scheduled": pd.to_numeric(df_raw["DEPARTURES_SCHEDULED"], errors="coerce")
            .fillna(0.0)
            .astype("float64"),
            "departures_performed": pd.to_numeric(df_raw["DEPARTURES_PERFORMED"], errors="coerce")
            .fillna(0.0)
            .astype("float64"),
            "seats": pd.to_numeric(df_raw["SEATS"], errors="coerce").fillna(0.0).astype("float64"),
            "passengers": pd.to_numeric(df_raw["PASSENGERS"], errors="coerce")
            .fillna(0.0)
            .astype("float64"),
            "distance_miles": pd.to_numeric(df_raw["DISTANCE"], errors="coerce")
            .fillna(0.0)
            .astype("float64"),
            "confidence_tier": "actual",
        }
    )
    return out


def ingest_t100(
    data_dir: Path,
    hub: str | None = "ORD",
    years: list[int] | None = None,
) -> pd.DataFrame:
    """Ingest all `t100_segment_all_*.csv` files under `data_dir / 't100'`."""
    hub_norm = hub.strip().upper() if hub is not None else None
    if hub_norm == "":
        raise ValueError("hub must not be empty")
    t100_dir = data_dir / "t100"
    files = sorted(t100_dir.glob("t100_segment_all_*.csv"))
    if not files:
        raise FileNotFoundError(f"No T-100 files found in {t100_dir}")

    allowed_years = set(years) if years is not None else None
    frames: list[pd.DataFrame] = []
    for fp in files:
        if allowed_years is not None:
            m = re.search(r"_(\d{4})\.csv$", fp.name)
            if m and int(m.group(1)) not in allowed_years:
                continue
        df_year = ingest_single_t100_csv(fp, hub=hub_norm, allowed_years=allowed_years)
        if not df_year.empty:
            frames.append(df_year)

    if not frames:
        raise RuntimeError(f"No T-100 segments ingested from {t100_dir}")

    combined = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Ingested {len(combined):,} T-100 Class 'F' segments (hub={hub_norm}) from {len(frames)} files"
    )
    return combined


SUMMARY_COLUMNS: tuple[str, ...] = (
    "year",
    "carrier",
    "seats",
    "passengers",
    "departures_performed",
    "load_factor",
)
SUMMARY_USECOLS: set[str] = {
    "YEAR",
    "UNIQUE_CARRIER",
    "CLASS",
    "SEATS",
    "PASSENGERS",
    "DEPARTURES_PERFORMED",
}


def ingest_t100_system_carrier_summary(
    data_dir: Path,
    carrier: str = "UA",
    years: list[int] | None = None,
) -> pd.DataFrame:
    """Compute system-wide annual carrier T-100 totals (`CLASS == 'F'`) for Sanity Check #4."""
    carrier_norm = carrier.strip().upper()
    if not carrier_norm:
        raise ValueError("carrier must not be empty")

    t100_dir = data_dir / "t100"
    files = sorted(t100_dir.glob("t100_segment_all_*.csv"))
    allowed_years = set(years) if years is not None else None
    grouped_frames: list[pd.DataFrame] = []

    for fp in files:
        if allowed_years is not None:
            m = re.search(r"_(\d{4})\.csv$", fp.name)
            if m and int(m.group(1)) not in allowed_years:
                continue
        df_raw = pd.read_csv(
            fp,
            dtype=str,
            keep_default_na=False,
            low_memory=False,
            usecols=lambda c: c.strip() in SUMMARY_USECOLS,
        )
        df_raw.columns = [c.strip() for c in df_raw.columns]
        mask = (df_raw["CLASS"].str.strip().str.upper() == "F") & (
            df_raw["UNIQUE_CARRIER"].str.strip().str.upper() == carrier_norm
        )
        sub = df_raw.loc[mask].copy()
        if sub.empty:
            continue

        year_vals = pd.to_numeric(sub["YEAR"].astype(str).str.strip(), errors="coerce")
        sub = sub.loc[year_vals.notna()].copy()
        if sub.empty:
            continue
        sub["year"] = year_vals.loc[sub.index].astype("int64")
        if allowed_years is not None:
            sub = sub.loc[sub["year"].isin(allowed_years)].copy()
            if sub.empty:
                continue

        sub["seats"] = pd.to_numeric(sub["SEATS"], errors="coerce").fillna(0.0).astype("float64")
        sub["passengers"] = (
            pd.to_numeric(sub["PASSENGERS"], errors="coerce").fillna(0.0).astype("float64")
        )
        sub["departures_performed"] = (
            pd.to_numeric(sub["DEPARTURES_PERFORMED"], errors="coerce")
            .fillna(0.0)
            .astype("float64")
        )
        grouped = sub.groupby("year", as_index=False)[
            ["seats", "passengers", "departures_performed"]
        ].sum()
        grouped_frames.append(grouped)

    if not grouped_frames:
        return pd.DataFrame(columns=list(SUMMARY_COLUMNS))

    combined = (
        pd.concat(grouped_frames, ignore_index=True)
        .groupby("year", as_index=False)[["seats", "passengers", "departures_performed"]]
        .sum()
        .sort_values("year")
        .reset_index(drop=True)
    )
    combined["carrier"] = carrier_norm
    combined["load_factor"] = combined.apply(
        lambda row: (float(row["passengers"]) / float(row["seats"])) if row["seats"] > 0 else 0.0,
        axis=1,
    )
    return combined[list(SUMMARY_COLUMNS)]
