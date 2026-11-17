"""Tests for Phase 2 flight-level enrichment (`src/standby/enrichment/`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from standby.config import StandbyConfig, load_config
from standby.enrichment import (
    COLUMN_CONFIDENCE_TIERS,
    LF_SOURCE_L1,
    LF_SOURCE_L2,
    LF_SOURCE_L3,
    LF_SOURCE_UNMATCHED,
    AircraftResolver,
    T100LoadFactorJoiner,
    compute_flight_estimates,
    enrich_flights,
    normalize_tail_num,
    tag_dataframe_confidence_tiers,
)


def _make_sample_otp_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "flight_date": "2024-01-15",
        "year": 2024,
        "quarter": 1,
        "month": 1,
        "day_of_month": 15,
        "day_of_week": 1,
        "mkt_carrier": "UA",
        "op_carrier": "UA",
        "flight_num_mkt": "123",
        "flight_num_op": "123",
        "tail_num": "N75432",
        "tail_num_clean": "75432",
        "origin": "ORD",
        "origin_airport_id": 13930,
        "dest": "EWR",
        "dest_airport_id": 11618,
        "crs_dep_time": "0800",
        "dep_time": "0800",
        "crs_arr_time": "1100",
        "arr_time": "1100",
        "dep_hour": 8,
        "hour_block": "morning",
        "dep_delay_min": 0.0,
        "arr_delay_min": 0.0,
        "is_cancelled": False,
        "cancellation_code": None,
        "is_diverted": False,
        "crs_elapsed_time_min": 120.0,
        "actual_elapsed_time_min": 120.0,
        "distance_miles": 719.0,
        "confidence_tier": "actual",
    }
    row.update(overrides)
    return row


def test_normalize_tail_num_handles_prefix_typos_and_nulls() -> None:
    """`normalize_tail_num` adds `'N'`, extracts FAA key, and rejects invalid N-number prefixes."""
    assert normalize_tail_num(None) == (None, None)
    assert normalize_tail_num("") == (None, None)
    assert normalize_tail_num("  nan ") == (None, None)
    assert normalize_tail_num("37502") == ("N37502", "37502")
    assert normalize_tail_num("n424ua") == ("N424UA", "424UA")
    assert normalize_tail_num("NTCR2") == ("NTCR2", None)
    assert normalize_tail_num("N0123") == ("N0123", None)


def test_aircraft_resolver_stearman_dual_registry_and_ga_collision_guards(
    repo_root: Path,
) -> None:
    """
    Verify strict `TAIL_OVERRIDE -> FAA_MASTER -> FAA_DEREG` precedence and Stearman guard:
      - Dual `MASTER` (737-924ER) + `DEREG` (2-seat Stearman A75N1) produces 1 row via `FAA_MASTER` (179 seats)
      - Standalone `DEREG` Stearman A75N1 (2 seats < 20) is rejected as `UNMATCHED`
      - Drone/GA N-number collisions (`151GJ`, `884AS`) are safely resolved/isolated
      - `NTCR2` typo -> `UNMATCHED_TYPO`
    """
    acftref = pd.DataFrame(
        [
            {
                "code": "13848A3",
                "faa_manufacturer": "BOEING",
                "faa_model": "737-924ER",
                "faa_tc_max_seats": 191,
                "is_commercial_airliner": True,
            },
            {
                "code": "1380100",
                "faa_manufacturer": "BOEING",
                "faa_model": "A75N1",
                "faa_tc_max_seats": 2,
                "is_commercial_airliner": True,
            },
            {
                "code": "06102EQ",
                "faa_manufacturer": "HYLIO INC",
                "faa_model": "AG-272",
                "faa_tc_max_seats": 0,
                "is_commercial_airliner": False,
            },
            {
                "code": "1390008",
                "faa_manufacturer": "BOMBARDIER INC",
                "faa_model": "CL-600-2B19",
                "faa_tc_max_seats": 55,
                "is_commercial_airliner": True,
            },
        ]
    )
    registry = pd.DataFrame(
        [
            # Tail 54552 in BOTH MASTER (737-924ER) and DEREG (2-seat Stearman A75N1)
            {
                "n_number": "54552",
                "serial_number": "1",
                "mfr_mdl_code": "13848A3",
                "year_mfr": "2015",
                "registry_source": "faa_master",
                "cancel_date": None,
            },
            {
                "n_number": "54552",
                "serial_number": "2",
                "mfr_mdl_code": "1380100",
                "year_mfr": "1942",
                "registry_source": "faa_dereg",
                "cancel_date": "19551130",
            },
            # Standalone Stearman-only tail in DEREG (must be rejected by <20 seat guard)
            {
                "n_number": "99999",
                "serial_number": "3",
                "mfr_mdl_code": "1380100",
                "year_mfr": "1943",
                "registry_source": "faa_dereg",
                "cancel_date": "19600101",
            },
            # Drone N-number reuse in MASTER + retired CRJ-200 in DEREG for 884AS
            {
                "n_number": "151GJ",
                "serial_number": "4",
                "mfr_mdl_code": "06102EQ",
                "year_mfr": "2023",
                "registry_source": "faa_master",
                "cancel_date": None,
            },
            {
                "n_number": "884AS",
                "serial_number": "5",
                "mfr_mdl_code": "06102EQ",
                "year_mfr": "2024",
                "registry_source": "faa_master",
                "cancel_date": None,
            },
            {
                "n_number": "884AS",
                "serial_number": "6",
                "mfr_mdl_code": "1390008",
                "year_mfr": "2001",
                "registry_source": "faa_dereg",
                "cancel_date": "20191126",
            },
        ]
    )

    otp_df = pd.DataFrame(
        [
            _make_sample_otp_row(tail_num="N54552", op_carrier="UA"),
            _make_sample_otp_row(
                tail_num="N99999", op_carrier="UA", is_cancelled=True, cancellation_code="A"
            ),
            _make_sample_otp_row(tail_num="N151GJ", op_carrier="G7", year=2019),
            _make_sample_otp_row(tail_num="N884AS", op_carrier="OO", year=2019),
            _make_sample_otp_row(
                tail_num="NTCR2", op_carrier="OO", is_cancelled=True, cancellation_code="B"
            ),
            _make_sample_otp_row(
                tail_num=None,
                tail_num_clean=None,
                op_carrier="UA",
                is_cancelled=True,
                cancellation_code="B",
            ),
        ]
    )

    resolver = AircraftResolver.from_reference_dir(
        reference_dir=repo_root / "reference",
        faa_registry_df=registry,
        faa_acftref_df=acftref,
        min_faa_tc_max_seats=20,
    )
    resolved, metrics = resolver.resolve(otp_df)

    # 1:1 row count invariant (no Cartesian duplication on dual-registry 54552)
    assert len(resolved) == len(otp_df)
    assert metrics.stearman_collisions_rejected == 2

    # Row 0: dual-registry N54552 resolves cleanly to FAA_MASTER 737-900ER (179 seats)
    assert resolved.loc[0, "aircraft_source"] == "FAA_MASTER"
    assert resolved.loc[0, "fleet_subtype"] == "B737-900ER"
    assert int(resolved.loc[0, "seats_config"]) == 179

    # Row 1: standalone Stearman N99999 is rejected (never resolves to 2 seats)
    assert resolved.loc[1, "aircraft_source"] == "UNMATCHED"
    assert pd.isna(resolved.loc[1, "seats_config"])

    # Row 2: N151GJ resolves via TAIL_OVERRIDE to 2019 GoJet CRJ-700 (70 seats), not Hylio drone
    assert resolved.loc[2, "aircraft_source"] == "TAIL_OVERRIDE"
    assert resolved.loc[2, "fleet_subtype"] == "CRJ-700"
    assert int(resolved.loc[2, "seats_config"]) == 70

    # Row 3: N884AS ignores Hylio drone MASTER and resolves via FAA_DEREG to SkyWest CRJ-200 (50 seats)
    assert resolved.loc[3, "aircraft_source"] == "FAA_DEREG"
    assert resolved.loc[3, "fleet_subtype"] == "CRJ-200"
    assert int(resolved.loc[3, "seats_config"]) == 50

    # Row 4: NTCR2 dispatcher typo -> UNMATCHED_TYPO
    assert resolved.loc[4, "aircraft_source"] == "UNMATCHED_TYPO"
    assert pd.isna(resolved.loc[4, "seats_config"])

    # Row 5: Null-tail cancelled flight -> UNASSIGNED_CANCELLED
    assert resolved.loc[5, "aircraft_source"] == "UNASSIGNED_CANCELLED"
    assert pd.isna(resolved.loc[5, "seats_config"])


def test_t100_load_factor_hierarchy_and_missing_month_null_invariant() -> None:
    """
    Verify 3-tier `lf_source` fallback (`CARRIER_ROUTE_MONTH_TYPE` -> `CARRIER_ROUTE_MONTH` ->
    `ROUTE_MONTH` -> `UNMATCHED`), shared spoke flag, and zero silent imputation when T-100 is absent.
    """
    t100_df = pd.DataFrame(
        [
            # L1 match for UA ORD->EWR 2024-01 type 614 (LF = 0.80)
            {
                "year": 2024,
                "quarter": 1,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "EWR",
                "bts_aircraft_type": "614",
                "service_class": "F",
                "data_source": "DU",
                "is_international": False,
                "departures_performed": 100.0,
                "seats": 10000.0,
                "passengers": 8000.0,
            },
            # L2 match for OO ORD->DSM 2024-01 type 530 (LF = 0.75, 100 T-100 deps vs 1 OTP -> shared spoke)
            {
                "year": 2024,
                "quarter": 1,
                "month": 1,
                "op_carrier": "OO",
                "origin": "ORD",
                "dest": "DSM",
                "bts_aircraft_type": "530",
                "service_class": "F",
                "data_source": "DU",
                "is_international": False,
                "departures_performed": 100.0,
                "seats": 5000.0,
                "passengers": 3750.0,
            },
            # L3 route-month match for ORD->ALB 2024-01 via UA (LF = 0.90) when ZW has no carrier row
            {
                "year": 2024,
                "quarter": 1,
                "month": 1,
                "op_carrier": "UA",
                "origin": "ORD",
                "dest": "ALB",
                "bts_aircraft_type": "698",
                "service_class": "F",
                "data_source": "DU",
                "is_international": False,
                "departures_performed": 50.0,
                "seats": 6300.0,
                "passengers": 5670.0,
            },
        ]
    )

    flights = pd.DataFrame(
        [
            # L1
            _make_sample_otp_row(
                op_carrier="UA", dest="EWR", bts_aircraft_type="614", seats_config=166
            ),
            # L2 (OO flying 631 on ORD->DSM where T-100 only has 530; also shared spoke)
            _make_sample_otp_row(
                op_carrier="OO", dest="DSM", bts_aircraft_type="631", seats_config=70
            ),
            # L3 (G7 flying ORD->ALB where only UA exists in T-100)
            _make_sample_otp_row(
                op_carrier="G7", dest="ALB", bts_aircraft_type="530", seats_config=50
            ),
            # Missing T-100 month (2026-09) -> MUST remain UNMATCHED with NaN LF
            _make_sample_otp_row(
                flight_date="2026-09-10",
                year=2026,
                month=9,
                op_carrier="UA",
                dest="EWR",
                bts_aircraft_type="614",
                seats_config=166,
            ),
        ]
    )
    flights["seats_config"] = pd.array(flights["seats_config"].tolist(), dtype="Int64")

    joiner = T100LoadFactorJoiner(t100_df=t100_df, hub="ORD")
    joined = joiner.join(flights)

    assert joined.loc[0, "lf_source"] == LF_SOURCE_L1
    assert joined.loc[0, "est_load_factor"] == pytest.approx(0.80)
    assert bool(joined.loc[0, "t100_available"]) is True

    assert joined.loc[1, "lf_source"] == LF_SOURCE_L2
    assert joined.loc[1, "est_load_factor"] == pytest.approx(0.75)
    assert bool(joined.loc[1, "is_shared_regional_spoke"]) is True

    assert joined.loc[2, "lf_source"] == LF_SOURCE_L3
    assert joined.loc[2, "est_load_factor"] == pytest.approx(0.90)

    assert bool(joined.loc[3, "t100_available"]) is False
    assert joined.loc[3, "lf_source"] == LF_SOURCE_UNMATCHED
    assert pd.isna(joined.loc[3, "est_load_factor"])

    tsa_df = pd.DataFrame(
        [
            {"date_day": "2024-01-15", "tsa_throughput": 2200000},
            {"date_day": "2024-01-16", "tsa_throughput": 2000000},
            {"date_day": "2026-09-10", "tsa_throughput": 2500000},
        ]
    )
    estimated = compute_flight_estimates(joined, tsa_df=tsa_df)

    # Base estimates never overwritten by adjusted estimates
    assert int(estimated.loc[0, "est_passengers"]) == round(166 * 0.80)
    assert int(estimated.loc[0, "est_open_seats"]) == 166 - int(estimated.loc[0, "est_passengers"])
    assert estimated.loc[0, "est_load_factor"] == pytest.approx(0.80)
    assert estimated.loc[0, "est_lf_adjusted"] != estimated.loc[0, "est_load_factor"]

    # Missing T-100 month (row 3) strictly keeps ALL est_* columns NULL
    for col in (
        "est_load_factor",
        "est_passengers",
        "est_open_seats",
        "est_lf_adjusted",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
    ):
        assert pd.isna(estimated.loc[3, col]), f"Expected NULL in {col} for missing T-100 month"


def test_confidence_tier_tagging_completeness_and_strict_untagged_error() -> None:
    """`tag_dataframe_confidence_tiers` attaches metadata for all columns and rejects untagged ones."""
    df = pd.DataFrame(
        [
            {
                **_make_sample_otp_row(),
                "aircraft_source": "FAA_MASTER",
                "faa_manufacturer": "BOEING",
                "faa_model": "737-924ER",
                "faa_tc_max_seats": 191,
                "fleet_subtype": "B737-900ER",
                "bts_aircraft_type": "888",
                "seat_config_source": "SUBTYPE_DEFAULT",
                "seats_config": 179,
                "seats_premium": 20,
                "seats_economy": 159,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "is_shared_regional_spoke": False,
                "tsa_throughput": 2100000,
                "est_load_factor": 0.82,
                "est_passengers": 147,
                "est_open_seats": 32,
                "tsa_demand_multiplier": 1.0,
                "hour_block_multiplier": 1.03,
                "est_lf_adjusted": 0.8446,
                "est_passengers_adjusted": 151,
                "est_open_seats_adjusted": 28,
            },
            {
                **_make_sample_otp_row(flight_date="2026-09-01", year=2026, month=9),
                "aircraft_source": "FAA_MASTER",
                "faa_manufacturer": "BOEING",
                "faa_model": "737-924ER",
                "faa_tc_max_seats": 191,
                "fleet_subtype": "B737-900ER",
                "bts_aircraft_type": "888",
                "seat_config_source": "SUBTYPE_DEFAULT",
                "seats_config": 179,
                "seats_premium": 20,
                "seats_economy": 159,
                "t100_available": False,
                "lf_source": LF_SOURCE_UNMATCHED,
                "is_shared_regional_spoke": False,
                "tsa_throughput": 2100000,
                "est_load_factor": np.nan,
                "est_passengers": pd.NA,
                "est_open_seats": pd.NA,
                "tsa_demand_multiplier": 1.0,
                "hour_block_multiplier": 1.03,
                "est_lf_adjusted": np.nan,
                "est_passengers_adjusted": pd.NA,
                "est_open_seats_adjusted": pd.NA,
            },
        ]
    )

    tagged = tag_dataframe_confidence_tiers(df)
    assert set(tagged.attrs["column_confidence_tiers"].keys()) == set(tagged.columns)
    for col, tier in tagged.attrs["column_confidence_tiers"].items():
        assert COLUMN_CONFIDENCE_TIERS[col] == tier
    assert tagged.loc[0, "record_confidence_summary"] == "actual+derived+estimated"
    assert tagged.loc[1, "record_confidence_summary"] == "actual+derived (unmatched_t100)"

    bad_df = df.copy()
    bad_df["unregistered_temp_col"] = 1
    with pytest.raises(KeyError, match="unregistered_temp_col"):
        tag_dataframe_confidence_tiers(bad_df)


def test_end_to_end_enrich_flights_on_sample(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
    repo_root: Path,
) -> None:
    """Full Phase 2 enrichment on `data/sample/` achieves 0 unmatched performed flights & passes sanity checks."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    result = enrich_flights(
        otp_df=sample_ingested["otp_marketing_flights"],
        t100_df=sample_ingested["t100_segments"],
        faa_registry_df=sample_ingested["faa_aircraft_registry"],
        faa_acftref_df=sample_ingested["faa_acftref"],
        tsa_df=sample_ingested["tsa_daily_throughput"],
        reference_dir=sample_config.reference_dir,
        config=cfg,
    )
    enriched = result.enriched_flights
    assert len(enriched) == 80
    assert result.faa_match_metrics.post_override_unmatched_performed == 0
    assert result.sanity_report.passed is True
    assert set(enriched.attrs["column_confidence_tiers"].keys()) == set(enriched.columns)

    perf = enriched[~enriched["is_cancelled"]]
    assert perf["seats_config"].notna().all()
    assert perf["est_load_factor"].notna().all()
    assert (perf["est_passengers"] <= perf["seats_config"]).all()
    assert (perf["est_passengers_adjusted"] <= perf["seats_config"]).all()


