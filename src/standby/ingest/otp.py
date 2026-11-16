"""Ingestion and standardization for BTS Marketing Carrier On-Time Performance (`OTP`)."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from standby.config import HourBlockSpec

logger = logging.getLogger("standby.ingest.otp")

OTP_RAW_USECOLS: set[str] = {
    "FlightDate",
    "Year",
    "Quarter",
    "Month",
    "DayofMonth",
    "DayOfWeek",
    "Marketing_Airline_Network",
    "Operating_Airline",
    "Flight_Number_Marketing_Airline",
    "Flight_Number_Operating_Airline",
    "Tail_Number",
    "Origin",
    "OriginAirportID",
    "Dest",
    "DestAirportID",
    "CRSDepTime",
    "DepTime",
    "CRSArrTime",
    "ArrTime",
    "DepDelay",
    "ArrDelay",
    "Cancelled",
    "CancellationCode",
    "Diverted",
    "CRSElapsedTime",
    "ActualElapsedTime",
    "Distance",
    "Duplicate",
}


def normalize_hhmm_value(val: object) -> str | None:
    """Normalize a local hhmm string/number to 4-digit `0000`..`2359` (mapping `'2400'` -> `'0000'`)."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    if "." in s:
        s = s.split(".", 1)[0]
    if not s.isdigit():
        return None
    padded = s.zfill(4)
    if padded == "2400":
        return "0000"
    return padded


def normalize_hhmm_series(series: pd.Series) -> pd.Series:
    """Vectorized normalization of local `hhmm` series (`'2400'` -> `'0000'`, 4-char zero-padded)."""
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(r"\.0+$", "", regex=True)
    mask_valid = s.str.fullmatch(r"[0-9]+")
    padded = s.str.zfill(4)
    padded = padded.where(padded != "2400", "0000")
    return padded.where(mask_valid, None)


