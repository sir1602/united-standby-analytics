"""Configuration loader for `config/standby.yaml` and CLI overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

YEAR_TOKEN_RE = re.compile(r"^\d{4}$")

DEFAULT_UA_FAMILY_CARRIERS: frozenset[str] = frozenset(
    {"UA", "OO", "YX", "YV", "G7", "C5", "ZW", "EV", "AX"}
)


@dataclass(frozen=True)
class HourBlockSpec:
    """Definition of a departure hour block and its demand load multiplier."""

    name: str
    start_hour: int
    end_hour: int
    load_multiplier: float


@dataclass(frozen=True)
class BigQueryConfig:
    """BigQuery project, dataset, and table configuration."""

    project_id: str
    location: str
    partition_expiration_ms: int | None
    datasets: dict[str, str]
    tables: dict[str, str]


@dataclass(frozen=True)
class EnrichmentConfig:
    """Configuration parameters for Phase 2 flight-level enrichment."""

    tsa_damping_alpha: float = 0.35
    tsa_multiplier_bounds: tuple[float, float] = (0.80, 1.20)
    min_faa_tc_max_seats: int = 20
    commercial_mfr_prefixes: tuple[str, ...] = (
        "BOEING",
        "AIRBUS",
        "EMBRAER",
        "YABORA",
        "BOMBARDIER",
        "CANADAIR",
        "MHI RJ",
    )
    dedicated_express_carriers: tuple[str, ...] = ("G7", "EV", "AX", "C5")
    shared_cpa_express_carriers: tuple[str, ...] = ("OO", "YX", "YV", "ZW")
    hour_block_multipliers: dict[str, float] = field(
        default_factory=lambda: {
            "early": 0.96,
            "morning": 1.03,
            "midday": 1.00,
            "afternoon": 1.02,
            "evening": 1.04,
            "late": 0.94,
        }
    )


@dataclass(frozen=True)
class SanityCheckConfig:
    """Thresholds for Section 7 automated sanity checks (`SC-1`..`SC-5`)."""

    departure_reconciliation_tolerance: float = 0.10
    seats_per_dep_warn_tolerance: float = 0.10
    seats_per_dep_fail_tolerance: float = 0.15
    system_lf_warn_bounds: tuple[float, float] = (0.80, 0.86)
    system_lf_fail_bounds: tuple[float, float] = (0.75, 0.90)
    sample_lf_fail_bounds: tuple[float, float] = (0.70, 0.92)
    sample_min_t100_rows: int = 500


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration parameters for Phase 4 Standby Opportunity Index scoring."""

    open_seats_abs_cap: float = 25.0
    open_seats_pct_cap: float = 0.30
    open_seats_abs_weight: float = 0.50
    disp_diversion_weight: float = 0.50
    disp_decay_base: float = 0.75
    disp_hist_cap: float = 2.0
    disp_plan_prior_factor: float = 0.65
    disp_plan_cap: float = 0.25
    disp_plan_min_obs: int = 5
    backup_max_flights: int = 6
    demand_holiday_adder: float = 0.10
    demand_ratio_min: float = 0.65
    demand_ratio_max: float = 1.35


@dataclass(frozen=True)
class ExportConfig:
    """Configuration parameters for Phase 5 Tableau CSV exports."""

    tableau_flight_mode: str = "stratified"
    tableau_flight_max_rows: int = 100_000
    tableau_stratify_seed: int = 42
    tableau_float_precision: int = 4
    tableau_max_file_mb: float = 50.0


