"""Google Looker Studio pre-joined (denormalized) CSV extract exporter.

Produces flat, blend-free CSV extracts in `exports/looker/` (or
`data/processed/sample/looker/` in `--sample` mode) optimized for Looker Studio's
native File Upload (CSV) connector (< 50 MiB per file, < 100 MiB dataset limit) and
Google Maps geocoding.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from standby.config import StandbyConfig
from standby.export.tableau import (
    _FORBIDDEN_PII_COLUMNS,
    _SC5_NULL_REQUIRED_COLS,
    _SOI_SCORE_AND_SUBSCORE_COLS,
    _STAGING_DIR_PREFIX,
    AGG_ROUTE_MONTH_COLUMNS,
    FCT_FLIGHTS_TABLEAU_COLUMNS,
    FLIGHT_EXPORT_MODES,
    VALIDATION_SUMMARY_COLUMNS,
    ExportedFileInfo,
    ExportValidationError,
    _build_agg_route_month,
    _build_dim_aircraft,
    _build_dim_airport,
    _build_dim_carrier,
    _build_dim_date,
    _build_flight_id,
    _build_validation_summary_df,
    _files_listed_in_metadata,
    _format_fct_flights_tableau,
    _load_source_tables_for_export,
    _resolve_hub_set,
    _round_float_columns,
    _select_flights_for_export,
    _sha256_file,
)

logger = logging.getLogger("standby.export.looker")

_DOW_LABELS: dict[int, str] = {
    1: "1 - Mon",
    2: "2 - Tue",
    3: "3 - Wed",
    4: "4 - Thu",
    5: "5 - Fri",
    6: "6 - Sat",
    7: "7 - Sun",
}

_HOUR_BLOCK_LABELS: dict[str, str] = {
    "early": "1 - Early (05-07)",
    "morning": "2 - Morning (08-10)",
    "midday": "3 - Midday (11-13)",
    "afternoon": "4 - Afternoon (14-16)",
    "evening": "5 - Evening (17-19)",
    "late": "6 - Late (20-04)",
}

LOOKER_FLIGHTS_COLUMNS: tuple[str, ...] = (
    *FCT_FLIGHTS_TABLEAU_COLUMNS,
    "year_month",
    "day_of_week_label",
    "is_weekend",
    "hour_block_label",
    "carrier_name",
    "carrier_group",
    "is_mainline",
    "is_shared_cpa_carrier",
    "route_label",
    "dest_city_name",
    "dest_state_or_country",
    "dest_country_code",
    "faa_manufacturer",
    "faa_model",
    "soi_historical_tier",
    "soi_planning_tier",
)

LOOKER_ROUTE_MONTH_COLUMNS: tuple[str, ...] = (
    *AGG_ROUTE_MONTH_COLUMNS,
    "year_month",
    "route_label",
    "carrier_name",
    "carrier_group",
    "is_mainline",
    "is_shared_cpa_carrier",
    "dest_airport_name",
    "dest_city_name",
    "dest_state_or_country",
    "dest_geo_query",
    "otp_avg_soi_historical",
    "otp_avg_soi_planning",
)

LOOKER_ROUTE_SUMMARY_COLUMNS: tuple[str, ...] = (
    "route_id",
    "origin",
    "dest",
    "route_label",
    "is_international",
    "scope_label",
    "data_granularity",
    "dest_airport_name",
    "dest_city_name",
    "dest_state_or_country",
    "dest_country_code",
    "dest_geo_query",
    "total_departures_scheduled",
    "total_departures_performed",
    "total_seats",
    "total_passengers",
    "avg_t100_load_factor",
    "avg_t100_seats_per_departure",
    "distance_miles",
    "months_active",
    "carrier_count",
    "is_shared_regional_spoke",
    "otp_scheduled_flights",
    "otp_performed_flights",
    "otp_cancelled_flights",
    "otp_cancellation_rate",
    "otp_avg_est_open_seats_adjusted",
    "otp_avg_soi_historical",
    "otp_avg_soi_planning",
)


@dataclass(frozen=True)
class LookerExportResult:
    """Result summary returned by `export_looker_extracts`."""

    output_dir: Path
    metadata_path: Path
    sample_mode: bool
    flight_mode: str
    flight_row_budget: int
    total_source_flights: int
    exported_flights: int
    files: dict[str, ExportedFileInfo] = field(default_factory=dict)


def _looker_flight_filename(flight_mode: str) -> str:
    """Flight extract filename for `flight_mode` (`full` gets a gitignored `_full.csv` suffix)."""
    return "looker_flights_full.csv" if flight_mode == "full" else "looker_flights.csv"


def _resolve_default_looker_output_dir(config: StandbyConfig, flight_mode: str) -> Path:
    """
    Default output directory for Looker Studio extracts (`exports/looker/`).

    - `--sample` runs write to gitignored `data/processed/sample/looker/`.
    - `--flight-mode full` runs write to gitignored `exports/looker/full/`.
    - Full-data `stratified` / `recent-12m` runs write to `exports/looker/`.
    """
    looker_base = config.repo_root / "exports" / "looker"
    if config.sample_mode:
        return config.processed_dir / "sample" / "looker"
    if flight_mode == "full":
        return looker_base / "full"
    return looker_base


def _classify_soi_tier(series: pd.Series) -> pd.Series:
    """Classify an SOI score `[0, 100]` series into Looker Studio display tiers."""
    vals = pd.to_numeric(series, errors="coerce")
    out = pd.Series("Unscored (NULL)", index=series.index, dtype=object)
    out = out.where(vals.isna(), "Tight (0-49.99)")
    out = out.where(vals.isna() | (vals < 50.0), "Moderate (50-69.99)")
    out = out.where(vals.isna() | (vals < 70.0), "Favorable (70-100)")
    return out


def _build_dest_geo_query(
    airport_code: pd.Series,
    city_name: pd.Series,
    state_or_country: pd.Series,
    country_code: pd.Series,
) -> pd.Series:
    """Build an unambiguous Google Maps search query string per airport for Looker Studio Geo fields."""
    codes = airport_code.fillna("").astype(str).str.strip().str.upper()
    cities = city_name.fillna("").astype(str).str.strip()
    regions = state_or_country.fillna("").astype(str).str.strip()
    countries = country_code.fillna("US").astype(str).str.strip().str.upper()

    queries: list[str] = []
    for code, city, region, country in zip(codes, cities, regions, countries, strict=True):
        parts = [f"{code} Airport"] if code else []
        if city:
            parts.append(city)
        if region and region.lower() != city.lower():
            parts.append(region)
        if country and country.lower() != region.lower():
            parts.append(country)
        queries.append(", ".join(parts) if parts else code)
    return pd.Series(queries, index=airport_code.index, dtype=object)


def _build_looker_flights(
    fct_flights_tableau: pd.DataFrame,
    dim_airport: pd.DataFrame,
    dim_carrier: pd.DataFrame,
    dim_aircraft: pd.DataFrame,
) -> pd.DataFrame:
    """Build denormalized `looker_flights.csv` by pre-joining dimensions onto `fct_flights_tableau`."""
    df = fct_flights_tableau.copy()

    df["year_month"] = (
        pd.to_numeric(df["year"], errors="coerce").astype("Int64").astype(str)
        + "-"
        + pd.to_numeric(df["month"], errors="coerce").astype("Int64").astype(str).str.zfill(2)
    )
    dow_int = pd.to_numeric(df["day_of_week"], errors="coerce").astype("Int64")
    df["day_of_week_label"] = dow_int.map(
        lambda d: _DOW_LABELS.get(int(d), str(d)) if pd.notna(d) else None
    )
    df["is_weekend"] = dow_int.isin([6, 7]).astype(bool)

    hb_clean = df["hour_block"].astype(str).str.strip().str.lower()
    df["hour_block_label"] = hb_clean.map(lambda h: _HOUR_BLOCK_LABELS.get(h, h))

    car_lookup = dim_carrier[
        ["op_carrier", "carrier_name", "carrier_group", "is_mainline", "is_shared_cpa_carrier"]
    ].drop_duplicates(subset=["op_carrier"], keep="first")
    df = df.merge(car_lookup, on="op_carrier", how="left")

    df["route_label"] = (
        df["origin"].astype(str).str.strip().str.upper()
        + " -> "
        + df["dest"].astype(str).str.strip().str.upper()
    )

    air_lookup = (
        dim_airport[["airport_code", "city_name", "state_or_country", "country_code"]]
        .drop_duplicates(subset=["airport_code"], keep="first")
        .rename(
            columns={
                "airport_code": "dest",
                "city_name": "dest_city_name",
                "state_or_country": "dest_state_or_country",
                "country_code": "dest_country_code",
            }
        )
    )
    df = df.merge(air_lookup, on="dest", how="left")

    ac_lookup = dim_aircraft[["tail_num", "faa_manufacturer", "faa_model"]].drop_duplicates(
        subset=["tail_num"], keep="first"
    )
    df = df.merge(ac_lookup, on="tail_num", how="left")

    df["soi_historical_tier"] = _classify_soi_tier(df["soi_historical"])
    df["soi_planning_tier"] = _classify_soi_tier(df["soi_planning"])

    return df.loc[:, list(LOOKER_FLIGHTS_COLUMNS)].reset_index(drop=True)


def _compute_route_month_soi_rollups(flights_df: pd.DataFrame) -> pd.DataFrame:
    """Compute monthly domestic OTP average `soi_historical` and `soi_planning` per `(year, month, op_carrier, origin, dest)`."""
    if flights_df.empty:
        return pd.DataFrame(
            columns=[
                "year",
                "month",
                "op_carrier",
                "origin",
                "dest",
                "is_international",
                "otp_avg_soi_historical",
                "otp_avg_soi_planning",
            ]
        )
    flt = flights_df.copy()
    flt["year"] = pd.to_numeric(flt["year"], errors="coerce").astype("int64")
    flt["month"] = pd.to_numeric(flt["month"], errors="coerce").astype("int64")
    flt["op_carrier"] = flt["op_carrier"].astype(str).str.strip().str.upper()
    flt["origin"] = flt["origin"].astype(str).str.strip().str.upper()
    flt["dest"] = flt["dest"].astype(str).str.strip().str.upper()
    flt["_soi_hist"] = pd.to_numeric(flt["soi_historical"], errors="coerce")
    flt["_soi_plan"] = pd.to_numeric(flt["soi_planning"], errors="coerce")

    grouped = flt.groupby(
        ["year", "month", "op_carrier", "origin", "dest"], as_index=False, sort=True
    ).agg(
        otp_avg_soi_historical=("_soi_hist", "mean"),
        otp_avg_soi_planning=("_soi_plan", "mean"),
    )
    grouped["is_international"] = False
    return grouped


def _build_looker_route_month(
    agg_route_month: pd.DataFrame,
    flights_for_soi_rollup: pd.DataFrame,
    dim_airport: pd.DataFrame,
    dim_carrier: pd.DataFrame,
) -> pd.DataFrame:
    """Build denormalized `looker_route_month.csv` with carrier, airport geo, and domestic SOI rollups."""
    df = agg_route_month.copy()
    df["is_international"] = df["is_international"].astype(bool)

    df["year_month"] = (
        pd.to_numeric(df["year"], errors="coerce").astype("int64").astype(str)
        + "-"
        + pd.to_numeric(df["month"], errors="coerce").astype("int64").astype(str).str.zfill(2)
    )
    df["route_label"] = (
        df["origin"].astype(str).str.strip().str.upper()
        + " -> "
        + df["dest"].astype(str).str.strip().str.upper()
    )

    car_lookup = dim_carrier[
        ["op_carrier", "carrier_name", "carrier_group", "is_mainline", "is_shared_cpa_carrier"]
    ].drop_duplicates(subset=["op_carrier"], keep="first")
    df = df.merge(car_lookup, on="op_carrier", how="left")

    air = (
        dim_airport[
            ["airport_code", "airport_name", "city_name", "state_or_country", "country_code"]
        ]
        .drop_duplicates(subset=["airport_code"], keep="first")
        .copy()
    )
    air["dest_geo_query"] = _build_dest_geo_query(
        airport_code=air["airport_code"],
        city_name=air["city_name"],
        state_or_country=air["state_or_country"],
        country_code=air["country_code"],
    )
    air_lookup = air[
        ["airport_code", "airport_name", "city_name", "state_or_country", "dest_geo_query"]
    ].rename(
        columns={
            "airport_code": "dest",
            "airport_name": "dest_airport_name",
            "city_name": "dest_city_name",
            "state_or_country": "dest_state_or_country",
        }
    )
    df = df.merge(air_lookup, on="dest", how="left")

    soi_rollup = _compute_route_month_soi_rollups(flights_for_soi_rollup)
    df = df.merge(
        soi_rollup,
        on=["year", "month", "op_carrier", "origin", "dest", "is_international"],
        how="left",
    )
    # Enforce strict NULL on international rows for all otp_* columns
    intl_mask = df["is_international"].fillna(False).astype(bool)
    if intl_mask.any():
        df.loc[intl_mask, ["otp_avg_soi_historical", "otp_avg_soi_planning"]] = np.nan

    df = df.sort_values(["year", "month", "op_carrier", "origin", "dest", "is_international"])
    return df.loc[:, list(LOOKER_ROUTE_MONTH_COLUMNS)].reset_index(drop=True)


def _build_looker_route_summary(
    looker_route_month_df: pd.DataFrame,
    flights_for_soi_rollup: pd.DataFrame,
    dim_airport: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build `looker_route_summary.csv` (1 row per `(origin, dest, is_international)` route)
    for Looker Studio's View 1 Google Maps Bubble Map and Route Network Leaderboard.
    """
    rm = looker_route_month_df.copy()
    rm["is_international"] = rm["is_international"].fillna(False).astype(bool)
    rm["_ym"] = rm["year_month"].astype(str)
    rm["_dist_x_deps"] = pd.to_numeric(rm["distance_miles"], errors="coerce") * pd.to_numeric(
        rm["departures_performed"], errors="coerce"
    )
    rm["_open_adj_x_perf"] = pd.to_numeric(
        rm["otp_avg_est_open_seats_adjusted"], errors="coerce"
    ) * pd.to_numeric(rm["otp_performed_flights"], errors="coerce")

    grouped = rm.groupby(["origin", "dest", "is_international"], as_index=False, sort=True).agg(
        scope_label=("scope_label", "first"),
        data_granularity=("data_granularity", "first"),
        total_departures_scheduled=("departures_scheduled", "sum"),
        total_departures_performed=("departures_performed", "sum"),
        total_seats=("seats", "sum"),
        total_passengers=("passengers", "sum"),
        _sum_dist_x_deps=("_dist_x_deps", "sum"),
        _max_distance=("distance_miles", "max"),
        months_active=("_ym", "nunique"),
        carrier_count=("op_carrier", "nunique"),
        is_shared_regional_spoke=("is_shared_regional_spoke", "any"),
        _otp_sched_sum=(
            "otp_scheduled_flights",
            lambda s: s.dropna().sum() if s.notna().any() else np.nan,
        ),
        _otp_perf_sum=(
            "otp_performed_flights",
            lambda s: s.dropna().sum() if s.notna().any() else np.nan,
        ),
        _otp_canc_sum=(
            "otp_cancelled_flights",
            lambda s: s.dropna().sum() if s.notna().any() else np.nan,
        ),
        _sum_open_adj_x_perf=(
            "_open_adj_x_perf",
            lambda s: s.dropna().sum() if s.notna().any() else np.nan,
        ),
    )

    # Self-loop exclusion matching route_map_paths
    grouped = grouped.loc[grouped["origin"] != grouped["dest"]].copy()

    deps_perf = grouped["total_departures_performed"].to_numpy(dtype="float64")
    seats_arr = grouped["total_seats"].to_numpy(dtype="float64")
    pax_arr = grouped["total_passengers"].to_numpy(dtype="float64")

    raw_lf = np.where(seats_arr > 0.0, pax_arr / np.where(seats_arr > 0.0, seats_arr, 1.0), np.nan)
    grouped["avg_t100_load_factor"] = np.where(np.isnan(raw_lf), np.nan, np.clip(raw_lf, 0.0, 1.0))
    grouped["avg_t100_seats_per_departure"] = np.where(
        deps_perf > 0.0,
        seats_arr / np.where(deps_perf > 0.0, deps_perf, 1.0),
        np.nan,
    )
    grouped["distance_miles"] = np.where(
        deps_perf > 0.0,
        grouped["_sum_dist_x_deps"].to_numpy(dtype="float64")
        / np.where(deps_perf > 0.0, deps_perf, 1.0),
        grouped["_max_distance"].to_numpy(dtype="float64"),
    )

    intl_mask = grouped["is_international"].fillna(False).astype(bool)
    grouped["otp_scheduled_flights"] = (
        pd.Series(grouped["_otp_sched_sum"], index=grouped.index)
        .where(~intl_mask, pd.NA)
        .round()
        .astype("Int64")
    )
    grouped["otp_performed_flights"] = (
        pd.Series(grouped["_otp_perf_sum"], index=grouped.index)
        .where(~intl_mask, pd.NA)
        .round()
        .astype("Int64")
    )
    grouped["otp_cancelled_flights"] = (
        pd.Series(grouped["_otp_canc_sum"], index=grouped.index)
        .where(~intl_mask, pd.NA)
        .round()
        .astype("Int64")
    )

    sched_float = pd.to_numeric(grouped["otp_scheduled_flights"], errors="coerce").to_numpy(
        dtype="float64"
    )
    canc_float = pd.to_numeric(grouped["otp_cancelled_flights"], errors="coerce").to_numpy(
        dtype="float64"
    )
    perf_float = pd.to_numeric(grouped["otp_performed_flights"], errors="coerce").to_numpy(
        dtype="float64"
    )
    open_num = pd.to_numeric(grouped["_sum_open_adj_x_perf"], errors="coerce").to_numpy(
        dtype="float64"
    )

    grouped["otp_cancellation_rate"] = np.where(
        (~intl_mask.to_numpy()) & (sched_float > 0.0),
        canc_float / np.where(sched_float > 0.0, sched_float, 1.0),
        np.nan,
    )
    grouped["otp_avg_est_open_seats_adjusted"] = np.where(
        (~intl_mask.to_numpy()) & (perf_float > 0.0) & (~np.isnan(open_num)),
        open_num / np.where(perf_float > 0.0, perf_float, 1.0),
        np.nan,
    )

    # Route-level domestic SOI averages across flights
    if not flights_for_soi_rollup.empty:
        flt = flights_for_soi_rollup.copy()
        flt["origin"] = flt["origin"].astype(str).str.strip().str.upper()
        flt["dest"] = flt["dest"].astype(str).str.strip().str.upper()
        flt["_soi_hist"] = pd.to_numeric(flt["soi_historical"], errors="coerce")
        flt["_soi_plan"] = pd.to_numeric(flt["soi_planning"], errors="coerce")
        route_soi = flt.groupby(["origin", "dest"], as_index=False, sort=True).agg(
            otp_avg_soi_historical=("_soi_hist", "mean"),
            otp_avg_soi_planning=("_soi_plan", "mean"),
        )
        route_soi["is_international"] = False
        grouped = grouped.merge(route_soi, on=["origin", "dest", "is_international"], how="left")
    else:
        grouped["otp_avg_soi_historical"] = np.nan
        grouped["otp_avg_soi_planning"] = np.nan

    intl_mask_post = grouped["is_international"].fillna(False).astype(bool)
    if intl_mask_post.any():
        grouped.loc[intl_mask_post, ["otp_avg_soi_historical", "otp_avg_soi_planning"]] = np.nan

    air = (
        dim_airport[
            ["airport_code", "airport_name", "city_name", "state_or_country", "country_code"]
        ]
        .drop_duplicates(subset=["airport_code"], keep="first")
        .copy()
    )
    air["dest_geo_query"] = _build_dest_geo_query(
        airport_code=air["airport_code"],
        city_name=air["city_name"],
        state_or_country=air["state_or_country"],
        country_code=air["country_code"],
    )
    air_lookup = air.rename(
        columns={
            "airport_code": "dest",
            "airport_name": "dest_airport_name",
            "city_name": "dest_city_name",
            "state_or_country": "dest_state_or_country",
            "country_code": "dest_country_code",
        }
    )
    grouped = grouped.merge(air_lookup, on="dest", how="left")

    grouped["route_id"] = (
        grouped["origin"]
        + "-"
        + grouped["dest"]
        + "-"
        + np.where(grouped["is_international"], "INTL", "DOM")
    )
    grouped["route_label"] = grouped["origin"] + " -> " + grouped["dest"]
    grouped["months_active"] = grouped["months_active"].astype("int64")
    grouped["carrier_count"] = grouped["carrier_count"].astype("int64")
    grouped["is_shared_regional_spoke"] = grouped["is_shared_regional_spoke"].astype(bool)

    grouped = grouped.sort_values(["origin", "dest", "is_international"]).reset_index(drop=True)
    return grouped.loc[:, list(LOOKER_ROUTE_SUMMARY_COLUMNS)]


