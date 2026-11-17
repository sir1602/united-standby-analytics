"""Phase 4 Standby Opportunity Index (`soi_historical` & leakage-free `soi_planning`)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from standby.config import DEFAULT_UA_FAMILY_CARRIERS, ScoringConfig, StandbyConfig
from standby.scoring.holidays import holiday_name_series

logger = logging.getLogger("standby.scoring.soi")

HISTORICAL_SUBSCORE_COLS: tuple[str, ...] = (
    "s_open_hist",
    "s_disp_hist",
    "s_backup_hist",
    "s_demand_hist",
)

PLANNING_SUBSCORE_COLS: tuple[str, ...] = (
    "s_open_plan",
    "s_disp_plan",
    "s_backup_plan",
    "s_demand_plan",
)

SOI_OUTPUT_COLUMNS: tuple[str, ...] = (
    "is_holiday_window",
    "holiday_name",
    "tsa_ratio_year",
    "prior_strict_scheduled_count",
    "weighted_displacement_hist",
    "remaining_scheduled_backups",
    "remaining_performed_backups",
    *HISTORICAL_SUBSCORE_COLS,
    "soi_historical",
    *PLANNING_SUBSCORE_COLS,
    "soi_planning",
)

_DEFAULT_WEIGHTS: dict[str, float] = {
    "open_seats": 0.50,
    "displacement_pressure": 0.20,
    "backup_options": 0.20,
    "day_holiday_demand": 0.10,
}


def _resolve_weights(
    config: StandbyConfig | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Resolve and normalize the 4 SOI component weights."""
    if weights is not None:
        raw = {k: float(weights.get(k, default_val)) for k, default_val in _DEFAULT_WEIGHTS.items()}
        total = sum(raw.values())
        if total <= 0.0:
            raise ValueError(f"SOI weights sum must be positive, got {raw}")
        if any(v < 0.0 for v in raw.values()):
            raise ValueError(f"SOI weights must be non-negative, got {raw}")
        return {k: v / total for k, v in raw.items()}
    if config is not None:
        return config.get_index_weights()
    return dict(_DEFAULT_WEIGHTS)


def _resolve_scoring_config(config: StandbyConfig | None) -> ScoringConfig:
    return config.scoring if config is not None else ScoringConfig()


def _normalize_date_str_series(dates: pd.Series) -> pd.Series:
    """Normalize date-like series to `'YYYY-MM-DD'` strings (NaN/None -> NaN)."""
    parsed = pd.to_datetime(dates, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d")


def _normalize_hhmm_series(series: pd.Series) -> pd.Series:
    """Normalize local hhmm strings (`'2400'` -> `'0000'`, zero-padded 4 chars)."""
    s = series.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True).str.zfill(4)
    return s.where(s != "2400", "0000")


