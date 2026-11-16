"""Configuration loader for `config/standby.yaml` and CLI overrides."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

YEAR_TOKEN_RE = re.compile(r"^\d{4}$")


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
        spec = self.hour_blocks.get(hour_block)
        return spec.load_multiplier if spec is not None else 1.0


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
        sample_mode=sample_mode,
        dry_run=dry_run,
        raw_yaml=raw,
    )
