"""Step 3 of Phase 2 enrichment: base vs. modeled adjusted flight-level seat estimates."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from standby.config import StandbyConfig

logger = logging.getLogger("standby.enrichment.calculations")


def compute_tsa_daily_multipliers(
    tsa_df: pd.DataFrame,
    alpha: float = 0.35,
    bounds: tuple[float, float] = (0.80, 1.20),
) -> pd.DataFrame:
    """
    Compute daily TSA throughput and damped monthly-relative multiplier `M_TSA(d)`:

        r_tsa(d) = TSA(d) / mean(TSA in month(d))
        M_TSA(d) = clip(1.0 + alpha * (r_tsa(d) - 1.0), bounds[0], bounds[1])
    """
    if tsa_df.empty:
        return pd.DataFrame(columns=["date_day", "tsa_throughput", "tsa_demand_multiplier"])

    df = tsa_df[["date_day", "tsa_throughput"]].copy()
    df["date_day"] = df["date_day"].astype(str).str.strip()
    df["tsa_throughput"] = pd.to_numeric(df["tsa_throughput"], errors="coerce")
    # Keep the highest throughput per duplicate date (parity with dbt `stg_tsa_throughput`).
    df = (
        df.sort_values(
            ["date_day", "tsa_throughput"],
            ascending=[True, False],
            na_position="last",
            kind="mergesort",
        )
        .drop_duplicates(subset=["date_day"], keep="first")
        .copy()
    )
    df["_ym"] = df["date_day"].str.slice(0, 7)

    monthly_mean = df.groupby("_ym")["tsa_throughput"].transform("mean")
    ratio = np.where(monthly_mean > 0, df["tsa_throughput"] / monthly_mean, np.nan)
    mult = 1.0 + float(alpha) * (ratio - 1.0)
    mult = np.clip(mult, float(bounds[0]), float(bounds[1]))

    df["tsa_demand_multiplier"] = pd.Series(mult, index=df.index, dtype="float64")
    df["tsa_throughput"] = df["tsa_throughput"].astype("Int64")
    return df[["date_day", "tsa_throughput", "tsa_demand_multiplier"]]


def compute_flight_estimates(
    flights_df: pd.DataFrame,
    tsa_df: pd.DataFrame,
    config: StandbyConfig | None = None,
    tsa_damping_alpha: float | None = None,
    tsa_multiplier_bounds: tuple[float, float] | None = None,
    hour_block_multipliers: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Compute base flight-level estimates (`est_passengers`, `est_open_seats`) and
    separate modeled adjustment columns (`tsa_demand_multiplier`, `hour_block_multiplier`,
    `est_lf_adjusted`, `est_passengers_adjusted`, `est_open_seats_adjusted`).

    Guarantees:
      - Modeled adjustments NEVER overwrite base estimates (`est_load_factor`, `est_passengers`, `est_open_seats`).
      - Missing T-100 matches (`~t100_available` or `lf_source == "UNMATCHED"` or `est_load_factor.isna()`)
        strictly retain NULL (`NaN` / `pd.NA`) for all `est_*` columns.
    """
    out = flights_df.copy()

    alpha = (
        float(tsa_damping_alpha)
        if tsa_damping_alpha is not None
        else (config.enrichment.tsa_damping_alpha if config is not None else 0.35)
    )
    bounds = (
        tsa_multiplier_bounds
        if tsa_multiplier_bounds is not None
        else (config.enrichment.tsa_multiplier_bounds if config is not None else (0.80, 1.20))
    )
    if hour_block_multipliers is not None:
        hb_map = {str(k).strip().lower(): float(v) for k, v in hour_block_multipliers.items()}
    elif config is not None:
        hb_map = {
            str(k).strip().lower(): float(v)
            for k, v in config.enrichment.hour_block_multipliers.items()
        }
        for name, spec in config.hour_blocks.items():
            hb_map.setdefault(str(name).strip().lower(), spec.load_multiplier)
    else:
        hb_map = {
            "early": 0.96,
            "morning": 1.03,
            "midday": 1.00,
            "afternoon": 1.02,
            "evening": 1.04,
            "late": 0.94,
        }

    # 1. Join TSA daily throughput & multiplier by flight_date == date_day
    tsa_daily = compute_tsa_daily_multipliers(tsa_df, alpha=alpha, bounds=bounds)
    tsa_throughput_map = dict(zip(tsa_daily["date_day"], tsa_daily["tsa_throughput"], strict=True))
    tsa_mult_map = dict(zip(tsa_daily["date_day"], tsa_daily["tsa_demand_multiplier"], strict=True))

    flight_dates = out["flight_date"].astype(str).str.strip()
    out["tsa_throughput"] = pd.array(
        [tsa_throughput_map.get(d, pd.NA) for d in flight_dates],
        dtype="Int64",
    )
    out["tsa_demand_multiplier"] = pd.Series(
        [tsa_mult_map.get(d, float("nan")) for d in flight_dates],
        index=out.index,
        dtype="float64",
    )

    # 2. Map hour_block_multiplier
    out["hour_block_multiplier"] = (
        out["hour_block"]
        .astype(str)
        .str.strip()
        .str.lower()
        .map(lambda hb: hb_map.get(hb, 1.0))
        .astype("float64")
    )

    # 3. Enforce strict nullability when T-100 match is missing
    valid_t100_mask = (
        out["t100_available"].astype(bool)
        & (out["lf_source"].astype(str) != "UNMATCHED")
        & out["est_load_factor"].notna()
    )
    base_lf = out["est_load_factor"].astype("float64").where(valid_t100_mask, np.nan)
    out["est_load_factor"] = base_lf

    seats_num = out["seats_config"].astype("Float64")
    has_seats_and_lf = valid_t100_mask & out["seats_config"].notna()

    # Base passenger & open seat calculations (never overwritten by adjustments)
    est_pax = pd.Series(pd.NA, index=out.index, dtype="Int64")
    est_open = pd.Series(pd.NA, index=out.index, dtype="Int64")
    if has_seats_and_lf.any():
        raw_pax = (seats_num.loc[has_seats_and_lf].to_numpy(dtype=float)) * (
            base_lf.loc[has_seats_and_lf].to_numpy(dtype=float)
        )
        seats_arr = seats_num.loc[has_seats_and_lf].to_numpy(dtype=float)
        pax_clipped = np.clip(np.round(raw_pax), 0, seats_arr).astype("int64")
        open_arr = seats_arr.astype("int64") - pax_clipped
        est_pax.loc[has_seats_and_lf] = pax_clipped
        est_open.loc[has_seats_and_lf] = open_arr

    out["est_passengers"] = est_pax
    out["est_open_seats"] = est_open

    # 4. Separate modeled adjustments
    tsa_m = out["tsa_demand_multiplier"].to_numpy(dtype=float)
    hb_m = out["hour_block_multiplier"].to_numpy(dtype=float)
    base_lf_arr = base_lf.to_numpy(dtype=float)

    adj_valid = valid_t100_mask.to_numpy(dtype=bool) & ~np.isnan(tsa_m) & ~np.isnan(hb_m)
    est_lf_adj_arr = np.full(len(out), np.nan, dtype="float64")
    est_lf_adj_arr[adj_valid] = np.clip(
        base_lf_arr[adj_valid] * tsa_m[adj_valid] * hb_m[adj_valid],
        0.0,
        1.0,
    )
    out["est_lf_adjusted"] = pd.Series(est_lf_adj_arr, index=out.index, dtype="float64")

    est_pax_adj = pd.Series(pd.NA, index=out.index, dtype="Int64")
    est_open_adj = pd.Series(pd.NA, index=out.index, dtype="Int64")
    has_seats_and_adj = out["seats_config"].notna() & out["est_lf_adjusted"].notna()
    if has_seats_and_adj.any():
        seats_adj_arr = seats_num.loc[has_seats_and_adj].to_numpy(dtype=float)
        lf_adj_sub = out.loc[has_seats_and_adj, "est_lf_adjusted"].to_numpy(dtype=float)
        pax_adj_clipped = np.clip(np.round(seats_adj_arr * lf_adj_sub), 0, seats_adj_arr).astype(
            "int64"
        )
        open_adj_arr = seats_adj_arr.astype("int64") - pax_adj_clipped
        est_pax_adj.loc[has_seats_and_adj] = pax_adj_clipped
        est_open_adj.loc[has_seats_and_adj] = open_adj_arr

    out["est_passengers_adjusted"] = est_pax_adj
    out["est_open_seats_adjusted"] = est_open_adj

    logger.info(
        "Computed base and adjusted flight-level estimates",
        extra={
            "extra_fields": {
                "rows": len(out),
                "base_estimated_rows": int(has_seats_and_lf.sum()),
                "adjusted_estimated_rows": int(has_seats_and_adj.sum()),
            }
        },
    )
    assert len(out) == len(flights_df), "Row count invariant violated in compute_flight_estimates"
    return out
