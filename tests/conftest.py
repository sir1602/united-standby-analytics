"""Shared pytest fixtures for Phase 1 ingestion, manifest, schema, and CLI tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from standby.config import StandbyConfig, load_config
from standby.ingest import ingest_all_sources

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the repository root path."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def sample_dir(repo_root: Path) -> Path:
    """Return the committed CI sample directory (`data/sample`)."""
    return repo_root / "data" / "sample"


@pytest.fixture(scope="session")
def sample_manifest_path(sample_dir: Path) -> Path:
    """Return the committed sample manifest path (`data/sample/manifest.csv`)."""
    return sample_dir / "manifest.csv"


@pytest.fixture()
def sample_config(repo_root: Path, tmp_path: Path) -> StandbyConfig:
    """Return a `StandbyConfig` configured for `--sample --dry-run` with isolated `processed_dir`."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    cfg.processed_dir = tmp_path / "processed"
    return cfg


@pytest.fixture(scope="session")
def sample_ingested(repo_root: Path) -> dict[str, pd.DataFrame]:
    """Ingest all 5 sample data sources once per test session."""
    cfg = load_config(repo_root=repo_root, sample_mode=True, dry_run=True)
    return ingest_all_sources(cfg)
