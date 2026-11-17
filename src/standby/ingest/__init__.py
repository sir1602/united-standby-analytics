"""Orchestrator for ingesting all five aviation data sources."""

from __future__ import annotations

import pandas as pd

from standby.config import StandbyConfig
from standby.ingest.faa import ingest_faa_acftref, ingest_faa_registry
from standby.ingest.lookups import ingest_lookups
from standby.ingest.otp import ingest_otp
from standby.ingest.t100 import ingest_t100
from standby.ingest.tsa import ingest_tsa

__all__ = [
    "ingest_otp",
    "ingest_t100",
    "ingest_lookups",
    "ingest_faa_acftref",
    "ingest_faa_registry",
    "ingest_tsa",
    "ingest_all_sources",
]


def ingest_all_sources(config: StandbyConfig) -> dict[str, pd.DataFrame]:
    """Ingest and standardize all raw/sample sources according to `config`."""
    data_dir = config.active_data_dir
    otp_df = ingest_otp(
        data_dir=data_dir,
        hub=config.active_hub,
        marketing_carrier=config.marketing_carriers,
        years=config.years,
        operating_carriers=config.all_operating_carriers or None,
        hour_blocks=config.hour_blocks or None,
    )
    t100_df = ingest_t100(
        data_dir=data_dir,
        hub=config.active_hub,
        years=config.years,
    )
    lookups_df = ingest_lookups(data_dir=data_dir)
    acftref_df = ingest_faa_acftref(data_dir=data_dir)
    faa_reg_df = ingest_faa_registry(
        data_dir=data_dir,
        acftref_df=acftref_df,
        commercial_only=True,
    )
    tsa_df = ingest_tsa(data_dir=data_dir, years=config.years)

    return {
        "otp_marketing_flights": otp_df,
        "t100_segments": t100_df,
        "bts_lookups": lookups_df,
        "faa_acftref": acftref_df,
        "faa_aircraft_registry": faa_reg_df,
        "tsa_daily_throughput": tsa_df,
    }