@dataclass
class StandbyConfig:
    """Typed pipeline configuration resolved from `config/standby.yaml`."""

    project_name: str
    project_version: str
    repo_root: Path
    hubs: list[str]
    active_hub: str
    marketing_carriers: list[str]
    mainline_carriers: list[str]
    express_carriers: list[str]
    years: list[int]
    raw_dir: Path
    sample_dir: Path
    manifest_path: Path
    sample_manifest_path: Path
    processed_dir: Path
    reference_dir: Path
    exports_dir: Path
    hour_blocks: dict[str, HourBlockSpec]
    index_weights: dict[str, float]
    bigquery: BigQueryConfig
    enrichment: EnrichmentConfig = field(default_factory=EnrichmentConfig)
    sanity_checks: SanityCheckConfig = field(default_factory=SanityCheckConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    sample_mode: bool = False
    dry_run: bool = False
    raw_yaml: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def all_operating_carriers(self) -> set[str]:
        """Set of all configured mainline and United Express operating carriers."""
        return set(self.mainline_carriers) | set(self.express_carriers)

    @property
    def active_data_dir(self) -> Path:
        """Returns `sample_dir` when `sample_mode` is True, else `raw_dir`."""
        return self.sample_dir if self.sample_mode else self.raw_dir

    @property
    def active_manifest_path(self) -> Path:
        """Returns `sample_manifest_path` when `sample_mode` is True, else `manifest_path`."""
        return self.sample_manifest_path if self.sample_mode else self.manifest_path

    def get_hour_block(self, dep_hour: int) -> str:
        """Map an integer departure hour (0..23) to its configured hour_block name."""
        hour = int(dep_hour) % 24
        for name, spec in self.hour_blocks.items():
            if spec.start_hour < spec.end_hour:
                if spec.start_hour <= hour < spec.end_hour:
                    return name
            elif spec.start_hour > spec.end_hour:
                if hour >= spec.start_hour or hour < spec.end_hour:
                    return name
            else:
                return name
        if self.hour_blocks:
            return "late" if "late" in self.hour_blocks else next(reversed(self.hour_blocks))
        raise ValueError(f"Unable to map dep_hour={dep_hour}: hour_blocks config is empty")

    def get_hour_multiplier(self, hour_block: str) -> float:
        """Return the configured load multiplier for a given hour block."""
        if hour_block in self.enrichment.hour_block_multipliers:
            return float(self.enrichment.hour_block_multipliers[hour_block])
        spec = self.hour_blocks.get(hour_block)
        return spec.load_multiplier if spec is not None else 1.0

    def get_index_weights(self) -> dict[str, float]:
        """Return validated/normalized SOI component weights."""
        defaults = {
            "open_seats": 0.50,
            "displacement_pressure": 0.20,
            "backup_options": 0.20,
            "day_holiday_demand": 0.10,
        }
        raw = {
            k: float(self.index_weights.get(k, default_val)) for k, default_val in defaults.items()
        }
        total = sum(raw.values())
        if total <= 0.0:
            raise ValueError(f"SOI index_weights sum must be positive, got {raw}")
        if any(v < 0.0 for v in raw.values()):
            raise ValueError(f"SOI index_weights must be non-negative, got {raw}")
        return {k: v / total for k, v in raw.items()}


def parse_years_arg(years_str: str | None, default_years: list[int]) -> list[int]:
    """Parse a comma/range string such as `'2019,2023-2026'` into sorted unique years."""
    if years_str is None:
        resolved = sorted({int(y) for y in default_years})
        if not resolved:
            raise ValueError("Default years configuration must not be empty")
        return resolved
    if not years_str.strip():
        raise ValueError(f"Invalid --years value {years_str!r}: must not be empty")

    years: set[int] = set()
    for raw_part in years_str.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"Invalid --years value {years_str!r}: empty comma-separated segment")
        if "-" in part:
            pieces = [p.strip() for p in part.split("-")]
            if len(pieces) != 2:
                raise ValueError(
                    f"Invalid year range {part!r} in {years_str!r}: expected YYYY-YYYY"
                )
            start_s, end_s = pieces
            if not YEAR_TOKEN_RE.fullmatch(start_s) or not YEAR_TOKEN_RE.fullmatch(end_s):
                raise ValueError(
                    f"Invalid year range {part!r} in {years_str!r}: expected 4-digit YYYY-YYYY"
                )
            start_y, end_y = int(start_s), int(end_s)
            if start_y > end_y:
                raise ValueError(
                    f"Invalid year range {part!r} in {years_str!r}: "
                    f"start year {start_y} > end year {end_y}"
                )
            years.update(range(start_y, end_y + 1))
        else:
            if not YEAR_TOKEN_RE.fullmatch(part):
                raise ValueError(
                    f"Invalid year token {part!r} in {years_str!r}: expected 4-digit YYYY"
                )
            years.add(int(part))

    if not years:
        raise ValueError(f"No valid years parsed from {years_str!r}")
    return sorted(years)