def _validate_looker_tables(
    tables: dict[str, pd.DataFrame],
    file_paths: dict[str, Path],
    flight_mode: str,
    max_file_mb: float = 50.0,
) -> None:
    """Enforce schema, null, bound, PII, and size invariants across all Looker Studio CSV extracts."""
    expected_schemas: dict[str, tuple[str, ...]] = {
        "looker_flights": LOOKER_FLIGHTS_COLUMNS,
        "looker_route_month.csv": LOOKER_ROUTE_MONTH_COLUMNS,
        "looker_route_summary.csv": LOOKER_ROUTE_SUMMARY_COLUMNS,
        "looker_validation_summary.csv": VALIDATION_SUMMARY_COLUMNS,
    }
    pks: dict[str, list[str]] = {
        "looker_flights": ["flight_id"],
        "looker_route_month.csv": ["route_month_id"],
        "looker_route_summary.csv": ["route_id"],
        "looker_validation_summary.csv": ["pillar", "metric_group", "metric_name"],
    }

    for key, expected_cols in expected_schemas.items():
        if key not in tables:
            raise ExportValidationError(f"Missing required Looker extract table: {key}")
        df = tables[key]
        if df.empty:
            raise ExportValidationError(f"Looker extract table {key} has 0 rows")
        actual_cols = tuple(df.columns)
        if actual_cols != expected_cols:
            raise ExportValidationError(
                f"Schema mismatch in {key}: expected {expected_cols}, got {actual_cols}"
            )
        lower_cols = {c.lower() for c in actual_cols}
        pii_hits = lower_cols & _FORBIDDEN_PII_COLUMNS
        if pii_hits:
            raise ExportValidationError(
                f"Forbidden PII columns detected in {key}: {sorted(pii_hits)}"
            )

        pk_cols = pks[key]
        if df[pk_cols].isna().any().any():
            raise ExportValidationError(f"Null primary key values in {key} ({pk_cols})")
        if df.duplicated(subset=pk_cols).any():
            raise ExportValidationError(f"Duplicate primary keys detected in {key} ({pk_cols})")

    # looker_route_month invariants: LF in [0, 1] and international OTP fields strictly NULL
    rm = tables["looker_route_month.csv"]
    t100_lf = rm["t100_load_factor"].dropna()
    if ((t100_lf < 0.0) | (t100_lf > 1.0)).any():
        raise ExportValidationError("looker_route_month.t100_load_factor outside [0, 1]")
    intl_rm = rm.loc[rm["is_international"].astype(bool)]
    if not intl_rm.empty:
        otp_cols = [c for c in rm.columns if c.startswith("otp_")]
        if intl_rm[otp_cols].notna().any().any():
            raise ExportValidationError(
                "International rows in looker_route_month.csv must have NULL otp_* fields"
            )

    # looker_route_summary invariants: LF in [0, 1], international OTP fields NULL, no self-loops
    rs = tables["looker_route_summary.csv"]
    rs_lf = rs["avg_t100_load_factor"].dropna()
    if ((rs_lf < 0.0) | (rs_lf > 1.0)).any():
        raise ExportValidationError("looker_route_summary.avg_t100_load_factor outside [0, 1]")
    if (
        rs["origin"].astype(str).str.strip().str.upper()
        == rs["dest"].astype(str).str.strip().str.upper()
    ).any():
        raise ExportValidationError(
            "looker_route_summary.csv must not contain origin == dest self-loop rows"
        )
    intl_rs = rs.loc[rs["is_international"].astype(bool)]
    if not intl_rs.empty:
        otp_rs_cols = [c for c in rs.columns if c.startswith("otp_")]
        if intl_rs[otp_rs_cols].notna().any().any():
            raise ExportValidationError(
                "International rows in looker_route_summary.csv must have NULL otp_* fields"
            )

    # looker_flights invariants: SC-5, LF in [0, 1], SOI in [0, 100], 2019 planning NULL, cancelled hist NULL
    fct = tables["looker_flights"]
    unmatched_mask = (~fct["t100_available"].astype(bool)) | (
        fct["lf_source"].astype(str) == "UNMATCHED"
    )
    if unmatched_mask.any():
        bad_sc5 = fct.loc[unmatched_mask, list(_SC5_NULL_REQUIRED_COLS)].notna().any().any()
        if bad_sc5:
            raise ExportValidationError(
                "SC-5 violated in looker_flights extract: non-null est/SOI on unmatched T-100"
            )

    for lf_col in ("est_load_factor", "est_lf_adjusted"):
        vals = fct[lf_col].dropna()
        if ((vals < 0.0) | (vals > 1.0)).any():
            raise ExportValidationError(f"looker_flights.{lf_col} outside [0, 1]")

    for soi_col in _SOI_SCORE_AND_SUBSCORE_COLS:
        vals = fct[soi_col].dropna()
        if ((vals < 0.0) | (vals > 100.0)).any():
            raise ExportValidationError(f"looker_flights.{soi_col} outside [0, 100]")

    for pax_col in ("est_passengers", "est_passengers_adjusted"):
        pax = pd.to_numeric(fct[pax_col], errors="coerce")
        seats = pd.to_numeric(fct["seats_config"], errors="coerce")
        both_valid = pax.notna() & seats.notna()
        if (pax.loc[both_valid] > seats.loc[both_valid]).any():
            raise ExportValidationError(f"looker_flights.{pax_col} exceeds seats_config")

    y2019_mask = pd.to_numeric(fct["year"], errors="coerce") == 2019
    if y2019_mask.any() and fct.loc[y2019_mask, "soi_planning"].notna().any():
        raise ExportValidationError("looker_flights 2019 rows must have NULL soi_planning")

    canc_mask = fct["is_cancelled"].fillna(False).astype(bool)
    if canc_mask.any() and fct.loc[canc_mask, "soi_historical"].notna().any():
        raise ExportValidationError("looker_flights cancelled rows must have NULL soi_historical")

    max_bytes = int(max_file_mb * 1024 * 1024)
    for fname, fpath in file_paths.items():
        if flight_mode == "full" and fname.endswith("_full.csv"):
            continue
        size = fpath.stat().st_size
        if size == 0:
            raise ExportValidationError(f"Exported Looker CSV {fname} is 0 bytes")
        if size >= max_bytes:
            raise ExportValidationError(
                f"Exported Looker CSV {fname} size {size:,} bytes exceeds {max_file_mb} MB limit"
            )


