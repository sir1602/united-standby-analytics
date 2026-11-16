"""Tests for Pandera schema validation, YAML extensibility, and malformed-data rejection."""

from __future__ import annotations

import pandas as pd
import pandera.errors as pa_errors
import pytest

from standby.config import HourBlockSpec, StandbyConfig
from standby.schemas import (
    BtsLookupSchema,
    FaaAcftrefSchema,
    OtpMarketingFlightsSchema,
    T100SegmentsSchema,
    TsaThroughputSchema,
    build_otp_schema,
    validate_all_ingested,
)


def test_validate_all_ingested_passes_on_sample(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
) -> None:
    """All 6 sample tables pass Pandera validation with config-aware validation."""
    counts = validate_all_ingested(sample_ingested, config=sample_config)
    assert counts["otp_marketing_flights"] == 80
    assert counts["t100_segments"] == 1472
    assert counts["bts_lookups"] == 810
    assert counts["faa_acftref"] == 19
    assert counts["faa_aircraft_registry"] == 17
    assert counts["tsa_daily_throughput"] == 62


@pytest.mark.parametrize(
    "mutator",
    [
        lambda df: df.assign(crs_dep_time="2400"),
        lambda df: df.assign(is_cancelled=False, cancellation_code=None, tail_num=None),
        lambda df: df.assign(is_cancelled=False, cancellation_code=None, dep_time=None),
        lambda df: df.assign(
            is_cancelled=False, is_diverted=False, cancellation_code=None, arr_delay_min=None
        ),
        lambda df: df.assign(is_cancelled=True, cancellation_code=None),
        lambda df: df.assign(is_cancelled=False, cancellation_code="A"),
        lambda df: df.assign(hour_block="invalid_block"),
        lambda df: df.assign(flight_date="2024/01/01"),
    ],
)
def test_otp_schema_rejects_malformed_rows(
    sample_ingested: dict[str, pd.DataFrame],
    mutator,
) -> None:
    """`OtpMarketingFlightsSchema` rejects `'2400'`, invalid nulls, bad dates, and invalid hour blocks."""
    base = (
        sample_ingested["otp_marketing_flights"]
        .loc[
            (~sample_ingested["otp_marketing_flights"]["is_cancelled"])
            & (~sample_ingested["otp_marketing_flights"]["is_diverted"])
        ]
        .head(1)
        .copy()
    )
    bad = mutator(base)
    with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
        OtpMarketingFlightsSchema.validate(bad, lazy=True)


def test_otp_schema_supports_yaml_expansion_without_hardcoded_blocks(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
) -> None:
    """Expanding hub, marketing carrier, year, or hour_block in YAML passes both schemas."""
    row = (
        sample_ingested["otp_marketing_flights"]
        .loc[
            (~sample_ingested["otp_marketing_flights"]["is_cancelled"])
            & (~sample_ingested["otp_marketing_flights"]["is_diverted"])
        ]
        .head(1)
        .copy()
    )
    row["year"] = 2027
    row["mkt_carrier"] = "DL"
    row["op_carrier"] = "DL"
    row["origin"] = "DEN"

    # Module-level schema allows valid future years, carriers, and hubs
    OtpMarketingFlightsSchema.validate(row, lazy=True)

    # Config-bound schema enforces custom YAML configuration
    expanded_cfg = StandbyConfig(
        project_name=sample_config.project_name,
        project_version=sample_config.project_version,
        repo_root=sample_config.repo_root,
        hubs=["DEN"],
        active_hub="DEN",
        marketing_carriers=["DL"],
        mainline_carriers=["DL"],
        express_carriers=[],
        years=[2027],
        raw_dir=sample_config.raw_dir,
        sample_dir=sample_config.sample_dir,
        manifest_path=sample_config.manifest_path,
        sample_manifest_path=sample_config.sample_manifest_path,
        processed_dir=sample_config.processed_dir,
        reference_dir=sample_config.reference_dir,
        exports_dir=sample_config.exports_dir,
        hour_blocks={
            "red_eye": HourBlockSpec(name="red_eye", start_hour=0, end_hour=6, load_multiplier=0.9),
            **sample_config.hour_blocks,
        },
        index_weights=sample_config.index_weights,
        bigquery=sample_config.bigquery,
    )
    row["hour_block"] = "red_eye"
    build_otp_schema(expanded_cfg).validate(row, lazy=True)


def test_other_schemas_reject_invalid_rows(sample_ingested: dict[str, pd.DataFrame]) -> None:
    """T-100, Lookup, FAA ACFTREF, and TSA schemas reject contract violations."""
    t100_bad = sample_ingested["t100_segments"].head(1).copy()
    t100_bad["service_class"] = "L"
    with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
        T100SegmentsSchema.validate(t100_bad, lazy=True)

    lookup_bad = sample_ingested["bts_lookups"].head(1).copy()
    lookup_bad["code"] = ""
    with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
        BtsLookupSchema.validate(lookup_bad, lazy=True)

    acft_bad = pd.concat(
        [sample_ingested["faa_acftref"].head(1), sample_ingested["faa_acftref"].head(1)],
        ignore_index=True,
    )
    with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
        FaaAcftrefSchema.validate(acft_bad, lazy=True)

    tsa_bad = sample_ingested["tsa_daily_throughput"].head(1).copy()
    tsa_bad["tsa_throughput"] = 0
    with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
        TsaThroughputSchema.validate(tsa_bad, lazy=True)


def test_otp_schema_rejects_blank_or_invalid_cancellation_code_and_empty_config_years(
    sample_ingested: dict[str, pd.DataFrame],
    sample_config: StandbyConfig,
) -> None:
    """OTP schema rejects blank/invalid cancellation codes, invalid hhmm like `'2599'`, and empty `config.years`."""
    otp_df = sample_ingested["otp_marketing_flights"]
    performed_row = otp_df.loc[(~otp_df["is_cancelled"]) & (~otp_df["is_diverted"])].head(1).copy()

    for bad_code in ("", "   ", "Z", " A "):
        bad_canc = performed_row.assign(is_cancelled=True, cancellation_code=bad_code)
        with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
            OtpMarketingFlightsSchema.validate(bad_canc, lazy=True)

    for time_col in ("crs_dep_time", "dep_time", "crs_arr_time", "arr_time"):
        bad_hhmm = performed_row.assign(**{time_col: "2599"})
        with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
            OtpMarketingFlightsSchema.validate(bad_hhmm, lazy=True)

    empty_years_cfg = StandbyConfig(
        project_name=sample_config.project_name,
        project_version=sample_config.project_version,
        repo_root=sample_config.repo_root,
        hubs=list(sample_config.hubs),
        active_hub=sample_config.active_hub,
        marketing_carriers=list(sample_config.marketing_carriers),
        mainline_carriers=list(sample_config.mainline_carriers),
        express_carriers=list(sample_config.express_carriers),
        years=[],
        raw_dir=sample_config.raw_dir,
        sample_dir=sample_config.sample_dir,
        manifest_path=sample_config.manifest_path,
        sample_manifest_path=sample_config.sample_manifest_path,
        processed_dir=sample_config.processed_dir,
        reference_dir=sample_config.reference_dir,
        exports_dir=sample_config.exports_dir,
        hour_blocks=dict(sample_config.hour_blocks),
        index_weights=dict(sample_config.index_weights),
        bigquery=sample_config.bigquery,
    )
    with pytest.raises((pa_errors.SchemaError, pa_errors.SchemaErrors)):
        build_otp_schema(empty_years_cfg).validate(performed_row, lazy=True)
