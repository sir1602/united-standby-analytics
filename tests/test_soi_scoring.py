"""Unit and leakage-prevention tests for Phase 4 Standby Opportunity Index (`src/standby/scoring/`)."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from standby.config import load_config
from standby.enrichment import enrich_flights
from standby.enrichment.confidence import COLUMN_CONFIDENCE_TIERS
from standby.enrichment.load_factor import LF_SOURCE_L1, LF_SOURCE_UNMATCHED
from standby.scoring import (
    compute_route_day_operational_signals,
    compute_soi_historical,
    compute_soi_planning,
    holiday_name,
    holiday_name_series,
    is_holiday_window,
    score_flights,
    thanksgiving_date,
)
from standby.validation.sanity import check_sc5_missing_t100_null_invariant, check_soi_bounds


def test_holiday_calendar_exact_windows_and_december_thanksgiving() -> None:
    """Holiday calendar matches all 6 deterministic windows including Dec 1 Thanksgiving Sundays."""
    assert holiday_name(date(2024, 12, 20)) == "Christmas / New Year"
    assert holiday_name(date(2025, 1, 3)) == "Christmas / New Year"
    assert holiday_name(date(2025, 1, 4)) is None

    # Thanksgiving 2019 = Nov 28 -> Sunday Dec 1; 2024 = Nov 28 -> Sunday Dec 1
    assert thanksgiving_date(2019) == date(2019, 11, 28)
    assert thanksgiving_date(2024) == date(2024, 11, 28)
    assert holiday_name("2019-12-01") == "Thanksgiving"
    assert holiday_name("2024-12-01") == "Thanksgiving"
    assert holiday_name("2024-11-27") == "Thanksgiving"
    assert holiday_name("2024-12-02") == "Thanksgiving"
    assert holiday_name("2024-12-03") is None

    assert holiday_name("2024-05-24") == "Memorial Day"
    assert holiday_name("2024-05-28") == "Memorial Day"
    assert holiday_name("2024-07-04") == "Independence Day"
    assert holiday_name("2024-08-30") == "Labor Day"
    assert holiday_name("2024-09-03") == "Labor Day"
    assert holiday_name("2024-03-10") == "Spring Break"
    assert holiday_name("2024-04-07") == "Spring Break"
    assert holiday_name("2024-01-17") is None
    assert is_holiday_window("2024-01-17") is False

    ser = holiday_name_series(pd.Series(["2024-12-25", "2024-01-17", None]))
    assert ser.tolist() == ["Christmas / New Year", None, None]


def test_route_day_operational_signals_tie_breaker_decay_and_strict_backups() -> None:
    """Route-day signals use deterministic tie-breaker, 0.75 decay, and strict backup inequality."""
    df = pd.DataFrame(
        [
            {
                "flight_id": "f1",
                "flight_date": "2024-05-01",
                "origin": "ORD",
                "dest": "DEN",
                "op_carrier": "UA",
                "flight_num_mkt": "101",
                "crs_dep_time": "0800",
                "is_cancelled": True,
                "is_diverted": False,
            },
            {
                "flight_id": "f2",
                "flight_date": "2024-05-01",
                "origin": "ORD",
                "dest": "DEN",
                "op_carrier": "UA",
                "flight_num_mkt": "102",
                "crs_dep_time": "0800",
                "is_cancelled": False,
                "is_diverted": True,
            },
            {
                "flight_id": "f3",
                "flight_date": "2024-05-01",
                "origin": "ORD",
                "dest": "DEN",
                "op_carrier": "UA",
                "flight_num_mkt": "103",
                "crs_dep_time": "1000",
                "is_cancelled": False,
                "is_diverted": False,
            },
            {
                "flight_id": "f4",
                "flight_date": "2024-05-01",
                "origin": "ORD",
                "dest": "DEN",
                "op_carrier": "UA",
                "flight_num_mkt": "104",
                "crs_dep_time": "1200",
                "is_cancelled": True,
                "is_diverted": False,
            },
        ]
    )
    # Shuffle input order to verify index/order preservation
    shuffled = df.iloc[[3, 1, 0, 2]].copy()
    sig = compute_route_day_operational_signals(shuffled, decay_base=0.75, diversion_weight=0.50)
    sig_by_id = sig.join(shuffled["flight_id"]).set_index("flight_id")

    # f1 (0800 cancelled) sorts before f2 (0800 diverted, performed)
    assert sig_by_id.loc["f1", "prior_strict_scheduled_count"] == 0
    assert sig_by_id.loc["f2", "prior_strict_scheduled_count"] == 0  # strict < 0800 -> 0!
    assert sig_by_id.loc["f3", "prior_strict_scheduled_count"] == 2
    assert sig_by_id.loc["f4", "prior_strict_scheduled_count"] == 3

    # Weighted displacement: f1=0.0, f2=1.0*0.75^0=1.0, f3=1.0*0.75^1 + 0.5*0.75^0 = 1.25
    assert sig_by_id.loc["f1", "weighted_displacement_hist"] == pytest.approx(0.0)
    assert sig_by_id.loc["f2", "weighted_displacement_hist"] == pytest.approx(1.0)
    assert sig_by_id.loc["f3", "weighted_displacement_hist"] == pytest.approx(1.25)
    assert sig_by_id.loc["f4", "weighted_displacement_hist"] == pytest.approx(1.25 * 0.75)

    # Strict backups (> crs_dep_time): f1/f2 both see f3 & f4 (2 sched, 1 perf)
    assert sig_by_id.loc["f1", "remaining_scheduled_backups"] == 2
    assert sig_by_id.loc["f1", "remaining_performed_backups"] == 1
    assert sig_by_id.loc["f2", "remaining_scheduled_backups"] == 2
    assert sig_by_id.loc["f2", "remaining_performed_backups"] == 1
    assert sig_by_id.loc["f3", "remaining_scheduled_backups"] == 1
    assert sig_by_id.loc["f3", "remaining_performed_backups"] == 0


def test_soi_historical_hand_calculation_and_sc5_nulls() -> None:
    """Verify exact sub-score math for `soi_historical` and SC-5 NULL behavior on cancelled/unmatched."""
    flights = pd.DataFrame(
        [
            {
                "flight_id": "ok",
                "flight_date": "2024-01-17",  # Non-holiday
                "year": 2024,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "flight_num_mkt": "100",
                "crs_dep_time": "0900",
                "hour_block": "morning",
                "is_cancelled": False,
                "is_diverted": False,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "seats_config": 100,
                "est_open_seats_adjusted": 15,  # 15/25=0.60, (15/100)/0.30=0.50 -> s_open=55.0
            },
            {
                "flight_id": "later_perf",
                "flight_date": "2024-01-17",
                "year": 2024,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "flight_num_mkt": "101",
                "crs_dep_time": "1400",
                "hour_block": "afternoon",
                "is_cancelled": False,
                "is_diverted": False,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "seats_config": 100,
                "est_open_seats_adjusted": 30,
            },
            {
                "flight_id": "cancelled",
                "flight_date": "2024-01-17",
                "year": 2024,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "flight_num_mkt": "102",
                "crs_dep_time": "1800",
                "hour_block": "evening",
                "is_cancelled": True,
                "is_diverted": False,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "seats_config": 100,
                "est_open_seats_adjusted": 15,
            },
            {
                "flight_id": "unmatched",
                "flight_date": "2024-01-17",
                "year": 2024,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "flight_num_mkt": "103",
                "crs_dep_time": "2000",
                "hour_block": "late",
                "is_cancelled": False,
                "is_diverted": False,
                "t100_available": False,
                "lf_source": LF_SOURCE_UNMATCHED,
                "seats_config": 100,
                "est_open_seats_adjusted": pd.NA,
            },
        ]
    )
    # Two TSA days in 2024 with mean = 2,000,000 -> Jan 17 ratio = 1.00
    tsa = pd.DataFrame(
        [
            {"date_day": "2024-01-17", "tsa_throughput": 2_000_000},
            {"date_day": "2024-01-18", "tsa_throughput": 2_000_000},
        ]
    )
    res = compute_soi_historical(flights, tsa)
    row0 = res.iloc[0]
    assert row0["s_open_hist"] == pytest.approx(55.0)
    assert row0["s_disp_hist"] == pytest.approx(100.0)
    expected_backup = 100.0 * math.log1p(2) / math.log1p(6)
    assert row0["s_backup_hist"] == pytest.approx(expected_backup)
    expected_demand = 100.0 * (1.35 - 1.00) / (1.35 - 0.65)  # 50.0
    assert row0["s_demand_hist"] == pytest.approx(expected_demand)
    expected_soi = round(
        0.50 * 55.0 + 0.20 * 100.0 + 0.20 * expected_backup + 0.10 * expected_demand, 2
    )
    assert row0["soi_historical"] == pytest.approx(expected_soi)

    # Cancelled & unmatched rows must be strictly NaN for soi_historical & all s_*_hist
    for idx in (2, 3):
        for col in (
            "s_open_hist",
            "s_disp_hist",
            "s_backup_hist",
            "s_demand_hist",
            "soi_historical",
        ):
            assert pd.isna(res.loc[idx, col])


def test_soi_planning_strict_zero_future_leakage_adversarial() -> None:
    """
    Adversarial leakage test: mutating evaluation year's realized actuals (`is_cancelled`,
    `is_diverted`, `seats_config`, `bts_aircraft_type`, `est_*`, `tsa_throughput`, T-100 LF)
    MUST NOT change `soi_planning` or any `s_*_plan` for evaluation flights.
    """
    base_rows = []
    # Training year 2023: 10 ORD->DEN flights across Jan
    for i in range(10):
        base_rows.append(
            {
                "flight_id": f"2023_{i}",
                "flight_date": f"2023-01-{10 + i:02d}",
                "year": 2023,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "flight_num_mkt": str(200 + i),
                "crs_dep_time": "0900" if i % 2 == 0 else "1500",
                "hour_block": "morning" if i % 2 == 0 else "afternoon",
                "is_cancelled": i == 0,
                "is_diverted": False,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "seats_config": 166,
                "bts_aircraft_type": "614",
                "est_load_factor": 0.80,
                "est_open_seats_adjusted": 33,
            }
        )

    # Eval year 2024: 4 flights (including 1 cancelled + 1 unmatched T-100)
    eval_rows = [
        {
            "flight_id": "2024_0",
            "flight_date": "2024-01-17",
            "year": 2024,
            "month": 1,
            "op_carrier": "UA",
            "origin": "ORD",
            "dest": "DEN",
            "flight_num_mkt": "300",
            "crs_dep_time": "0900",
            "hour_block": "morning",
            "is_cancelled": False,
            "is_diverted": False,
            "t100_available": True,
            "lf_source": LF_SOURCE_L1,
            "seats_config": 166,
            "bts_aircraft_type": "614",
            "est_load_factor": 0.80,
            "est_open_seats_adjusted": 33,
        },
        {
            "flight_id": "2024_1_canc",
            "flight_date": "2024-01-17",
            "year": 2024,
            "month": 1,
            "op_carrier": "UA",
            "origin": "ORD",
            "dest": "DEN",
            "flight_num_mkt": "301",
            "crs_dep_time": "1500",
            "hour_block": "afternoon",
            "is_cancelled": True,
            "is_diverted": False,
            "t100_available": True,
            "lf_source": LF_SOURCE_L1,
            "seats_config": 166,
            "bts_aircraft_type": "614",
            "est_load_factor": 0.80,
            "est_open_seats_adjusted": 33,
        },
        {
            "flight_id": "2024_2_unmatched",
            "flight_date": "2024-01-17",
            "year": 2024,
            "month": 1,
            "op_carrier": "UA",
            "origin": "ORD",
            "dest": "DEN",
            "flight_num_mkt": "302",
            "crs_dep_time": "1900",
            "hour_block": "evening",
            "is_cancelled": False,
            "is_diverted": False,
            "t100_available": False,
            "lf_source": LF_SOURCE_UNMATCHED,
            "seats_config": 166,
            "bts_aircraft_type": "614",
            "est_load_factor": np.nan,
            "est_open_seats_adjusted": pd.NA,
        },
    ]

    flights_a = pd.DataFrame(base_rows + eval_rows)
    t100_a = pd.DataFrame(
        [
            {
                "year": 2023,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "bts_aircraft_type": "614",
                "service_class": "F",
                "data_source": "DU",
                "departures_performed": 100.0,
                "seats": 16600.0,
                "passengers": 13280.0,
            },
            {
                "year": 2024,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "DEN",
                "bts_aircraft_type": "614",
                "service_class": "F",
                "data_source": "DU",
                "departures_performed": 100.0,
                "seats": 16600.0,
                "passengers": 13280.0,
            },
        ]
    )
    tsa_a = pd.DataFrame(
        [
            {"date_day": "2023-01-18", "tsa_throughput": 2_000_000},
            {"date_day": "2023-01-19", "tsa_throughput": 2_200_000},
            {"date_day": "2024-01-17", "tsa_throughput": 2_000_000},
        ]
    )

    # Adversarial mutation of ALL 2024 realized values
    flights_b = flights_a.copy()
    mask_2024 = flights_b["year"] == 2024
    flights_b.loc[mask_2024, "is_cancelled"] = True
    flights_b.loc[mask_2024, "is_diverted"] = True
    flights_b.loc[mask_2024, "seats_config"] = 364
    flights_b.loc[mask_2024, "bts_aircraft_type"] = "627"
    flights_b.loc[mask_2024, "est_load_factor"] = 0.99
    flights_b.loc[mask_2024, "est_open_seats_adjusted"] = 1

    t100_b = t100_a.copy()
    t100_b.loc[t100_b["year"] == 2024, "passengers"] = 16590.0
    tsa_b = tsa_a.copy()
    tsa_b.loc[tsa_b["date_day"] == "2024-01-17", "tsa_throughput"] = 9_999_999

    scored_a = compute_soi_planning(flights_a, t100_df=t100_a, tsa_df=tsa_a)
    scored_b = compute_soi_planning(flights_b, t100_df=t100_b, tsa_df=tsa_b)

    # 2023 (minimum year) must be 100% NaN
    assert scored_a.loc[scored_a["year"] == 2023, "soi_planning"].isna().all()

    # 2024 planning scores must be bit-for-bit identical across A and B
    eval_a = scored_a[scored_a["year"] == 2024]
    eval_b = scored_b[scored_b["year"] == 2024]
    for col in ("s_open_plan", "s_disp_plan", "s_backup_plan", "s_demand_plan", "soi_planning"):
        assert np.array_equal(
            eval_a[col].to_numpy(dtype="float64"),
            eval_b[col].to_numpy(dtype="float64"),
            equal_nan=True,
        ), f"Leakage detected in {col}"

    # Cancelled 2024 flight on valid route IS scored in soi_planning; unmatched T-100 is NaN
    assert pd.notna(eval_a.loc[eval_a["flight_id"] == "2024_1_canc", "soi_planning"].iloc[0])
    assert pd.isna(eval_a.loc[eval_a["flight_id"] == "2024_2_unmatched", "soi_planning"].iloc[0])


def test_score_flights_end_to_end_on_sample(
    sample_ingested: dict[str, pd.DataFrame],
    repo_root,
) -> None:
    """`score_flights` scores `data/sample/` with registered confidence tiers, 2019 NULL planning, and valid bounds."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    enriched = enrich_flights(
        otp_df=sample_ingested["otp_marketing_flights"],
        t100_df=sample_ingested["t100_segments"],
        faa_registry_df=sample_ingested["faa_aircraft_registry"],
        faa_acftref_df=sample_ingested["faa_acftref"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        reference_dir=cfg.reference_dir,
        config=cfg,
        raise_on_sanity_failure=True,
    )
    scored = score_flights(
        enriched_df=enriched.enriched_flights,
        t100_df=sample_ingested["t100_segments"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        config=cfg,
    )
    assert len(scored) == 80
    assert set(scored.columns).issubset(set(COLUMN_CONFIDENCE_TIERS.keys()))
    assert scored.loc[scored["year"] == 2019, "soi_planning"].isna().all()
    assert scored.loc[scored["year"] >= 2023, "soi_planning"].notna().any()
    assert scored.loc[~scored["is_cancelled"], "soi_historical"].notna().all()
    assert check_sc5_missing_t100_null_invariant(scored).status == "PASS"
    assert check_soi_bounds(scored).status == "PASS"


def test_soi_planning_fallback_without_t100_table(
    sample_ingested: dict[str, pd.DataFrame],
    repo_root,
) -> None:
    """`compute_soi_planning(..., train_t100_df=None)` falls back cleanly to training `est_load_factor`."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    enriched = enrich_flights(
        otp_df=sample_ingested["otp_marketing_flights"],
        t100_df=sample_ingested["t100_segments"],
        faa_registry_df=sample_ingested["faa_aircraft_registry"],
        faa_acftref_df=sample_ingested["faa_acftref"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        reference_dir=cfg.reference_dir,
        config=cfg,
        raise_on_sanity_failure=True,
    )
    scored = compute_soi_planning(
        flights_df=enriched.enriched_flights,
        t100_df=None,
        tsa_df=sample_ingested["tsa_daily_throughput"],
        config=cfg,
    )
    assert scored.loc[scored["year"] == 2019, "soi_planning"].isna().all()
    assert scored.loc[scored["year"] >= 2023, "soi_planning"].notna().any()
    assert check_soi_bounds(scored).status == "PASS"