def _can_load_from_verified_tableau_extracts(
    config: StandbyConfig,
    flight_mode: str,
    max_flight_rows: int,
    stratify_seed: int,
) -> bool:
    """
    Check whether `config.exports_dir` (`exports/tableau/`) holds SHA-256-verified Tableau
    star-schema CSVs matching the requested `(hub, years, sample_mode, flight_mode)`.
    """
    meta_path = config.exports_dir / "export_metadata.json"
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    if (
        meta.get("hub") != config.active_hub
        or meta.get("years") != config.years
        or bool(meta.get("sample_mode")) != bool(config.sample_mode)
        or meta.get("flight_mode") != flight_mode
        or not meta.get("invariants_passed")
    ):
        return False
    if flight_mode != "full" and (
        int(meta.get("flight_row_budget", -1)) != max_flight_rows
        or int(meta.get("stratify_seed", -1)) != stratify_seed
    ):
        return False

    tableau_flight_fname = (
        "fct_flights_tableau_full.csv" if flight_mode == "full" else "fct_flights_tableau.csv"
    )
    required_files = (
        "agg_route_month.csv",
        "dim_airport.csv",
        "dim_aircraft.csv",
        "dim_carrier.csv",
        "dim_date.csv",
        tableau_flight_fname,
        "validation_summary.csv",
    )
    files_meta = meta.get("files", {})
    for fname in required_files:
        info = files_meta.get(fname)
        fpath = config.exports_dir / fname
        if not isinstance(info, dict) or not fpath.is_file():
            return False
        if int(fpath.stat().st_size) != int(info.get("bytes", -1)):
            return False
        if _sha256_file(fpath) != str(info.get("sha256", "")):
            return False
    return True


