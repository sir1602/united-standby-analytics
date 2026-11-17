"""Tests for Section 6 SOI Validation Suite (`src/standby/validation/soi_validation.py`)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from standby.config import load_config
from standby.enrichment import enrich_flights
from standby.enrichment.load_factor import LF_SOURCE_L1
from standby.scoring import score_flights
from standby.scoring.holidays import is_holiday_window, thanksgiving_date
from standby.validation.sanity import check_soi_bounds
from standby.validation.soi_validation import run_soi_validation


def _build_synthetic_multiyear_dataset() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Build a deterministic 5-year synthetic dataset (>600 flights) with realistic seasonal peak/trough
    separation (`late_jan_midweek` > `baseline` > `spring_break` > `christmas_peak` > `thanksgiving_sunday`).
    """
    years = [2019, 2023, 2024, 2025, 2026]
    tsa_rows: list[dict] = []
    flight_rows: list[dict] = []
    t100_rows: list[dict] = []

    for y in years:
        tg_sun = thanksgiving_date(y) + timedelta(days=3)
        # Candidate dates across calendar windows (2026 stops at August)
        sample_dates = [
            date(y, 1, 15),
            date(y, 1, 16),
            date(y, 1, 21),
            date(y, 1, 22),
            date(y, 3, 18),
            date(y, 3, 22),
            date(y, 5, 8),
            date(y, 6, 12),
            date(y, 8, 14),
        ]
        if y < 2026:
            sample_dates.extend(
                [
                    date(y, 10, 10),
                    date(y, 12, 22),
                    date(y, 12, 26),
                    tg_sun,
                ]
            )

        for d in sorted(set(sample_dates)):
            if d.month == 1 and 15 <= d.day <= 31:
                tsa_vol = 1_550_000  # Deep Jan trough
            elif d == tg_sun:
                tsa_vol = 2_650_000  # Thanksgiving Sunday surge
            elif d.month == 12 and 20 <= d.day <= 30:
                tsa_vol = 2_500_000  # Christmas surge
            elif d.month == 3 and 15 <= d.day <= 31:
                tsa_vol = 2_300_000  # Spring Break
            else:
                tsa_vol = 2_050_000

            tsa_rows.append({"date_day": d.isoformat(), "tsa_throughput": tsa_vol})

            # 12 flights per sample date across two routes and hour blocks
            for slot in range(12):
                dest = "DEN" if slot % 2 == 0 else "EWR"
                dep_h = 6 + (slot % 6) * 2
                hb = (
                    "early"
                    if dep_h < 8
                    else "morning"
                    if dep_h < 11
                    else "midday"
                    if dep_h < 14
                    else "afternoon"
                    if dep_h < 17
                    else "evening"
                )
                if d.month == 1 and 15 <= d.day <= 31:
                    base_lf = 0.64
                elif d == tg_sun:
                    base_lf = 0.96
                elif d.month == 12 and 20 <= d.day <= 30:
                    base_lf = 0.92
                elif d.month == 3 and 15 <= d.day <= 31:
                    base_lf = 0.88
                else:
                    base_lf = 0.82

                seats = 166
                # Force slight disruption on Thanksgiving Sunday & Christmas
                is_canc = (d == tg_sun and slot == 0) or (d.month == 12 and slot == 0)
                tsa_ratio_m = tsa_vol / 2_050_000.0
                m_tsa = float(np.clip(1.0 + 0.35 * (tsa_ratio_m - 1.0), 0.80, 1.20))
                lf_adj = float(np.clip(base_lf * m_tsa, 0.0, 1.0))
                pax_adj = int(np.clip(round(seats * lf_adj), 0, seats))
                open_adj = seats - pax_adj

                flight_rows.append(
                    {
                        "flight_date": d.isoformat(),
                        "year": y,
                        "month": d.month,
                        "op_carrier": "UA",
                        "origin": "ORD",
                        "dest": dest,
                        "flight_num_mkt": str(400 + slot),
                        "crs_dep_time": f"{dep_h:02d}00",
                        "hour_block": hb,
                        "is_cancelled": is_canc,
                        "is_diverted": False,
                        "t100_available": True,
                        "lf_source": LF_SOURCE_L1,
                        "seats_config": seats,
                        "bts_aircraft_type": "614",
                        "est_load_factor": base_lf,
                        "est_open_seats_adjusted": open_adj,
                    }
                )

        # Monthly T-100 rows matching seasonal LFs
        for m in (1, 3, 5, 6, 8, 10, 11, 12):
            if y == 2026 and m > 8:
                continue
            lf_m = (
                0.64
                if m == 1
                else 0.88
                if m == 3
                else 0.92
                if m == 12
                else 0.94
                if m == 11
                else 0.82
            )
            for dest in ("DEN", "EWR"):
                t100_rows.append(
                    {
                        "year": y,
                        "month": m,
                        "op_carrier": "UA",
                        "origin": "ORD",
                        "dest": dest,
                        "bts_aircraft_type": "614",
                        "service_class": "F",
                        "data_source": "DU",
                        "departures_performed": 100.0,
                        "seats": 16600.0,
                        "passengers": 16600.0 * lf_m,
                    }
                )

    return pd.DataFrame(flight_rows), pd.DataFrame(t100_rows), pd.DataFrame(tsa_rows)


