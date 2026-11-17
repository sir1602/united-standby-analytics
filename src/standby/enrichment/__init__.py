"""Phase 2 flight-level enrichment package (`src/standby/enrichment/`)."""

from standby.enrichment.aircraft import (
    AircraftResolver,
    FaaMatchMetrics,
    load_faa_t100_crosswalk,
    load_seat_config,
    load_tail_overrides,
    normalize_tail_num,
)
from standby.enrichment.calculations import compute_flight_estimates, compute_tsa_daily_multipliers
from standby.enrichment.confidence import (
    COLUMN_CONFIDENCE_TIERS,
    summarize_confidence_tiers,
    tag_dataframe_confidence_tiers,
)
from standby.enrichment.load_factor import (
    LF_SOURCE_L1,
    LF_SOURCE_L2,
    LF_SOURCE_L3,
    LF_SOURCE_UNMATCHED,
    T100LoadFactorJoiner,
)
from standby.enrichment.pipeline import EnrichmentResult, enrich_flights, run_enrichment_pipeline

__all__ = [
    "AircraftResolver",
    "COLUMN_CONFIDENCE_TIERS",
    "EnrichmentResult",
    "FaaMatchMetrics",
    "LF_SOURCE_L1",
    "LF_SOURCE_L2",
    "LF_SOURCE_L3",
    "LF_SOURCE_UNMATCHED",
    "T100LoadFactorJoiner",
    "compute_flight_estimates",
    "compute_tsa_daily_multipliers",
    "enrich_flights",
    "load_faa_t100_crosswalk",
    "load_seat_config",
    "load_tail_overrides",
    "normalize_tail_num",
    "run_enrichment_pipeline",
    "summarize_confidence_tiers",
    "tag_dataframe_confidence_tiers",
]
