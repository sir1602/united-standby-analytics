"""Hierarchical empirical Bayes priors for day-based standby seat likelihood prediction."""

from __future__ import annotations

import calendar
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from standby.config import PredictionConfig, StandbyConfig

HOUR_BLOCK_NAMES: tuple[str, ...] = (
    "early",
    "morning",
    "midday",
    "afternoon",
    "evening",
    "late",
)
HOUR_BLOCK_TO_IDX: dict[str, int] = {name: idx for idx, name in enumerate(HOUR_BLOCK_NAMES)}


@dataclass
class PredictorPriors:
    """Compact hierarchical priors shared between Python CLI and GitHub Pages JS simulator.

    Cell vector layout for domestic tiers (``tier1``, ``tier2``, ``tier4``):
      ``[n, mean_open, std_open, mean_seats, mean_lf, cancel_rate, hb_share]``
    Cell vector layout for route-month ``tier3`` (domestic + international v1):
      ``[n, mean_open, std_open, mean_seats, mean_lf, cancel_rate, avg_daily_flights]``
    """

    meta: dict[str, Any]
    routes: dict[str, dict[str, Any]]
    tier1: dict[str, list[float]]
    tier2: dict[str, list[float]]
    tier3: dict[str, list[float]]
    tier4: dict[str, list[float]]
    holiday_deltas: dict[str, dict[str, float]]
    dow_multipliers: dict[str, float]
    route_carrier_factors: dict[str, dict[str, list[float]]]
    calibration: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "meta": self.meta,
            "routes": self.routes,
            "tier1": self.tier1,
            "tier2": self.tier2,
            "tier3": self.tier3,
            "tier4": self.tier4,
            "holiday_deltas": self.holiday_deltas,
            "dow_multipliers": self.dow_multipliers,
            "route_carrier_factors": self.route_carrier_factors,
        }
        if self.calibration is not None:
            payload["calibration"] = self.calibration
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PredictorPriors:
        return cls(
            meta=dict(data["meta"]),
            routes=dict(data["routes"]),
            tier1=dict(data["tier1"]),
            tier2=dict(data["tier2"]),
            tier3=dict(data["tier3"]),
            tier4=dict(data["tier4"]),
            holiday_deltas=dict(data["holiday_deltas"]),
            dow_multipliers=dict(data["dow_multipliers"]),
            route_carrier_factors=dict(data.get("route_carrier_factors", {})),
            calibration=data.get("calibration"),
        )


def _round_cell(
    n: int,
    mean_open: float,
    std_open: float,
    mean_seats: float,
    mean_lf: float,
    cancel_rate: float,
    rate_or_share: float,
) -> list[float]:
    return [
        int(n),
        round(float(mean_open), 1),
        round(max(1.5, float(std_open)), 1),
        round(float(mean_seats), 1),
        round(min(1.0, max(0.0, float(mean_lf))), 3),
        round(min(1.0, max(0.0, float(cancel_rate))), 3),
        round(max(0.0, float(rate_or_share)), 3),
    ]


def _days_in_ym(year: int, month: int) -> int:
    return calendar.monthrange(int(year), int(month))[1]


def _compute_group_stats(
    df: pd.DataFrame,
    group_cols: list[str],
    min_n: int = 1,
) -> dict[tuple[Any, ...], tuple[int, float, float, float, float, float]]:
    """Return mapping key -> (n_total, mean_open, std_open, mean_seats, mean_lf, cancel_rate)."""
    if df.empty:
        return {}

    out: dict[tuple[Any, ...], tuple[int, float, float, float, float, float]] = {}
    grouped = df.groupby(group_cols, sort=False, observed=True)
    for key, grp in grouped:
        n_total = int(len(grp))
        if n_total < min_n:
            continue
        k_tuple = key if isinstance(key, tuple) else (key,)
        canc_rate = float(grp["is_cancelled_bool"].mean())
        perf = grp.loc[~grp["is_cancelled_bool"]]
        open_vals = perf["est_open_seats_adjusted"].dropna()
        seats_vals = perf["seats_config"].dropna()
        lf_vals = perf["est_lf_adjusted"].dropna()
        if open_vals.empty:
            continue
        mean_open = float(open_vals.mean())
        std_open = (
            float(open_vals.std(ddof=1)) if len(open_vals) >= 2 else max(3.5, mean_open * 0.45)
        )
        if math.isnan(std_open) or std_open < 1.5:
            std_open = max(2.5, mean_open * 0.35)
        mean_seats = float(seats_vals.mean()) if not seats_vals.empty else 120.0
        mean_lf = float(lf_vals.mean()) if not lf_vals.empty else 0.82
        out[k_tuple] = (n_total, mean_open, std_open, mean_seats, mean_lf, canc_rate)
    return out