def normalize_tail_series(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Normalize `Tail_Number` to `(tail_num_with_N, tail_num_clean_without_N)`."""
    s = series.fillna("").astype(str).str.strip().str.upper()
    mask_empty = s.eq("") | s.isin(["NAN", "NONE"])
    with_n = np.where(s.str.startswith("N"), s, "N" + s)
    clean_no_n = np.where(s.str.startswith("N"), s.str.slice(1), s)
    tail_num = pd.Series(with_n, index=series.index, dtype=object).where(~mask_empty, None)
    tail_clean = pd.Series(clean_no_n, index=series.index, dtype=object).where(~mask_empty, None)
    return tail_num, tail_clean


def map_dep_hour_to_block(
    dep_hours: pd.Series,
    hour_blocks: dict[str, HourBlockSpec] | None = None,
) -> pd.Series:
    """Vectorized mapping of integer `dep_hour` (0..23) to configured hour block names."""
    if hour_blocks:
        conditions = []
        choices = []
        default_block = "late" if "late" in hour_blocks else next(reversed(hour_blocks))
        for name, spec in hour_blocks.items():
            if spec.start_hour < spec.end_hour:
                conditions.append((dep_hours >= spec.start_hour) & (dep_hours < spec.end_hour))
            elif spec.start_hour > spec.end_hour:
                conditions.append((dep_hours >= spec.start_hour) | (dep_hours < spec.end_hour))
            else:
                conditions.append(pd.Series(True, index=dep_hours.index))
            choices.append(name)
        return pd.Series(
            np.select(conditions, choices, default=default_block),
            index=dep_hours.index,
            dtype=str,
        )

    conditions = [
        (dep_hours >= 5) & (dep_hours < 8),
        (dep_hours >= 8) & (dep_hours < 11),
        (dep_hours >= 11) & (dep_hours < 14),
        (dep_hours >= 14) & (dep_hours < 17),
        (dep_hours >= 17) & (dep_hours < 20),
    ]
    choices = ["early", "morning", "midday", "afternoon", "evening"]
    return pd.Series(
        np.select(conditions, choices, default="late"),
        index=dep_hours.index,
        dtype=str,
    )


def ingest_single_otp_csv(
    file_path: Path,
    hub: str = "ORD",
    marketing_carrier: str | Sequence[str] = "UA",
    allowed_years: set[int] | None = None,
    operating_carriers: Collection[str] | None = None,
    hour_blocks: dict[str, HourBlockSpec] | None = None,
) -> pd.DataFrame:
    """Read and standardize a single BTS Marketing Carrier OTP CSV file."""
    df_raw = pd.read_csv(
        file_path,
        dtype=str,
        keep_default_na=False,
        low_memory=False,
        usecols=lambda c: c.strip() in OTP_RAW_USECOLS,
    )
    # Strip whitespace from raw header names (fixes `'Operating_Airline '` quirk)
    df_raw.columns = [c.strip() for c in df_raw.columns]
    # Drop any empty trailing column names if present
    df_raw = df_raw.loc[:, [c for c in df_raw.columns if c]]

    hub_norm = hub.strip().upper()
    if not hub_norm:
        raise ValueError("hub must not be empty")

    mkt_carriers = (
        [marketing_carrier.strip().upper()]
        if isinstance(marketing_carrier, str)
        else [str(c).strip().upper() for c in marketing_carrier]
    )

    # Filter to hub, marketing carrier, optional operating carriers, and non-duplicate rows
    mask = (df_raw["Origin"].str.strip().str.upper() == hub_norm) & (
        df_raw["Marketing_Airline_Network"].str.strip().str.upper().isin(mkt_carriers)
    )
    if operating_carriers:
        allowed_op = {str(c).strip().upper() for c in operating_carriers}
        mask = mask & (df_raw["Operating_Airline"].str.strip().str.upper().isin(allowed_op))
    if "Duplicate" in df_raw.columns:
        mask = mask & (df_raw["Duplicate"].str.strip().isin(["N", ""]))
    df_raw = df_raw.loc[mask].copy()

    if df_raw.empty:
        return pd.DataFrame()

    years_int = df_raw["Year"].astype("int64")
    if allowed_years is not None:
        year_mask = years_int.isin(allowed_years)
        df_raw = df_raw.loc[year_mask].copy()
        if df_raw.empty:
            return pd.DataFrame()
        years_int = df_raw["Year"].astype("int64")

    crs_dep_time = normalize_hhmm_series(df_raw["CRSDepTime"])
    dep_time = normalize_hhmm_series(df_raw["DepTime"])
    crs_arr_time = normalize_hhmm_series(df_raw["CRSArrTime"])
    arr_time = normalize_hhmm_series(df_raw["ArrTime"])

    if crs_dep_time.isna().any():
        bad_count = int(crs_dep_time.isna().sum())
        raise ValueError(
            f"Found {bad_count} row(s) with blank or non-digit CRSDepTime in {file_path}"
        )

    dep_hour = crs_dep_time.str.slice(0, 2).astype("int64")
    hour_block = map_dep_hour_to_block(dep_hour, hour_blocks=hour_blocks)

    tail_num, tail_num_clean = normalize_tail_series(df_raw["Tail_Number"])

    is_cancelled = pd.to_numeric(df_raw["Cancelled"], errors="coerce").fillna(0.0) == 1.0
    is_diverted = pd.to_numeric(df_raw["Diverted"], errors="coerce").fillna(0.0) == 1.0

    canc_code_raw = df_raw["CancellationCode"].astype(str).str.strip()
    cancellation_code = canc_code_raw.where(is_cancelled & (canc_code_raw != ""), None)

    flight_num_op_raw = df_raw["Flight_Number_Operating_Airline"].astype(str).str.strip()
    flight_num_op_raw = flight_num_op_raw.str.replace(r"\.0+$", "", regex=True)
    flight_num_op = flight_num_op_raw.where(flight_num_op_raw != "", None)

    flight_num_mkt = (
        df_raw["Flight_Number_Marketing_Airline"]
        .astype(str)
        .str.strip()
        .str.replace(r"\.0+$", "", regex=True)
    )

    out = pd.DataFrame(
        {
            "flight_date": df_raw["FlightDate"].astype(str).str.strip(),
            "year": years_int,
            "quarter": df_raw["Quarter"].astype("int64"),
            "month": df_raw["Month"].astype("int64"),
            "day_of_month": df_raw["DayofMonth"].astype("int64"),
            "day_of_week": df_raw["DayOfWeek"].astype("int64"),
            "mkt_carrier": df_raw["Marketing_Airline_Network"].astype(str).str.strip(),
            "op_carrier": df_raw["Operating_Airline"].astype(str).str.strip(),
            "flight_num_mkt": flight_num_mkt,
            "flight_num_op": flight_num_op,
            "tail_num": tail_num,
            "tail_num_clean": tail_num_clean,
            "origin": df_raw["Origin"].astype(str).str.strip(),
            "origin_airport_id": df_raw["OriginAirportID"].astype("int64"),
            "dest": df_raw["Dest"].astype(str).str.strip(),
            "dest_airport_id": df_raw["DestAirportID"].astype("int64"),
            "crs_dep_time": crs_dep_time,
            "dep_time": dep_time,
            "crs_arr_time": crs_arr_time,
            "arr_time": arr_time,
            "dep_hour": dep_hour,
            "hour_block": hour_block,
            "dep_delay_min": pd.to_numeric(df_raw["DepDelay"], errors="coerce").astype("float64"),
            "arr_delay_min": pd.to_numeric(df_raw["ArrDelay"], errors="coerce").astype("float64"),
            "is_cancelled": is_cancelled.astype(bool),
            "cancellation_code": cancellation_code,
            "is_diverted": is_diverted.astype(bool),
            "crs_elapsed_time_min": pd.to_numeric(df_raw["CRSElapsedTime"], errors="coerce").astype(
                "float64"
            ),
            "actual_elapsed_time_min": pd.to_numeric(
                df_raw["ActualElapsedTime"], errors="coerce"
            ).astype("float64"),
            "distance_miles": pd.to_numeric(df_raw["Distance"], errors="coerce").astype("float64"),
            "confidence_tier": "actual",
        }
    )
    return out


def ingest_otp(
    data_dir: Path,
    hub: str = "ORD",
    marketing_carrier: str | Sequence[str] = "UA",
    years: list[int] | None = None,
    operating_carriers: Collection[str] | None = None,
    hour_blocks: dict[str, HourBlockSpec] | None = None,
) -> pd.DataFrame:
    """Ingest all matching `otp_mkt_{hub}_{carrier}_*.csv` files under `data_dir / 'otp'`."""
    hub_norm = hub.strip().upper()
    if not hub_norm:
        raise ValueError("hub must not be empty")
    otp_dir = data_dir / "otp"
    carriers = (
        [marketing_carrier] if isinstance(marketing_carrier, str) else list(marketing_carrier)
    )
    files_set: set[Path] = set()
    for carrier in carriers:
        files_set.update(
            otp_dir.glob(f"otp_mkt_{hub_norm.lower()}_{carrier.strip().lower()}_*.csv")
        )
    files = sorted(files_set)
    if not files:
        files = sorted(otp_dir.glob("otp_mkt_*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No OTP files matching hub={hub_norm} carriers={carriers} found in {otp_dir}"
        )

    allowed_years = set(years) if years is not None else None
    frames: list[pd.DataFrame] = []
    for fp in files:
        if allowed_years is not None:
            m = re.search(r"_(\d{4})_\d{2}\.csv$", fp.name)
            if m and int(m.group(1)) not in allowed_years:
                continue
        df_month = ingest_single_otp_csv(
            fp,
            hub=hub_norm,
            marketing_carrier=carriers,
            allowed_years=allowed_years,
            operating_carriers=operating_carriers,
            hour_blocks=hour_blocks,
        )
        if not df_month.empty:
            frames.append(df_month)

    if not frames:
        raise RuntimeError(
            f"No OTP records ingested from {otp_dir} for hub={hub_norm}, years={years}"
        )

    combined = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Ingested {len(combined):,} OTP flights from {len(frames)} monthly files in {otp_dir}"
    )
    return combined
