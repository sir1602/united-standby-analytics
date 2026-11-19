"""Tests for the Day-Based Standby Seat Likelihood Predictor (`standby.prediction`)."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner

from standby.cli import main
from standby.prediction import (
    Mulberry32,
    PredictionRequest,
    evaluate_walk_forward_calibration,
    fit_predictor_priors,
    load_default_priors,
    predict_standby_day,
)


@pytest.fixture(scope="module")
def fitted_priors(repo_root: Path):
    """Load pre-built priors JSON (or fit from committed Looker extracts)."""
    return load_default_priors(repo_root=repo_root)


def test_mulberry32_deterministic_sequence() -> None:
    """Mulberry32 produces identical uniform, normal, and Poisson sequences for the same seed."""
    r1 = Mulberry32(202604)
    r2 = Mulberry32(202604)
    assert [r1.random() for _ in range(10)] == [r2.random() for _ in range(10)]
    assert [r1.normal() for _ in range(10)] == [r2.normal() for _ in range(10)]
    assert [r1.poisson(4.2) for _ in range(10)] == [r2.poisson(4.2) for _ in range(10)]


def test_priors_cover_all_348_routes_and_international_v1(fitted_priors) -> None:
    """Fitted priors cover all 277 domestic + 71 international ORD destinations."""
    assert len(fitted_priors.routes) == 348
    assert fitted_priors.meta["routes_dom"] == 277
    assert fitted_priors.meta["routes_intl"] == 71
    assert fitted_priors.routes["DEN"]["intl"] == 0
    assert fitted_priors.routes["DEN"]["hub_to_hub"] == 1
    assert fitted_priors.routes["LHR"]["intl"] == 1
    assert fitted_priors.routes["CUN"]["intl"] == 1


def test_predict_determinism_and_probability_bounds(fitted_priors) -> None:
    """Repeated calls with identical request return bit-identical probabilities within [0, 1]."""
    req = PredictionRequest(
        dest="DEN",
        flight_date=date(2026, 11, 29),
        party_size=2,
        priority="BUDDY",
    )
    r1 = predict_standby_day(req, fitted_priors)
    r2 = predict_standby_day(req, fitted_priors)
    assert r1.to_dict() == r2.to_dict()
    assert 0.0 <= r1.ci_low_80 <= r1.whole_day_prob <= r1.ci_high_80 <= 1.0
    assert 0.0 <= r1.whole_day_prob <= r1.seat_only_whole_day_prob <= 1.0
    assert len(r1.hour_blocks) == 6
    assert len(r1.neighbor_days) == 7
    assert any(
        n.flight_date == "2026-11-29" and n.delta_vs_selected == 0.0 for n in r1.neighbor_days
    )


def test_holiday_regime_and_priority_ordering(fitted_priors) -> None:
    """Off-peak Late Jan Tuesday beats Thanksgiving Sunday; SA1P beats BUDDY pass."""
    off_peak = predict_standby_day(
        PredictionRequest(
            dest="DEN", flight_date=date(2027, 1, 19), party_size=2, priority="BUDDY"
        ),
        fitted_priors,
        include_neighbors=False,
    )
    tg_sun = predict_standby_day(
        PredictionRequest(
            dest="DEN", flight_date=date(2026, 11, 29), party_size=2, priority="BUDDY"
        ),
        fitted_priors,
        include_neighbors=False,
    )
    tg_sa1p = predict_standby_day(
        PredictionRequest(
            dest="DEN", flight_date=date(2026, 11, 29), party_size=2, priority="SA1P"
        ),
        fitted_priors,
        include_neighbors=False,
    )
    assert off_peak.whole_day_prob > tg_sun.whole_day_prob
    assert tg_sa1p.whole_day_prob > tg_sun.whole_day_prob
    assert tg_sun.holiday_name == "Thanksgiving"
    assert off_peak.holiday_name is None


def test_international_v1_scope_and_caveat(fitted_priors) -> None:
    """International routes consolidate into a single T-100 route-month bank labeled v1."""
    res = predict_standby_day(
        PredictionRequest(dest="CUN", flight_date=date(2026, 7, 18), party_size=2, priority="SA3"),
        fitted_priors,
        include_neighbors=False,
    )
    assert res.is_international_v1 is True
    assert len(res.hour_blocks) == 1
    assert res.hour_blocks[0].hour_block == "all_day"
    assert res.hour_blocks[0].prior_tier == "TIER3_INTL_T100_V1"
    assert any("v1" in drv.lower() for drv in res.drivers)


def test_employeeres_live_override_sharpens_forecast(fitted_priors) -> None:
    """Live EmployeeRES overrides shift odds monotonically and replace pass-tier queue 1:1."""
    tight = predict_standby_day(
        PredictionRequest(
            dest="EWR",
            flight_date=date(2026, 10, 15),
            party_size=2,
            priority="SA2",
            open_seats_override=2,
            standbys_ahead_override=8,
            hours_until_departure=3.0,
            target_hour_block="evening",
        ),
        fitted_priors,
        include_neighbors=False,
    )
    wide_open = predict_standby_day(
        PredictionRequest(
            dest="EWR",
            flight_date=date(2026, 10, 15),
            party_size=2,
            priority="SA2",
            open_seats_override=25,
            standbys_ahead_override=1,
            hours_until_departure=3.0,
            target_hour_block="evening",
        ),
        fitted_priors,
        include_neighbors=False,
    )
    assert tight.mode == "live_employeeres"
    assert tight.queue_source == "EmployeeRES live input"
    assert tight.estimated_queue_ahead == 8.0
    ev_tight = next(h for h in tight.hour_blocks if h.hour_block == "evening")
    ev_wide = next(h for h in wide_open.hour_blocks if h.hour_block == "evening")
    assert ev_wide.clearance_prob > ev_tight.clearance_prob + 0.50


def test_walk_forward_calibration_metrics_valid(repo_root: Path) -> None:
    """Walk-forward calibration runs strictly out-of-sample with valid Brier, ECE, and ROC-AUC."""
    looker = repo_root / "exports" / "looker"
    flights_df = pd.read_csv(looker / "looker_flights.csv", low_memory=False)
    route_month_df = pd.read_csv(looker / "looker_route_month.csv", low_memory=False)
    route_summary_df = pd.read_csv(looker / "looker_route_summary.csv", low_memory=False)

    # Evaluate on 2025 holdout using strictly prior years (<= 2024) for fast CI execution
    cal = evaluate_walk_forward_calibration(
        flights_df=flights_df,
        route_month_df=route_month_df,
        route_summary_df=route_summary_df,
        thresholds=(1, 5, 10),
        eval_years=(2025,),
    )
    assert cal.eval_years == [2025]
    assert cal.n_eval_flights > 10000
    for key in ("ge_1", "ge_5", "ge_10"):
        th = cal.thresholds[key]
        assert 0.0 <= th.brier_score <= 0.25
        assert 0.0 <= th.ece <= 0.15
        assert th.roc_auc > 0.55
        assert len(th.reliability_bins) >= 2
    # Meaningful positive Brier Skill Score on non-trivial >= 10 open seats threshold
    assert cal.thresholds["ge_10"].brier_skill_score > 0.15


def test_python_and_js_simulator_bit_for_bit_parity(repo_root: Path, fitted_priors) -> None:
    """`dashboard/predictor.js` matches `src/standby/prediction/simulator.py` identically."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable not installed in environment")

    priors_path = repo_root / "dashboard" / "data" / "predictor_priors.json"
    if not priors_path.is_file():
        pytest.skip("dashboard/data/predictor_priors.json not built")

    test_cases = [
        {
            "dest": "DEN",
            "flight_date": "2026-11-29",
            "party_size": 2,
            "priority": "BUDDY",
            "carrier_group": "ALL",
        },
        {
            "dest": "EWR",
            "flight_date": "2026-10-15",
            "party_size": 1,
            "priority": "SA2",
            "carrier_group": "MAINLINE",
            "open_seats_override": 6.0,
            "standbys_ahead_override": 4,
            "hours_until_departure": 3.0,
            "target_hour_block": "evening",
        },
        {
            "dest": "CUN",
            "flight_date": "2026-07-18",
            "party_size": 2,
            "priority": "SA3",
            "carrier_group": "ALL",
        },
    ]

    js_script = (
        "const fs = require('fs');"
        "const pred = require('./dashboard/predictor.js');"
        "const priors = JSON.parse(fs.readFileSync('./dashboard/data/predictor_priors.json', 'utf8'));"
        "const cases = JSON.parse(process.argv[1]);"
        "const out = cases.map((c) => pred.predictStandbyDay(c, priors, true));"
        "process.stdout.write(JSON.stringify(out));"
    )
    proc = subprocess.run(
        [node, "-e", js_script, json.dumps(test_cases)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    js_results = json.loads(proc.stdout)

    for case, js_res in zip(test_cases, js_results, strict=True):
        py_res = predict_standby_day(
            PredictionRequest(**case), fitted_priors, include_neighbors=True
        )
        assert js_res["whole_day_prob"] == py_res.whole_day_prob
        assert js_res["ci_low_80"] == py_res.ci_low_80
        assert js_res["ci_high_80"] == py_res.ci_high_80
        assert js_res["seat_only_whole_day_prob"] == py_res.seat_only_whole_day_prob
        assert [b["clearance_prob"] for b in js_res["hour_blocks"]] == [
            b.clearance_prob for b in py_res.hour_blocks
        ]
        assert [n["whole_day_prob"] for n in js_res["neighbor_days"]] == [
            n.whole_day_prob for n in py_res.neighbor_days
        ]


def test_predict_cli_text_json_and_error_handling() -> None:
    """`standby predict` CLI works in text and JSON mode and rejects unknown destinations."""
    runner = CliRunner()
    res_text = runner.invoke(
        main,
        [
            "predict",
            "--dest",
            "DEN",
            "--date",
            "2026-11-29",
            "--party-size",
            "2",
            "--priority",
            "BUDDY",
        ],
    )
    assert res_text.exit_code == 0, res_text.output
    assert "ORD -> DEN" in res_text.output
    assert "Whole-Day Clearance Likelihood" in res_text.output
    assert "Flexible Dates" in res_text.output

    res_json = runner.invoke(
        main,
        ["predict", "--dest", "LHR", "--date", "2026-02-10", "--json"],
    )
    assert res_json.exit_code == 0, res_json.output
    payload = json.loads(res_json.output)
    assert payload["dest"] == "LHR"
    assert payload["is_international_v1"] is True
    assert 0.0 <= payload["whole_day_prob"] <= 1.0

    res_bad = runner.invoke(main, ["predict", "--dest", "ZZZ", "--date", "2026-02-10"])
    assert res_bad.exit_code != 0
    assert "not found" in res_bad.output.lower()


def test_fit_predictor_priors_strict_max_year_filter(repo_root: Path) -> None:
    """Passing `max_year=2023` strictly restricts fitted training years to `<= 2023`."""
    looker = repo_root / "exports" / "looker"
    flights_df = pd.read_csv(looker / "looker_flights.csv", low_memory=False)
    route_month_df = pd.read_csv(looker / "looker_route_month.csv", low_memory=False)
    route_summary_df = pd.read_csv(looker / "looker_route_summary.csv", low_memory=False)

    priors = fit_predictor_priors(flights_df, route_month_df, route_summary_df, max_year=2023)
    assert all(y <= 2023 for y in priors.meta["trained_years"])
