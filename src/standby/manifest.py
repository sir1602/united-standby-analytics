"""Checksum manifest generation and verification (`data/manifest.csv`)."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("standby.manifest")

MANIFEST_COLUMNS: tuple[str, ...] = (
    "file",
    "source_url",
    "download_date",
    "period_covered",
    "row_count",
    "sha256_checksum",
)

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LOOKUP_SOURCE_URLS: dict[str, str] = {
    "L_AIRCRAFT_TYPE.csv": "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVePeNSg_glcR",
    "L_AIRPORT_ID.csv": "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVecbeg_VQ",
    "L_CARRIER_HISTORY.csv": "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_PNeeVRe_UVfgbel",
    "L_AIRPORT.csv": "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVecbeg",
    "L_UNIQUE_CARRIERS.csv": "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_haVdhR_PNeeVRef",
}


class ManifestVerificationError(RuntimeError):
    """Raised when one or more files fail manifest checksum or row-count verification."""


@dataclass(frozen=True)
class ManifestEntry:
    """Single row in `data/manifest.csv`."""

    file: str
    source_url: str
    download_date: str
    period_covered: str
    row_count: int
    sha256_checksum: str


@dataclass
class ManifestVerificationReport:
    """Summary of manifest verification execution."""

    manifest_path: Path
    total_entries: int
    verified_entries: int
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0 and self.verified_entries == self.total_entries


def compute_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute lowercase 64-char SHA-256 hex digest of a file in streaming chunks."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def count_data_rows(file_path: Path) -> int:
    """Count data lines (total lines minus 1 header line) in a CSV/TXT file."""
    with open(file_path, "rb") as f:
        total_lines = sum(1 for _ in f)
    return max(0, total_lines - 1)


def read_manifest_entries(manifest_path: Path) -> list[ManifestEntry]:
    """Read and parse entries from a manifest CSV file."""
    if not manifest_path.is_file():
        raise ManifestVerificationError(f"Manifest file does not exist: {manifest_path}")

    entries: list[ManifestEntry] = []
    with open(manifest_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or tuple(reader.fieldnames) != MANIFEST_COLUMNS:
            raise ManifestVerificationError(
                f"Invalid manifest columns in {manifest_path}: expected {MANIFEST_COLUMNS}, "
                f"got {reader.fieldnames}"
            )
        for idx, row in enumerate(reader, start=2):
            try:
                rc = int(row["row_count"])
            except (TypeError, ValueError) as exc:
                raise ManifestVerificationError(
                    f"Invalid row_count at {manifest_path}:{idx}: {row.get('row_count')!r}"
                ) from exc
            entries.append(
                ManifestEntry(
                    file=row["file"].strip(),
                    source_url=row["source_url"].strip(),
                    download_date=row["download_date"].strip(),
                    period_covered=row["period_covered"].strip(),
                    row_count=rc,
                    sha256_checksum=row["sha256_checksum"].strip(),
                )
            )
    if not entries:
        raise ManifestVerificationError(f"Manifest is empty: {manifest_path}")
    return entries


def _infer_repo_root(resolved_manifest: Path, entries: list[ManifestEntry]) -> Path:
    """Infer repository root for nested manifests such as `data/manifest.csv` or `data/sample/manifest.csv`."""
    manifest_dir = resolved_manifest.parent
    if entries:
        first_file = entries[0].file
        for parent in resolved_manifest.parents:
            rel_dir = manifest_dir.relative_to(parent).as_posix()
            if rel_dir != "." and first_file.startswith(f"{rel_dir}/"):
                return parent
        for parent in resolved_manifest.parents:
            if (parent / first_file).exists():
                return parent
    for parent in resolved_manifest.parents:
        if (parent / "pyproject.toml").is_file() or (parent / ".git").exists():
            return parent
    return resolved_manifest.parents[1] if len(resolved_manifest.parents) > 1 else manifest_dir


def verify_manifest(
    manifest_path: Path,
    repo_root: Path | None = None,
    subset_glob: str | None = None,
) -> ManifestVerificationReport:
    """Verify every file in `manifest_path` against disk SHA-256 checksum and row count."""
    resolved_manifest = manifest_path.resolve()
    entries = read_manifest_entries(resolved_manifest)
    root = repo_root.resolve() if repo_root else _infer_repo_root(resolved_manifest, entries)

    if subset_glob:
        entries = [e for e in entries if fnmatch.fnmatch(e.file, subset_glob)]
        if not entries:
            raise ManifestVerificationError(
                f"No manifest entries matched subset_glob={subset_glob!r} in {resolved_manifest}"
            )

    failures: list[str] = []
    verified = 0

    for entry in entries:
        if entry.row_count <= 0:
            failures.append(f"{entry.file}: row_count must be > 0 (got {entry.row_count})")
            continue
        if not HEX64_RE.match(entry.sha256_checksum):
            failures.append(
                f"{entry.file}: invalid sha256_checksum format {entry.sha256_checksum!r}"
            )
            continue
        if not ISO_DATE_RE.match(entry.download_date):
            failures.append(f"{entry.file}: invalid download_date {entry.download_date!r}")
            continue

        target = root / entry.file
        if not target.is_file():
            failures.append(f"{entry.file}: file missing on disk at {target}")
            continue

        actual_sha = compute_sha256(target)
        if actual_sha != entry.sha256_checksum:
            failures.append(
                f"{entry.file}: SHA-256 mismatch (expected {entry.sha256_checksum}, "
                f"actual {actual_sha})"
            )
            continue

        actual_rows = count_data_rows(target)
        if actual_rows != entry.row_count:
            failures.append(
                f"{entry.file}: row_count mismatch (expected {entry.row_count}, "
                f"actual {actual_rows})"
            )
            continue

        verified += 1

    report = ManifestVerificationReport(
        manifest_path=resolved_manifest,
        total_entries=len(entries),
        verified_entries=verified,
        failures=failures,
    )
    if not report.passed:
        error_msg = (
            f"Manifest verification failed for {resolved_manifest} "
            f"({len(failures)} error(s) out of {len(entries)} files):\n  - "
            + "\n  - ".join(failures[:10])
        )
        raise ManifestVerificationError(error_msg)

    logger.info(
        f"Verified {report.verified_entries}/{report.total_entries} files in {resolved_manifest.name}"
    )
    return report


def _infer_tsa_period(file_path: Path | None) -> str:
    """Compute exact `min_date..max_date` range from a TSA CSV when available."""
    if file_path is not None and file_path.is_file():
        with open(file_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            dates = sorted(
                row["date"].strip() for row in reader if row.get("date") and row["date"].strip()
            )
        if dates:
            return f"{dates[0]}..{dates[-1]}"
    return "unknown"


def _infer_t100_period(file_path: Path | None, year: str) -> str:
    """Compute exact `YYYY-MM..YYYY-MM` period covered from a T-100 CSV `MONTH` column when available."""
    if file_path is not None and file_path.is_file():
        with open(file_path, encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is not None:
                month_idx = next(
                    (i for i, col in enumerate(header) if col.strip() == "MONTH"),
                    None,
                )
                if month_idx is not None:
                    months: set[int] = set()
                    for row in reader:
                        if len(row) <= month_idx:
                            continue
                        raw_m = row[month_idx].strip()
                        if raw_m.isdigit():
                            m_int = int(raw_m)
                            if 1 <= m_int <= 12:
                                months.add(m_int)
                    if months:
                        return f"{year}-{min(months):02d}..{year}-{max(months):02d}"
    return f"{year}-01..{year}-12"


def infer_metadata_for_file(
    rel_path: str,
    default_date: str = "2026-11-13",
    file_path: Path | None = None,
) -> tuple[str, str, str]:
    """Infer `(source_url, download_date, period_covered)` from relative file path."""
    name = Path(rel_path).name

    m_otp = re.match(r"otp_mkt_ord_ua_(\d{4})_(\d{2})\.csv$", name)
    if m_otp:
        year, month_str = m_otp.group(1), m_otp.group(2)
        month_int = int(month_str)
        url = (
            "https://transtats.bts.gov/PREZIP/"
            f"On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{year}_{month_int}.zip"
        )
        return url, default_date, f"{year}-{month_str}"

    m_t100 = re.match(r"t100_segment_all_(\d{4})\.csv$", name)
    if m_t100:
        year = m_t100.group(1)
        url = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4"
        return url, default_date, _infer_t100_period(file_path, year)

    if name in LOOKUP_SOURCE_URLS:
        return LOOKUP_SOURCE_URLS[name], default_date, "cumulative"

    if name in {"ACFTREF.txt", "DEREG.txt", "MASTER.txt"}:
        return (
            "https://registry.faa.gov/database/ReleasableAircraft.zip",
            default_date,
            "1948..2026",
        )

    if name == "tsa_checkpoint_volumes.csv":
        return (
            "https://www.tsa.gov/travel/passenger-volumes",
            default_date,
            _infer_tsa_period(file_path),
        )

    return "https://www.transtats.bts.gov/", default_date, default_date


def build_manifest(
    data_dir: Path,
    output_manifest_path: Path,
    repo_root: Path,
    download_date: str = "2026-11-13",
) -> list[ManifestEntry]:
    """Scan core pipeline files under `data_dir` and write a deterministic `manifest.csv`."""
    candidates: list[Path] = []
    candidates.extend(sorted((data_dir / "otp").glob("otp_mkt_ord_ua_*.csv")))
    candidates.extend(sorted((data_dir / "t100").glob("t100_segment_all_*.csv")))
    candidates.extend(sorted((data_dir / "lookups").glob("L_*.csv")))
    for faa_name in ("ACFTREF.txt", "DEREG.txt", "MASTER.txt"):
        p = data_dir / "faa" / faa_name
        if p.is_file():
            candidates.append(p)
    tsa_file = data_dir / "tsa" / "tsa_checkpoint_volumes.csv"
    if tsa_file.is_file():
        candidates.append(tsa_file)

    entries: list[ManifestEntry] = []
    for file_path in sorted(candidates, key=lambda p: p.relative_to(repo_root).as_posix()):
        rel = file_path.relative_to(repo_root).as_posix()
        source_url, dl_date, period = infer_metadata_for_file(
            rel,
            default_date=download_date,
            file_path=file_path,
        )
        row_count = count_data_rows(file_path)
        sha = compute_sha256(file_path)
        entries.append(
            ManifestEntry(
                file=rel,
                source_url=source_url,
                download_date=dl_date,
                period_covered=period,
                row_count=row_count,
                sha256_checksum=sha,
            )
        )

    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_manifest_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(MANIFEST_COLUMNS)
        for e in entries:
            writer.writerow(
                [
                    e.file,
                    e.source_url,
                    e.download_date,
                    e.period_covered,
                    e.row_count,
                    e.sha256_checksum,
                ]
            )

    logger.info(f"Wrote {len(entries)} manifest entries to {output_manifest_path}")
    return entries
