"""Tableau Public CSV extract exporter aligned 1:1 with `dbt/models/marts/`."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from standby.config import DEFAULT_UA_FAMILY_CARRIERS, StandbyConfig
from standby.ingest import ingest_all_sources
from standby.manifest import verify_manifest
from standby.schemas import validate_all_ingested
from standby.scoring.holidays import holiday_name_series
from standby.scoring.soi import compute_tsa_annual_ratios, score_flights

logger = logging.getLogger("standby.export.tableau")

FLIGHT_EXPORT_MODES: tuple[str, ...] = ("stratified", "recent-12m", "full")

AGG_ROUTE_MONTH_COLUMNS: tuple[str, ...] = (
    "route_month_id",
    "year",
    "quarter",
    "month",
    "op_carrier",
    "origin",
    "origin_country",
    "dest",
    "dest_country",
    "is_international",
    "scope_label",
    "data_granularity",
    "departures_scheduled",
    "departures_performed",
    "seats",
    "passengers",
    "distance_miles",
    "aircraft_type_count",
    "t100_load_factor",
    "t100_seats_per_departure",
    "is_shared_regional_spoke",
    "otp_scheduled_flights",
    "otp_performed_flights",
    "otp_cancelled_flights",
    "otp_diverted_flights",
    "otp_cancellation_rate",
    "otp_avg_dep_delay_min",
    "otp_avg_arr_delay_min",
    "otp_avg_est_open_seats",
    "otp_avg_est_open_seats_adjusted",
)

DIM_AIRPORT_COLUMNS: tuple[str, ...] = (
    "airport_code",
    "airport_id",
    "airport_name",
    "city_name",
    "state_or_country",
    "country_code",
    "is_hub",
    "has_domestic_service",
    "has_international_service",
    "confidence_tier",
)

DIM_AIRCRAFT_COLUMNS: tuple[str, ...] = (
    "tail_num",
    "tail_num_clean",
    "aircraft_source",
    "faa_manufacturer",
    "faa_model",
    "faa_tc_max_seats",
    "fleet_subtype",
    "bts_aircraft_type",
    "op_carrier_group",
    "seat_config_source",
    "seats_config",
    "seats_premium",
    "seats_economy",
    "operating_carriers",
    "operating_carrier_count",
    "observed_flights_count",
    "first_observed_flight_date",
    "last_observed_flight_date",
    "source_url",
    "as_of",
    "override_reason",
    "confidence_tier",
)

DIM_CARRIER_COLUMNS: tuple[str, ...] = (
    "op_carrier",
    "carrier_name",
    "carrier_group",
    "is_mainline",
    "is_shared_cpa_carrier",
    "history_summary",
    "history_era_count",
    "confidence_tier",
)

DIM_DATE_COLUMNS: tuple[str, ...] = (
    "date_day",
    "year",
    "quarter",
    "month",
    "day_of_month",
    "day_of_week",
    "day_name",
    "is_weekend",
    "is_holiday_window",
    "holiday_name",
    "tsa_throughput",
    "tsa_ratio_month",
    "tsa_demand_multiplier",
    "tsa_ratio_year",
)

FCT_FLIGHTS_TABLEAU_COLUMNS: tuple[str, ...] = (
    "flight_id",
    "flight_date",
    "year",
    "month",
    "day_of_week",
    "op_carrier",
    "flight_num_mkt",
    "tail_num",
    "origin",
    "dest",
    "crs_dep_time",
    "dep_hour",
    "hour_block",
    "dep_delay_min",
    "arr_delay_min",
    "is_cancelled",
    "cancellation_code",
    "is_diverted",
    "distance_miles",
    "fleet_subtype",
    "bts_aircraft_type",
    "seats_config",
    "t100_available",
    "lf_source",
    "is_shared_regional_spoke",
    "est_load_factor",
    "est_passengers",
    "est_open_seats",
    "tsa_demand_multiplier",
    "hour_block_multiplier",
    "est_lf_adjusted",
    "est_passengers_adjusted",
    "est_open_seats_adjusted",
    "prior_strict_scheduled_count",
    "weighted_displacement_hist",
    "remaining_scheduled_backups",
    "remaining_performed_backups",
    "is_holiday_window",
    "holiday_name",
    "confidence_tier",
    "record_confidence_summary",
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
)

ROUTE_MAP_PATHS_COLUMNS: tuple[str, ...] = (
    "path_id",
    "origin",
    "dest",
    "path_order",
    "airport_code",
    "route_label",
    "is_international",
    "scope_label",
    "data_granularity",
    "total_departures_performed",
    "total_seats",
    "total_passengers",
    "avg_t100_load_factor",
    "months_active",
)

VALIDATION_SUMMARY_COLUMNS: tuple[str, ...] = (
    "pillar",
    "metric_group",
    "metric_name",
    "metric_value",
    "sample_n",
    "status_or_gate",
    "notes",
)

_FORBIDDEN_PII_COLUMNS: frozenset[str] = frozenset(
    {
        "passenger_name",
        "first_name",
        "last_name",
        "full_name",
        "pnr",
        "record_locator",
        "ticket_number",
        "email",
        "phone",
        "phone_number",
        "ssn",
        "employee_id",
        "employee_name",
        "pass_rider_name",
        "seniority_date",
        "hire_date",
        "address",
    }
)

_SOI_SCORE_AND_SUBSCORE_COLS: tuple[str, ...] = (
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
)

_SC5_NULL_REQUIRED_COLS: tuple[str, ...] = (
    "est_load_factor",
    "est_passengers",
    "est_open_seats",
    "est_lf_adjusted",
    "est_passengers_adjusted",
    "est_open_seats_adjusted",
    *_SOI_SCORE_AND_SUBSCORE_COLS,
)


class ExportValidationError(ValueError):
    """Raised when post-export Tableau invariant checks fail."""


@dataclass(frozen=True)
class ExportedFileInfo:
    """Metadata for a single exported Tableau artifact."""

    filename: str
    path: Path
    rows: int
    columns: int
    bytes: int
    sha256: str


@dataclass(frozen=True)
class TableauExportResult:
    """Result summary returned by `export_tableau_extracts`."""

    output_dir: Path
    metadata_path: Path
    sample_mode: bool
    flight_mode: str
    flight_row_budget: int
    total_source_flights: int
    exported_flights: int
    files: dict[str, ExportedFileInfo] = field(default_factory=dict)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _round_float_columns(df: pd.DataFrame, precision: int) -> pd.DataFrame:
    """Round float64 columns to `precision` decimals while preserving integer/nullable Int64 types."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col].dtype):
            out[col] = out[col].round(precision)
    return out