def test_aircraft_resolver_handles_na_effective_dates_and_timestamp_flight_date() -> None:
    """`AircraftResolver` treats `None`/`np.nan` override bounds as open and normalizes `pd.Timestamp` `flight_date`."""
    seat_config = pd.DataFrame(
        [
            {
                "subtype": "B777-200_HD",
                "op_carrier_group": "MAINLINE",
                "seats_total": 364,
                "seats_premium": 28,
                "seats_economy": 336,
                "source_url": "https://example.com",
                "as_of": "2026-08-31",
            },
            {
                "subtype": "B777-200_ER_INTL",
                "op_carrier_group": "MAINLINE",
                "seats_total": 276,
                "seats_premium": 50,
                "seats_economy": 226,
                "source_url": "https://example.com",
                "as_of": "2026-08-31",
            },
        ]
    )
    tail_overrides = pd.DataFrame(
        [
            {
                "tail_num": "N204UA",
                "op_carrier": "UA",
                "subtype": "B777-200_HD",
                "bts_aircraft_type": "627",
                "seats_total": 364,
                "seats_premium": 28,
                "seats_economy": 336,
                "source_url": "https://example.com",
                "as_of": "2026-08-31",
                "override_reason": "pre-retrofit",
                "effective_from": np.nan,
                "effective_to": "2023-12-31",
            },
            {
                "tail_num": "N204UA",
                "op_carrier": "UA",
                "subtype": "B777-200_ER_INTL",
                "bts_aircraft_type": "627",
                "seats_total": 276,
                "seats_premium": 50,
                "seats_economy": 226,
                "source_url": "https://example.com",
                "as_of": "2026-08-31",
                "override_reason": "post-retrofit",
                "effective_from": "2024-01-01",
                "effective_to": None,
            },
        ]
    )
    empty_crosswalk = pd.DataFrame(
        columns=["faa_mfr", "faa_model", "op_carrier", "subtype", "bts_aircraft_type"]
    )
    empty_reg = pd.DataFrame(columns=["n_number", "mfr_mdl_code", "registry_source", "cancel_date"])
    empty_acft = pd.DataFrame(columns=["code", "faa_manufacturer", "faa_model", "faa_tc_max_seats"])

    resolver = AircraftResolver(
        seat_config_df=seat_config,
        crosswalk_df=empty_crosswalk,
        tail_overrides_df=tail_overrides,
        faa_registry_df=empty_reg,
        faa_acftref_df=empty_acft,
    )

    otp_df = pd.DataFrame(
        [
            _make_sample_otp_row(
                tail_num="N204UA",
                op_carrier="UA",
                flight_date=pd.Timestamp("2023-12-31"),
            ),
            _make_sample_otp_row(
                tail_num="N204UA",
                op_carrier="UA",
                flight_date=pd.Timestamp("2024-01-01"),
            ),
        ]
    )
    resolved, metrics = resolver.resolve(otp_df)
    assert metrics.post_override_unmatched_performed == 0
    assert resolved.loc[0, "aircraft_source"] == "TAIL_OVERRIDE"
    assert resolved.loc[0, "fleet_subtype"] == "B777-200_HD"
    assert int(resolved.loc[0, "seats_config"]) == 364
    assert resolved.loc[1, "aircraft_source"] == "TAIL_OVERRIDE"
    assert resolved.loc[1, "fleet_subtype"] == "B777-200_ER_INTL"
    assert int(resolved.loc[1, "seats_config"]) == 276