def load_config(
    config_path: Path | str | None = None,
    repo_root: Path | str | None = None,
    hub: str | None = None,
    years: str | list[int] | None = None,
    sample_mode: bool = False,
    dry_run: bool = False,
) -> StandbyConfig:
    """Load `config/standby.yaml` and apply any runtime CLI overrides."""
    if repo_root is None:
        repo_root_path = Path(__file__).resolve().parents[2]
    else:
        repo_root_path = Path(repo_root).resolve()

    cfg_file = Path(config_path) if config_path else repo_root_path / "config" / "standby.yaml"
    if not cfg_file.is_absolute():
        cfg_file = repo_root_path / cfg_file

    with open(cfg_file, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    proj = raw.get("project", {})
    scope = raw.get("scope", {})
    paths = raw.get("paths", {})
    hb_raw = raw.get("hour_blocks", {})
    weights = raw.get("index_weights", {})
    bq_raw = raw.get("bigquery", {})

    default_years = [int(y) for y in scope.get("years", [2019, 2023, 2024, 2025, 2026])]
    if isinstance(years, str):
        resolved_years = parse_years_arg(years, default_years)
    elif isinstance(years, list):
        resolved_years = sorted({int(y) for y in years})
    elif years is None:
        resolved_years = sorted(set(default_years))
    else:
        raise TypeError(f"Invalid years argument type: {type(years).__name__}")
    if not resolved_years:
        raise ValueError("Resolved years configuration must not be empty")

    active_hub = (hub or scope.get("default_hub", "ORD")).strip().upper()
    op_carriers = scope.get("operating_carriers", {})

    hour_blocks: dict[str, HourBlockSpec] = {}
    for name, spec_dict in hb_raw.items():
        hour_blocks[name] = HourBlockSpec(
            name=name,
            start_hour=int(spec_dict["start_hour"]),
            end_hour=int(spec_dict["end_hour"]),
            load_multiplier=float(spec_dict["load_multiplier"]),
        )

    bq_config = BigQueryConfig(
        project_id=str(bq_raw.get("project_id", "united-standby-analytics")),
        location=str(bq_raw.get("location", "US")),
        partition_expiration_ms=bq_raw.get("partition_expiration_ms", None),
        datasets=dict(bq_raw.get("datasets", {})),
        tables=dict(bq_raw.get("tables", {})),
    )

    enr_raw = raw.get("enrichment", {})
    default_enr = EnrichmentConfig()
    tsa_bounds_raw = enr_raw.get("tsa_multiplier_bounds", list(default_enr.tsa_multiplier_bounds))
    hb_mults_raw = enr_raw.get("hour_block_multipliers")
    if isinstance(hb_mults_raw, dict):
        hb_mults = {str(k): float(v) for k, v in hb_mults_raw.items()}
    else:
        hb_mults = {name: spec.load_multiplier for name, spec in hour_blocks.items()} or dict(
            default_enr.hour_block_multipliers
        )

    enrichment_config = EnrichmentConfig(
        tsa_damping_alpha=float(enr_raw.get("tsa_damping_alpha", default_enr.tsa_damping_alpha)),
        tsa_multiplier_bounds=(float(tsa_bounds_raw[0]), float(tsa_bounds_raw[1])),
        min_faa_tc_max_seats=int(
            enr_raw.get("min_faa_tc_max_seats", default_enr.min_faa_tc_max_seats)
        ),
        commercial_mfr_prefixes=tuple(
            str(p).strip().upper()
            for p in enr_raw.get("commercial_mfr_prefixes", default_enr.commercial_mfr_prefixes)
        ),
        dedicated_express_carriers=tuple(
            str(c).strip().upper()
            for c in enr_raw.get(
                "dedicated_express_carriers", default_enr.dedicated_express_carriers
            )
        ),
        shared_cpa_express_carriers=tuple(
            str(c).strip().upper()
            for c in enr_raw.get(
                "shared_cpa_express_carriers", default_enr.shared_cpa_express_carriers
            )
        ),
        hour_block_multipliers=hb_mults,
    )

    sc_raw = raw.get("sanity_checks", {})
    default_sc = SanityCheckConfig()
    warn_b = sc_raw.get("system_lf_warn_bounds", list(default_sc.system_lf_warn_bounds))
    fail_b = sc_raw.get("system_lf_fail_bounds", list(default_sc.system_lf_fail_bounds))
    samp_b = sc_raw.get("sample_lf_fail_bounds", list(default_sc.sample_lf_fail_bounds))
    sanity_config = SanityCheckConfig(
        departure_reconciliation_tolerance=float(
            sc_raw.get(
                "departure_reconciliation_tolerance",
                default_sc.departure_reconciliation_tolerance,
            )
        ),
        seats_per_dep_warn_tolerance=float(
            sc_raw.get("seats_per_dep_warn_tolerance", default_sc.seats_per_dep_warn_tolerance)
        ),
        seats_per_dep_fail_tolerance=float(
            sc_raw.get("seats_per_dep_fail_tolerance", default_sc.seats_per_dep_fail_tolerance)
        ),
        system_lf_warn_bounds=(float(warn_b[0]), float(warn_b[1])),
        system_lf_fail_bounds=(float(fail_b[0]), float(fail_b[1])),
        sample_lf_fail_bounds=(float(samp_b[0]), float(samp_b[1])),
        sample_min_t100_rows=int(
            sc_raw.get("sample_min_t100_rows", default_sc.sample_min_t100_rows)
        ),
    )

    scr_raw = raw.get("scoring", {})
    default_scr = ScoringConfig()
    scoring_config = ScoringConfig(
        open_seats_abs_cap=float(scr_raw.get("open_seats_abs_cap", default_scr.open_seats_abs_cap)),
        open_seats_pct_cap=float(scr_raw.get("open_seats_pct_cap", default_scr.open_seats_pct_cap)),
        open_seats_abs_weight=float(
            scr_raw.get("open_seats_abs_weight", default_scr.open_seats_abs_weight)
        ),
        disp_diversion_weight=float(
            scr_raw.get("disp_diversion_weight", default_scr.disp_diversion_weight)
        ),
        disp_decay_base=float(scr_raw.get("disp_decay_base", default_scr.disp_decay_base)),
        disp_hist_cap=float(scr_raw.get("disp_hist_cap", default_scr.disp_hist_cap)),
        disp_plan_prior_factor=float(
            scr_raw.get("disp_plan_prior_factor", default_scr.disp_plan_prior_factor)
        ),
        disp_plan_cap=float(scr_raw.get("disp_plan_cap", default_scr.disp_plan_cap)),
        disp_plan_min_obs=int(scr_raw.get("disp_plan_min_obs", default_scr.disp_plan_min_obs)),
        backup_max_flights=int(scr_raw.get("backup_max_flights", default_scr.backup_max_flights)),
        demand_holiday_adder=float(
            scr_raw.get("demand_holiday_adder", default_scr.demand_holiday_adder)
        ),
        demand_ratio_min=float(scr_raw.get("demand_ratio_min", default_scr.demand_ratio_min)),
        demand_ratio_max=float(scr_raw.get("demand_ratio_max", default_scr.demand_ratio_max)),
    )

    exp_raw = raw.get("export", {})
    default_exp = ExportConfig()
    export_config = ExportConfig(
        tableau_flight_mode=str(
            exp_raw.get("tableau_flight_mode", default_exp.tableau_flight_mode)
        ).strip(),
        tableau_flight_max_rows=int(
            exp_raw.get("tableau_flight_max_rows", default_exp.tableau_flight_max_rows)
        ),
        tableau_stratify_seed=int(
            exp_raw.get("tableau_stratify_seed", default_exp.tableau_stratify_seed)
        ),
        tableau_float_precision=int(
            exp_raw.get("tableau_float_precision", default_exp.tableau_float_precision)
        ),
        tableau_max_file_mb=float(
            exp_raw.get("tableau_max_file_mb", default_exp.tableau_max_file_mb)
        ),
    )

    return StandbyConfig(
        project_name=str(proj.get("name", "united-standby-analytics")),
        project_version=str(proj.get("version", "0.1.0")),
        repo_root=repo_root_path,
        hubs=[str(h) for h in scope.get("hubs", ["ORD"])],
        active_hub=active_hub,
        marketing_carriers=[str(c) for c in scope.get("marketing_carriers", ["UA"])],
        mainline_carriers=[str(c) for c in op_carriers.get("mainline", ["UA"])],
        express_carriers=[str(c) for c in op_carriers.get("express", [])],
        years=resolved_years,
        raw_dir=repo_root_path / paths.get("raw_dir", "data/raw"),
        sample_dir=repo_root_path / paths.get("sample_dir", "data/sample"),
        manifest_path=repo_root_path / paths.get("manifest_path", "data/manifest.csv"),
        sample_manifest_path=repo_root_path
        / paths.get("sample_manifest_path", "data/sample/manifest.csv"),
        processed_dir=repo_root_path / paths.get("processed_dir", "data/processed"),
        reference_dir=repo_root_path / paths.get("reference_dir", "reference"),
        exports_dir=repo_root_path / paths.get("exports_dir", "exports/tableau"),
        hour_blocks=hour_blocks,
        index_weights={k: float(v) for k, v in weights.items()},
        bigquery=bq_config,
        enrichment=enrichment_config,
        sanity_checks=sanity_config,
        scoring=scoring_config,
        export=export_config,
        sample_mode=sample_mode,
        dry_run=dry_run,
        raw_yaml=raw,
    )