def _normalize_flight_date_str(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d")


def _build_flight_id(df: pd.DataFrame) -> pd.Series:
    """Deterministic `flight_id` matching `dbt/models/staging/stg_otp_flights.sql`."""
    date_part = pd.to_datetime(df["flight_date"], errors="coerce").dt.strftime("%Y%m%d")
    mkt = df["mkt_carrier"].astype(str).str.strip().str.upper()
    flt_num = df["flight_num_mkt"].astype(str).str.strip()
    origin = df["origin"].astype(str).str.strip().str.upper()
    dest = df["dest"].astype(str).str.strip().str.upper()
    crs_dep = (
        df["crs_dep_time"]
        .astype(str)
        .str.strip()
        .str.replace(r"\.0+$", "", regex=True)
        .str.zfill(4)
    )
    crs_dep = crs_dep.where(crs_dep != "2400", "0000")
    return date_part + "|" + mkt + "|" + flt_num + "|" + origin + "|" + dest + "|" + crs_dep


def _parse_airport_location(airport_name: Any) -> tuple[str | None, str | None]:
    """Parse `(city_name, state_or_country)` from BTS `L_AIRPORT` `'City, ST: Airport'` text."""
    if airport_name is None or (isinstance(airport_name, float) and np.isnan(airport_name)):
        return None, None
    text = str(airport_name).strip()
    if not text:
        return None, None
    loc_part = text.split(":", 1)[0].strip()
    if "," in loc_part:
        city_part, region_part = loc_part.rsplit(",", 1)
        city = city_part.strip() or None
        region = region_part.strip() or None
        return city, region
    return (loc_part or None), None


def _resolve_hub_set(config: StandbyConfig) -> set[str]:
    """Resolve target hub set, respecting CLI `--hub` (`config.active_hub`) overrides."""
    if config.active_hub and config.active_hub.strip():
        return {config.active_hub.strip().upper()}
    if config.hubs:
        return {h.strip().upper() for h in config.hubs if h.strip()}
    return {"ORD"}


def _build_agg_route_month(
    flights_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    config: StandbyConfig,
) -> pd.DataFrame:
    """Build `agg_route_month` matching `dbt/models/marts/agg_route_month.sql` 1:1."""
    ua_family = config.all_operating_carriers or set(DEFAULT_UA_FAMILY_CARRIERS)
    hub_set = _resolve_hub_set(config)
    year_set = set(config.years)

    t100 = t100_df.copy()
    t100["op_carrier"] = t100["op_carrier"].astype(str).str.strip().str.upper()
    t100["origin"] = t100["origin"].astype(str).str.strip().str.upper()
    t100["dest"] = t100["dest"].astype(str).str.strip().str.upper()
    t100["service_class"] = t100["service_class"].astype(str).str.strip().str.upper()
    t100["is_international"] = t100["is_international"].astype(bool)

    t100 = t100.loc[
        (t100["service_class"] == "F")
        & (t100["origin"].isin(hub_set))
        & (t100["year"].isin(year_set))
        & (t100["op_carrier"].isin(ua_family))
    ].copy()

    t100["_dist_x_deps"] = pd.to_numeric(t100["distance_miles"], errors="coerce") * pd.to_numeric(
        t100["departures_performed"], errors="coerce"
    )
    group_cols = ["year", "month", "op_carrier", "origin", "dest", "is_international"]
    t100_agg = t100.groupby(group_cols, as_index=False, sort=True).agg(
        quarter=("quarter", "min"),
        origin_country=("origin_country", "max"),
        dest_country=("dest_country", "max"),
        departures_scheduled=("departures_scheduled", "sum"),
        departures_performed=("departures_performed", "sum"),
        seats=("seats", "sum"),
        passengers=("passengers", "sum"),
        _sum_dist_x_deps=("_dist_x_deps", "sum"),
        _max_distance=("distance_miles", "max"),
        aircraft_type_count=("bts_aircraft_type", "nunique"),
    )

    deps_perf = t100_agg["departures_performed"].to_numpy(dtype="float64")
    t100_agg["distance_miles"] = np.where(
        deps_perf > 0.0,
        t100_agg["_sum_dist_x_deps"].to_numpy(dtype="float64")
        / np.where(deps_perf > 0, deps_perf, 1.0),
        t100_agg["_max_distance"].to_numpy(dtype="float64"),
    )
    seats_arr = t100_agg["seats"].to_numpy(dtype="float64")
    pax_arr = t100_agg["passengers"].to_numpy(dtype="float64")
    raw_lf = np.where(seats_arr > 0.0, pax_arr / np.where(seats_arr > 0.0, seats_arr, 1.0), np.nan)
    t100_agg["t100_load_factor"] = np.where(np.isnan(raw_lf), np.nan, np.clip(raw_lf, 0.0, 1.0))
    t100_agg["t100_seats_per_departure"] = np.where(
        deps_perf > 0.0,
        seats_arr / np.where(deps_perf > 0.0, deps_perf, 1.0),
        np.nan,
    )
    t100_agg = t100_agg.drop(columns=["_sum_dist_x_deps", "_max_distance"])

    # Monthly OTP aggregations (domestic only)
    flt = flights_df.copy()
    flt["is_cancelled"] = flt["is_cancelled"].astype(bool)
    flt["is_diverted"] = flt["is_diverted"].astype(bool)
    flt["_perf"] = (~flt["is_cancelled"]).astype("int64")
    flt["_canc"] = flt["is_cancelled"].astype("int64")
    flt["_div"] = flt["is_diverted"].astype("int64")
    flt["_dep_delay_perf"] = pd.to_numeric(flt["dep_delay_min"], errors="coerce").where(
        ~flt["is_cancelled"]
    )
    flt["_arr_delay_comp"] = pd.to_numeric(flt["arr_delay_min"], errors="coerce").where(
        (~flt["is_cancelled"]) & (~flt["is_diverted"])
    )
    flt["_open_base"] = pd.to_numeric(flt["est_open_seats"], errors="coerce")
    flt["_open_adj"] = pd.to_numeric(flt["est_open_seats_adjusted"], errors="coerce")
    flt["_shared"] = flt["is_shared_regional_spoke"].fillna(False).astype(bool)

    otp_monthly = flt.groupby(
        ["year", "month", "op_carrier", "origin", "dest"], as_index=False, sort=True
    ).agg(
        otp_scheduled_flights=("flight_id", "count"),
        otp_performed_flights=("_perf", "sum"),
        otp_cancelled_flights=("_canc", "sum"),
        otp_diverted_flights=("_div", "sum"),
        otp_avg_dep_delay_min=("_dep_delay_perf", "mean"),
        otp_avg_arr_delay_min=("_arr_delay_comp", "mean"),
        otp_avg_est_open_seats=("_open_base", "mean"),
        otp_avg_est_open_seats_adjusted=("_open_adj", "mean"),
        is_shared_regional_spoke=("_shared", "any"),
    )
    otp_monthly["is_international"] = False
    otp_monthly["otp_cancellation_rate"] = np.where(
        otp_monthly["otp_scheduled_flights"] > 0,
        otp_monthly["otp_cancelled_flights"] / otp_monthly["otp_scheduled_flights"],
        np.nan,
    )

    merged = t100_agg.merge(
        otp_monthly,
        on=["year", "month", "op_carrier", "origin", "dest", "is_international"],
        how="left",
    )
    merged["is_shared_regional_spoke"] = merged["is_shared_regional_spoke"].eq(True).astype(bool)
    merged["route_month_id"] = (
        merged["year"].astype(int).astype(str)
        + "-"
        + merged["month"].astype(int).astype(str).str.zfill(2)
        + "|"
        + merged["op_carrier"]
        + "|"
        + merged["origin"]
        + "|"
        + merged["dest"]
        + "|"
        + np.where(merged["is_international"], "INTL", "DOM")
    )
    merged["scope_label"] = np.where(
        merged["is_international"],
        "International (T-100 Route-Month Only v1)",
        "Domestic (OTP Flight + T-100)",
    )
    merged["data_granularity"] = np.where(
        merged["is_international"],
        "route_month_only_v1",
        "flight_and_route_month",
    )

    for int_col in (
        "year",
        "quarter",
        "month",
        "aircraft_type_count",
    ):
        merged[int_col] = merged[int_col].astype("int64")
    for nullable_int in (
        "otp_scheduled_flights",
        "otp_performed_flights",
        "otp_cancelled_flights",
        "otp_diverted_flights",
    ):
        merged[nullable_int] = pd.array(
            np.where(
                merged["is_international"] | merged[nullable_int].isna(),
                pd.NA,
                merged[nullable_int],
            ),
            dtype="Int64",
        )

    # Ensure strictly NULL OTP metrics on international rows
    intl_mask = merged["is_international"]
    for otp_float_col in (
        "otp_cancellation_rate",
        "otp_avg_dep_delay_min",
        "otp_avg_arr_delay_min",
        "otp_avg_est_open_seats",
        "otp_avg_est_open_seats_adjusted",
    ):
        merged.loc[intl_mask, otp_float_col] = np.nan

    merged = merged.sort_values(group_cols, kind="mergesort").reset_index(drop=True)
    return merged[list(AGG_ROUTE_MONTH_COLUMNS)]


def _build_dim_airport(
    flights_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    bts_lookups_df: pd.DataFrame,
    config: StandbyConfig,
) -> pd.DataFrame:
    """Build `dim_airport` matching `dbt/models/marts/dim_airport.sql` plus parsed city/region."""
    ua_family = config.all_operating_carriers or set(DEFAULT_UA_FAMILY_CARRIERS)
    hub_set = _resolve_hub_set(config)
    year_set = set(config.years)

    otp_orig = pd.DataFrame(
        {
            "airport_code": flights_df["origin"].astype(str).str.strip().str.upper(),
            "airport_id": pd.to_numeric(flights_df["origin_airport_id"], errors="coerce"),
            "country_code": "US",
            "has_domestic_service": True,
            "has_international_service": False,
        }
    )
    otp_dest = pd.DataFrame(
        {
            "airport_code": flights_df["dest"].astype(str).str.strip().str.upper(),
            "airport_id": pd.to_numeric(flights_df["dest_airport_id"], errors="coerce"),
            "country_code": "US",
            "has_domestic_service": True,
            "has_international_service": False,
        }
    )

    t100 = t100_df.copy()
    t100["op_carrier"] = t100["op_carrier"].astype(str).str.strip().str.upper()
    t100["origin"] = t100["origin"].astype(str).str.strip().str.upper()
    t100["dest"] = t100["dest"].astype(str).str.strip().str.upper()
    t100["service_class"] = t100["service_class"].astype(str).str.strip().str.upper()
    t100_ua = t100.loc[
        (t100["service_class"] == "F")
        & (t100["origin"].isin(hub_set))
        & (t100["year"].isin(year_set))
        & (t100["op_carrier"].isin(ua_family))
    ].copy()

    t100_orig = pd.DataFrame(
        {
            "airport_code": t100_ua["origin"],
            "airport_id": pd.to_numeric(t100_ua["origin_airport_id"], errors="coerce"),
            "country_code": t100_ua["origin_country"].astype(str).str.strip().str.upper(),
            "has_domestic_service": ~t100_ua["is_international"].astype(bool),
            "has_international_service": t100_ua["is_international"].astype(bool),
        }
    )
    t100_dest = pd.DataFrame(
        {
            "airport_code": t100_ua["dest"],
            "airport_id": pd.to_numeric(t100_ua["dest_airport_id"], errors="coerce"),
            "country_code": t100_ua["dest_country"].astype(str).str.strip().str.upper(),
            "has_domestic_service": ~t100_ua["is_international"].astype(bool),
            "has_international_service": t100_ua["is_international"].astype(bool),
        }
    )

    combined = pd.concat([otp_orig, otp_dest, t100_orig, t100_dest], ignore_index=True)
    combined = combined.loc[
        combined["airport_code"].notna() & (combined["airport_code"] != "")
    ].copy()

    agg = combined.groupby("airport_code", as_index=False, sort=True).agg(
        observed_airport_id=("airport_id", "max"),
        country_code=("country_code", "max"),
        has_domestic_service=("has_domestic_service", "any"),
        has_international_service=("has_international_service", "any"),
    )

    lkp = bts_lookups_df.copy()
    lkp["lookup_table"] = lkp["lookup_table"].astype(str).str.strip()
    lkp_air = (
        lkp.loc[lkp["lookup_table"] == "L_AIRPORT", ["code", "description"]]
        .assign(
            airport_code=lambda d: d["code"].astype(str).str.strip().str.upper(),
            airport_name=lambda d: d["description"].astype(str).str.strip(),
        )
        .loc[lambda d: d["airport_code"] != ""]
        .sort_values(["airport_code", "airport_name"], kind="mergesort")
        .drop_duplicates(subset=["airport_code"], keep="first")[["airport_code", "airport_name"]]
    )
    lkp_ids = (
        lkp.loc[lkp["lookup_table"] == "L_AIRPORT_ID", ["code", "description"]]
        .assign(
            lkp_airport_id=lambda d: pd.to_numeric(
                d["code"].astype(str).str.strip(), errors="coerce"
            ),
            _name_lower=lambda d: d["description"].astype(str).str.strip().str.lower(),
        )
        .dropna(subset=["lkp_airport_id"])
        .sort_values(["_name_lower", "lkp_airport_id"], ascending=[True, False], kind="mergesort")
        .drop_duplicates(subset=["_name_lower"], keep="first")[["_name_lower", "lkp_airport_id"]]
    )

    out = agg.merge(lkp_air, on="airport_code", how="left")
    out["_name_lower"] = out["airport_name"].fillna("").astype(str).str.strip().str.lower()
    out = out.merge(lkp_ids, on="_name_lower", how="left")
    obs_id = pd.to_numeric(out["observed_airport_id"], errors="coerce")
    lkp_id = pd.to_numeric(out["lkp_airport_id"], errors="coerce")
    out["airport_id"] = pd.Series(
        np.where(obs_id.notna(), obs_id, lkp_id),
        index=out.index,
    ).astype("Int64")
    out["country_code"] = out["country_code"].fillna("US")
    out["is_hub"] = out["airport_code"].isin(hub_set)
    out["has_domestic_service"] = out["has_domestic_service"].astype(bool)
    out["has_international_service"] = out["has_international_service"].astype(bool)
    out["confidence_tier"] = "actual"

    parsed_locs = [_parse_airport_location(v) for v in out["airport_name"]]
    out["city_name"] = [p[0] for p in parsed_locs]
    out["state_or_country"] = [p[1] for p in parsed_locs]

    out = out.sort_values("airport_code", kind="mergesort").reset_index(drop=True)
    return out[list(DIM_AIRPORT_COLUMNS)]


def _build_dim_aircraft(flights_df: pd.DataFrame, config: StandbyConfig) -> pd.DataFrame:
    """Build `dim_aircraft` matching `dbt/models/marts/dim_aircraft.sql` 1:1."""
    obs = flights_df.loc[flights_df["tail_num"].notna()].copy()
    obs["tail_num"] = obs["tail_num"].astype(str).str.strip().str.upper()
    obs = obs.loc[obs["tail_num"] != ""].copy()
    obs["_flight_date_str"] = _normalize_flight_date_str(obs["flight_date"])

    carrier_agg = obs.groupby("tail_num", as_index=False, sort=True).agg(
        operating_carriers=("op_carrier", lambda s: ",".join(sorted({str(x) for x in s}))),
        operating_carrier_count=("op_carrier", "nunique"),
        observed_flights_count=("flight_id", "count"),
        first_observed_flight_date=("_flight_date_str", "min"),
        last_observed_flight_date=("_flight_date_str", "max"),
    )

    priority_map = {"TAIL_OVERRIDE": 1, "FAA_MASTER": 2, "FAA_DEREG": 3}
    obs["_src_prio"] = obs["aircraft_source"].map(priority_map).fillna(4).astype("int64")
    deduped = (
        obs.sort_values(
            ["tail_num", "_src_prio", "_flight_date_str"],
            ascending=[True, True, False],
            kind="mergesort",
        )
        .drop_duplicates(subset=["tail_num"], keep="first")[
            [
                "tail_num",
                "tail_num_clean",
                "aircraft_source",
                "faa_manufacturer",
                "faa_model",
                "faa_tc_max_seats",
                "fleet_subtype",
                "bts_aircraft_type",
                "seat_config_source",
                "seats_config",
                "seats_premium",
                "seats_economy",
            ]
        ]
        .copy()
    )

    seat_cfg = pd.read_csv(config.reference_dir / "seat_config.csv", dtype=str)
    seat_cfg["fleet_subtype"] = seat_cfg["subtype"].astype(str).str.strip()
    seat_cfg["op_carrier_group"] = seat_cfg["op_carrier_group"].astype(str).str.strip().str.upper()
    seat_cfg["sc_source_url"] = seat_cfg["source_url"].astype(str).str.strip()
    seat_cfg["sc_as_of"] = seat_cfg["as_of"].astype(str).str.strip()
    seat_cfg = seat_cfg[
        ["fleet_subtype", "op_carrier_group", "sc_source_url", "sc_as_of"]
    ].drop_duplicates(subset=["fleet_subtype"], keep="first")

    tail_ov = pd.read_csv(config.reference_dir / "tail_overrides.csv", dtype=str)
    tail_ov["tail_num"] = tail_ov["tail_num"].astype(str).str.strip().str.upper()
    tail_ov["ov_source_url"] = tail_ov["source_url"].astype(str).str.strip()
    tail_ov["ov_as_of"] = tail_ov["as_of"].astype(str).str.strip()
    tail_ov["override_reason"] = tail_ov["override_reason"].astype(str).str.strip()
    tail_ov = tail_ov[["tail_num", "ov_source_url", "ov_as_of", "override_reason"]].drop_duplicates(
        subset=["tail_num"], keep="first"
    )

    out = deduped.merge(carrier_agg, on="tail_num", how="inner")
    out = out.merge(seat_cfg, on="fleet_subtype", how="left")
    out = out.merge(tail_ov, on="tail_num", how="left")

    out["source_url"] = out["ov_source_url"].combine_first(out["sc_source_url"])
    out["as_of"] = out["ov_as_of"].combine_first(out["sc_as_of"])
    out["confidence_tier"] = np.where(out["seats_config"].notna(), "derived", "actual")

    for int_col in (
        "faa_tc_max_seats",
        "seats_config",
        "seats_premium",
        "seats_economy",
        "operating_carrier_count",
        "observed_flights_count",
    ):
        out[int_col] = pd.to_numeric(out[int_col], errors="coerce").astype("Int64")

    out = out.sort_values("tail_num", kind="mergesort").reset_index(drop=True)
    return out[list(DIM_AIRCRAFT_COLUMNS)]


def _build_dim_carrier(
    flights_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    bts_lookups_df: pd.DataFrame,
    config: StandbyConfig,
) -> pd.DataFrame:
    """Build `dim_carrier` matching `dbt/models/marts/dim_carrier.sql` 1:1."""
    ua_family = config.all_operating_carriers or set(DEFAULT_UA_FAMILY_CARRIERS)
    hub_set = _resolve_hub_set(config)
    year_set = set(config.years)

    t100 = t100_df.copy()
    t100["op_carrier"] = t100["op_carrier"].astype(str).str.strip().str.upper()
    t100["origin"] = t100["origin"].astype(str).str.strip().str.upper()
    t100["service_class"] = t100["service_class"].astype(str).str.strip().str.upper()
    t100_carriers = set(
        t100.loc[
            (t100["service_class"] == "F")
            & (t100["origin"].isin(hub_set))
            & (t100["year"].isin(year_set))
            & (t100["op_carrier"].isin(ua_family)),
            "op_carrier",
        ].unique()
    )
    otp_carriers = set(flights_df["op_carrier"].astype(str).str.strip().str.upper().unique())
    all_carriers = sorted(ua_family | otp_carriers | t100_carriers)

    lkp = bts_lookups_df.copy()
    lkp["lookup_table"] = lkp["lookup_table"].astype(str).str.strip()
    lkp_uc = (
        lkp.loc[lkp["lookup_table"] == "L_UNIQUE_CARRIERS", ["code", "description"]]
        .assign(
            op_carrier=lambda d: d["code"].astype(str).str.strip().str.upper(),
            carrier_name=lambda d: d["description"].astype(str).str.strip(),
        )
        .loc[lambda d: d["op_carrier"] != ""]
        .sort_values(["op_carrier", "carrier_name"], kind="mergesort")
        .drop_duplicates(subset=["op_carrier"], keep="first")[["op_carrier", "carrier_name"]]
    )
    lkp_hist_raw = lkp.loc[
        lkp["lookup_table"] == "L_CARRIER_HISTORY", ["code", "description"]
    ].copy()
    lkp_hist_raw["op_carrier"] = lkp_hist_raw["code"].astype(str).str.strip().str.upper()
    lkp_hist_raw["desc"] = lkp_hist_raw["description"].astype(str).str.strip()
    lkp_hist_raw = lkp_hist_raw.loc[lkp_hist_raw["op_carrier"] != ""].copy()
    lkp_hist = lkp_hist_raw.groupby("op_carrier", as_index=False, sort=True).agg(
        history_summary=("desc", lambda s: " | ".join(sorted({str(x) for x in s}))),
        history_era_count=("desc", "count"),
    )

    mkt_set = {c.strip().upper() for c in config.marketing_carriers} or {"UA"}
    shared_cpa = set(config.enrichment.shared_cpa_express_carriers)

    out = pd.DataFrame({"op_carrier": all_carriers})
    out = out.merge(lkp_uc, on="op_carrier", how="left")
    out = out.merge(lkp_hist, on="op_carrier", how="left")
    out["is_mainline"] = out["op_carrier"].isin(mkt_set)
    out["carrier_group"] = np.where(out["is_mainline"], "MAINLINE", "EXPRESS")
    out["is_shared_cpa_carrier"] = out["op_carrier"].isin(shared_cpa)
    out["history_era_count"] = (
        pd.to_numeric(out["history_era_count"], errors="coerce").fillna(0).astype("int64")
    )
    out["confidence_tier"] = "actual"

    out = out.sort_values("op_carrier", kind="mergesort").reset_index(drop=True)
    return out[list(DIM_CARRIER_COLUMNS)]


def _build_dim_date(
    flights_df: pd.DataFrame,
    tsa_df: pd.DataFrame,
    config: StandbyConfig,
) -> pd.DataFrame:
    """Build `dim_date` matching `dbt/models/marts/dim_date.sql` 1:1."""
    tsa_ratios = compute_tsa_annual_ratios(tsa_df)
    alpha = float(config.enrichment.tsa_damping_alpha)
    min_b, max_b = config.enrichment.tsa_multiplier_bounds
    if not tsa_ratios.empty:
        tsa_ratios["tsa_demand_multiplier"] = np.clip(
            1.0 + alpha * (tsa_ratios["tsa_ratio_month"] - 1.0),
            float(min_b),
            float(max_b),
        )
    else:
        tsa_ratios["tsa_demand_multiplier"] = pd.Series([], dtype="float64")

    flight_dates = pd.to_datetime(flights_df["flight_date"], errors="coerce").dropna()
    tsa_dates = pd.to_datetime(tsa_ratios["date_day"], errors="coerce").dropna()
    all_dates = pd.concat([flight_dates, tsa_dates], ignore_index=True)
    if all_dates.empty:
        return pd.DataFrame(columns=list(DIM_DATE_COLUMNS))

    min_d = all_dates.min()
    max_d = all_dates.max()
    spine = pd.date_range(min_d, max_d, freq="D")
    year_set = set(config.years)
    spine = spine[spine.year.isin(year_set)]

    out = pd.DataFrame({"_dt": spine})
    out["date_day"] = out["_dt"].dt.strftime("%Y-%m-%d")
    out["year"] = out["_dt"].dt.year.astype("int64")
    out["quarter"] = out["_dt"].dt.quarter.astype("int64")
    out["month"] = out["_dt"].dt.month.astype("int64")
    out["day_of_month"] = out["_dt"].dt.day.astype("int64")
    out["day_of_week"] = (out["_dt"].dt.dayofweek + 1).astype("int64")
    out["day_name"] = out["_dt"].dt.day_name()
    out["is_weekend"] = out["day_of_week"].isin([6, 7])
    out["holiday_name"] = holiday_name_series(out["date_day"])
    out["is_holiday_window"] = out["holiday_name"].notna().astype(bool)

    tsa_join = tsa_ratios[
        ["date_day", "tsa_throughput", "tsa_ratio_month", "tsa_demand_multiplier", "tsa_ratio_year"]
    ].copy()
    out = out.merge(tsa_join, on="date_day", how="left")
    out["tsa_throughput"] = pd.to_numeric(out["tsa_throughput"], errors="coerce").astype("Int64")

    out = out.sort_values("date_day", kind="mergesort").reset_index(drop=True)
    return out[list(DIM_DATE_COLUMNS)]


def _stratified_sample_flights(
    df: pd.DataFrame,
    max_rows: int,
    seed: int,
) -> pd.DataFrame:
    """
    Deterministic proportional stratified sample across `(year, month, op_carrier)`.

    Uses Hamilton largest-remainder allocation so total sampled rows equals `max_rows`
    exactly whenever `len(df) > max_rows`.
    """
    if len(df) <= max_rows:
        return df.sort_values(
            ["flight_date", "crs_dep_time", "flight_id"], kind="mergesort"
        ).reset_index(drop=True)
    if max_rows <= 0:
        raise ValueError(f"max_rows must be positive, got {max_rows}")

    work = df.sort_values(["year", "month", "op_carrier", "flight_id"], kind="mergesort").copy()
    strata_cols = ["year", "month", "op_carrier"]
    counts = work.groupby(strata_cols, sort=True).size()
    n_strata = len(counts)

    # Base quota: guarantee >= 1 row per stratum whenever feasible
    if n_strata <= max_rows:
        base = np.ones(n_strata, dtype="int64")
        base = np.minimum(base, counts.to_numpy(dtype="int64"))
    else:
        base = np.zeros(n_strata, dtype="int64")

    remaining_budget = max_rows - int(base.sum())
    avail = counts.to_numpy(dtype="int64") - base
    avail_total = int(avail.sum())

    if remaining_budget > 0 and avail_total > 0:
        exact_extra = remaining_budget * (avail / avail_total)
        extra_floor = np.floor(exact_extra).astype("int64")
        extra_floor = np.minimum(extra_floor, avail)
        alloc = base + extra_floor
        leftover = max_rows - int(alloc.sum())
        if leftover > 0:
            remainders = exact_extra - extra_floor
            # Break ties deterministically by stratum index order
            order = np.argsort(-remainders, kind="mergesort")
            for idx in order:
                if leftover <= 0:
                    break
                if alloc[idx] < counts.iloc[idx]:
                    alloc[idx] += 1
                    leftover -= 1
    else:
        alloc = base

    sampled_parts: list[pd.DataFrame] = []
    for i, (_key, grp) in enumerate(work.groupby(strata_cols, sort=True)):
        k = int(alloc[i])
        if k <= 0:
            continue
        if k >= len(grp):
            sampled_parts.append(grp)
        else:
            sampled_parts.append(grp.sample(n=k, random_state=seed + i))

    out = pd.concat(sampled_parts, ignore_index=True)
    return out.sort_values(
        ["flight_date", "crs_dep_time", "flight_id"], kind="mergesort"
    ).reset_index(drop=True)


def _select_flights_for_export(
    flights_df: pd.DataFrame,
    flight_mode: str,
    max_flight_rows: int,
    stratify_seed: int,
) -> pd.DataFrame:
    """Apply `stratified`, `recent-12m`, or `full` selection to `fct_flights_tableau`."""
    if flight_mode not in FLIGHT_EXPORT_MODES:
        raise ValueError(
            f"Unsupported flight_mode {flight_mode!r}; expected one of {FLIGHT_EXPORT_MODES}"
        )

    work = flights_df.copy()
    work["flight_date"] = _normalize_flight_date_str(work["flight_date"])

    if flight_mode == "full":
        return work.sort_values(
            ["flight_date", "crs_dep_time", "flight_id"], kind="mergesort"
        ).reset_index(drop=True)

    if flight_mode == "recent-12m":
        ym_pairs = (
            work[["year", "month"]]
            .drop_duplicates()
            .sort_values(["year", "month"], kind="mergesort")
            .tail(12)
        )
        recent = work.merge(ym_pairs, on=["year", "month"], how="inner")
        if max_flight_rows > 0 and len(recent) > max_flight_rows:
            return _stratified_sample_flights(recent, max_rows=max_flight_rows, seed=stratify_seed)
        return recent.sort_values(
            ["flight_date", "crs_dep_time", "flight_id"], kind="mergesort"
        ).reset_index(drop=True)

    # Default: stratified
    return _stratified_sample_flights(work, max_rows=max_flight_rows, seed=stratify_seed)


def _format_fct_flights_tableau(df: pd.DataFrame) -> pd.DataFrame:
    """Project and type-normalize the 51 BI columns for `fct_flights_tableau.csv`."""
    out = df[list(FCT_FLIGHTS_TABLEAU_COLUMNS)].copy()
    out["flight_date"] = _normalize_flight_date_str(out["flight_date"])
    for int_col in (
        "year",
        "month",
        "day_of_week",
        "dep_hour",
        "prior_strict_scheduled_count",
        "remaining_scheduled_backups",
        "remaining_performed_backups",
    ):
        out[int_col] = pd.to_numeric(out[int_col], errors="coerce").astype("int64")

    for nullable_int in (
        "seats_config",
        "est_passengers",
        "est_open_seats",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
    ):
        out[nullable_int] = pd.to_numeric(out[nullable_int], errors="coerce").astype("Int64")

    for bool_col in (
        "is_cancelled",
        "is_diverted",
        "t100_available",
        "is_shared_regional_spoke",
        "is_holiday_window",
    ):
        out[bool_col] = out[bool_col].astype(bool)

    return out


def _build_route_map_paths(agg_route_month_df: pd.DataFrame, config: StandbyConfig) -> pd.DataFrame:
    """
    Build 2-point (`path_order` 1=origin, 2=dest) rows per active ORD destination
    for Tableau's built-in `Airport` geographic role route map (no invented lat/lon).
    """
    hub_set = _resolve_hub_set(config)
    active = agg_route_month_df.loc[
        agg_route_month_df["origin"].isin(hub_set)
        & (agg_route_month_df["origin"] != agg_route_month_df["dest"])
    ].copy()

    if active.empty:
        return pd.DataFrame(columns=list(ROUTE_MAP_PATHS_COLUMNS))

    active["_ym"] = (
        active["year"].astype(int).astype(str)
        + "-"
        + active["month"].astype(int).astype(str).str.zfill(2)
    )
    summary = active.groupby(
        ["origin", "dest", "is_international", "scope_label", "data_granularity"],
        as_index=False,
        sort=True,
    ).agg(
        total_departures_performed=("departures_performed", "sum"),
        total_seats=("seats", "sum"),
        total_passengers=("passengers", "sum"),
        months_active=("_ym", "nunique"),
    )
    seats_arr = summary["total_seats"].to_numpy(dtype="float64")
    pax_arr = summary["total_passengers"].to_numpy(dtype="float64")
    raw_lf = np.where(seats_arr > 0.0, pax_arr / np.where(seats_arr > 0.0, seats_arr, 1.0), np.nan)
    summary["avg_t100_load_factor"] = np.where(np.isnan(raw_lf), np.nan, np.clip(raw_lf, 0.0, 1.0))
    summary["path_id"] = (
        summary["origin"]
        + "-"
        + summary["dest"]
        + "-"
        + np.where(summary["is_international"], "INTL", "DOM")
    )
    summary["route_label"] = summary["origin"] + " -> " + summary["dest"]

    p1 = summary.copy()
    p1["path_order"] = 1
    p1["airport_code"] = p1["origin"]

    p2 = summary.copy()
    p2["path_order"] = 2
    p2["airport_code"] = p2["dest"]

    paths = pd.concat([p1, p2], ignore_index=True)
    paths["path_order"] = paths["path_order"].astype("int64")
    paths["months_active"] = paths["months_active"].astype("int64")
    paths["is_international"] = paths["is_international"].astype(bool)
    paths = paths.sort_values(
        ["origin", "dest", "is_international", "path_order"], kind="mergesort"
    ).reset_index(drop=True)
    return paths[list(ROUTE_MAP_PATHS_COLUMNS)]


def _build_validation_summary_df(summary_json: dict[str, Any]) -> pd.DataFrame:
    """Flatten Phase 4 validation JSON into a compact BI-friendly CSV table."""
    rows: list[dict[str, Any]] = []
    meta = summary_json.get("metadata", {})
    cov = summary_json.get("coverage_and_invariants", {})

    rows.append(
        {
            "pillar": "Coverage & Invariants",
            "metric_group": "Dataset Scope",
            "metric_name": "total_flights",
            "metric_value": float(meta.get("total_flights", 0)),
            "sample_n": int(meta.get("total_flights", 0)),
            "status_or_gate": "PASS" if summary_json.get("passed") else "INFO",
            "notes": f"hub={meta.get('hub')}, sample_mode={meta.get('sample_mode')}",
        }
    )

    hist_dist = cov.get("soi_historical_distribution", {})
    if hist_dist:
        for k in ("mean", "median", "std", "p10", "p90"):
            if hist_dist.get(k) is not None:
                rows.append(
                    {
                        "pillar": "Coverage & Invariants",
                        "metric_group": "soi_historical",
                        "metric_name": k,
                        "metric_value": float(hist_dist[k]),
                        "sample_n": int(hist_dist.get("n", 0)),
                        "status_or_gate": "INFO",
                        "notes": "Completed flights only (is_cancelled = FALSE)",
                    }
                )

    plan_dist = cov.get("soi_planning_distribution", {})
    if plan_dist:
        for k in ("mean", "median", "std", "p10", "p90"):
            if plan_dist.get(k) is not None:
                rows.append(
                    {
                        "pillar": "Coverage & Invariants",
                        "metric_group": "soi_planning",
                        "metric_name": k,
                        "metric_value": float(plan_dist[k]),
                        "sample_n": int(plan_dist.get("n", 0)),
                        "status_or_gate": "INFO",
                        "notes": "Leakage-free 2023-2026 (2019 = NULL)",
                    }
                )

    for inv_key in ("sc5_missing_t100_null_invariant", "soi_bounds_check"):
        inv = cov.get(inv_key, {})
        if inv:
            rows.append(
                {
                    "pillar": "Coverage & Invariants",
                    "metric_group": inv.get("check_id", inv_key),
                    "metric_name": "total_violations",
                    "metric_value": float(inv.get("details", {}).get("total_violations", 0)),
                    "sample_n": int(meta.get("total_flights", 0)),
                    "status_or_gate": str(inv.get("status", "PASS")),
                    "notes": str(inv.get("message", "")),
                }
            )

    p1 = summary_json.get("pillar_1_time_based_backtest", {})
    fh = p1.get("fixed_holdout", {})
    if fh and fh.get("spearman_rho") is not None:
        for m_name in (
            "spearman_rho",
            "top_decile_hit_rate",
            "top_decile_lift",
            "planning_coverage_pct",
            "mae",
            "rmse",
        ):
            if fh.get(m_name) is not None:
                rows.append(
                    {
                        "pillar": "Pillar 1: Time-Based Backtest",
                        "metric_group": "fixed_holdout_2025_2026",
                        "metric_name": m_name,
                        "metric_value": float(fh[m_name]),
                        "sample_n": int(fh.get("paired_flights", 0)),
                        "status_or_gate": "PASS" if summary_json.get("passed") else "INFO",
                        "notes": "Consistency vs soi_historical (same T-100 baseline), not ground-truth boarding",
                    }
                )

    wf_comb = p1.get("expanding_walk_forward", {}).get("combined", {})
    if wf_comb and wf_comb.get("spearman_rho") is not None:
        for m_name in (
            "spearman_rho",
            "top_decile_hit_rate",
            "top_decile_lift",
            "planning_coverage_pct",
        ):
            if wf_comb.get(m_name) is not None:
                rows.append(
                    {
                        "pillar": "Pillar 1: Time-Based Backtest",
                        "metric_group": "walk_forward_combined_2023_2026",
                        "metric_name": m_name,
                        "metric_value": float(wf_comb[m_name]),
                        "sample_n": int(wf_comb.get("paired_flights", 0)),
                        "status_or_gate": "PASS" if summary_json.get("passed") else "INFO",
                        "notes": "Expanding train.year < eval.year walk-forward backtest",
                    }
                )

    p2 = summary_json.get("pillar_2_face_validity", {})
    for variant in ("soi_historical", "soi_planning"):
        slices = p2.get(variant, {})
        for slice_name, vals in slices.items():
            if isinstance(vals, dict) and vals.get("mean") is not None:
                rows.append(
                    {
                        "pillar": "Pillar 2: Face Validity",
                        "metric_group": variant,
                        "metric_name": f"{slice_name}_mean",
                        "metric_value": float(vals["mean"]),
                        "sample_n": int(vals.get("n", 0)),
                        "status_or_gate": "PASS" if summary_json.get("passed") else "INFO",
                        "notes": f"median={vals.get('median')}, std={vals.get('std')}",
                    }
                )

    p3 = summary_json.get("pillar_3_weight_sensitivity", {})
    for scheme_name, vals in p3.get("schemes", {}).items():
        if isinstance(vals, dict) and vals.get("spearman_rho_vs_baseline") is not None:
            rows.append(
                {
                    "pillar": "Pillar 3: Weight Sensitivity",
                    "metric_group": scheme_name,
                    "metric_name": "spearman_rho_vs_baseline",
                    "metric_value": float(vals["spearman_rho_vs_baseline"]),
                    "sample_n": int(p3.get("n_scored_flights", 0)),
                    "status_or_gate": "PASS" if p3.get("ranking_stability_passed") else "INFO",
                    "notes": f"top10_overlap={vals.get('top10_overlap_pct')}%, weights={vals.get('weights')}",
                }
            )

    out = pd.DataFrame(rows, columns=list(VALIDATION_SUMMARY_COLUMNS))
    out["sample_n"] = pd.to_numeric(out["sample_n"], errors="coerce").fillna(0).astype("int64")
    return out


def _validate_exported_tables(
    tables: dict[str, pd.DataFrame],
    file_paths: dict[str, Path],
    flight_mode: str,
    max_file_mb: float,
) -> None:
    """Enforce post-export invariants: non-empty, unique PKs, SC-5, bounds, zero PII, size <50 MB."""
    pk_map: dict[str, str | list[str]] = {
        "agg_route_month.csv": "route_month_id",
        "dim_airport.csv": "airport_code",
        "dim_aircraft.csv": "tail_num",
        "dim_carrier.csv": "op_carrier",
        "dim_date.csv": "date_day",
        "fct_flights": "flight_id",
        "route_map_paths.csv": ["path_id", "path_order"],
    }

    for key, df in tables.items():
        if df.empty:
            raise ExportValidationError(f"Exported table {key!r} is empty (0 rows)")

        # Zero PII check
        lower_cols = {c.lower() for c in df.columns}
        pii_hits = sorted(lower_cols & _FORBIDDEN_PII_COLUMNS)
        if pii_hits:
            raise ExportValidationError(f"Forbidden PII columns found in {key}: {pii_hits}")

        pk = pk_map.get(key)
        if pk is not None:
            pk_cols = [pk] if isinstance(pk, str) else pk
            if df[pk_cols].isna().any().any():
                raise ExportValidationError(f"Null primary key values in {key} ({pk_cols})")
            if df.duplicated(subset=pk_cols).any():
                raise ExportValidationError(f"Duplicate primary keys detected in {key} ({pk_cols})")

    # agg_route_month invariants: LF in [0,1] and international OTP fields strictly NULL
    agg = tables["agg_route_month.csv"]
    t100_lf = agg["t100_load_factor"].dropna()
    if ((t100_lf < 0.0) | (t100_lf > 1.0)).any():
        raise ExportValidationError("agg_route_month.t100_load_factor outside [0, 1]")
    intl_rows = agg.loc[agg["is_international"].astype(bool)]
    if not intl_rows.empty:
        otp_cols = [c for c in agg.columns if c.startswith("otp_")]
        if intl_rows[otp_cols].notna().any().any():
            raise ExportValidationError(
                "International rows in agg_route_month must have NULL otp_* fields"
            )

    # fct_flights invariants: SC-5, LF in [0,1], SOI in [0,100]
    fct = tables["fct_flights"]
    unmatched_mask = (~fct["t100_available"].astype(bool)) | (
        fct["lf_source"].astype(str) == "UNMATCHED"
    )
    if unmatched_mask.any():
        bad_sc5 = fct.loc[unmatched_mask, list(_SC5_NULL_REQUIRED_COLS)].notna().any().any()
        if bad_sc5:
            raise ExportValidationError(
                "SC-5 violated in fct_flights extract: non-null est/SOI on unmatched T-100"
            )

    for lf_col in ("est_load_factor", "est_lf_adjusted"):
        vals = fct[lf_col].dropna()
        if ((vals < 0.0) | (vals > 1.0)).any():
            raise ExportValidationError(f"fct_flights.{lf_col} outside [0, 1]")

    for soi_col in _SOI_SCORE_AND_SUBSCORE_COLS:
        vals = fct[soi_col].dropna()
        if ((vals < 0.0) | (vals > 100.0)).any():
            raise ExportValidationError(f"fct_flights.{soi_col} outside [0, 100]")

    if {"est_passengers", "seats_config"} <= set(fct.columns):
        pax = pd.to_numeric(fct["est_passengers"], errors="coerce")
        seats = pd.to_numeric(fct["seats_config"], errors="coerce")
        both_valid = pax.notna() & seats.notna()
        if (pax.loc[both_valid] > seats.loc[both_valid]).any():
            raise ExportValidationError("fct_flights.est_passengers exceeds seats_config")

    if {"est_passengers_adjusted", "seats_config"} <= set(fct.columns):
        pax_adj = pd.to_numeric(fct["est_passengers_adjusted"], errors="coerce")
        seats = pd.to_numeric(fct["seats_config"], errors="coerce")
        both_valid = pax_adj.notna() & seats.notna()
        if (pax_adj.loc[both_valid] > seats.loc[both_valid]).any():
            raise ExportValidationError("fct_flights.est_passengers_adjusted exceeds seats_config")

    if {"year", "soi_planning"} <= set(fct.columns):
        y2019_mask = pd.to_numeric(fct["year"], errors="coerce") == 2019
        if y2019_mask.any() and fct.loc[y2019_mask, "soi_planning"].notna().any():
            raise ExportValidationError("fct_flights 2019 rows must have NULL soi_planning")

    if {"is_cancelled", "soi_historical"} <= set(fct.columns):
        canc_mask = fct["is_cancelled"].fillna(False).astype(bool)
        if canc_mask.any() and fct.loc[canc_mask, "soi_historical"].notna().any():
            raise ExportValidationError("fct_flights cancelled rows must have NULL soi_historical")

    rmp = tables["route_map_paths.csv"]
    if {"origin", "dest"} <= set(rmp.columns):
        if (
            rmp["origin"].astype(str).str.strip().str.upper()
            == rmp["dest"].astype(str).str.strip().str.upper()
        ).any():
            raise ExportValidationError(
                "route_map_paths must not contain origin == dest self-loop rows"
            )

    # File size guard (< 50 MB for committed/non-full files)
    max_bytes = int(max_file_mb * 1024 * 1024)
    for fname, fpath in file_paths.items():
        if flight_mode == "full" and fname.endswith("_full.csv"):
            continue
        size = fpath.stat().st_size
        if size == 0:
            raise ExportValidationError(f"Exported CSV {fname} is 0 bytes")
        if size >= max_bytes:
            raise ExportValidationError(
                f"Exported CSV {fname} size {size:,} bytes exceeds {max_file_mb} MB limit"
            )


def _load_source_tables_for_export(
    config: StandbyConfig,
    from_processed: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load processed Parquets when available, else run ingest + enrich + score."""
    mode_dir = config.processed_dir / ("sample" if config.sample_mode else "full")
    otp_pq = mode_dir / "otp_marketing_flights.parquet"
    t100_pq = mode_dir / "t100_segments.parquet"
    lkp_pq = mode_dir / "bts_lookups.parquet"
    tsa_pq = mode_dir / "tsa_daily_throughput.parquet"

    if (
        from_processed
        and otp_pq.is_file()
        and t100_pq.is_file()
        and lkp_pq.is_file()
        and tsa_pq.is_file()
    ):
        logger.info("Loading processed Parquet tables from %s", mode_dir)
        flights_df = pd.read_parquet(otp_pq)
        t100_df = pd.read_parquet(t100_pq)
        lookups_df = pd.read_parquet(lkp_pq)
        tsa_df = pd.read_parquet(tsa_pq)
        if "soi_historical" not in flights_df.columns or "soi_planning" not in flights_df.columns:
            flights_df = score_flights(
                enriched_df=flights_df,
                t100_df=t100_df,
                tsa_df=tsa_df,
                config=config,
            )
        return flights_df, t100_df, lookups_df, tsa_df

    logger.info(
        "Processed Parquets missing or recompute requested; running ingest -> enrich -> score"
    )
    from standby.enrichment.pipeline import run_enrichment_pipeline

    verify_manifest(config.active_manifest_path, repo_root=config.repo_root)
    ingested = ingest_all_sources(config)
    validate_all_ingested(ingested, config=config)
    enrichment_result = run_enrichment_pipeline(ingested=ingested, config=config)
    scored_df = score_flights(
        enriched_df=enrichment_result.enriched_flights,
        t100_df=ingested["t100_segments"],
        tsa_df=ingested["tsa_daily_throughput"],
        config=config,
    )
    return (
        scored_df,
        ingested["t100_segments"],
        ingested["bts_lookups"],
        ingested["tsa_daily_throughput"],
    )


def export_tableau_extracts(
    config: StandbyConfig,
    output_dir: Path | None = None,
    flight_mode: str | None = None,
    max_flight_rows: int | None = None,
    stratify_seed: int | None = None,
    float_precision: int | None = None,
    from_processed: bool = True,
    validation_summary_path: Path | None = None,
) -> TableauExportResult:
    """
    Generate mart-aligned Tableau CSV extracts and `export_metadata.json` in `output_dir`.
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
    resolved_out_dir = (output_dir or config.exports_dir).resolve()
    resolved_out_dir.mkdir(parents=True, exist_ok=True)

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

    # Build all mart-aligned DataFrames
    agg_route_month = _build_agg_route_month(flights_df=flights_df, t100_df=t100_df, config=config)
    dim_airport = _build_dim_airport(
        flights_df=flights_df, t100_df=t100_df, bts_lookups_df=lookups_df, config=config
    )
    dim_aircraft = _build_dim_aircraft(flights_df=flights_df, config=config)
    dim_carrier = _build_dim_carrier(
        flights_df=flights_df, t100_df=t100_df, bts_lookups_df=lookups_df, config=config
    )
    dim_date = _build_dim_date(flights_df=flights_df, tsa_df=tsa_df, config=config)

    selected_flights = _select_flights_for_export(
        flights_df=flights_df,
        flight_mode=resolved_mode,
        max_flight_rows=resolved_max_rows,
        stratify_seed=resolved_seed,
    )
    fct_flights_tableau = _format_fct_flights_tableau(selected_flights)
    route_map_paths = _build_route_map_paths(agg_route_month_df=agg_route_month, config=config)

    # Validation summary CSV
    mode_dir = config.processed_dir / ("sample" if config.sample_mode else "full")
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
        else:
            logger.info(
                "Cached validation summary %s metadata (hub=%s, years=%s) mismatches "
                "config (hub=%s, years=%s); recomputing validation summary",
                resolved_val_path,
                candidate_meta.get("hub"),
                candidate_meta.get("years"),
                config.active_hub,
                config.years,
            )
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

    flight_filename = (
        "fct_flights_tableau_full.csv" if resolved_mode == "full" else "fct_flights_tableau.csv"
    )
    output_frames: dict[str, pd.DataFrame] = {
        "agg_route_month.csv": _round_float_columns(agg_route_month, resolved_precision),
        "dim_airport.csv": dim_airport,
        "dim_aircraft.csv": dim_aircraft,
        "dim_carrier.csv": dim_carrier,
        "dim_date.csv": _round_float_columns(dim_date, resolved_precision),
        flight_filename: _round_float_columns(fct_flights_tableau, resolved_precision),
        "route_map_paths.csv": _round_float_columns(route_map_paths, resolved_precision),
        "validation_summary.csv": _round_float_columns(validation_summary_df, resolved_precision),
    }

    written_paths: dict[str, Path] = {}
    for fname, df in output_frames.items():
        fpath = resolved_out_dir / fname
        df.to_csv(fpath, index=False, encoding="utf-8")
        written_paths[fname] = fpath

    validation_tables = {
        "agg_route_month.csv": output_frames["agg_route_month.csv"],
        "dim_airport.csv": output_frames["dim_airport.csv"],
        "dim_aircraft.csv": output_frames["dim_aircraft.csv"],
        "dim_carrier.csv": output_frames["dim_carrier.csv"],
        "dim_date.csv": output_frames["dim_date.csv"],
        "fct_flights": output_frames[flight_filename],
        "route_map_paths.csv": output_frames["route_map_paths.csv"],
        "validation_summary.csv": output_frames["validation_summary.csv"],
    }
    _validate_exported_tables(
        tables=validation_tables,
        file_paths=written_paths,
        flight_mode=resolved_mode,
        max_file_mb=float(config.export.tableau_max_file_mb),
    )

    files_meta: dict[str, ExportedFileInfo] = {}
    files_json: dict[str, dict[str, Any]] = {}
    for fname, fpath in written_paths.items():
        df = output_frames[fname]
        size_bytes = int(fpath.stat().st_size)
        sha = _sha256_file(fpath)
        files_meta[fname] = ExportedFileInfo(
            filename=fname,
            path=fpath,
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
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hub": config.active_hub,
        "years": config.years,
        "sample_mode": config.sample_mode,
        "flight_mode": resolved_mode,
        "flight_row_budget": resolved_max_rows if resolved_mode != "full" else total_source_flights,
        "stratify_seed": resolved_seed,
        "float_precision": resolved_precision,
        "total_source_flights": total_source_flights,
        "exported_flights": int(len(fct_flights_tableau)),
        "invariants_passed": True,
        "files": files_json,
    }
    metadata_path = resolved_out_dir / "export_metadata.json"
    metadata_path.write_text(json.dumps(metadata_payload, indent=2) + "\n", encoding="utf-8")

    return TableauExportResult(
        output_dir=resolved_out_dir,
        metadata_path=metadata_path,
        sample_mode=config.sample_mode,
        flight_mode=resolved_mode,
        flight_row_budget=resolved_max_rows if resolved_mode != "full" else total_source_flights,
        total_source_flights=total_source_flights,
        exported_flights=int(len(fct_flights_tableau)),
        files=files_meta,
    )