def _load_from_verified_tableau_extracts(
    config: StandbyConfig,
    flight_mode: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    int,
]:
    """Load verified star-schema tables from `config.exports_dir` (`exports/tableau/`)."""
    meta = json.loads((config.exports_dir / "export_metadata.json").read_text(encoding="utf-8"))
    total_source_flights = int(meta["total_source_flights"])
    tableau_flight_fname = (
        "fct_flights_tableau_full.csv" if flight_mode == "full" else "fct_flights_tableau.csv"
    )

    agg_route_month = pd.read_csv(config.exports_dir / "agg_route_month.csv", low_memory=False)
    for nullable_int in (
        "otp_scheduled_flights",
        "otp_performed_flights",
        "otp_cancelled_flights",
        "otp_diverted_flights",
    ):
        if nullable_int in agg_route_month.columns:
            agg_route_month[nullable_int] = (
                pd.to_numeric(agg_route_month[nullable_int], errors="coerce")
                .round()
                .astype("Int64")
            )

    dim_airport = pd.read_csv(config.exports_dir / "dim_airport.csv", low_memory=False)
    if "airport_id" in dim_airport.columns:
        dim_airport["airport_id"] = pd.to_numeric(
            dim_airport["airport_id"], errors="coerce"
        ).astype("Int64")

    dim_aircraft = pd.read_csv(
        config.exports_dir / "dim_aircraft.csv",
        dtype={"bts_aircraft_type": str},
        low_memory=False,
    )
    dim_carrier = pd.read_csv(config.exports_dir / "dim_carrier.csv", low_memory=False)
    fct_flights_tableau = pd.read_csv(
        config.exports_dir / tableau_flight_fname,
        dtype={
            "flight_num_mkt": str,
            "crs_dep_time": str,
            "cancellation_code": str,
            "bts_aircraft_type": str,
            "holiday_name": str,
        },
        low_memory=False,
    )
    for int_col in (
        "seats_config",
        "est_passengers",
        "est_open_seats",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
        "prior_strict_scheduled_count",
        "remaining_scheduled_backups",
        "remaining_performed_backups",
    ):
        if int_col in fct_flights_tableau.columns:
            fct_flights_tableau[int_col] = (
                pd.to_numeric(fct_flights_tableau[int_col], errors="coerce").round().astype("Int64")
            )

    validation_summary_df = pd.read_csv(
        config.exports_dir / "validation_summary.csv", low_memory=False
    )
    if "sample_n" in validation_summary_df.columns:
        validation_summary_df["sample_n"] = (
            pd.to_numeric(validation_summary_df["sample_n"], errors="coerce")
            .round()
            .astype("Int64")
        )

    return (
        agg_route_month,
        dim_airport,
        dim_aircraft,
        dim_carrier,
        fct_flights_tableau,
        validation_summary_df,
        total_source_flights,
    )