def compute_tsa_annual_ratios(tsa_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute deduplicated daily TSA ratios matching `dbt/models/staging/stg_tsa_throughput.sql`:
      - Deduplicates by `date_day` keeping highest `tsa_throughput`
      - `tsa_ratio_month = tsa_throughput / mean(tsa_throughput within year-month)`
      - `tsa_ratio_year = tsa_throughput / mean(tsa_throughput within calendar year)`
      - Exposes `year`, `month`, `iso_dow` (1=Mon..7=Sun), `is_holiday_window`, `holiday_name`.
    """
    cols = [
        "date_day",
        "year",
        "month",
        "iso_dow",
        "is_holiday_window",
        "holiday_name",
        "tsa_throughput",
        "tsa_ratio_month",
        "tsa_ratio_year",
    ]
    if tsa_df.empty:
        return pd.DataFrame(columns=cols)

    df = tsa_df[["date_day", "tsa_throughput"]].copy()
    parsed = pd.to_datetime(df["date_day"], errors="coerce")
    df = df.loc[parsed.notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["date_day"] = parsed.loc[parsed.notna()].dt.strftime("%Y-%m-%d")
    df["tsa_throughput"] = pd.to_numeric(df["tsa_throughput"], errors="coerce")
    df = (
        df.sort_values(["date_day", "tsa_throughput"], ascending=[True, False], kind="mergesort")
        .drop_duplicates(subset=["date_day"], keep="first")
        .copy()
    )

    dt = pd.to_datetime(df["date_day"], format="%Y-%m-%d")
    df["year"] = dt.dt.year.astype("int64")
    df["month"] = dt.dt.month.astype("int64")
    df["iso_dow"] = dt.dt.dayofweek.astype("int64") + 1
    df["holiday_name"] = holiday_name_series(df["date_day"])
    df["is_holiday_window"] = df["holiday_name"].notna().astype(bool)

    month_mean = df.groupby(["year", "month"])["tsa_throughput"].transform("mean")
    year_mean = df.groupby("year")["tsa_throughput"].transform("mean")

    df["tsa_ratio_month"] = pd.Series(
        np.where(month_mean > 0, df["tsa_throughput"] / month_mean, np.nan),
        index=df.index,
        dtype="float64",
    )
    df["tsa_ratio_year"] = pd.Series(
        np.where(year_mean > 0, df["tsa_throughput"] / year_mean, np.nan),
        index=df.index,
        dtype="float64",
    )
    return df[cols].reset_index(drop=True)


def compute_strict_schedule_counts(flights_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute leakage-free strict departure-time schedule counts per `(origin, dest, flight_date)`:
      - `prior_strict_scheduled_count`: count of flights with `j.crs_dep_time < i.crs_dep_time`
      - `remaining_scheduled_backups`: count of flights with `j.crs_dep_time > i.crs_dep_time`

    Reads ONLY schedule keys (`origin`, `dest`, `flight_date`, `crs_dep_time`), never `is_cancelled`.
    Preserves the exact input index.
    """
    if flights_df.empty:
        return pd.DataFrame(
            {
                "prior_strict_scheduled_count": pd.Series([], dtype="int64"),
                "remaining_scheduled_backups": pd.Series([], dtype="int64"),
            },
            index=flights_df.index,
        )

    work = pd.DataFrame(
        {
            "_orig": flights_df["origin"].astype(str).str.strip().str.upper(),
            "_dest": flights_df["dest"].astype(str).str.strip().str.upper(),
            "_date": _normalize_date_str_series(flights_df["flight_date"]),
            "_dep": _normalize_hhmm_series(flights_df["crs_dep_time"]),
        },
        index=flights_df.index,
    )

    minute_counts = (
        work.groupby(["_orig", "_dest", "_date", "_dep"], sort=True, as_index=False)
        .size()
        .rename(columns={"size": "_cnt"})
    )
    grp = minute_counts.groupby(["_orig", "_dest", "_date"], sort=False)["_cnt"]
    minute_counts["prior_strict_scheduled_count"] = (grp.cumsum() - minute_counts["_cnt"]).astype(
        "int64"
    )
    total_per_day = grp.transform("sum")
    minute_counts["remaining_scheduled_backups"] = (
        total_per_day - minute_counts["prior_strict_scheduled_count"] - minute_counts["_cnt"]
    ).astype("int64")

    merged = work.reset_index().merge(
        minute_counts[
            [
                "_orig",
                "_dest",
                "_date",
                "_dep",
                "prior_strict_scheduled_count",
                "remaining_scheduled_backups",
            ]
        ],
        on=["_orig", "_dest", "_date", "_dep"],
        how="left",
    )
    merged = merged.set_index("index").reindex(flights_df.index)
    return merged[["prior_strict_scheduled_count", "remaining_scheduled_backups"]]


def compute_route_day_operational_signals(
    flights_df: pd.DataFrame,
    decay_base: float = 0.75,
    diversion_weight: float = 0.50,
) -> pd.DataFrame:
    """
    Compute deterministic route-day displacement & backup signals matching
    `dbt/models/intermediate/int_flight_displacements_backups.sql` 1:1.

    Tie-breaker within `(origin, dest, flight_date)`:
      `(crs_dep_time ASC, is_cancelled DESC, flight_num_mkt ASC, op_carrier ASC)`.

    Preserves `flights_df.index` and original row ordering.
    """
    if flights_df.empty:
        return pd.DataFrame(
            {
                "prior_strict_scheduled_count": pd.Series([], dtype="int64"),
                "weighted_displacement_hist": pd.Series([], dtype="float64"),
                "remaining_scheduled_backups": pd.Series([], dtype="int64"),
                "remaining_performed_backups": pd.Series([], dtype="int64"),
            },
            index=flights_df.index,
        )

    strict_counts = compute_strict_schedule_counts(flights_df)

    work = pd.DataFrame(
        {
            "_orig_pos": np.arange(len(flights_df), dtype="int64"),
            "_orig": flights_df["origin"].astype(str).str.strip().str.upper(),
            "_dest": flights_df["dest"].astype(str).str.strip().str.upper(),
            "_date": _normalize_date_str_series(flights_df["flight_date"]),
            "_dep": _normalize_hhmm_series(flights_df["crs_dep_time"]),
            "_canc": flights_df["is_cancelled"].astype(bool),
            "_div": flights_df["is_diverted"].astype(bool),
            "_fnum": flights_df["flight_num_mkt"].astype(str).str.strip(),
            "_car": flights_df["op_carrier"].astype(str).str.strip().str.upper(),
        },
        index=flights_df.index,
    )

    work = work.sort_values(
        ["_orig", "_dest", "_date", "_dep", "_canc", "_fnum", "_car"],
        ascending=[True, True, True, True, False, True, True],
        kind="mergesort",
    )

    orig_arr = work["_orig"].to_numpy()
    dest_arr = work["_dest"].to_numpy()
    date_arr = work["_date"].to_numpy()
    dep_arr = work["_dep"].to_numpy()
    canc_arr = work["_canc"].to_numpy(dtype=bool)
    div_arr = work["_div"].to_numpy(dtype=bool)
    weights_arr = np.where(canc_arr, 1.0, np.where(div_arr, float(diversion_weight), 0.0))

    n = len(work)
    disp_arr = np.zeros(n, dtype="float64")
    rem_perf_arr = np.zeros(n, dtype="int64")

    decay = float(decay_base)

    # Walk sorted route-day groups in O(N) using exact recurrence:
    #   D_0 = 0.0, D_k = decay * D_{k-1} + w_{k-1}
    # And track strict j.crs_dep_time > i.crs_dep_time for remaining_performed_backups.
    group_start = 0
    while group_start < n:
        o_g = orig_arr[group_start]
        d_g = dest_arr[group_start]
        dt_g = date_arr[group_start]
        group_end = group_start + 1
        while (
            group_end < n
            and orig_arr[group_end] == o_g
            and dest_arr[group_end] == d_g
            and date_arr[group_end] == dt_g
        ):
            group_end += 1

        running_disp = 0.0
        total_perf = 0
        for idx in range(group_start, group_end):
            disp_arr[idx] = running_disp
            running_disp = decay * running_disp + weights_arr[idx]
            if not canc_arr[idx]:
                total_perf += 1

        # Forward pass over distinct dep times within [group_start, group_end)
        seen_perf = 0
        idx = group_start
        while idx < group_end:
            t_cur = dep_arr[idx]
            t_end = idx + 1
            perf_at_t = 0 if canc_arr[idx] else 1
            while t_end < group_end and dep_arr[t_end] == t_cur:
                if not canc_arr[t_end]:
                    perf_at_t += 1
                t_end += 1
            rem_after_t = total_perf - seen_perf - perf_at_t
            rem_perf_arr[idx:t_end] = rem_after_t
            seen_perf += perf_at_t
            idx = t_end

        group_start = group_end

    # Map sorted positions back to original index order
    orig_pos = work["_orig_pos"].to_numpy()
    disp_reordered = np.empty(n, dtype="float64")
    rem_perf_reordered = np.empty(n, dtype="int64")
    disp_reordered[orig_pos] = disp_arr
    rem_perf_reordered[orig_pos] = rem_perf_arr

    return pd.DataFrame(
        {
            "prior_strict_scheduled_count": strict_counts["prior_strict_scheduled_count"].to_numpy(
                dtype="int64"
            ),
            "weighted_displacement_hist": disp_reordered,
            "remaining_scheduled_backups": strict_counts["remaining_scheduled_backups"].to_numpy(
                dtype="int64"
            ),
            "remaining_performed_backups": rem_perf_reordered,
        },
        index=flights_df.index,
    )


def compute_soi_historical(
    flights_df: pd.DataFrame,
    tsa_df: pd.DataFrame,
    config: StandbyConfig | None = None,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Compute post-hoc `soi_historical` (0-100) and its 4 sub-scores (`s_open_hist`, `s_disp_hist`,
    `s_backup_hist`, `s_demand_hist`), attaching shared operational and calendar signals.

    Enforces `SC-5` NULL invariants: all historical score columns are strictly `NaN` (`float64`)
    when `~t100_available | (lf_source == 'UNMATCHED') | is_cancelled | seats_config.isna() |
    seats_config <= 0 | est_open_seats_adjusted.isna() | tsa_ratio_year.isna()`.
    """
    out = flights_df.copy()
    sc = _resolve_scoring_config(config)
    w = _resolve_weights(config=config, weights=weights)

    # Calendar & TSA annual ratio
    out["holiday_name"] = holiday_name_series(out["flight_date"])
    out["is_holiday_window"] = out["holiday_name"].notna().astype(bool)

    tsa_ratios = compute_tsa_annual_ratios(tsa_df)
    tsa_year_map = dict(zip(tsa_ratios["date_day"], tsa_ratios["tsa_ratio_year"], strict=True))
    flight_date_strs = _normalize_date_str_series(out["flight_date"])
    out["tsa_ratio_year"] = pd.Series(
        [tsa_year_map.get(d, float("nan")) for d in flight_date_strs],
        index=out.index,
        dtype="float64",
    )

    # Route-day displacement & backup signals
    ops = compute_route_day_operational_signals(
        out,
        decay_base=sc.disp_decay_base,
        diversion_weight=sc.disp_diversion_weight,
    )
    for col in ops.columns:
        out[col] = ops[col]

    # Eligibility gate
    seats_num = pd.to_numeric(out["seats_config"], errors="coerce").to_numpy(dtype="float64")
    open_adj = pd.to_numeric(out["est_open_seats_adjusted"], errors="coerce").to_numpy(
        dtype="float64"
    )
    r_year = out["tsa_ratio_year"].to_numpy(dtype="float64")
    disp_hist = out["weighted_displacement_hist"].to_numpy(dtype="float64")
    rem_perf = out["remaining_performed_backups"].to_numpy(dtype="float64")
    is_hol = out["is_holiday_window"].to_numpy(dtype="float64")
    is_canc = out["is_cancelled"].astype(bool).to_numpy()

    eligible = (
        out["t100_available"].astype(bool).to_numpy()
        & (out["lf_source"].astype(str).to_numpy() != "UNMATCHED")
        & (~is_canc)
        & (~np.isnan(seats_num))
        & (seats_num > 0.0)
        & (~np.isnan(open_adj))
        & (~np.isnan(r_year))
    )

    n = len(out)
    s_open = np.full(n, np.nan, dtype="float64")
    s_disp = np.full(n, np.nan, dtype="float64")
    s_backup = np.full(n, np.nan, dtype="float64")
    s_demand = np.full(n, np.nan, dtype="float64")
    soi_hist = np.full(n, np.nan, dtype="float64")

    if eligible.any():
        open_e = open_adj[eligible]
        seats_e = seats_num[eligible]
        s_abs = 100.0 * np.clip(open_e / sc.open_seats_abs_cap, 0.0, 1.0)
        s_pct = 100.0 * np.clip((open_e / seats_e) / sc.open_seats_pct_cap, 0.0, 1.0)
        s_open_e = sc.open_seats_abs_weight * s_abs + (1.0 - sc.open_seats_abs_weight) * s_pct

        s_disp_e = 100.0 * np.clip(1.0 - disp_hist[eligible] / sc.disp_hist_cap, 0.0, 1.0)
        s_backup_e = 100.0 * np.clip(
            np.log1p(rem_perf[eligible]) / np.log1p(float(sc.backup_max_flights)),
            0.0,
            1.0,
        )

        i_demand_e = r_year[eligible] + sc.demand_holiday_adder * is_hol[eligible]
        denom = sc.demand_ratio_max - sc.demand_ratio_min
        s_demand_e = 100.0 * np.clip((sc.demand_ratio_max - i_demand_e) / denom, 0.0, 1.0)

        comp_e = np.round(
            w["open_seats"] * s_open_e
            + w["displacement_pressure"] * s_disp_e
            + w["backup_options"] * s_backup_e
            + w["day_holiday_demand"] * s_demand_e,
            2,
        )

        s_open[eligible] = s_open_e
        s_disp[eligible] = s_disp_e
        s_backup[eligible] = s_backup_e
        s_demand[eligible] = s_demand_e
        soi_hist[eligible] = comp_e

    out["s_open_hist"] = pd.Series(s_open, index=out.index, dtype="float64")
    out["s_disp_hist"] = pd.Series(s_disp, index=out.index, dtype="float64")
    out["s_backup_hist"] = pd.Series(s_backup, index=out.index, dtype="float64")
    out["s_demand_hist"] = pd.Series(s_demand, index=out.index, dtype="float64")
    out["soi_historical"] = pd.Series(soi_hist, index=out.index, dtype="float64")
    return out


@dataclass(frozen=True)
class PlanningPriors:
    """Leakage-free lookup tables fitted strictly on prior training years (`year < eval_year`)."""

    # 4-tier modal (seats_config, bts_aircraft_type)
    modal_t1: dict[tuple[str, str, str, int, str], tuple[int, str]]
    modal_t2: dict[tuple[str, str, str, int], tuple[int, str]]
    modal_t3: dict[tuple[str, str, str], tuple[int, str]]
    modal_t4: dict[tuple[str, str], tuple[int, str]]

    # 5-tier T-100 LF lookup
    lf_t1: dict[tuple[str, str, str, int, str], float]
    lf_t2: dict[tuple[str, str, str, int], float]
    lf_t3: dict[tuple[str, str, int], float]
    lf_t4: dict[tuple[str, str, str], float]
    lf_t5: dict[tuple[str, str], float]

    # 4-tier disruption rate q_hat (min_obs on tiers 1 & 2)
    q_t1: dict[tuple[str, str, int, str], float]
    q_t2: dict[tuple[str, str, int], float]
    q_t3: dict[tuple[str, str], float]
    q_global: float

    # 4-tier TSA ratio lookup: (r_tsa_month, r_tsa_year)
    tsa_t1: dict[tuple[int, int, bool], tuple[float, float]]
    tsa_t2: dict[tuple[int, int], tuple[float, float]]
    tsa_t3: dict[int, tuple[float, float]]
    tsa_global: tuple[float, float]


def _fit_modal_tier(
    df: pd.DataFrame,
    group_cols: list[str],
) -> dict[Any, tuple[int, str]]:
    """Compute modal `(seats_config, bts_aircraft_type)` per key with deterministic tie-breaker."""
    if df.empty:
        return {}
    counts = (
        df.groupby([*group_cols, "seats_int", "bts_type"], as_index=False)
        .size()
        .rename(columns={"size": "row_cnt"})
    )
    counts = counts.sort_values(
        [*group_cols, "row_cnt", "seats_int", "bts_type"],
        ascending=[*([True] * len(group_cols)), False, True, True],
        kind="mergesort",
    )
    winners = counts.drop_duplicates(subset=group_cols, keep="first")
    result: dict[Any, tuple[int, str]] = {}
    for row in winners.itertuples(index=False):
        key = tuple(getattr(row, c) for c in group_cols)
        result[key] = (int(row.seats_int), str(row.bts_type))
    return result


def fit_planning_priors(
    train_flights_df: pd.DataFrame,
    train_t100_df: pd.DataFrame | None = None,
    train_tsa_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
) -> PlanningPriors:
    """
    Fit all 4 leakage-free planning lookup hierarchies strictly from training data (`year < eval_year`).

    - 4-tier modal `(seats_config, bts_aircraft_type)`: `(op_carrier, origin, dest, month, hour_block)`
      -> `(op_carrier, origin, dest, month)` -> `(op_carrier, origin, dest)` -> `(origin, dest)`
    - 5-tier T-100 LF (`train_t100_df` when provided, else `train_flights_df['est_load_factor']`):
      `(op_carrier, origin, dest, month, bts_aircraft_type)` -> `(op_carrier, origin, dest, month)`
      -> `(origin, dest, month)` UA-family -> `(op_carrier, origin, dest)` -> `(origin, dest)` UA-family
    - 4-tier disruption rate `q_hat` (`1.0` cancelled, `0.5` diverted) with `disp_plan_min_obs = 5`
      on Tiers 1 and 2 -> `(origin, dest)` -> global
    - 4-tier TSA ratio `(r_month, r_year)`: `(month, iso_dow, is_holiday_window)`
      -> `(month, iso_dow)` -> `(month)` -> global training mean
    """
    sc = _resolve_scoring_config(config)
    ua_family = (
        {c.strip().upper() for c in config.all_operating_carriers}
        if config is not None
        else set(DEFAULT_UA_FAMILY_CARRIERS)
    )

    if train_flights_df.empty:
        return PlanningPriors(
            modal_t1={},
            modal_t2={},
            modal_t3={},
            modal_t4={},
            lf_t1={},
            lf_t2={},
            lf_t3={},
            lf_t4={},
            lf_t5={},
            q_t1={},
            q_t2={},
            q_t3={},
            q_global=float("nan"),
            tsa_t1={},
            tsa_t2={},
            tsa_t3={},
            tsa_global=(float("nan"), float("nan")),
        )

    tf = pd.DataFrame(
        {
            "op_carrier": train_flights_df["op_carrier"].astype(str).str.strip().str.upper(),
            "origin": train_flights_df["origin"].astype(str).str.strip().str.upper(),
            "dest": train_flights_df["dest"].astype(str).str.strip().str.upper(),
            "month": train_flights_df["month"].astype("int64"),
            "hour_block": train_flights_df["hour_block"].astype(str).str.strip().str.lower(),
            "_seats_num": pd.to_numeric(train_flights_df["seats_config"], errors="coerce"),
            "_bts_raw": train_flights_df["bts_aircraft_type"],
            "_canc": train_flights_df["is_cancelled"].astype(bool),
            "_div": train_flights_df["is_diverted"].astype(bool),
        },
        index=train_flights_df.index,
    )

    # 1. 4-tier modal (seats_config, bts_aircraft_type)
    bts_str = tf["_bts_raw"].astype(str).str.strip()
    valid_modal = (
        tf["_seats_num"].notna()
        & (tf["_seats_num"] > 0)
        & tf["_bts_raw"].notna()
        & (bts_str != "")
        & (~bts_str.str.lower().isin(["nan", "none", "<na>"]))
    )
    tf_modal = tf.loc[valid_modal].copy()
    tf_modal["seats_int"] = tf_modal["_seats_num"].astype("int64")
    tf_modal["bts_type"] = bts_str.loc[valid_modal]

    modal_t1 = _fit_modal_tier(tf_modal, ["op_carrier", "origin", "dest", "month", "hour_block"])
    modal_t2 = _fit_modal_tier(tf_modal, ["op_carrier", "origin", "dest", "month"])
    modal_t3 = _fit_modal_tier(tf_modal, ["op_carrier", "origin", "dest"])
    modal_t4 = _fit_modal_tier(tf_modal, ["origin", "dest"])

    # 2. 5-tier T-100 LF lookup
    lf_t1: dict[tuple[str, str, str, int, str], float] = {}
    lf_t2: dict[tuple[str, str, str, int], float] = {}
    lf_t3: dict[tuple[str, str, int], float] = {}
    lf_t4: dict[tuple[str, str, str], float] = {}
    lf_t5: dict[tuple[str, str], float] = {}

    if train_t100_df is not None:
        if not train_t100_df.empty:
            hubs = (
                {h.strip().upper() for h in config.hubs}
                if config is not None and config.hubs
                else set(tf["origin"].unique())
            )
            dom = train_t100_df.copy()
            dom["service_class"] = dom["service_class"].astype(str).str.strip().str.upper()
            dom["data_source"] = dom["data_source"].astype(str).str.strip().str.upper()
            dom["origin"] = dom["origin"].astype(str).str.strip().str.upper()
            dom["dest"] = dom["dest"].astype(str).str.strip().str.upper()
            dom["op_carrier"] = dom["op_carrier"].astype(str).str.strip().str.upper()
            dom["bts_aircraft_type"] = dom["bts_aircraft_type"].astype(str).str.strip()
            dom["month"] = dom["month"].astype("int64")
            dom = dom[
                (dom["service_class"] == "F")
                & (dom["data_source"].isin(["DU", "DF"]))
                & (dom["origin"].isin(hubs))
            ].copy()

            if not dom.empty:

                def _agg_t100(df_in: pd.DataFrame, cols: list[str]) -> dict[tuple[Any, ...], float]:
                    g = df_in.groupby(cols, as_index=False)[
                        ["passengers", "seats", "departures_performed"]
                    ].sum()
                    g = g[(g["seats"] > 0) & (g["departures_performed"] > 0)].copy()
                    g["lf"] = (g["passengers"] / g["seats"]).clip(0.0, 1.0)
                    return {
                        tuple(getattr(r, c) for c in cols): float(r.lf)
                        for r in g.itertuples(index=False)
                    }

                dom_valid_type = dom[
                    dom["bts_aircraft_type"].notna()
                    & (dom["bts_aircraft_type"] != "")
                    & (~dom["bts_aircraft_type"].str.lower().isin(["nan", "none"]))
                ]
                lf_t1 = _agg_t100(
                    dom_valid_type,
                    ["op_carrier", "origin", "dest", "month", "bts_aircraft_type"],
                )
                lf_t2 = _agg_t100(dom, ["op_carrier", "origin", "dest", "month"])
                dom_ua = dom[dom["op_carrier"].isin(ua_family)]
                lf_t3 = _agg_t100(dom_ua, ["origin", "dest", "month"])
                lf_t4 = _agg_t100(dom, ["op_carrier", "origin", "dest"])
                lf_t5 = _agg_t100(dom_ua, ["origin", "dest"])
    else:
        # Fallback ONLY when train_t100_df is None
        if "est_load_factor" in train_flights_df.columns:
            lf_vals = pd.to_numeric(train_flights_df["est_load_factor"], errors="coerce")
            valid_lf = lf_vals.notna()
            if "t100_available" in train_flights_df.columns:
                valid_lf &= train_flights_df["t100_available"].astype(bool)
            if "lf_source" in train_flights_df.columns:
                valid_lf &= train_flights_df["lf_source"].astype(str) != "UNMATCHED"
            tf_lf = tf.loc[valid_lf].copy()
            tf_lf["lf_val"] = lf_vals.loc[valid_lf].clip(0.0, 1.0)
            tf_lf["bts_type"] = bts_str.loc[valid_lf]

            def _agg_flight_lf(
                df_in: pd.DataFrame, cols: list[str]
            ) -> dict[tuple[Any, ...], float]:
                if df_in.empty:
                    return {}
                g = df_in.groupby(cols, as_index=False)["lf_val"].mean()
                return {
                    tuple(getattr(r, c) for c in cols): float(r.lf_val)
                    for r in g.itertuples(index=False)
                }

            tf_lf_type = tf_lf[
                (tf_lf["bts_type"] != "") & (~tf_lf["bts_type"].str.lower().isin(["nan", "none"]))
            ]
            lf_t1 = _agg_flight_lf(
                tf_lf_type, ["op_carrier", "origin", "dest", "month", "bts_type"]
            )
            lf_t2 = _agg_flight_lf(tf_lf, ["op_carrier", "origin", "dest", "month"])
            tf_lf_ua = tf_lf[tf_lf["op_carrier"].isin(ua_family)]
            lf_t3 = _agg_flight_lf(tf_lf_ua, ["origin", "dest", "month"])
            lf_t4 = _agg_flight_lf(tf_lf, ["op_carrier", "origin", "dest"])
            lf_t5 = _agg_flight_lf(tf_lf_ua, ["origin", "dest"])

    # 3. 4-tier disruption rate q_hat (min_obs on Tiers 1 & 2)
    tf["_disr_w"] = np.where(
        tf["_canc"], 1.0, np.where(tf["_div"], float(sc.disp_diversion_weight), 0.0)
    )
    min_obs = int(sc.disp_plan_min_obs)

    def _agg_q(cols: list[str], min_count: int) -> dict[tuple[Any, ...], float]:
        g = tf.groupby(cols, as_index=False)["_disr_w"].agg(["count", "mean"])
        g = g[g["count"] >= min_count]
        return {
            tuple(getattr(r, c) for c in cols): float(r.mean) for r in g.itertuples(index=False)
        }

    q_t1 = _agg_q(["origin", "dest", "month", "hour_block"], min_obs)
    q_t2 = _agg_q(["origin", "dest", "month"], min_obs)
    q_t3 = _agg_q(["origin", "dest"], 1)
    q_global = float(tf["_disr_w"].mean())

    # 4. 4-tier TSA ratio lookup: (month, iso_dow, is_holiday_window) -> (month, iso_dow) -> (month) -> global
    tsa_t1: dict[tuple[int, int, bool], tuple[float, float]] = {}
    tsa_t2: dict[tuple[int, int], tuple[float, float]] = {}
    tsa_t3: dict[int, tuple[float, float]] = {}
    tsa_global: tuple[float, float] = (float("nan"), float("nan"))

    if train_tsa_df is None:
        tsa_global = (1.0, 1.0)
    else:
        tsa_ratios = compute_tsa_annual_ratios(train_tsa_df)
        tsa_valid = tsa_ratios.dropna(subset=["tsa_ratio_month", "tsa_ratio_year"])
        if not tsa_valid.empty:
            g1 = tsa_valid.groupby(["month", "iso_dow", "is_holiday_window"], as_index=False)[
                ["tsa_ratio_month", "tsa_ratio_year"]
            ].mean()
            tsa_t1 = {
                (int(r.month), int(r.iso_dow), bool(r.is_holiday_window)): (
                    float(r.tsa_ratio_month),
                    float(r.tsa_ratio_year),
                )
                for r in g1.itertuples(index=False)
            }

            g2 = tsa_valid.groupby(["month", "iso_dow"], as_index=False)[
                ["tsa_ratio_month", "tsa_ratio_year"]
            ].mean()
            tsa_t2 = {
                (int(r.month), int(r.iso_dow)): (
                    float(r.tsa_ratio_month),
                    float(r.tsa_ratio_year),
                )
                for r in g2.itertuples(index=False)
            }

            g3 = tsa_valid.groupby("month", as_index=False)[
                ["tsa_ratio_month", "tsa_ratio_year"]
            ].mean()
            tsa_t3 = {
                int(r.month): (float(r.tsa_ratio_month), float(r.tsa_ratio_year))
                for r in g3.itertuples(index=False)
            }

            tsa_global = (
                float(tsa_valid["tsa_ratio_month"].mean()),
                float(tsa_valid["tsa_ratio_year"].mean()),
            )

    return PlanningPriors(
        modal_t1=modal_t1,
        modal_t2=modal_t2,
        modal_t3=modal_t3,
        modal_t4=modal_t4,
        lf_t1=lf_t1,
        lf_t2=lf_t2,
        lf_t3=lf_t3,
        lf_t4=lf_t4,
        lf_t5=lf_t5,
        q_t1=q_t1,
        q_t2=q_t2,
        q_t3=q_t3,
        q_global=q_global,
        tsa_t1=tsa_t1,
        tsa_t2=tsa_t2,
        tsa_t3=tsa_t3,
        tsa_global=tsa_global,
    )


def score_planning_with_priors(
    eval_flights_df: pd.DataFrame,
    priors: PlanningPriors,
    config: StandbyConfig | None = None,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Score `eval_flights_df` using ONLY schedule/calendar keys and pre-fitted `PlanningPriors`
    (plus `t100_available` and `lf_source` solely for the `SC-5` NULL gate).

    Never reads `seats_config`, `bts_aircraft_type`, `est_load_factor`, `is_cancelled`,
    `is_diverted`, `tsa_throughput`, or `est_*` from `eval_flights_df`.
    """
    out = eval_flights_df.copy()
    sc = _resolve_scoring_config(config)
    w = _resolve_weights(config=config, weights=weights)

    alpha = config.enrichment.tsa_damping_alpha if config is not None else 0.35
    tsa_bounds = config.enrichment.tsa_multiplier_bounds if config is not None else (0.80, 1.20)
    hb_mults = (
        {
            str(k).strip().lower(): float(v)
            for k, v in config.enrichment.hour_block_multipliers.items()
        }
        if config is not None
        else {
            "early": 0.96,
            "morning": 1.03,
            "midday": 1.00,
            "afternoon": 1.02,
            "evening": 1.04,
            "late": 0.94,
        }
    )

    # Pure schedule & calendar signals
    sched_counts = compute_strict_schedule_counts(out)
    prior_strict = sched_counts["prior_strict_scheduled_count"].to_numpy(dtype="float64")
    rem_sched = sched_counts["remaining_scheduled_backups"].to_numpy(dtype="float64")

    flight_dt = pd.to_datetime(out["flight_date"], errors="raise")
    iso_dows = (flight_dt.dt.dayofweek.astype("int64") + 1).tolist()
    hol_names = holiday_name_series(out["flight_date"])
    is_hol_bools = hol_names.notna().astype(bool).tolist()
    if "holiday_name" not in out.columns:
        out["holiday_name"] = hol_names
    if "is_holiday_window" not in out.columns:
        out["is_holiday_window"] = pd.Series(is_hol_bools, index=out.index, dtype=bool)

    carriers = out["op_carrier"].astype(str).str.strip().str.upper().tolist()
    origins = out["origin"].astype(str).str.strip().str.upper().tolist()
    dests = out["dest"].astype(str).str.strip().str.upper().tolist()
    months = out["month"].astype("int64").tolist()
    hbs = out["hour_block"].astype(str).str.strip().str.lower().tolist()

    n = len(out)
    prior_seats = np.full(n, np.nan, dtype="float64")
    prior_lf = np.full(n, np.nan, dtype="float64")
    prior_q = np.full(n, np.nan, dtype="float64")
    r_month_arr = np.full(n, np.nan, dtype="float64")
    r_year_arr = np.full(n, np.nan, dtype="float64")
    hb_mult_arr = np.array([hb_mults.get(hb, 1.0) for hb in hbs], dtype="float64")

    m1, m2, m3, m4 = priors.modal_t1, priors.modal_t2, priors.modal_t3, priors.modal_t4
    l1, l2, l3, l4, l5 = priors.lf_t1, priors.lf_t2, priors.lf_t3, priors.lf_t4, priors.lf_t5
    q1, q2, q3, q_glob = priors.q_t1, priors.q_t2, priors.q_t3, priors.q_global
    t1, t2, t3, t_glob = priors.tsa_t1, priors.tsa_t2, priors.tsa_t3, priors.tsa_global

    for i in range(n):
        c = carriers[i]
        o = origins[i]
        d = dests[i]
        m = months[i]
        hb = hbs[i]
        dow = iso_dows[i]
        hol = is_hol_bools[i]

        # 4-tier modal (seats_config, bts_aircraft_type)
        modal_hit = (
            m1.get((c, o, d, m, hb)) or m2.get((c, o, d, m)) or m3.get((c, o, d)) or m4.get((o, d))
        )
        if modal_hit is not None:
            p_seats, p_bts = modal_hit
            prior_seats[i] = float(p_seats)
            # 5-tier T-100 LF using prior_bts (NEVER eval actual bts_aircraft_type)
            lf_hit = l1.get((c, o, d, m, p_bts))
            if lf_hit is None:
                lf_hit = l2.get((c, o, d, m))
            if lf_hit is None:
                lf_hit = l3.get((o, d, m))
            if lf_hit is None:
                lf_hit = l4.get((c, o, d))
            if lf_hit is None:
                lf_hit = l5.get((o, d))
            if lf_hit is not None:
                prior_lf[i] = float(lf_hit)

        # 4-tier disruption rate q_hat
        q_hit = q1.get((o, d, m, hb))
        if q_hit is None:
            q_hit = q2.get((o, d, m))
        if q_hit is None:
            q_hit = q3.get((o, d))
        if q_hit is None:
            q_hit = q_glob
        prior_q[i] = float(q_hit)

        # 4-tier TSA ratios
        tsa_hit = t1.get((m, dow, hol)) or t2.get((m, dow)) or t3.get(m) or t_glob
        r_month_arr[i] = float(tsa_hit[0])
        r_year_arr[i] = float(tsa_hit[1])

    # SC-5 NULL gate (is_cancelled is NOT excluded for planning!)
    t100_ok = out["t100_available"].astype(bool).to_numpy()
    lf_src_ok = out["lf_source"].astype(str).to_numpy() != "UNMATCHED"
    eligible = (
        t100_ok
        & lf_src_ok
        & (~np.isnan(prior_seats))
        & (prior_seats > 0.0)
        & (~np.isnan(prior_lf))
        & (~np.isnan(prior_q))
        & (~np.isnan(r_month_arr))
        & (~np.isnan(r_year_arr))
    )

    s_open = np.full(n, np.nan, dtype="float64")
    s_disp = np.full(n, np.nan, dtype="float64")
    s_backup = np.full(n, np.nan, dtype="float64")
    s_demand = np.full(n, np.nan, dtype="float64")
    soi_plan = np.full(n, np.nan, dtype="float64")

    if eligible.any():
        seats_e = prior_seats[eligible]
        lf_e = prior_lf[eligible]
        q_e = prior_q[eligible]
        rm_e = r_month_arr[eligible]
        ry_e = r_year_arr[eligible]
        hb_e = hb_mult_arr[eligible]
        p_strict_e = prior_strict[eligible]
        rem_sched_e = rem_sched[eligible]
        hol_e = np.array(is_hol_bools, dtype="float64")[eligible]

        m_tsa_plan = np.clip(1.0 + float(alpha) * (rm_e - 1.0), tsa_bounds[0], tsa_bounds[1])
        lf_adj_e = np.clip(lf_e * m_tsa_plan * hb_e, 0.0, 1.0)
        pax_adj_e = np.clip(np.round(seats_e * lf_adj_e), 0.0, seats_e)
        open_adj_e = seats_e - pax_adj_e

        s_abs = 100.0 * np.clip(open_adj_e / sc.open_seats_abs_cap, 0.0, 1.0)
        s_pct = 100.0 * np.clip((open_adj_e / seats_e) / sc.open_seats_pct_cap, 0.0, 1.0)
        s_open_e = sc.open_seats_abs_weight * s_abs + (1.0 - sc.open_seats_abs_weight) * s_pct

        d_hat_e = p_strict_e * q_e * sc.disp_plan_prior_factor + q_e
        s_disp_e = 100.0 * np.clip(1.0 - d_hat_e / sc.disp_plan_cap, 0.0, 1.0)

        s_backup_e = 100.0 * np.clip(
            np.log1p(rem_sched_e) / np.log1p(float(sc.backup_max_flights)),
            0.0,
            1.0,
        )

        i_demand_e = ry_e + sc.demand_holiday_adder * hol_e
        denom = sc.demand_ratio_max - sc.demand_ratio_min
        s_demand_e = 100.0 * np.clip((sc.demand_ratio_max - i_demand_e) / denom, 0.0, 1.0)

        comp_e = np.round(
            w["open_seats"] * s_open_e
            + w["displacement_pressure"] * s_disp_e
            + w["backup_options"] * s_backup_e
            + w["day_holiday_demand"] * s_demand_e,
            2,
        )

        s_open[eligible] = s_open_e
        s_disp[eligible] = s_disp_e
        s_backup[eligible] = s_backup_e
        s_demand[eligible] = s_demand_e
        soi_plan[eligible] = comp_e

    out["s_open_plan"] = pd.Series(s_open, index=out.index, dtype="float64")
    out["s_disp_plan"] = pd.Series(s_disp, index=out.index, dtype="float64")
    out["s_backup_plan"] = pd.Series(s_backup, index=out.index, dtype="float64")
    out["s_demand_plan"] = pd.Series(s_demand, index=out.index, dtype="float64")
    out["soi_planning"] = pd.Series(soi_plan, index=out.index, dtype="float64")
    return out


def compute_soi_planning(
    flights_df: pd.DataFrame,
    t100_df: pd.DataFrame | None = None,
    tsa_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Compute leakage-free `soi_planning` across all years via expanding `train.year < eval.year`
    walk-forward fitting. The minimum year (e.g. `2019`) strictly receives `NaN` (`float64`).
    Preserves exact input index and row ordering.
    """
    out = flights_df.copy()
    for col in (*PLANNING_SUBSCORE_COLS, "soi_planning"):
        out[col] = pd.Series(np.nan, index=out.index, dtype="float64")

    if out.empty:
        return out

    # Pre-normalize tsa_df year if supplied
    tsa_with_year: pd.DataFrame | None = None
    if tsa_df is not None:
        if tsa_df.empty:
            tsa_with_year = tsa_df.copy()
            tsa_with_year["_year"] = pd.Series([], dtype="int64")
        else:
            tsa_with_year = tsa_df.copy()
            tsa_dt = pd.to_datetime(tsa_with_year["date_day"], errors="coerce")
            tsa_with_year["_year"] = tsa_dt.dt.year.fillna(-1).astype("int64")

    eval_years = sorted(int(y) for y in out["year"].dropna().unique())
    for eval_year in eval_years:
        eval_mask = out["year"].astype("int64") == eval_year
        train_flights = out.loc[out["year"].astype("int64") < eval_year]
        if train_flights.empty:
            continue

        train_t100: pd.DataFrame | None = None
        if t100_df is not None:
            train_t100 = t100_df.loc[t100_df["year"].astype("int64") < eval_year]

        train_tsa: pd.DataFrame | None = None
        if tsa_with_year is not None:
            train_tsa = tsa_with_year.loc[
                (tsa_with_year["_year"] > 0) & (tsa_with_year["_year"] < eval_year)
            ].drop(columns=["_year"])

        priors = fit_planning_priors(
            train_flights_df=train_flights,
            train_t100_df=train_t100,
            train_tsa_df=train_tsa,
            config=config,
        )
        scored_eval = score_planning_with_priors(
            eval_flights_df=out.loc[eval_mask],
            priors=priors,
            config=config,
            weights=weights,
        )
        for col in (*PLANNING_SUBSCORE_COLS, "soi_planning"):
            out.loc[eval_mask, col] = scored_eval[col].astype("float64")

    return out


def score_flights(
    enriched_df: pd.DataFrame,
    t100_df: pd.DataFrame | None = None,
    tsa_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Execute full Phase 4 Standby Opportunity Index scoring on enriched flights:
      1. `compute_soi_historical`
      2. `compute_soi_planning` (expanding `train.year < eval.year`)
      3. `tag_dataframe_confidence_tiers`
    """
    from standby.enrichment.confidence import tag_dataframe_confidence_tiers

    tsa_input = (
        tsa_df if tsa_df is not None else pd.DataFrame(columns=["date_day", "tsa_throughput"])
    )
    df_hist = compute_soi_historical(
        flights_df=enriched_df,
        tsa_df=tsa_input,
        config=config,
        weights=weights,
    )
    df_both = compute_soi_planning(
        flights_df=df_hist,
        t100_df=t100_df,
        tsa_df=tsa_df,
        config=config,
        weights=weights,
    )
    tagged = tag_dataframe_confidence_tiers(df_both)
    assert len(tagged) == len(enriched_df), "Row count invariant violated in score_flights"
    return tagged
