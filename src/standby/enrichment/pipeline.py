"""End-to-end Phase 2 flight-level enrichment orchestrator (Steps 1-5)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from standby.config import StandbyConfig
from standby.enrichment.aircraft import (
    AircraftResolver,
    FaaMatchMetrics,
    load_faa_t100_crosswalk,
    load_seat_config,
    load_tail_overrides,
)
from standby.enrichment.calculations import compute_flight_estimates
from standby.enrichment.confidence import (
    summarize_confidence_tiers,
    tag_dataframe_confidence_tiers,
)
from standby.enrichment.load_factor import DEFAULT_UA_FAMILY_CARRIERS, T100LoadFactorJoiner
from standby.ingest.faa import COMMERCIAL_MFR_PREFIXES
from standby.ingest.t100 import ingest_t100_system_carrier_summary
from standby.validation.sanity import SanityCheckReport, run_all_sanity_checks

logger = logging.getLogger("standby.enrichment.pipeline")


@dataclass
class EnrichmentResult:
    """Structured output of the Phase 2 flight-level enrichment pipeline."""

    enriched_flights: pd.DataFrame
    faa_match_metrics: FaaMatchMetrics
    sanity_report: SanityCheckReport
    summary: dict[str, Any]
    summary_path: Path | None = None


def _numeric_col_stats(series: pd.Series) -> dict[str, Any]:
    non_null = series.dropna().astype("float64")
    if non_null.empty:
        return {"non_null": 0, "mean": None, "min": None, "median": None, "max": None}
    return {
        "non_null": int(len(non_null)),
        "mean": round(float(non_null.mean()), 4),
        "min": round(float(non_null.min()), 4),
        "median": round(float(non_null.median()), 4),
        "max": round(float(non_null.max()), 4),
    }


def build_enrichment_summary(
    enriched_df: pd.DataFrame,
    faa_metrics: FaaMatchMetrics,
    sanity_report: SanityCheckReport,
    config: StandbyConfig | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable summary of Phase 2 enrichment metrics and sanity checks."""
    performed = enriched_df[~enriched_df["is_cancelled"].astype(bool)]
    stat_cols = (
        "seats_config",
        "est_load_factor",
        "est_passengers",
        "est_open_seats",
        "tsa_demand_multiplier",
        "hour_block_multiplier",
        "est_lf_adjusted",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
    )
    return {
        "hub": config.active_hub if config is not None else "ORD",
        "years": list(config.years)
        if config is not None
        else sorted(enriched_df["year"].unique().tolist()),
        "sample_mode": bool(config.sample_mode) if config is not None else False,
        "total_flights": int(len(enriched_df)),
        "performed_flights": int(len(performed)),
        "cancelled_flights": int(enriched_df["is_cancelled"].astype(bool).sum()),
        "faa_match_metrics": faa_metrics.to_dict(),
        "aircraft_source_counts_all": {
            str(k): int(v)
            for k, v in enriched_df["aircraft_source"].value_counts().to_dict().items()
        },
        "aircraft_source_counts_performed": {
            str(k): int(v) for k, v in performed["aircraft_source"].value_counts().to_dict().items()
        },
        "lf_source_counts_all": {
            str(k): int(v) for k, v in enriched_df["lf_source"].value_counts().to_dict().items()
        },
        "lf_source_counts_performed": {
            str(k): int(v) for k, v in performed["lf_source"].value_counts().to_dict().items()
        },
        "shared_regional_spoke_flights": int(enriched_df["is_shared_regional_spoke"].sum()),
        "estimate_summary_performed": {
            col: _numeric_col_stats(performed[col]) for col in stat_cols if col in performed.columns
        },
        "confidence_tiers": summarize_confidence_tiers(enriched_df),
        "sanity_checks": sanity_report.to_dict(),
    }


