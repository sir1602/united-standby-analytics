"""Pandera DataFrameSchemas for all ingested aviation datasets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from standby.schemas.faa import FaaAcftrefSchema, FaaMasterDeregSchema
from standby.schemas.lookups import BtsLookupSchema
from standby.schemas.otp import OtpMarketingFlightsSchema, build_otp_schema
from standby.schemas.t100 import T100SegmentsSchema
from standby.schemas.tsa import TsaThroughputSchema

if TYPE_CHECKING:
    from standby.config import StandbyConfig

__all__ = [
    "OtpMarketingFlightsSchema",
    "build_otp_schema",
    "T100SegmentsSchema",
    "BtsLookupSchema",
    "FaaMasterDeregSchema",
    "FaaAcftrefSchema",
    "TsaThroughputSchema",
    "validate_all_ingested",
]


def validate_all_ingested(
    ingested: dict[str, pd.DataFrame],
    config: StandbyConfig | None = None,
) -> dict[str, Any]:
    """Validate all ingested DataFrames against their respective Pandera schemas."""
    otp_schema = build_otp_schema(config) if config is not None else OtpMarketingFlightsSchema
    otp_schema.validate(ingested["otp_marketing_flights"], lazy=True)
    T100SegmentsSchema.validate(ingested["t100_segments"], lazy=True)
    BtsLookupSchema.validate(ingested["bts_lookups"], lazy=True)
    FaaMasterDeregSchema.validate(ingested["faa_aircraft_registry"], lazy=True)
    FaaAcftrefSchema.validate(ingested["faa_acftref"], lazy=True)
    TsaThroughputSchema.validate(ingested["tsa_daily_throughput"], lazy=True)
    return {name: len(df) for name, df in ingested.items()}