def fit_predictor_priors(
    flights_df: pd.DataFrame,
    route_month_df: pd.DataFrame,
    route_summary_df: pd.DataFrame,
    config: StandbyConfig | None = None,
    max_year: int | None = None,
    min_tier1_n: int = 8,
) -> PredictorPriors:
    """Fit hierarchical empirical Bayes priors from flight and route-month DataFrames.

    If ``max_year`` is set, strictly filters both DataFrames to ``year <= max_year`` so
    expanding walk-forward calibration has zero future leakage.
    """
    pred_cfg = config.prediction if config is not None else PredictionConfig()
    hub = config.active_hub if config is not None else "ORD"

    fdf = flights_df.copy()
    rmdf = route_month_df.copy()
    rsdf = route_summary_df.copy()

    if max_year is not None:
        fdf = fdf.loc[pd.to_numeric(fdf["year"], errors="coerce") <= int(max_year)].copy()
        rmdf = rmdf.loc[pd.to_numeric(rmdf["year"], errors="coerce") <= int(max_year)].copy()

    # Standardize key columns
    fdf["dest"] = fdf["dest"].astype(str).str.strip().str.upper()
    fdf["month"] = pd.to_numeric(fdf["month"], errors="coerce").astype("Int64")
    fdf["day_of_week"] = pd.to_numeric(fdf["day_of_week"], errors="coerce").astype("Int64")
    fdf["hour_block"] = fdf["hour_block"].astype(str).str.strip().str.lower()
    fdf["hb_idx"] = fdf["hour_block"].map(HOUR_BLOCK_TO_IDX)
    fdf["carrier_group"] = fdf["carrier_group"].astype(str).str.strip().str.upper()
    fdf["is_cancelled_bool"] = fdf["is_cancelled"].astype(str).str.lower().isin({"true", "1"})
    for num_col in (
        "est_open_seats_adjusted",
        "seats_config",
        "est_lf_adjusted",
        "est_load_factor",
    ):
        if num_col in fdf.columns:
            fdf[num_col] = pd.to_numeric(fdf[num_col], errors="coerce")
    fdf = fdf.loc[fdf["month"].notna() & fdf["day_of_week"].notna() & fdf["hb_idx"].notna()].copy()
    fdf["month"] = fdf["month"].astype(int)
    fdf["day_of_week"] = fdf["day_of_week"].astype(int)
    fdf["hb_idx"] = fdf["hb_idx"].astype(int)

    rmdf["dest"] = rmdf["dest"].astype(str).str.strip().str.upper()
    rmdf["year"] = pd.to_numeric(rmdf["year"], errors="coerce").astype(int)
    rmdf["month"] = pd.to_numeric(rmdf["month"], errors="coerce").astype(int)
    rmdf["is_intl_bool"] = rmdf["is_international"].astype(str).str.lower().isin({"true", "1"})
    for num_col in (
        "departures_scheduled",
        "departures_performed",
        "seats",
        "passengers",
        "t100_load_factor",
        "t100_seats_per_departure",
        "otp_scheduled_flights",
        "otp_cancelled_flights",
        "otp_avg_est_open_seats_adjusted",
    ):
        if num_col in rmdf.columns:
            rmdf[num_col] = pd.to_numeric(rmdf[num_col], errors="coerce")

    # 1. Exact full-population daily departure rate per (dest, month, is_intl) from route_month_df
    rm_ym = rmdf.groupby(["dest", "is_intl_bool", "year", "month"], as_index=False).agg(
        deps_sched=("departures_scheduled", "sum"),
        deps_perf=("departures_performed", "sum"),
        seats=("seats", "sum"),
        pax=("passengers", "sum"),
        otp_sched=("otp_scheduled_flights", "sum"),
        otp_canc=("otp_cancelled_flights", "sum"),
    )
    rm_ym["days"] = [_days_in_ym(y, m) for y, m in zip(rm_ym["year"], rm_ym["month"], strict=True)]
    rm_ym["daily_flights"] = np.where(
        (~rm_ym["is_intl_bool"]) & (rm_ym["otp_sched"] > 0),
        rm_ym["otp_sched"] / rm_ym["days"],
        np.maximum(rm_ym["deps_sched"], rm_ym["deps_perf"]) / rm_ym["days"],
    )

    rm_dest_month = rm_ym.groupby(["dest", "is_intl_bool", "month"], as_index=False).agg(
        active_months=("year", "count"),
        avg_daily_flights=("daily_flights", "mean"),
        total_deps=("deps_perf", "sum"),
        total_seats=("seats", "sum"),
        total_pax=("pax", "sum"),
        total_otp_sched=("otp_sched", "sum"),
        total_otp_canc=("otp_canc", "sum"),
    )

    # Global baseline stats for Tier 4 fallback
    tier4: dict[str, list[float]] = {}
    for cg_label, sub_df in [
        ("ALL", fdf),
        ("MAINLINE", fdf.loc[fdf["carrier_group"] == "MAINLINE"]),
        ("EXPRESS", fdf.loc[fdf["carrier_group"] == "EXPRESS"]),
    ]:
        if sub_df.empty:
            continue
        m_totals = sub_df.groupby("month").size().to_dict()
        stats_m_hb = _compute_group_stats(sub_df, ["month", "hb_idx"], min_n=1)
        for (m, hb), (n, mo, so, ms, mlf, cr) in stats_m_hb.items():
            share = n / max(1, m_totals.get(m, n))
            tier4[f"{cg_label}|{m}|{hb}"] = _round_cell(n, mo, so, ms, mlf, cr, share)

    # Tier 3: route x month (covers both domestic and all 71 international v1 routes)
    dom_t3_stats = _compute_group_stats(fdf, ["dest", "month"], min_n=1)
    tier3: dict[str, list[float]] = {}
    for row in rm_dest_month.itertuples(index=False):
        dest = str(row.dest)
        m = int(row.month)
        is_intl = bool(row.is_intl_bool)
        daily_fl = float(row.avg_daily_flights)
        mean_seats = float(row.total_seats) / float(row.total_deps) if row.total_deps > 0 else 140.0
        mean_lf = (
            min(1.0, max(0.0, float(row.total_pax) / float(row.total_seats)))
            if row.total_seats > 0
            else 0.82
        )
        t100_open = max(0.0, mean_seats * (1.0 - mean_lf))
        if not is_intl and (dest, m) in dom_t3_stats:
            n, mo, so, ms, mlf, cr = dom_t3_stats[(dest, m)]
            # Prefer exact full-population OTP cancellation rate when available
            if row.total_otp_sched >= 20:
                cr = float(row.total_otp_canc) / float(row.total_otp_sched)
            tier3[f"{dest}|{m}"] = _round_cell(n, mo, so, ms, mlf, cr, daily_fl)
        else:
            # International v1 or sparse domestic month: derive from T-100 monthly totals
            std_est = max(4.0, mean_seats * 0.09)
            cr_est = 0.015 if is_intl else 0.02
            n_eff = max(1, int(round(row.total_deps)))
            key = f"{dest}|{m}"
            # Avoid overwriting domestic entry with an international segment on same dest (rare)
            if key not in tier3:
                tier3[key] = _round_cell(
                    n_eff, t100_open, std_est, mean_seats, mean_lf, cr_est, daily_fl
                )

    # Also add any domestic (dest, month) present in flights_df but absent in rm_dest_month
    for (dest, m), (n, mo, so, ms, mlf, cr) in dom_t3_stats.items():
        key = f"{dest}|{m}"
        if key not in tier3:
            tier3[key] = _round_cell(n, mo, so, ms, mlf, cr, max(1.0, n / 30.0))

    # Tier 2: domestic dest x month x hour_block (with hour-block share of day)
    t2_stats = _compute_group_stats(fdf, ["dest", "month", "hb_idx"], min_n=1)
    t2_totals = fdf.groupby(["dest", "month"]).size().to_dict()
    tier2: dict[str, list[float]] = {}
    for (dest, m, hb), (n, mo, so, ms, mlf, cr) in t2_stats.items():
        share = n / max(1, t2_totals.get((dest, m), n))
        tier2[f"{dest}|{m}|{hb}"] = _round_cell(n, mo, so, ms, mlf, cr, share)

    # Tier 1: domestic dest x month x dow x hour_block (pruned at min_tier1_n for compact JSON)
    t1_stats = _compute_group_stats(
        fdf, ["dest", "month", "day_of_week", "hb_idx"], min_n=min_tier1_n
    )
    t1_totals = fdf.groupby(["dest", "month", "day_of_week"]).size().to_dict()
    tier1: dict[str, list[float]] = {}
    for (dest, m, dow, hb), (n, mo, so, ms, mlf, cr) in t1_stats.items():
        share = n / max(1, t1_totals.get((dest, m, dow), n))
        tier1[f"{dest}|{m}|{dow}|{hb}"] = _round_cell(n, mo, so, ms, mlf, cr, share)

    # Empirical day-of-week open-seat multipliers (relative to overall performed mean)
    perf_all = fdf.loc[~fdf["is_cancelled_bool"] & fdf["est_open_seats_adjusted"].notna()]
    overall_open_mean = (
        float(perf_all["est_open_seats_adjusted"].mean()) if not perf_all.empty else 18.0
    )
    dow_multipliers: dict[str, float] = {}
    for dow in range(1, 8):
        sub = perf_all.loc[perf_all["day_of_week"] == dow, "est_open_seats_adjusted"]
        ratio = (
            float(sub.mean()) / overall_open_mean
            if (not sub.empty and overall_open_mean > 0)
            else 1.0
        )
        dow_multipliers[str(dow)] = round(min(1.35, max(0.70, ratio)), 4)

    # Empirical holiday-window effects (mean open-seat ratio & cancel-rate multiplier vs non-holiday)
    holiday_deltas: dict[str, dict[str, float]] = {}
    if "holiday_name" in fdf.columns:
        hol_series = fdf["holiday_name"].fillna("").astype(str).str.strip()
        non_hol = fdf.loc[hol_series == ""]
        non_hol_perf = non_hol.loc[
            ~non_hol["is_cancelled_bool"] & non_hol["est_open_seats_adjusted"].notna()
        ]
        base_open = (
            float(non_hol_perf["est_open_seats_adjusted"].mean())
            if not non_hol_perf.empty
            else overall_open_mean
        )
        base_canc = float(non_hol["is_cancelled_bool"].mean()) if not non_hol.empty else 0.02

        for hname in sorted(h for h in hol_series.unique() if h):
            h_df = fdf.loc[hol_series == hname]
            h_perf = h_df.loc[~h_df["is_cancelled_bool"] & h_df["est_open_seats_adjusted"].notna()]
            if h_perf.empty:
                continue
            open_ratio = float(h_perf["est_open_seats_adjusted"].mean()) / max(1.0, base_open)
            canc_ratio = float(h_df["is_cancelled_bool"].mean()) / max(0.005, base_canc)
            holiday_deltas[hname] = {
                "open_mult": round(min(1.15, max(0.55, open_ratio)), 4),
                "cancel_mult": round(min(2.2, max(0.6, canc_ratio)), 4),
                "n": int(len(h_df)),
            }

    # Route-level carrier-group adjustments (MAINLINE vs EXPRESS ratio relative to route average)
    route_carrier_factors: dict[str, dict[str, list[float]]] = {}
    if not perf_all.empty:
        r_all = perf_all.groupby("dest")["est_open_seats_adjusted"].mean().to_dict()
        r_cg = (
            fdf.groupby(["dest", "carrier_group"])
            .agg(
                n=("is_cancelled_bool", "size"),
                mean_open=("est_open_seats_adjusted", "mean"),
                mean_seats=("seats_config", "mean"),
                canc=("is_cancelled_bool", "mean"),
            )
            .reset_index()
        )
        r_tot = fdf.groupby("dest").size().to_dict()
        for row in r_cg.itertuples(index=False):
            dest = str(row.dest)
            cg = str(row.carrier_group)
            if pd.isna(row.mean_open) or r_all.get(dest, 0.0) <= 0:
                continue
            share = float(row.n) / max(1, r_tot.get(dest, row.n))
            open_ratio = min(2.2, max(0.35, float(row.mean_open) / float(r_all[dest])))
            route_carrier_factors.setdefault(dest, {})[cg] = [
                round(share, 3),
                round(open_ratio, 3),
                round(float(row.mean_seats) if not pd.isna(row.mean_seats) else 100.0, 1),
                round(float(row.canc), 4),
            ]

    # Route metadata dictionary (all 348 ORD destinations)
    routes: dict[str, dict[str, Any]] = {}
    for row in rsdf.itertuples(index=False):
        dest = str(row.dest).strip().upper()
        is_intl = str(row.is_international).lower() in {"true", "1"}
        # Prefer domestic entry if a dest appears in both (rare charter/diversion edge case)
        if dest in routes and is_intl:
            continue
        deps = (
            int(round(float(row.total_departures_performed)))
            if not pd.isna(row.total_departures_performed)
            else 0
        )
        lf = float(row.avg_t100_load_factor) if not pd.isna(row.avg_t100_load_factor) else 0.82
        spd = (
            float(row.avg_t100_seats_per_departure)
            if not pd.isna(row.avg_t100_seats_per_departure)
            else 120.0
        )
        mos = int(row.months_active) if not pd.isna(row.months_active) else 1
        routes[dest] = {
            "dst": dest,
            "cty": str(row.dest_city_name),
            "st": str(row.dest_state_or_country),
            "cc": str(row.dest_country_code),
            "intl": 1 if is_intl else 0,
            "sha": 1 if str(row.is_shared_regional_spoke).lower() in {"true", "1"} else 0,
            "deps": deps,
            "lf": round(lf, 4),
            "spd": round(spd, 1),
            "avg_daily": round(deps / max(1.0, mos * 30.4), 2),
            "hub_to_hub": 1 if dest in pred_cfg.hub_to_hub_destinations else 0,
        }

    years_used = sorted(int(y) for y in fdf["year"].dropna().unique()) if not fdf.empty else []
    meta = {
        "hub": hub,
        "trained_years": years_used,
        "source_flight_rows": int(len(fdf)),
        "routes_total": len(routes),
        "routes_dom": sum(1 for r in routes.values() if not r["intl"]),
        "routes_intl": sum(1 for r in routes.values() if r["intl"]),
        "shrinkage_pseudo_count": pred_cfg.shrinkage_pseudo_count,
        "simulation_draws": pred_cfg.simulation_draws,
        "simulation_seed": pred_cfg.simulation_seed,
        "hub_irrops_corr": pred_cfg.hub_irrops_corr,
        "spillover_seats_per_cancel": pred_cfg.spillover_seats_per_cancel,
        "spillover_decay": pred_cfg.spillover_decay,
        "hub_to_hub_destinations": list(pred_cfg.hub_to_hub_destinations),
        "hub_to_hub_queue_multiplier": pred_cfg.hub_to_hub_queue_multiplier,
        "pass_tier_queue_means": dict(pred_cfg.pass_tier_queue_means),
        "horizon_volatility_scale": dict(pred_cfg.horizon_volatility_scale),
    }

    return PredictorPriors(
        meta=meta,
        routes=routes,
        tier1=tier1,
        tier2=tier2,
        tier3=tier3,
        tier4=tier4,
        holiday_deltas=holiday_deltas,
        dow_multipliers=dow_multipliers,
        route_carrier_factors=route_carrier_factors,
    )