def test_soi_validation_suite_passes_all_pillars_on_synthetic_multiyear(tmp_path: Path) -> None:
    """Section 6 validation suite passes Pillar 1, Pillar 2 (incl. Dec 1 TG Sunday), and Pillar 3."""
    flights_df, t100_df, tsa_df = _build_synthetic_multiyear_dataset()
    scored_df = score_flights(flights_df, t100_df=t100_df, tsa_df=tsa_df)
    summary_path = tmp_path / "phase4_validation.json"

    report = run_soi_validation(
        flights_df=scored_df,
        t100_df=t100_df,
        tsa_df=tsa_df,
        sample_mode=False,
        summary_path=summary_path,
        raise_on_failure=True,
    )
    assert report.passed is True
    assert summary_path.is_file()
    assert report.coverage_and_invariants["min_year"] == 2019
    assert report.coverage_and_invariants["min_year_soi_planning_non_null"] == 0

    holdout = report.pillar_1_time_based_backtest["fixed_holdout"]
    assert holdout["spearman_rho"] >= 0.55
    assert holdout["top_decile_lift"] >= 3.0

    # Verify Thanksgiving Sunday captured 2019-12-01 and 2024-12-01 (non-empty)
    assert report.pillar_2_face_validity["soi_historical"]["thanksgiving_sunday"]["n"] > 0
    assert report.pillar_2_face_validity["soi_planning"]["thanksgiving_sunday"]["n"] > 0
    assert report.pillar_3_weight_sensitivity["ranking_stability_passed"] is True
    assert not is_holiday_window("2024-05-08")


def test_soi_validation_sample_mode_skips_full_gates_cleanly(
    sample_ingested: dict[str, pd.DataFrame],
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """In `--sample` mode (<500 rows), validation enforces bounds/SC-5/2019-NULL and skips full-set gates."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    enriched = enrich_flights(
        otp_df=sample_ingested["otp_marketing_flights"],
        t100_df=sample_ingested["t100_segments"],
        faa_registry_df=sample_ingested["faa_aircraft_registry"],
        faa_acftref_df=sample_ingested["faa_acftref"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        reference_dir=cfg.reference_dir,
        config=cfg,
    )
    scored = score_flights(
        enriched_df=enriched.enriched_flights,
        t100_df=sample_ingested["t100_segments"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        config=cfg,
    )
    report = run_soi_validation(
        flights_df=scored,
        t100_df=sample_ingested["t100_segments"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        config=cfg,
        sample_mode=True,
        summary_path=tmp_path / "sample_val.json",
        raise_on_failure=True,
    )
    assert report.passed is True
    assert report.metadata["sample_mode"] is True
    assert report.pillar_1_time_based_backtest["status"] == "SKIPPED_SAMPLE"
    assert report.pillar_2_face_validity["status"] == "SKIPPED_SAMPLE"


def test_check_soi_bounds_catches_out_of_range_scores() -> None:
    """`check_soi_bounds` fails when any composite or sub-score is outside `[0.0, 100.0]`."""
    df = pd.DataFrame(
        {
            "soi_historical": [50.0, 105.0],
            "soi_planning": [45.0, -1.0],
            "s_open_hist": [50.0, 50.0],
        }
    )
    res = check_soi_bounds(df)
    assert res.status == "FAIL"
    assert res.details["violations_by_column"]["soi_historical"] == 1
    assert res.details["violations_by_column"]["soi_planning"] == 1