def test_calculations_deduplicates_tsa_date_day_and_normalizes_hour_block_case() -> None:
    """TSA monthly mean deduplicates `date_day` first and `hour_block` matching is case-insensitive."""
    from standby.enrichment.calculations import compute_tsa_daily_multipliers

    tsa_dup = pd.DataFrame(
        [
            {"date_day": "2024-01-15", "tsa_throughput": 2_000_000},
            {"date_day": "2024-01-15", "tsa_throughput": 2_000_000},
            {"date_day": "2024-01-16", "tsa_throughput": 1_000_000},
        ]
    )
    tsa_mults = compute_tsa_daily_multipliers(tsa_dup, alpha=0.35, bounds=(0.80, 1.20))
    assert len(tsa_mults) == 2
    # Deduplicated monthly mean = 1.5M -> 2024-01-15 ratio = 4/3 -> 1 + 0.35*(1/3) = 1.116667
    jan15_mult = float(
        tsa_mults.loc[tsa_mults["date_day"] == "2024-01-15", "tsa_demand_multiplier"].iloc[0]
    )
    assert jan15_mult == pytest.approx(1.0 + 0.35 * (2_000_000 / 1_500_000 - 1.0))

    flights = pd.DataFrame(
        [
            {
                **_make_sample_otp_row(flight_date="2024-01-15", hour_block="MORNING"),
                "seats_config": 166,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "est_load_factor": 0.70,
            },
            {
                **_make_sample_otp_row(flight_date="2024-01-15", hour_block=" Morning "),
                "seats_config": 166,
                "t100_available": True,
                "lf_source": LF_SOURCE_L1,
                "est_load_factor": 0.70,
            },
        ]
    )
    flights["seats_config"] = pd.array(flights["seats_config"].tolist(), dtype="Int64")
    est = compute_flight_estimates(flights, tsa_df=tsa_dup)
    assert est["hour_block_multiplier"].tolist() == pytest.approx([1.03, 1.03])
    assert est.loc[0, "est_lf_adjusted"] == pytest.approx(0.70 * jan15_mult * 1.03)