def save_priors_json(priors: PredictorPriors, path: Path) -> Path:
    """Write ``PredictorPriors`` as minified JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(priors.to_dict(), separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_priors_json(path: Path) -> PredictorPriors:
    """Load ``PredictorPriors`` from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return PredictorPriors.from_dict(data)


def load_default_priors(
    repo_root: Path | None = None,
    config: StandbyConfig | None = None,
    force_refit: bool = False,
) -> PredictorPriors:
    """Load cached ``dashboard/data/predictor_priors.json`` or fit from committed ``exports/looker/``."""
    root = repo_root or (
        config.repo_root if config is not None else Path(__file__).resolve().parents[3]
    )
    cached = root / "dashboard" / "data" / "predictor_priors.json"
    if cached.is_file() and not force_refit:
        return load_priors_json(cached)

    looker_dir = root / "exports" / "looker"
    flights_df = pd.read_csv(looker_dir / "looker_flights.csv", low_memory=False)
    route_month_df = pd.read_csv(looker_dir / "looker_route_month.csv", low_memory=False)
    route_summary_df = pd.read_csv(looker_dir / "looker_route_summary.csv", low_memory=False)
    return fit_predictor_priors(
        flights_df=flights_df,
        route_month_df=route_month_df,
        route_summary_df=route_summary_df,
        config=config,
    )
