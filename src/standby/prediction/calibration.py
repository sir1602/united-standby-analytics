"""Expanding walk-forward calibration for empirical open-seat availability probabilities."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from standby.config import StandbyConfig
from standby.prediction.priors import HOUR_BLOCK_TO_IDX, PredictorPriors, fit_predictor_priors
from standby.scoring.holidays import holiday_name


@dataclass(frozen=True)
class ReliabilityBin:
    """Single probability bin in a 10-bin calibration reliability diagram."""

    bin_index: int
    bin_range: str
    n: int
    mean_pred: float
    empirical_rate: float


@dataclass(frozen=True)
class ThresholdCalibration:
    """Walk-forward calibration metrics for event ``est_open_seats_adjusted >= k``."""

    threshold_seats: int
    n_eval: int
    base_rate: float
    brier_score: float
    climatology_brier: float
    brier_skill_score: float
    ece: float
    roc_auc: float
    reliability_bins: list[ReliabilityBin]


@dataclass(frozen=True)
class CalibrationReport:
    """Expanding walk-forward calibration summary across 2023–2026."""

    eval_years: list[int]
    n_eval_flights: int
    thresholds: dict[str, ThresholdCalibration]
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lognormal_sf(x: float, mu: float, sigma: float) -> float:
    """Survival function P(X >= x) for shifted Log-Normal X = exp(Z) - 1 matching (mu, sigma)."""
    if x <= 0.0:
        return 1.0
    m1 = max(0.5, mu + 1.0)
    v = max(0.25, sigma * sigma)
    s2 = math.log(1.0 + v / (m1 * m1))
    s = math.sqrt(s2)
    m_log = math.log(m1) - 0.5 * s2
    z = (math.log(x + 1.0) - m_log) / max(1e-6, s)
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _roc_auc_rank(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Exact ROC-AUC via average-rank Mann-Whitney U statistic."""
    y = y_true.astype(np.int64)
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(y_score, kind="mergesort")
    sorted_scores = y_score[order]
    ranks = np.empty(len(y_score), dtype=np.float64)
    i = 0
    n = len(sorted_scores)
    while i < n:
        j = i
        while j + 1 < n and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        avg_rank = 0.5 * (i + j) + 1.0
        ranks[order[i : j + 1]] = avg_rank
        i = j + 1
    sum_pos_ranks = float(ranks[y == 1].sum())
    return (sum_pos_ranks - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _predict_flight_mu_sigma(
    dest: str,
    month: int,
    dow: int,
    hb_idx: int,
    d_obj: date,
    priors: PredictorPriors,
) -> tuple[float, float, float]:
    """Fast analytical lookup of (mean_open, std_open, cancel_rate) from fitted priors."""
    shrink_m = float(priors.meta.get("shrinkage_pseudo_count", 10.0))
    t4 = priors.tier4.get(f"ALL|{month}|{hb_idx}") or [
        100,
        16.0,
        12.0,
        120.0,
        0.82,
        0.02,
        1.0 / 6.0,
    ]
    t3 = priors.tier3.get(f"{dest}|{month}")
    t2 = priors.tier2.get(f"{dest}|{month}|{hb_idx}")
    t1 = priors.tier1.get(f"{dest}|{month}|{dow}|{hb_idx}")

    dow_mult = float(priors.dow_multipliers.get(str(dow), 1.0))

    if t3 is not None:
        parent_open, parent_std, parent_canc = float(t3[1]), float(t3[2]), float(t3[5])
    else:
        parent_open, parent_std, parent_canc = float(t4[1]), float(t4[2]), float(t4[5])

    if t2 is not None:
        n2 = float(t2[0])
        w2 = n2 / (n2 + shrink_m)
        mu = w2 * float(t2[1]) + (1.0 - w2) * parent_open
        sigma = w2 * float(t2[2]) + (1.0 - w2) * parent_std
        canc = w2 * float(t2[5]) + (1.0 - w2) * parent_canc
    else:
        mu, sigma, canc = parent_open, parent_std, parent_canc

    if t1 is not None:
        n1 = float(t1[0])
        w1 = n1 / (n1 + shrink_m)
        mu = w1 * float(t1[1]) + (1.0 - w1) * (mu * dow_mult)
        sigma = w1 * float(t1[2]) + (1.0 - w1) * sigma
        canc = w1 * float(t1[5]) + (1.0 - w1) * canc
    else:
        mu *= dow_mult

    hname = holiday_name(d_obj)
    if hname and hname in priors.holiday_deltas:
        mu *= float(priors.holiday_deltas[hname]["open_mult"])
        canc = min(0.35, canc * float(priors.holiday_deltas[hname]["cancel_mult"]))

    return max(0.0, mu), max(2.0, sigma), min(0.35, max(0.002, canc))


def evaluate_walk_forward_calibration(
    flights_df: pd.DataFrame,
    route_month_df: pd.DataFrame,
    route_summary_df: pd.DataFrame,
    config: StandbyConfig | None = None,
    thresholds: tuple[int, ...] = (1, 2, 5, 10),
    eval_years: tuple[int, ...] = (2023, 2024, 2025, 2026),
) -> CalibrationReport:
    """Evaluate strictly leakage-free (``train.year < eval.year``) probability calibration.

    Measures how well the hierarchical prior model predicts flight-level open-seat availability
    ``P(not cancelled and est_open_seats_adjusted >= k)`` across out-of-sample years.
    """
    fdf = flights_df.copy()
    fdf["year_int"] = pd.to_numeric(fdf["year"], errors="coerce").astype("Int64")
    fdf["month_int"] = pd.to_numeric(fdf["month"], errors="coerce").astype("Int64")
    fdf["dow_int"] = pd.to_numeric(fdf["day_of_week"], errors="coerce").astype("Int64")
    fdf["hb_idx"] = fdf["hour_block"].astype(str).str.strip().str.lower().map(HOUR_BLOCK_TO_IDX)
    fdf["dest_u"] = fdf["dest"].astype(str).str.strip().str.upper()
    fdf["is_canc_b"] = fdf["is_cancelled"].astype(str).str.lower().isin({"true", "1"})
    fdf["open_adj"] = pd.to_numeric(fdf["est_open_seats_adjusted"], errors="coerce")

    valid_mask = (
        fdf["year_int"].notna()
        & fdf["month_int"].notna()
        & fdf["dow_int"].notna()
        & fdf["hb_idx"].notna()
        & (fdf["is_canc_b"] | fdf["open_adj"].notna())
    )
    fdf = fdf.loc[valid_mask].copy()

    preds_by_k: dict[int, list[float]] = {k: [] for k in thresholds}
    obs_by_k: dict[int, list[int]] = {k: [] for k in thresholds}
    evaluated_years: list[int] = []

    available_years = sorted(int(y) for y in fdf["year_int"].unique())
    for ey in eval_years:
        if ey not in available_years:
            continue
        if not any(y < ey for y in available_years):
            continue

        priors = fit_predictor_priors(
            flights_df=flights_df,
            route_month_df=route_month_df,
            route_summary_df=route_summary_df,
            config=config,
            max_year=ey - 1,
        )
        eval_df = fdf.loc[fdf["year_int"] == ey]
        if eval_df.empty:
            continue
        evaluated_years.append(ey)

        cache: dict[tuple[str, str, int, int, int], tuple[float, float, float]] = {}
        for row in eval_df.itertuples(index=False):
            d_str = str(row.flight_date)[:10]
            ckey = (str(row.dest_u), d_str, int(row.month_int), int(row.dow_int), int(row.hb_idx))
            if ckey not in cache:
                cache[ckey] = _predict_flight_mu_sigma(
                    dest=ckey[0],
                    month=ckey[2],
                    dow=ckey[3],
                    hb_idx=ckey[4],
                    d_obj=date.fromisoformat(d_str),
                    priors=priors,
                )
            mu, sigma, canc = cache[ckey]
            is_canc = bool(row.is_canc_b)
            obs_open = 0.0 if is_canc else float(row.open_adj)

            for k in thresholds:
                p_avail = (1.0 - canc) * _lognormal_sf(float(k) - 0.5, mu, sigma)
                p_avail = min(0.995, max(0.005, p_avail))
                preds_by_k[k].append(p_avail)
                obs_by_k[k].append(1 if (not is_canc and obs_open >= k) else 0)

    results: dict[str, ThresholdCalibration] = {}
    n_eval_total = len(obs_by_k[thresholds[0]]) if thresholds else 0

    for k in thresholds:
        p_arr = np.asarray(preds_by_k[k], dtype=np.float64)
        y_arr = np.asarray(obs_by_k[k], dtype=np.float64)
        n = int(len(y_arr))
        if n == 0:
            continue
        base_rate = float(y_arr.mean())
        brier = float(np.mean((p_arr - y_arr) ** 2))
        clim_brier = float(base_rate * (1.0 - base_rate))
        bss = 1.0 - (brier / clim_brier) if clim_brier > 0 else 0.0
        auc = _roc_auc_rank(y_arr, p_arr)

        bins: list[ReliabilityBin] = []
        ece = 0.0
        for b in range(10):
            lo = b / 10.0
            hi = (b + 1) / 10.0
            mask = (p_arr >= lo) & (p_arr <= hi if b == 9 else p_arr < hi)
            cnt = int(mask.sum())
            if cnt == 0:
                continue
            mp = float(p_arr[mask].mean())
            er = float(y_arr[mask].mean())
            ece += (cnt / n) * abs(mp - er)
            bins.append(
                ReliabilityBin(
                    bin_index=b,
                    bin_range=f"{int(lo * 100)}–{int(hi * 100)}%",
                    n=cnt,
                    mean_pred=round(mp, 4),
                    empirical_rate=round(er, 4),
                )
            )

        results[f"ge_{k}"] = ThresholdCalibration(
            threshold_seats=k,
            n_eval=n,
            base_rate=round(base_rate, 4),
            brier_score=round(brier, 4),
            climatology_brier=round(clim_brier, 4),
            brier_skill_score=round(bss, 4),
            ece=round(ece, 4),
            roc_auc=round(auc, 4),
            reliability_bins=bins,
        )

    return CalibrationReport(
        eval_years=evaluated_years,
        n_eval_flights=n_eval_total,
        thresholds=results,
        note=(
            "Strictly out-of-sample expanding walk-forward calibration (train.year < eval.year) "
            "evaluating flight-level open-seat availability P(not cancelled & est_open_seats_adjusted >= k). "
            "Competing pass-tier standby queues are layered separately and are not observed in public BTS data."
        ),
    )
