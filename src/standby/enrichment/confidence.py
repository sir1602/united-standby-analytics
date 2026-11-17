"""Step 4 of Phase 2 enrichment: column confidence-tier registry and metadata tagging."""

from __future__ import annotations

from typing import Literal

import pandas as pd

ConfidenceTier = Literal["actual", "derived", "estimated"]

COLUMN_CONFIDENCE_TIERS: dict[str, ConfidenceTier] = {
    # 1. ACTUAL (Directly observed in BTS OTP, BTS T-100, FAA Registry, or TSA)
    "flight_date": "actual",
    "year": "actual",
    "quarter": "actual",
    "month": "actual",
    "day_of_month": "actual",
    "day_of_week": "actual",
    "mkt_carrier": "actual",
    "op_carrier": "actual",
    "flight_num_mkt": "actual",
    "flight_num_op": "actual",
    "tail_num": "actual",
    "origin": "actual",
    "origin_airport_id": "actual",
    "dest": "actual",
    "dest_airport_id": "actual",
    "crs_dep_time": "actual",
    "dep_time": "actual",
    "crs_arr_time": "actual",
    "arr_time": "actual",
    "dep_delay_min": "actual",
    "arr_delay_min": "actual",
    "is_cancelled": "actual",
    "cancellation_code": "actual",
    "is_diverted": "actual",
    "crs_elapsed_time_min": "actual",
    "actual_elapsed_time_min": "actual",
    "distance_miles": "actual",
    "tsa_throughput": "actual",
    # 2. DERIVED (Deterministic transformations, aircraft registry resolution, & curated cabin configs)
    "confidence_tier": "derived",
    "tail_num_clean": "derived",
    "dep_hour": "derived",
    "hour_block": "derived",
    "aircraft_source": "derived",
    "faa_manufacturer": "derived",
    "faa_model": "derived",
    "faa_tc_max_seats": "derived",
    "fleet_subtype": "derived",
    "bts_aircraft_type": "derived",
    "seat_config_source": "derived",
    "seats_config": "derived",
    "seats_premium": "derived",
    "seats_economy": "derived",
    "t100_available": "derived",
    "lf_source": "derived",
    "is_shared_regional_spoke": "derived",
    "record_confidence_summary": "derived",
    # 3. ESTIMATED (T-100 monthly load factors, rounded passenger/open-seat proxies, modeled adjustments & SOI)
    "est_load_factor": "estimated",
    "est_passengers": "estimated",
    "est_open_seats": "estimated",
    "tsa_demand_multiplier": "estimated",
    "hour_block_multiplier": "estimated",
    "est_lf_adjusted": "estimated",
    "est_passengers_adjusted": "estimated",
    "est_open_seats_adjusted": "estimated",
    "soi_historical": "estimated",
    "soi_planning": "estimated",
}


def tag_dataframe_confidence_tiers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate every column in `df` against `COLUMN_CONFIDENCE_TIERS`, attach
    `df.attrs["column_confidence_tiers"]`, and populate `record_confidence_summary`.

    Raises `KeyError` if any column in `df` is missing from `COLUMN_CONFIDENCE_TIERS`.
    """
    out = df.copy()
    untagged = [col for col in out.columns if col not in COLUMN_CONFIDENCE_TIERS]
    if untagged:
        raise KeyError(f"Untagged columns not registered in COLUMN_CONFIDENCE_TIERS: {untagged}")

    has_estimates = (
        out["t100_available"].astype(bool)
        & (out["lf_source"].astype(str) != "UNMATCHED")
        & out["est_load_factor"].notna()
    )
    out["record_confidence_summary"] = pd.Series(
        "actual+derived (unmatched_t100)",
        index=out.index,
        dtype=object,
    ).where(~has_estimates, "actual+derived+estimated")

    out.attrs["column_confidence_tiers"] = {
        col: COLUMN_CONFIDENCE_TIERS[col] for col in out.columns
    }
    return out


def summarize_confidence_tiers(df: pd.DataFrame) -> dict[str, list[str]]:
    """Return columns grouped by confidence tier (`actual`, `derived`, `estimated`)."""
    tiers = df.attrs.get("column_confidence_tiers")
    if not isinstance(tiers, dict):
        tiers = {col: COLUMN_CONFIDENCE_TIERS[col] for col in df.columns}
    grouped: dict[str, list[str]] = {"actual": [], "derived": [], "estimated": []}
    for col in df.columns:
        tier = tiers.get(col) or COLUMN_CONFIDENCE_TIERS[col]
        grouped[tier].append(col)
    return grouped