def enrich_flights(
    otp_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    faa_registry_df: pd.DataFrame,
    faa_acftref_df: pd.DataFrame,
    tsa_df: pd.DataFrame,
    reference_dir: Path | None = None,
    seat_config_df: pd.DataFrame | None = None,
    crosswalk_df: pd.DataFrame | None = None,
    tail_overrides_df: pd.DataFrame | None = None,
    system_summary_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
    raise_on_sanity_failure: bool = True,
) -> EnrichmentResult:
    """
    Execute Steps 1-5 of Phase 2 flight-level enrichment on ingested DataFrames:
      1. 3-tier aircraft & seat resolution (`TAIL_OVERRIDE -> FAA_MASTER -> FAA_DEREG`)
      2. 3-tier T-100 domestic load factor join (`CARRIER_ROUTE_MONTH_TYPE -> CARRIER_ROUTE_MONTH -> ROUTE_MONTH -> UNMATCHED`)
      3. Base & separate modeled adjusted estimates (`est_*` & `est_*_adjusted`)
      4. Confidence-tier metadata tagging (`actual`, `derived`, `estimated`)
      5. Section 7 sanity checks (`SC-1`..`SC-5`)
    """
    ref_dir = reference_dir or (config.reference_dir if config is not None else Path("reference"))
    sc_df = seat_config_df if seat_config_df is not None else load_seat_config(ref_dir)
    cw_df = crosswalk_df if crosswalk_df is not None else load_faa_t100_crosswalk(ref_dir)
    to_df = tail_overrides_df if tail_overrides_df is not None else load_tail_overrides(ref_dir)

    min_tc_seats = config.enrichment.min_faa_tc_max_seats if config is not None else 20
    comm_prefixes = (
        config.enrichment.commercial_mfr_prefixes if config is not None else COMMERCIAL_MFR_PREFIXES
    )
    hub = config.active_hub if config is not None else "ORD"
    ua_family = config.all_operating_carriers if config is not None else DEFAULT_UA_FAMILY_CARRIERS
    shared_cpa = (
        config.enrichment.shared_cpa_express_carriers
        if config is not None
        else ("OO", "YX", "YV", "ZW")
    )
    dep_tol = (
        config.sanity_checks.departure_reconciliation_tolerance if config is not None else 0.10
    )

    # Step 1: Aircraft & seat config resolution
    resolver = AircraftResolver(
        seat_config_df=sc_df,
        crosswalk_df=cw_df,
        tail_overrides_df=to_df,
        faa_registry_df=faa_registry_df,
        faa_acftref_df=faa_acftref_df,
        min_faa_tc_max_seats=min_tc_seats,
        commercial_mfr_prefixes=comm_prefixes,
    )
    df_aircraft, faa_metrics = resolver.resolve(otp_df)

    # Step 2: 3-tier T-100 domestic load factor join
    lf_joiner = T100LoadFactorJoiner(
        t100_df=t100_df,
        hub=hub,
        ua_family_carriers=ua_family,
        shared_cpa_carriers=shared_cpa,
        shared_spoke_tolerance=dep_tol,
    )
    df_lf = lf_joiner.join(df_aircraft)

    # Step 3: Base & modeled adjusted calculations
    df_calc = compute_flight_estimates(df_lf, tsa_df=tsa_df, config=config)

    # Step 4: Confidence-tier tagging
    df_tagged = tag_dataframe_confidence_tiers(df_calc)

    # Step 5: Section 7 Sanity Checks
    sanity_report = run_all_sanity_checks(
        enriched_df=df_tagged,
        t100_df=t100_df,
        seat_config_df=sc_df,
        crosswalk_df=cw_df,
        tail_overrides_df=to_df,
        system_summary_df=system_summary_df,
        config=config,
        raise_on_failure=raise_on_sanity_failure,
    )

    summary = build_enrichment_summary(
        enriched_df=df_tagged,
        faa_metrics=faa_metrics,
        sanity_report=sanity_report,
        config=config,
    )
    return EnrichmentResult(
        enriched_flights=df_tagged,
        faa_match_metrics=faa_metrics,
        sanity_report=sanity_report,
        summary=summary,
    )


def run_enrichment_pipeline(
    ingested: dict[str, pd.DataFrame],
    config: StandbyConfig,
    summary_path: Path | None = None,
) -> EnrichmentResult:
    """Run Phase 2 enrichment on ingested tables using `StandbyConfig` and write summary JSON."""
    t100_dir = config.active_data_dir / "t100"
    system_summary_df: pd.DataFrame | None = None
    if t100_dir.is_dir() and any(t100_dir.glob("t100_segment_all_*.csv")):
        system_summary_df = ingest_t100_system_carrier_summary(
            data_dir=config.active_data_dir,
            carrier=(config.mainline_carriers[0] if config.mainline_carriers else "UA"),
            years=config.years,
        )

    result = enrich_flights(
        otp_df=ingested["otp_marketing_flights"],
        t100_df=ingested["t100_segments"],
        faa_registry_df=ingested["faa_aircraft_registry"],
        faa_acftref_df=ingested["faa_acftref"],
        tsa_df=ingested["tsa_daily_throughput"],
        reference_dir=config.reference_dir,
        system_summary_df=system_summary_df,
        config=config,
        raise_on_sanity_failure=True,
    )

    # Always write a run-scoped summary under processed_dir; optionally also write explicit summary_path
    mode_dir = config.processed_dir / ("sample" if config.sample_mode else "full")
    mode_dir.mkdir(parents=True, exist_ok=True)
    processed_summary = mode_dir / "phase2_enrichment_summary.json"
    processed_summary.write_text(json.dumps(result.summary, indent=2) + "\n", encoding="utf-8")
    result.summary_path = processed_summary

    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result.summary, indent=2) + "\n", encoding="utf-8")
        result.summary_path = summary_path

    logger.info(
        "Phase 2 enrichment pipeline completed",
        extra={
            "extra_fields": {
                "total_flights": result.summary["total_flights"],
                "performed_flights": result.summary["performed_flights"],
                "sanity_passed": result.sanity_report.passed,
                "sanity_warnings": result.sanity_report.warnings_count,
                "summary_path": str(result.summary_path),
            }
        },
    )
    return result
