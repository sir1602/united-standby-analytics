"""Tests for Section 7 automated sanity checks (`SC-1` through `SC-5`)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from standby.enrichment.aircraft import (
    load_faa_t100_crosswalk,
    load_seat_config,
    load_tail_overrides,
)
from standby.enrichment.load_factor import LF_SOURCE_L1, LF_SOURCE_UNMATCHED
from standby.validation.sanity import (
    SanityCheckError,
    check_sc1_passenger_and_lf_bounds,
    check_sc2_t100_otp_departure_reconciliation,
    check_sc3_seats_per_dep_vs_config,
    check_sc4_system_wide_ua_load_factor,
    check_sc5_missing_t100_null_invariant,
    run_all_sanity_checks,
)


def _base_enriched_df(n: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "flight_date": ["2024-01-15"] * n,
            "year": [2024] * n,
            "month": [1] * n,
            "op_carrier": ["UA"] * n,
            "origin": ["ORD"] * n,
            "dest": ["EWR"] * n,
            "is_cancelled": [False] * n,
            "t100_available": [True] * n,
            "lf_source": [LF_SOURCE_L1] * n,
            "seats_config": pd.array([166] * n, dtype="Int64"),
            "est_load_factor": [0.82] * n,
            "est_passengers": pd.array([136] * n, dtype="Int64"),
            "est_open_seats": pd.array([30] * n, dtype="Int64"),
            "est_lf_adjusted": [0.84] * n,
            "est_passengers_adjusted": pd.array([139] * n, dtype="Int64"),
            "est_open_seats_adjusted": pd.array([27] * n, dtype="Int64"),
        }
    )


def _base_t100_df(deps: float = 20.0, lf: float = 0.82) -> pd.DataFrame:
    seats = deps * 166.0
    pax = seats * lf
    return pd.DataFrame(
        [
            {
                "year": 2024,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "EWR",
                "bts_aircraft_type": "614",
                "service_class": "F",
                "data_source": "DU",
                "is_international": False,
                "departures_performed": deps,
                "seats": seats,
                "passengers": pax,
            }
        ]
    )


def test_sc1_detects_bound_violations() -> None:
    """SC-1 passes on valid bounds and fails when `est_passengers > seats_config` or `LF > 1`."""
    df = _base_enriched_df(5)
    assert check_sc1_passenger_and_lf_bounds(df).status == "PASS"

    bad_pax = df.copy()
    bad_pax.loc[0, "est_passengers"] = 170
    res_pax = check_sc1_passenger_and_lf_bounds(bad_pax)
    assert res_pax.status == "FAIL"
    assert res_pax.details["bad_base_pax"] == 1

    bad_lf = df.copy()
    bad_lf.loc[0, "est_load_factor"] = 1.05
    res_lf = check_sc1_passenger_and_lf_bounds(bad_lf)
    assert res_lf.status == "FAIL"
    assert res_lf.details["bad_lf"] == 1


def test_sc2_reconciliation_pass_warn_and_fail(monkeypatch) -> None:
    """SC-2 passes when within ±10%, warns on cell drift, and fails when UA aggregate drift > 10%."""
    # Use a 600-row dataset so sample-mode guard (>500 rows) does not skip SC-2
    df_600 = _base_enriched_df(600)
    t100_ok = _base_t100_df(deps=600.0)
    res_pass = check_sc2_t100_otp_departure_reconciliation(df_600, t100_ok, sample_mode=False)
    assert res_pass.status == "PASS"

    # Cell drift > 10% on one route-month while aggregate drift <= 10% -> WARN
    df_warn = df_600.copy()
    df_warn.loc[0:9, "dest"] = "DEN"  # 10 flights with 0 T-100 deps on DEN, 590 vs 600 on EWR
    res_warn = check_sc2_t100_otp_departure_reconciliation(df_warn, t100_ok, sample_mode=False)
    assert res_warn.status == "WARN"

    # Aggregate UA mainline drift > 10% -> FAIL
    t100_bad = _base_t100_df(deps=450.0)
    res_fail = check_sc2_t100_otp_departure_reconciliation(df_600, t100_bad, sample_mode=False)
    assert res_fail.status == "FAIL"


def test_sc3_seats_per_dep_crosswalk_error_detection(repo_root) -> None:
    """SC-3 detects crosswalk seat-per-departure mismatches (>10% WARN, >15% FAIL)."""
    ref_dir = repo_root / "reference"
    sc = load_seat_config(ref_dir)
    cw = load_faa_t100_crosswalk(ref_dir)
    to = load_tail_overrides(ref_dir)

    # Normal 737-800 (614 = 166 seats)
    t100_ok = _base_t100_df(deps=100.0)
    assert check_sc3_seats_per_dep_vs_config(t100_ok, sc, cw, to).status == "PASS"

    # 12% lower (146 seats/dep vs 166) -> WARN
    t100_warn = t100_ok.copy()
    t100_warn["seats"] = 14600.0
    assert check_sc3_seats_per_dep_vs_config(t100_warn, sc, cw, to).status == "WARN"

    # 50 seats/dep on type 614 (737-800 = 166) -> FAIL (catches gross crosswalk error)
    t100_fail = t100_ok.copy()
    t100_fail["seats"] = 5000.0
    assert check_sc3_seats_per_dep_vs_config(t100_fail, sc, cw, to).status == "FAIL"


def test_sc4_system_wide_ua_load_factor_bounds() -> None:
    """SC-4 passes inside [0.80, 0.86], warns in [0.75, 0.80), and fails outside [0.75, 0.90]."""
    assert (
        check_sc4_system_wide_ua_load_factor(
            _base_t100_df(deps=600.0, lf=0.83), sample_min_t100_rows=1
        ).status
        == "PASS"
    )
    assert (
        check_sc4_system_wide_ua_load_factor(
            _base_t100_df(deps=600.0, lf=0.77), sample_min_t100_rows=1
        ).status
        == "WARN"
    )
    assert (
        check_sc4_system_wide_ua_load_factor(
            _base_t100_df(deps=600.0, lf=0.60), sample_min_t100_rows=1
        ).status
        == "FAIL"
    )


def test_sc5_missing_t100_and_cancelled_soi_null_invariant() -> None:
    """SC-5 fails immediately if any unmatched T-100 row or cancelled `soi_historical` is non-null."""
    df = _base_enriched_df(3)
    df.loc[2, "t100_available"] = False
    df.loc[2, "lf_source"] = LF_SOURCE_UNMATCHED
    for col in (
        "est_load_factor",
        "est_passengers",
        "est_open_seats",
        "est_lf_adjusted",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
    ):
        df.loc[2, col] = np.nan if "lf" in col or "factor" in col else pd.NA

    assert check_sc5_missing_t100_null_invariant(df).status == "PASS"

    # Silent imputation of est_open_seats on unmatched row -> FAIL
    bad = df.copy()
    bad.loc[2, "est_open_seats"] = 25
    res = check_sc5_missing_t100_null_invariant(bad)
    assert res.status == "FAIL"
    assert res.details["violations_by_column"]["est_open_seats"] == 1

    # Non-null soi_historical on cancelled row -> FAIL
    bad_soi = df.copy()
    bad_soi["soi_historical"] = [80.0, np.nan, np.nan]
    bad_soi.loc[1, "is_cancelled"] = True
    bad_soi.loc[1, "soi_historical"] = 50.0
    res_soi = check_sc5_missing_t100_null_invariant(bad_soi)
    assert res_soi.status == "FAIL"
    assert res_soi.details["violations_by_column"]["soi_historical_cancelled"] == 1


def test_run_all_sanity_checks_raises_sanity_check_error_on_failure(repo_root) -> None:
    """`run_all_sanity_checks` raises `SanityCheckError` when a blocking check fails."""
    ref_dir = repo_root / "reference"
    sc = load_seat_config(ref_dir)
    cw = load_faa_t100_crosswalk(ref_dir)
    to = load_tail_overrides(ref_dir)

    df = _base_enriched_df(5)
    df.loc[0, "est_passengers"] = 999  # Violates SC-1
    t100 = _base_t100_df(deps=5.0, lf=0.83)

    with pytest.raises(SanityCheckError, match="SC-1"):
        run_all_sanity_checks(
            enriched_df=df,
            t100_df=t100,
            seat_config_df=sc,
            crosswalk_df=cw,
            tail_overrides_df=to,
            raise_on_failure=True,
        )


def test_sc1_handles_na_open_seats_without_value_error() -> None:
    """SC-1 records violations and returns `FAIL` instead of raising `ValueError` on NA open seats."""
    df_base_na = _base_enriched_df(3)
    df_base_na.loc[1, "est_open_seats"] = pd.NA
    res_base = check_sc1_passenger_and_lf_bounds(df_base_na)
    assert res_base.status == "FAIL"
    assert res_base.details["bad_base_open"] == 1

    df_adj_na = _base_enriched_df(3)
    df_adj_na.loc[2, "est_open_seats_adjusted"] = pd.NA
    res_adj = check_sc1_passenger_and_lf_bounds(df_adj_na)
    assert res_adj.status == "FAIL"
    assert res_adj.details["bad_adj_open"] == 1


def test_sc2_uses_configurable_sample_min_t100_rows(repo_root) -> None:
    """SC-2 skips strict gate below `sample_min_t100_rows` and enforces it at or above threshold."""
    from dataclasses import replace

    from standby.config import load_config

    df_small = _base_enriched_df(20)
    t100_bad = _base_t100_df(deps=5.0, lf=0.83)

    # Below threshold (20 < 50) -> WARN
    res_warn = check_sc2_t100_otp_departure_reconciliation(
        df_small, t100_bad, sample_mode=False, sample_min_t100_rows=50
    )
    assert res_warn.status == "WARN"

    # At/above threshold (20 >= 10) -> FAIL
    res_fail = check_sc2_t100_otp_departure_reconciliation(
        df_small, t100_bad, sample_mode=False, sample_min_t100_rows=10
    )
    assert res_fail.status == "FAIL"

    # Also verify run_all_sanity_checks forwards sc_cfg.sample_min_t100_rows
    cfg = load_config(repo_root=repo_root, sample_mode=False, dry_run=True)
    cfg.sanity_checks = replace(cfg.sanity_checks, sample_min_t100_rows=10)
    ref_dir = repo_root / "reference"
    report = run_all_sanity_checks(
        enriched_df=df_small,
        t100_df=t100_bad,
        seat_config_df=load_seat_config(ref_dir),
        crosswalk_df=load_faa_t100_crosswalk(ref_dir),
        tail_overrides_df=load_tail_overrides(ref_dir),
        config=cfg,
        raise_on_failure=False,
    )
    sc2_res = next(r for r in report.results if r.check_id == "SC-2")
    assert sc2_res.status == "FAIL"