def export_looker_extracts(
    config: StandbyConfig,
    output_dir: Path | None = None,
    flight_mode: str | None = None,
    max_flight_rows: int | None = None,
    stratify_seed: int | None = None,
    float_precision: int | None = None,
    from_processed: bool = True,
    validation_summary_path: Path | None = None,
) -> LookerExportResult:
    """
    Generate pre-joined (denormalized) Google Looker Studio CSV extracts and
    `export_metadata.json` in `output_dir`.
    """
    resolved_mode = (flight_mode or config.export.tableau_flight_mode).strip().lower()
    if resolved_mode not in FLIGHT_EXPORT_MODES:
        raise ValueError(
            f"Invalid flight_mode {resolved_mode!r}; expected one of {FLIGHT_EXPORT_MODES}"
        )
    resolved_max_rows = (
        int(max_flight_rows)
        if max_flight_rows is not None
        else int(config.export.tableau_flight_max_rows)
    )
    if resolved_mode != "full" and resolved_max_rows < 1:
        raise ValueError(
            f"max_flight_rows must be >= 1 for flight_mode {resolved_mode!r}, got {resolved_max_rows}"
        )
    resolved_seed = (
        int(stratify_seed)
        if stratify_seed is not None
        else int(config.export.tableau_stratify_seed)
    )
    resolved_precision = (
        int(float_precision)
        if float_precision is not None
        else int(config.export.tableau_float_precision)
    )
    resolved_out_dir = (
        output_dir or _resolve_default_looker_output_dir(config, resolved_mode)
    ).resolve()
    resolved_out_dir.mkdir(parents=True, exist_ok=True)

    mode_dir = config.processed_dir / ("sample" if config.sample_mode else "full")
    has_processed_parquets = all(
        (mode_dir / fname).is_file()
        for fname in (
            "otp_marketing_flights.parquet",
            "t100_segments.parquet",
            "bts_lookups.parquet",
            "tsa_daily_throughput.parquet",
        )
    )
    has_raw_sources = (config.active_data_dir / "tsa" / "tsa_checkpoint_volumes.csv").is_file()

    if (
        from_processed
        and not has_processed_parquets
        and not has_raw_sources
        and _can_load_from_verified_tableau_extracts(
            config=config,
            flight_mode=resolved_mode,
            max_flight_rows=resolved_max_rows,
            stratify_seed=resolved_seed,
        )
    ):
        logger.info(
            "Building Looker Studio extracts from SHA-256-verified Tableau extracts in %s",
            config.exports_dir,
        )
        (
            agg_route_month,
            dim_airport,
            dim_aircraft,
            dim_carrier,
            fct_flights_tableau,
            validation_summary_df,
            total_source_flights,
        ) = _load_from_verified_tableau_extracts(config=config, flight_mode=resolved_mode)
        flights_for_soi_rollup = fct_flights_tableau
    else:
        flights_raw, t100_df, lookups_df, tsa_df = _load_source_tables_for_export(
            config=config,
            from_processed=from_processed,
        )
        hub_set = _resolve_hub_set(config)
        year_set = set(config.years)
        mkt_set = {c.strip().upper() for c in config.marketing_carriers} or {"UA"}

        flights_df = flights_raw.copy()
        flights_df["origin"] = flights_df["origin"].astype(str).str.strip().str.upper()
        flights_df["dest"] = flights_df["dest"].astype(str).str.strip().str.upper()
        flights_df["op_carrier"] = flights_df["op_carrier"].astype(str).str.strip().str.upper()
        flights_df["mkt_carrier"] = flights_df["mkt_carrier"].astype(str).str.strip().str.upper()
        flights_df = flights_df.loc[
            flights_df["origin"].isin(hub_set)
            & flights_df["year"].isin(year_set)
            & flights_df["mkt_carrier"].isin(mkt_set)
        ].copy()

        if "flight_id" not in flights_df.columns:
            flights_df["flight_id"] = _build_flight_id(flights_df)
        total_source_flights = int(len(flights_df))

        agg_route_month = _build_agg_route_month(
            flights_df=flights_df, t100_df=t100_df, config=config
        )
        dim_airport = _build_dim_airport(
            flights_df=flights_df, t100_df=t100_df, bts_lookups_df=lookups_df, config=config
        )
        dim_aircraft = _build_dim_aircraft(flights_df=flights_df, config=config)
        dim_carrier = _build_dim_carrier(
            flights_df=flights_df, t100_df=t100_df, bts_lookups_df=lookups_df, config=config
        )
        _ = _build_dim_date(flights_df=flights_df, tsa_df=tsa_df, config=config)

        selected_flights = _select_flights_for_export(
            flights_df=flights_df,
            flight_mode=resolved_mode,
            max_flight_rows=resolved_max_rows,
            stratify_seed=resolved_seed,
        )
        fct_flights_tableau = _format_fct_flights_tableau(selected_flights)
        flights_for_soi_rollup = flights_df

        default_val_path = (
            mode_dir / "phase4_validation_summary.json"
            if config.sample_mode
            else config.repo_root / "docs" / "phase4_validation_summary.json"
        )
        resolved_val_path = validation_summary_path or default_val_path
        val_json: dict[str, Any] | None = None
        if resolved_val_path.is_file():
            candidate_json = json.loads(resolved_val_path.read_text(encoding="utf-8"))
            candidate_meta = candidate_json.get("metadata", {})
            if validation_summary_path is not None or (
                candidate_meta.get("hub") == config.active_hub
                and candidate_meta.get("years") == config.years
            ):
                val_json = candidate_json
        if val_json is None:
            from standby.validation.soi_validation import run_soi_validation

            report = run_soi_validation(
                flights_df=flights_df,
                t100_df=t100_df,
                tsa_df=tsa_df,
                config=config,
                sample_mode=config.sample_mode,
                summary_path=None,
                raise_on_failure=False,
            )
            val_json = report.to_dict()
        validation_summary_df = _build_validation_summary_df(val_json)

    looker_flights = _build_looker_flights(
        fct_flights_tableau=fct_flights_tableau,
        dim_airport=dim_airport,
        dim_carrier=dim_carrier,
        dim_aircraft=dim_aircraft,
    )
    looker_route_month = _build_looker_route_month(
        agg_route_month=agg_route_month,
        flights_for_soi_rollup=flights_for_soi_rollup,
        dim_airport=dim_airport,
        dim_carrier=dim_carrier,
    )
    looker_route_summary = _build_looker_route_summary(
        looker_route_month_df=looker_route_month,
        flights_for_soi_rollup=flights_for_soi_rollup,
        dim_airport=dim_airport,
    )

    flight_filename = _looker_flight_filename(resolved_mode)
    output_frames: dict[str, pd.DataFrame] = {
        flight_filename: _round_float_columns(looker_flights, resolved_precision),
        "looker_route_month.csv": _round_float_columns(looker_route_month, resolved_precision),
        "looker_route_summary.csv": _round_float_columns(looker_route_summary, resolved_precision),
        "looker_validation_summary.csv": _round_float_columns(
            validation_summary_df, resolved_precision
        ),
    }

    metadata_path = resolved_out_dir / "export_metadata.json"
    staging_dir = Path(tempfile.mkdtemp(prefix=_STAGING_DIR_PREFIX, dir=resolved_out_dir))
    try:
        staged_paths: dict[str, Path] = {}
        for fname, df in output_frames.items():
            fpath = staging_dir / fname
            df.to_csv(fpath, index=False, encoding="utf-8")
            staged_paths[fname] = fpath

        validation_tables = {
            "looker_flights": output_frames[flight_filename],
            "looker_route_month.csv": output_frames["looker_route_month.csv"],
            "looker_route_summary.csv": output_frames["looker_route_summary.csv"],
            "looker_validation_summary.csv": output_frames["looker_validation_summary.csv"],
        }
        _validate_looker_tables(
            tables=validation_tables,
            file_paths=staged_paths,
            flight_mode=resolved_mode,
            max_file_mb=float(config.export.tableau_max_file_mb),
        )

        files_meta: dict[str, ExportedFileInfo] = {}
        files_json: dict[str, dict[str, Any]] = {}
        for fname, fpath in staged_paths.items():
            df = output_frames[fname]
            size_bytes = int(fpath.stat().st_size)
            sha = _sha256_file(fpath)
            files_meta[fname] = ExportedFileInfo(
                filename=fname,
                path=resolved_out_dir / fname,
                rows=int(len(df)),
                columns=int(len(df.columns)),
                bytes=size_bytes,
                sha256=sha,
            )
            files_json[fname] = {
                "rows": int(len(df)),
                "columns": int(len(df.columns)),
                "bytes": size_bytes,
                "sha256": sha,
            }

        metadata_payload = {
            "bi_target": "looker_studio",
            "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hub": config.active_hub,
            "years": config.years,
            "sample_mode": config.sample_mode,
            "flight_mode": resolved_mode,
            "flight_row_budget": (
                resolved_max_rows if resolved_mode != "full" else total_source_flights
            ),
            "stratify_seed": resolved_seed,
            "float_precision": resolved_precision,
            "total_source_flights": total_source_flights,
            "exported_flights": int(len(looker_flights)),
            "invariants_passed": True,
            "files": files_json,
        }
        staged_metadata = staging_dir / "export_metadata.json"
        staged_metadata.write_text(json.dumps(metadata_payload, indent=2) + "\n", encoding="utf-8")

        previous_files = _files_listed_in_metadata(metadata_path)
        metadata_path.unlink(missing_ok=True)
        for fname, fpath in staged_paths.items():
            os.replace(fpath, resolved_out_dir / fname)

        other_mode = "stratified" if resolved_mode == "full" else "full"
        stale_flight_path = resolved_out_dir / _looker_flight_filename(other_mode)
        if stale_flight_path.exists() and stale_flight_path.name in previous_files:
            stale_flight_path.unlink()

        os.replace(staged_metadata, metadata_path)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return LookerExportResult(
        output_dir=resolved_out_dir,
        metadata_path=metadata_path,
        sample_mode=config.sample_mode,
        flight_mode=resolved_mode,
        flight_row_budget=resolved_max_rows if resolved_mode != "full" else total_source_flights,
        total_source_flights=total_source_flights,
        exported_flights=int(len(looker_flights)),
        files=files_meta,
    )
