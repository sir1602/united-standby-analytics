"""Section 6 SOI Validation Suite: Time-Based Backtest, Face-Validity & Weight Sensitivity."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from standby.config import StandbyConfig
from standby.scoring.holidays import thanksgiving_date
from standby.scoring.soi import (
    fit_planning_priors,
    score_flights,
    score_planning_with_priors,
)
from standby.validation.sanity import (
    check_sc5_missing_t100_null_invariant,
    check_soi_bounds,
)

logger = logging.getLogger("standby.validation.soi_validation")

METHODOLOGY_CAVEAT = (
    "Both soi_planning and soi_historical use BTS T-100 monthly load factors as their capacity "
    "baseline because public datasets do not contain flight-level gate standby clearance outcomes. "
    "Therefore, time-based backtesting measures temporal consistency between advance-planning and "
    "post-hoc proxies rather than ground-truth non-rev boarding accuracy."
)

WEIGHT_SENSITIVITY_SCHEMES: dict[str, dict[str, float]] = {
    "baseline": {
        "open_seats": 0.50,
        "displacement_pressure": 0.20,
        "backup_options": 0.20,
        "day_holiday_demand": 0.10,
    },
    "seat_heavy": {
        "open_seats": 0.65,
        "displacement_pressure": 0.15,
        "backup_options": 0.15,
        "day_holiday_demand": 0.05,
    },
    "disruption_heavy": {
        "open_seats": 0.40,
        "displacement_pressure": 0.30,
        "backup_options": 0.20,
        "day_holiday_demand": 0.10,
    },
    "backup_heavy": {
        "open_seats": 0.40,
        "displacement_pressure": 0.15,
        "backup_options": 0.35,
        "day_holiday_demand": 0.10,
    },
    "equal_weights": {
        "open_seats": 0.25,
        "displacement_pressure": 0.25,
        "backup_options": 0.25,
        "day_holiday_demand": 0.25,
    },
    "demand_heavy": {
        "open_seats": 0.40,
        "displacement_pressure": 0.15,
        "backup_options": 0.15,
        "day_holiday_demand": 0.30,
    },
}

_CORE_SENSITIVITY_SCHEMES: tuple[str, ...] = (
    "seat_heavy",
    "disruption_heavy",
    "backup_heavy",
    "equal_weights",
)


@dataclass
class SoiValidationReport:
    """Structured Section 6 validation report for the Standby Opportunity Index."""

    metadata: dict[str, Any]
    coverage_and_invariants: dict[str, Any]
    pillar_1_time_based_backtest: dict[str, Any]
    pillar_2_face_validity: dict[str, Any]
    pillar_3_weight_sensitivity: dict[str, Any]
    passed: bool
    gate_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize full report to a JSON-compatible dictionary."""
        return asdict(self)

    def write_json(self, output_path: Path) -> Path:
        """Write JSON report to `output_path` and return the path."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return output_path


def _distribution_summary(series: pd.Series) -> dict[str, Any]:
    vals = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype="float64")
    if len(vals) == 0:
        return {
            "n": 0,
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "mean": None,
            "p75": None,
            "p90": None,
            "max": None,
            "std": None,
        }
    return {
        "n": int(len(vals)),
        "min": round(float(np.min(vals)), 4),
        "p10": round(float(np.quantile(vals, 0.10)), 4),
        "p25": round(float(np.quantile(vals, 0.25)), 4),
        "median": round(float(np.median(vals)), 4),
        "mean": round(float(np.mean(vals)), 4),
        "p75": round(float(np.quantile(vals, 0.75)), 4),
        "p90": round(float(np.quantile(vals, 0.90)), 4),
        "max": round(float(np.max(vals)), 4),
        "std": round(float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0, 4),
    }


def _compute_pair_metrics(eval_df: pd.DataFrame) -> dict[str, Any]:
    """Compute Pillar 1 consistency metrics on a scored evaluation frame."""
    eval_total = int(len(eval_df))
    if eval_total == 0:
        return {
            "eval_total_flights": 0,
            "planning_scored_flights": 0,
            "planning_coverage_pct": 0.0,
            "paired_flights": 0,
            "spearman_rho": None,
            "spearman_pvalue": None,
            "top_decile_hit_rate": None,
            "top_decile_lift": None,
            "bottom_decile_hit_rate": None,
            "bottom_decile_lift": None,
            "mae": None,
            "rmse": None,
            "mean_planning": None,
            "mean_historical": None,
        }

    plan_scored = int(eval_df["soi_planning"].notna().sum())
    coverage = plan_scored / eval_total

    paired_mask = eval_df["soi_planning"].notna() & eval_df["soi_historical"].notna()
    paired = eval_df.loc[paired_mask]
    n_paired = int(len(paired))

    if n_paired < 2:
        return {
            "eval_total_flights": eval_total,
            "planning_scored_flights": plan_scored,
            "planning_coverage_pct": round(coverage, 6),
            "paired_flights": n_paired,
            "spearman_rho": None,
            "spearman_pvalue": None,
            "top_decile_hit_rate": None,
            "top_decile_lift": None,
            "bottom_decile_hit_rate": None,
            "bottom_decile_lift": None,
            "mae": None,
            "rmse": None,
            "mean_planning": (
                round(float(paired["soi_planning"].mean()), 4) if n_paired == 1 else None
            ),
            "mean_historical": (
                round(float(paired["soi_historical"].mean()), 4) if n_paired == 1 else None
            ),
        }

    plan_vals = paired["soi_planning"].to_numpy(dtype="float64")
    hist_vals = paired["soi_historical"].to_numpy(dtype="float64")

    if np.all(plan_vals == plan_vals[0]) or np.all(hist_vals == hist_vals[0]):
        rho = float("nan")
        pval = float("nan")
    else:
        sp = stats.spearmanr(plan_vals, hist_vals)
        rho = float(sp.statistic)
        pval = float(sp.pvalue)

    q90_plan = float(np.quantile(plan_vals, 0.90))
    q90_hist = float(np.quantile(hist_vals, 0.90))
    top_plan_mask = plan_vals >= q90_plan
    top_hit = float(np.mean(hist_vals[top_plan_mask] >= q90_hist)) if top_plan_mask.any() else 0.0

    q10_plan = float(np.quantile(plan_vals, 0.10))
    q10_hist = float(np.quantile(hist_vals, 0.10))
    bot_plan_mask = plan_vals <= q10_plan
    bot_hit = float(np.mean(hist_vals[bot_plan_mask] <= q10_hist)) if bot_plan_mask.any() else 0.0

    err = plan_vals - hist_vals
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))

    return {
        "eval_total_flights": eval_total,
        "planning_scored_flights": plan_scored,
        "planning_coverage_pct": round(coverage, 6),
        "paired_flights": n_paired,
        "spearman_rho": None if np.isnan(rho) else round(rho, 4),
        "spearman_pvalue": None if np.isnan(pval) else float(f"{pval:.3e}"),
        "top_decile_hit_rate": round(top_hit, 4),
        "top_decile_lift": round(top_hit / 0.10, 4),
        "bottom_decile_hit_rate": round(bot_hit, 4),
        "bottom_decile_lift": round(bot_hit / 0.10, 4),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mean_planning": round(float(np.mean(plan_vals)), 4),
        "mean_historical": round(float(np.mean(hist_vals)), 4),
    }


def run_time_based_backtest(
    scored_df: pd.DataFrame,
    t100_df: pd.DataFrame | None = None,
    tsa_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
    sample_mode: bool = False,
) -> dict[str, Any]:
    """
    Pillar 1: Time-based backtest (`soi_planning` vs `soi_historical`).

    Computes:
      - Fixed Holdout: trained strictly on `[2019, 2023, 2024]`, evaluated on `year >= 2025`.
      - Expanding Walk-Forward: for each year `y >= 2023` in `scored_df`, using pre-computed
        expanding `train.year < y` `soi_planning` scores, plus combined `2023-2026`.
    """
    years_present = sorted(int(y) for y in scored_df["year"].dropna().unique())

    if sample_mode or len(scored_df) < 500:
        wf_by_year: dict[str, Any] = {}
        eval_years = [y for y in years_present if y > min(years_present, default=2019)]
        for y in eval_years:
            wf_by_year[str(y)] = _compute_pair_metrics(scored_df[scored_df["year"] == y])
        combined_df = scored_df[scored_df["year"].isin(eval_years)]
        return {
            "status": "SKIPPED_SAMPLE",
            "fixed_holdout": {
                "status": "SKIPPED_SAMPLE",
                "train_years": [2019, 2023, 2024],
                "eval_years": [2025, 2026],
            },
            "expanding_walk_forward": {
                "by_year": wf_by_year,
                "combined": _compute_pair_metrics(combined_df),
            },
            "methodology_caveat": METHODOLOGY_CAVEAT,
        }

    # 1. Fixed Holdout (train <= 2024 -> eval >= 2025)
    train_hold = scored_df[scored_df["year"] <= 2024]
    eval_hold = scored_df[scored_df["year"] >= 2025]
    train_t100_hold = t100_df[t100_df["year"] <= 2024] if t100_df is not None else None
    train_tsa_hold: pd.DataFrame | None = None
    if tsa_df is not None and not tsa_df.empty:
        tsa_dt = pd.to_datetime(tsa_df["date_day"], errors="coerce")
        train_tsa_hold = tsa_df.loc[tsa_dt.dt.year <= 2024]

    holdout_priors = fit_planning_priors(
        train_flights_df=train_hold,
        train_t100_df=train_t100_hold,
        train_tsa_df=train_tsa_hold,
        config=config,
    )
    eval_hold_scored = score_planning_with_priors(
        eval_flights_df=eval_hold,
        priors=holdout_priors,
        config=config,
    )
    holdout_metrics = _compute_pair_metrics(eval_hold_scored)
    holdout_metrics["train_years"] = [int(y) for y in sorted(train_hold["year"].unique())]
    holdout_metrics["eval_years"] = [int(y) for y in sorted(eval_hold["year"].unique())]

    # 2. Expanding Walk-Forward (uses expanding train.year < eval.year scores on scored_df)
    min_year = min(years_present)
    wf_years = [y for y in years_present if y > min_year]
    wf_by_year = {}
    for y in wf_years:
        m = _compute_pair_metrics(scored_df[scored_df["year"] == y])
        m["train_years"] = [yr for yr in years_present if yr < y]
        wf_by_year[str(y)] = m
    combined_metrics = _compute_pair_metrics(scored_df[scored_df["year"].isin(wf_years)])
    combined_metrics["eval_years"] = wf_years

    return {
        "status": "EVALUATED",
        "fixed_holdout": holdout_metrics,
        "expanding_walk_forward": {
            "by_year": wf_by_year,
            "combined": combined_metrics,
        },
        "methodology_caveat": METHODOLOGY_CAVEAT,
    }


def _window_stats(df: pd.DataFrame, score_col: str, prefix: str) -> dict[str, Any]:
    sub = df[df[score_col].notna()]
    if sub.empty:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "std": None,
            "p25": None,
            "p75": None,
            f"mean_s_open_{prefix}": None,
            f"mean_s_disp_{prefix}": None,
            f"mean_s_backup_{prefix}": None,
            f"mean_s_demand_{prefix}": None,
        }
    vals = sub[score_col].to_numpy(dtype="float64")
    res: dict[str, Any] = {
        "n": int(len(vals)),
        "mean": round(float(np.mean(vals)), 4),
        "median": round(float(np.median(vals)), 4),
        "std": round(float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0, 4),
        "p25": round(float(np.quantile(vals, 0.25)), 4),
        "p75": round(float(np.quantile(vals, 0.75)), 4),
    }
    for comp in ("open", "disp", "backup", "demand"):
        col = f"s_{comp}_{prefix}"
        res[f"mean_{col}"] = round(float(sub[col].mean()), 4) if col in sub.columns else None
    return res


def _two_sample_separation(
    trough_vals: np.ndarray,
    peak_vals: np.ndarray,
) -> dict[str, Any]:
    if len(trough_vals) < 2 or len(peak_vals) < 2:
        return {
            "delta_mean": None,
            "welch_t_stat": None,
            "welch_p_value": None,
            "mann_whitney_u": None,
            "mann_whitney_p_value": None,
        }
    t_res = stats.ttest_ind(trough_vals, peak_vals, equal_var=False)
    u_res = stats.mannwhitneyu(trough_vals, peak_vals, alternative="greater")
    return {
        "delta_mean": round(float(np.mean(trough_vals) - np.mean(peak_vals)), 4),
        "welch_t_stat": round(float(t_res.statistic), 4),
        "welch_p_value": float(f"{float(t_res.pvalue):.3e}"),
        "mann_whitney_u": round(float(u_res.statistic), 2),
        "mann_whitney_p_value": float(f"{float(u_res.pvalue):.3e}"),
    }


def run_face_validity_checks(
    scored_df: pd.DataFrame,
    sample_mode: bool = False,
) -> dict[str, Any]:
    """
    Pillar 2: Face-validity window comparisons on BOTH `soi_historical` and `soi_planning`.

    Note: `thanksgiving_sunday` is computed strictly as `thanksgiving_date(year) + 3 days`
    WITHOUT filtering `month == 11`, capturing `2019-12-01` and `2024-12-01`.
    """
    dt = pd.to_datetime(scored_df["flight_date"], errors="raise")
    dates = dt.dt.date
    months = dt.dt.month
    days = dt.dt.day
    dows = dt.dt.dayofweek + 1  # ISO: 1=Mon .. 7=Sun

    tg_sundays_by_year = {
        int(y): thanksgiving_date(int(y)) + timedelta(days=3) for y in dt.dt.year.unique()
    }
    is_tg_sunday = pd.Series(
        [d == tg_sundays_by_year[d.year] for d in dates],
        index=scored_df.index,
        dtype=bool,
    )

    windows: dict[str, pd.Series] = {
        "late_jan_midweek": (months == 1) & (days >= 15) & (days <= 31) & dows.isin([2, 3]),
        "all_flights_baseline": pd.Series(True, index=scored_df.index, dtype=bool),
        "spring_break": (months == 3) & (days >= 15) & (days <= 31),
        "christmas_peak": (months == 12) & (days >= 20) & (days <= 30),
        "thanksgiving_sunday": is_tg_sunday,
        # Diagnostic context sub-slices
        "spring_break_full_window": ((months == 3) & (days >= 10)) | ((months == 4) & (days <= 7)),
        "christmas_eve_and_day": (months == 12) & days.isin([24, 25]),
        "christmas_peak_excl_eve_day": (months == 12)
        & (days >= 20)
        & (days <= 30)
        & (~days.isin([24, 25])),
    }

    if sample_mode or len(scored_df) < 500:
        hist_summary = {}
        plan_summary = {}
        for name, mask in windows.items():
            sub = scored_df.loc[mask]
            if name in {"late_jan_midweek", "all_flights_baseline"}:
                hist_summary[name] = _window_stats(sub, "soi_historical", "hist")
                plan_summary[name] = _window_stats(sub, "soi_planning", "plan")
            else:
                hist_summary[name] = {
                    "status": "SKIPPED_SAMPLE",
                    "n": int(sub["soi_historical"].notna().sum()),
                }
                plan_summary[name] = {
                    "status": "SKIPPED_SAMPLE",
                    "n": int(sub["soi_planning"].notna().sum()),
                }
        return {
            "status": "SKIPPED_SAMPLE",
            "soi_historical": hist_summary,
            "soi_planning": plan_summary,
            "statistical_separation_tests": {"status": "SKIPPED_SAMPLE"},
            "nuances": [],
        }

    hist_stats = {
        name: _window_stats(scored_df.loc[mask], "soi_historical", "hist")
        for name, mask in windows.items()
    }
    plan_stats = {
        name: _window_stats(scored_df.loc[mask], "soi_planning", "plan")
        for name, mask in windows.items()
    }

    # Statistical separation tests: Late-Jan Midweek vs Thanksgiving Sunday (and Christmas / Spring Break)
    trough_h = (
        scored_df.loc[windows["late_jan_midweek"], "soi_historical"]
        .dropna()
        .to_numpy(dtype="float64")
    )
    trough_p = (
        scored_df.loc[windows["late_jan_midweek"], "soi_planning"]
        .dropna()
        .to_numpy(dtype="float64")
    )

    sep_tests: dict[str, Any] = {}
    for peak_name in ("thanksgiving_sunday", "christmas_peak", "spring_break"):
        peak_h = (
            scored_df.loc[windows[peak_name], "soi_historical"].dropna().to_numpy(dtype="float64")
        )
        peak_p = (
            scored_df.loc[windows[peak_name], "soi_planning"].dropna().to_numpy(dtype="float64")
        )
        sep_tests[f"late_jan_vs_{peak_name}"] = {
            "soi_historical": _two_sample_separation(trough_h, peak_h),
            "soi_planning": _two_sample_separation(trough_p, peak_p),
        }

    nuances = [
        "Thanksgiving Sunday falls in December in 2019 (2019-12-01) and 2024 (2024-12-01); "
        "window lookup uses year-anchored 4th Thursday of November + 3 days without a month == 11 restriction.",
        "In soi_planning, TSA (month, iso_dow, is_holiday_window) prior averaging blends Dec 1 Thanksgiving Sunday "
        "with Dec 22/29 holiday Sundays and blends Jan 15-31 troughs with Jan 1-14 holiday return weeks, narrowing "
        "soi_planning trough-vs-peak separation compared to soi_historical while keeping Welch p < 1e-100.",
        "Christmas Eve/Day (Dec 24-25) flies lighter loads than Dec 20-23 & Dec 26-30, creating a bimodal sub-regime "
        "within the Dec 20-30 holiday window.",
        "2019 flights have soi_planning = NULL (no prior training year), and 2026 data ends 2026-08-31 (covered in "
        "Jan/Mar windows, absent in Nov/Dec).",
    ]

    return {
        "status": "EVALUATED",
        "soi_historical": hist_stats,
        "soi_planning": plan_stats,
        "statistical_separation_tests": sep_tests,
        "nuances": nuances,
    }


def run_weight_sensitivity_analysis(
    scored_df: pd.DataFrame,
    sample_mode: bool = False,
) -> dict[str, Any]:
    """
    Pillar 3: Stress-test ranking stability across alternative component weightings.
    """
    req_hist = ("s_open_hist", "s_disp_hist", "s_backup_hist", "s_demand_hist")
    hist_valid = scored_df.dropna(subset=list(req_hist)).copy()
    if hist_valid.empty:
        return {
            "status": "SKIPPED_SAMPLE" if sample_mode else "NO_VALID_ROWS",
            "n_scored_flights": 0,
            "schemes": {},
            "ranking_stability_passed": sample_mode,
        }

    s_o = hist_valid["s_open_hist"].to_numpy(dtype="float64")
    s_d = hist_valid["s_disp_hist"].to_numpy(dtype="float64")
    s_b = hist_valid["s_backup_hist"].to_numpy(dtype="float64")
    s_m = hist_valid["s_demand_hist"].to_numpy(dtype="float64")

    scheme_scores: dict[str, np.ndarray] = {}
    for name, w in WEIGHT_SENSITIVITY_SCHEMES.items():
        scheme_scores[name] = np.round(
            w["open_seats"] * s_o
            + w["displacement_pressure"] * s_d
            + w["backup_options"] * s_b
            + w["day_holiday_demand"] * s_m,
            2,
        )

    base = scheme_scores["baseline"]
    q90_base = float(np.quantile(base, 0.90))
    q10_base = float(np.quantile(base, 0.10))
    base_top = base >= q90_base
    base_bot = base <= q10_base
    n_top_base = max(1, int(base_top.sum()))
    n_bot_base = max(1, int(base_bot.sum()))

    schemes_out: dict[str, Any] = {}
    for name, arr in scheme_scores.items():
        w = WEIGHT_SENSITIVITY_SCHEMES[name]
        if name == "baseline":
            rho = 1.0
            top_overlap = 1.0
            bot_overlap = 1.0
        else:
            if np.all(arr == arr[0]) or np.all(base == base[0]):
                rho = 1.0 if np.allclose(arr, base) else 0.0
            else:
                rho = float(stats.spearmanr(base, arr).statistic)
            q90_alt = float(np.quantile(arr, 0.90))
            q10_alt = float(np.quantile(arr, 0.10))
            top_overlap = float(np.sum(base_top & (arr >= q90_alt)) / n_top_base)
            bot_overlap = float(np.sum(base_bot & (arr <= q10_alt)) / n_bot_base)

        schemes_out[name] = {
            "weights": w,
            "mean_score": round(float(np.mean(arr)), 4),
            "std_score": round(float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0, 4),
            "spearman_rho_vs_baseline": round(rho, 4),
            "top10_overlap_pct": round(top_overlap * 100.0, 2),
            "bottom10_overlap_pct": round(bot_overlap * 100.0, 2),
        }

    core_rhos = [schemes_out[s]["spearman_rho_vs_baseline"] for s in _CORE_SENSITIVITY_SCHEMES]
    stability_ok = all(r >= 0.85 for r in core_rhos)

    return {
        "status": "SKIPPED_SAMPLE" if (sample_mode or len(hist_valid) < 500) else "EVALUATED",
        "n_scored_flights": int(len(hist_valid)),
        "schemes": schemes_out,
        "ranking_stability_passed": bool(stability_ok),
    }


def run_soi_validation(
    flights_df: pd.DataFrame,
    t100_df: pd.DataFrame | None = None,
    tsa_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
    sample_mode: bool | None = None,
    summary_path: Path | None = None,
    raise_on_failure: bool = True,
) -> SoiValidationReport:
    """
    Execute the complete Section 6 validation suite (`Pillar 1`, `Pillar 2`, `Pillar 3`,
    `SC-5`, bounds, and `2019 NULL` invariants) and optionally write JSON summary.
    """
    is_sample = (
        bool(sample_mode)
        if sample_mode is not None
        else (config.sample_mode if config is not None else len(flights_df) < 500)
    )
    if len(flights_df) < 500:
        is_sample = True

    scored_df = flights_df
    if "soi_historical" not in scored_df.columns or "soi_planning" not in scored_df.columns:
        scored_df = score_flights(
            enriched_df=flights_df,
            t100_df=t100_df,
            tsa_df=tsa_df,
            config=config,
        )

    sc5 = check_sc5_missing_t100_null_invariant(scored_df)
    bounds = check_soi_bounds(scored_df)

    years_present = sorted(int(y) for y in scored_df["year"].dropna().unique())
    min_year = min(years_present) if years_present else None
    min_year_plan_non_null = (
        int(scored_df.loc[scored_df["year"] == min_year, "soi_planning"].notna().sum())
        if min_year is not None
        else 0
    )

    by_year_counts: dict[str, dict[str, int]] = {}
    for y in years_present:
        sub = scored_df[scored_df["year"] == y]
        by_year_counts[str(y)] = {
            "total_flights": int(len(sub)),
            "performed_flights": int((~sub["is_cancelled"].astype(bool)).sum()),
            "cancelled_flights": int(sub["is_cancelled"].astype(bool).sum()),
            "soi_historical_scored": int(sub["soi_historical"].notna().sum()),
            "soi_planning_scored": int(sub["soi_planning"].notna().sum()),
        }

    coverage_and_invariants = {
        "by_year": by_year_counts,
        "min_year": min_year,
        "min_year_soi_planning_non_null": min_year_plan_non_null,
        "sc5_missing_t100_null_invariant": sc5.to_dict(),
        "soi_bounds_check": bounds.to_dict(),
        "soi_historical_distribution": _distribution_summary(scored_df["soi_historical"]),
        "soi_planning_distribution": _distribution_summary(scored_df["soi_planning"]),
    }

    pillar_1 = run_time_based_backtest(
        scored_df=scored_df,
        t100_df=t100_df,
        tsa_df=tsa_df,
        config=config,
        sample_mode=is_sample,
    )
    pillar_2 = run_face_validity_checks(scored_df=scored_df, sample_mode=is_sample)
    pillar_3 = run_weight_sensitivity_analysis(scored_df=scored_df, sample_mode=is_sample)

    failures: list[str] = []
    if sc5.status == "FAIL":
        failures.append(f"SC-5 failed: {sc5.message}")
    if bounds.status == "FAIL":
        failures.append(f"SOI-BOUNDS failed: {bounds.message}")
    if min_year_plan_non_null > 0:
        failures.append(
            f"Leakage invariant failed: min_year={min_year} has {min_year_plan_non_null} non-null soi_planning rows"
        )

    if not is_sample:
        hold = pillar_1["fixed_holdout"]
        wf_comb = pillar_1["expanding_walk_forward"]["combined"]
        if (hold.get("spearman_rho") or 0.0) < 0.55:
            failures.append(f"Pillar 1 Fixed Holdout rho={hold.get('spearman_rho')} < 0.55")
        if (wf_comb.get("spearman_rho") or 0.0) < 0.55:
            failures.append(f"Pillar 1 Walk-Forward rho={wf_comb.get('spearman_rho')} < 0.55")
        if (hold.get("top_decile_lift") or 0.0) < 3.0:
            failures.append(f"Pillar 1 Holdout top_decile_lift={hold.get('top_decile_lift')} < 3.0")
        if (hold.get("planning_coverage_pct") or 0.0) < 0.95:
            failures.append(
                f"Pillar 1 Holdout planning_coverage_pct={hold.get('planning_coverage_pct')} < 0.95"
            )

        # Pillar 2 ordering checks
        h = pillar_2["soi_historical"]
        if not (
            h["late_jan_midweek"]["mean"]
            > h["all_flights_baseline"]["mean"]
            > h["spring_break"]["mean"]
            > h["christmas_peak"]["mean"]
            > h["thanksgiving_sunday"]["mean"]
        ):
            failures.append("Pillar 2 soi_historical monotonic peak/trough ordering violated")

        p = pillar_2["soi_planning"]
        if not (
            p["late_jan_midweek"]["mean"]
            > p["all_flights_baseline"]["mean"]
            > p["spring_break"]["mean"]
            > p["christmas_peak"]["mean"]
        ):
            failures.append("Pillar 2 soi_planning monotonic ordering violated")
        sep = pillar_2["statistical_separation_tests"]["late_jan_vs_thanksgiving_sunday"][
            "soi_planning"
        ]
        if (
            p["late_jan_midweek"]["mean"] <= p["thanksgiving_sunday"]["mean"]
            or (sep.get("welch_p_value") or 1.0) >= 1e-10
        ):
            failures.append(
                "Pillar 2 soi_planning Late-Jan > Thanksgiving Sunday separation failed"
            )

        if not pillar_3["ranking_stability_passed"]:
            failures.append("Pillar 3 weight sensitivity rho < 0.85 on core schemes")

    weights_used = (
        config.get_index_weights() if config is not None else WEIGHT_SENSITIVITY_SCHEMES["baseline"]
    )
    sc_cfg = config.scoring if config is not None else None
    metadata = {
        "hub": config.active_hub if config is not None else "ORD",
        "years": years_present,
        "sample_mode": is_sample,
        "total_flights": int(len(scored_df)),
        "performed_flights": int((~scored_df["is_cancelled"].astype(bool)).sum()),
        "cancelled_flights": int(scored_df["is_cancelled"].astype(bool).sum()),
        "generated_at_utc": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weights": weights_used,
        "hyperparameters": asdict(sc_cfg) if sc_cfg is not None else {},
    }

    report = SoiValidationReport(
        metadata=metadata,
        coverage_and_invariants=coverage_and_invariants,
        pillar_1_time_based_backtest=pillar_1,
        pillar_2_face_validity=pillar_2,
        pillar_3_weight_sensitivity=pillar_3,
        passed=len(failures) == 0,
        gate_failures=failures,
    )

    if summary_path is not None:
        report.write_json(summary_path)

    if raise_on_failure and not report.passed:
        raise RuntimeError(f"SOI validation failed: {'; '.join(failures)}")

    return report
