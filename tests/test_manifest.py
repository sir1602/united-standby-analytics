"""Tests for checksum manifest generation and verification (`standby.manifest`)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from standby.manifest import (
    MANIFEST_COLUMNS,
    ManifestVerificationError,
    build_manifest,
    compute_sha256,
    infer_metadata_for_file,
    verify_manifest,
)


def _write_mini_sample_tree(root: Path) -> tuple[Path, Path]:
    """Create a minimal nested `data/sample/` tree with one OTP file and its manifest."""
    otp_dir = root / "data" / "sample" / "otp"
    otp_dir.mkdir(parents=True, exist_ok=True)
    otp_file = otp_dir / "otp_mkt_ord_ua_2019_01.csv"
    otp_file.write_text("col_a,col_b\n1,2\n3,4\n", encoding="utf-8")

    manifest_path = root / "data" / "sample" / "manifest.csv"
    build_manifest(
        data_dir=root / "data" / "sample",
        output_manifest_path=manifest_path,
        repo_root=root,
        download_date="2026-11-13",
    )
    return manifest_path, otp_file


def test_verify_sample_manifest_passes_with_inferred_root(sample_manifest_path: Path) -> None:
    """Nested `data/sample/manifest.csv` verifies all 13 entries even when `repo_root=None`."""
    report = verify_manifest(sample_manifest_path)
    assert report.passed is True
    assert report.total_entries == 13
    assert report.verified_entries == 13
    assert report.failures == []


def test_verify_manifest_subset_glob_matches_and_empty_raises(sample_manifest_path: Path) -> None:
    """`subset_glob` verifies matching subset and raises `ManifestVerificationError` on 0 matches."""
    report = verify_manifest(sample_manifest_path, subset_glob="data/sample/otp/*")
    assert report.passed is True
    assert report.total_entries == 2
    assert report.verified_entries == 2

    with pytest.raises(ManifestVerificationError, match="No manifest entries matched"):
        verify_manifest(sample_manifest_path, subset_glob="data/sample/nonexistent/*.csv")


def test_verify_manifest_detects_sha256_tampering(tmp_path: Path) -> None:
    """Modifying file content with identical line count fails with `SHA-256 mismatch`."""
    manifest_path, otp_file = _write_mini_sample_tree(tmp_path)
    otp_file.write_text("col_a,col_b\n1,2\n3,9\n", encoding="utf-8")

    with pytest.raises(ManifestVerificationError, match="SHA-256 mismatch"):
        verify_manifest(manifest_path)


def test_verify_manifest_detects_row_count_mismatch(tmp_path: Path) -> None:
    """A valid SHA-256 paired with an incorrect `row_count` fails with `row_count mismatch`."""
    manifest_path, otp_file = _write_mini_sample_tree(tmp_path)
    otp_file.write_text("col_a,col_b\n1,2\n3,4\n5,6\n", encoding="utf-8")
    new_sha = compute_sha256(otp_file)

    with open(manifest_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["sha256_checksum"] = new_sha
    # Keep stale row_count=2 instead of 3
    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(MANIFEST_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ManifestVerificationError, match="row_count mismatch"):
        verify_manifest(manifest_path)


def test_verify_manifest_detects_missing_file_on_nested_manifest(tmp_path: Path) -> None:
    """Deleting a tracked file triggers `file missing on disk` even without passing `repo_root`."""
    manifest_path, otp_file = _write_mini_sample_tree(tmp_path)
    otp_file.unlink()

    with pytest.raises(ManifestVerificationError, match="file missing on disk"):
        verify_manifest(manifest_path)


def test_build_manifest_computes_tsa_period_from_file(tmp_path: Path) -> None:
    """`build_manifest` computes accurate min..max date period for TSA CSVs and round-trips cleanly."""
    tsa_dir = tmp_path / "data" / "sample" / "tsa"
    tsa_dir.mkdir(parents=True, exist_ok=True)
    tsa_file = tsa_dir / "tsa_checkpoint_volumes.csv"
    tsa_file.write_text(
        "date,tsa_throughput,source_page_year,source_url\n"
        "2019-01-01,2201765,2019,https://www.tsa.gov/travel/passenger-volumes/2019\n"
        "2024-01-31,2100000,2024,https://www.tsa.gov/travel/passenger-volumes/2024\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "data" / "sample" / "manifest.csv"
    entries = build_manifest(
        data_dir=tmp_path / "data" / "sample",
        output_manifest_path=manifest_path,
        repo_root=tmp_path,
    )
    assert len(entries) == 1
    assert entries[0].period_covered == "2019-01-01..2024-01-31"
    report = verify_manifest(manifest_path)
    assert report.passed is True


def test_build_manifest_infers_t100_max_month_from_csv(tmp_path: Path) -> None:
    """`build_manifest` inspects T-100 `MONTH` values rather than hardcoding 2026 or 12."""
    t100_dir = tmp_path / "data" / "sample" / "t100"
    t100_dir.mkdir(parents=True, exist_ok=True)
    (t100_dir / "t100_segment_all_2019.csv").write_text(
        "YEAR,MONTH,ORIGIN\n2019,1,ORD\n2019,11,ORD\n",
        encoding="utf-8",
    )
    (t100_dir / "t100_segment_all_2026.csv").write_text(
        "YEAR,MONTH,ORIGIN\n2026,1,ORD\n2026,9,ORD\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "data" / "sample" / "manifest.csv"
    entries = build_manifest(
        data_dir=tmp_path / "data" / "sample",
        output_manifest_path=manifest_path,
        repo_root=tmp_path,
    )
    periods = {Path(e.file).name: e.period_covered for e in entries}
    assert periods["t100_segment_all_2019.csv"] == "2019-01..2019-11"
    assert periods["t100_segment_all_2026.csv"] == "2026-01..2026-09"
    assert (
        infer_metadata_for_file("data/raw/t100/t100_segment_all_2026.csv")[2] == "2026-01..2026-12"
    )


def test_verify_manifest_rejects_non_positive_row_count(tmp_path: Path) -> None:
    """Manifest entries with `row_count <= 0` fail with `ManifestVerificationError`."""
    manifest_path, _ = _write_mini_sample_tree(tmp_path)
    with open(manifest_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["row_count"] = "0"
    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(MANIFEST_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ManifestVerificationError, match="row_count must be > 0"):
        verify_manifest(manifest_path)
