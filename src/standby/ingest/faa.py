"""Ingestion and standardization for FAA Aircraft Registry (`MASTER.txt`, `DEREG.txt`, `ACFTREF.txt`)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("standby.ingest.faa")

COMMERCIAL_MFR_PREFIXES: tuple[str, ...] = (
    "BOEING",
    "AIRBUS",
    "EMBRAER",
    "YABORA",
    "BOMBARDIER",
    "CANADAIR",
    "MHI RJ",
)


def is_commercial_manufacturer(mfr_series: pd.Series) -> pd.Series:
    """Return boolean Series indicating whether manufacturer starts with a commercial airliner prefix."""
    upper = mfr_series.fillna("").astype(str).str.strip().str.upper()
    mask = pd.Series(False, index=mfr_series.index)
    for prefix in COMMERCIAL_MFR_PREFIXES:
        mask = mask | upper.str.startswith(prefix)
    return mask


def ingest_faa_acftref(data_dir: Path) -> pd.DataFrame:
    """Read and standardize `ACFTREF.txt` (`utf-8-sig`, fixed-width strip, commercial flag)."""
    acftref_path = data_dir / "faa" / "ACFTREF.txt"
    if not acftref_path.is_file():
        raise FileNotFoundError(f"Missing FAA ACFTREF file: {acftref_path}")

    df_raw = pd.read_csv(
        acftref_path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        encoding_errors="replace",
        low_memory=False,
        usecols=lambda c: c.strip() in {"CODE", "MFR", "MODEL", "NO-SEATS"},
    )
    df_raw.columns = [c.strip() for c in df_raw.columns]

    code = df_raw["CODE"].astype(str).str.strip()
    mfr = df_raw["MFR"].astype(str).str.strip()
    model = df_raw["MODEL"].astype(str).str.strip()
    seats = (
        pd.to_numeric(df_raw["NO-SEATS"].astype(str).str.strip(), errors="coerce")
        .fillna(0)
        .astype("int64")
    )

    out = pd.DataFrame(
        {
            "code": code,
            "faa_manufacturer": mfr,
            "faa_model": model,
            "faa_tc_max_seats": seats,
            "is_commercial_airliner": is_commercial_manufacturer(mfr),
        }
    )
    out = (
        out[out["code"] != ""].drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    )
    logger.info(
        f"Ingested {len(out):,} FAA ACFTREF rows ({int(out['is_commercial_airliner'].sum()):,} commercial)"
    )
    return out


def ingest_faa_registry(
    data_dir: Path,
    acftref_df: pd.DataFrame | None = None,
    commercial_only: bool = True,
) -> pd.DataFrame:
    """Ingest `MASTER.txt` and `DEREG.txt` (`utf-8-sig`, stripped).

    `MASTER` is deduplicated per `n_number`; `DEREG` keeps every record (sorted by `n_number`,
    latest `cancel_date` first) so the latest-record choice happens after the seat-floor filter.
    """
    faa_dir = data_dir / "faa"
    master_path = faa_dir / "MASTER.txt"
    dereg_path = faa_dir / "DEREG.txt"
    if not master_path.is_file() or not dereg_path.is_file():
        raise FileNotFoundError(f"Missing FAA MASTER.txt or DEREG.txt in {faa_dir}")

    if acftref_df is None:
        acftref_df = ingest_faa_acftref(data_dir)

    commercial_codes: set[str] | None = None
    if commercial_only:
        commercial_codes = set(
            acftref_df.loc[acftref_df["is_commercial_airliner"], "code"].astype(str)
        )

    # 1. Read MASTER.txt (uses `MFR MDL CODE`, `YEAR MFR`)
    df_m = pd.read_csv(
        master_path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        encoding_errors="replace",
        low_memory=False,
        usecols=lambda c: c.strip() in {"N-NUMBER", "SERIAL NUMBER", "MFR MDL CODE", "YEAR MFR"},
    )
    df_m.columns = [c.strip() for c in df_m.columns]
    master_std = pd.DataFrame(
        {
            "n_number": df_m["N-NUMBER"].astype(str).str.strip().str.upper(),
            "serial_number": df_m["SERIAL NUMBER"].astype(str).str.strip(),
            "mfr_mdl_code": df_m["MFR MDL CODE"].astype(str).str.strip(),
            "year_mfr": df_m["YEAR MFR"].astype(str).str.strip(),
            "registry_source": "faa_master",
            "cancel_date": None,
        }
    )
    master_std = master_std[
        (master_std["n_number"] != "") & (master_std["mfr_mdl_code"] != "")
    ].copy()
    if commercial_codes is not None:
        master_std = master_std[master_std["mfr_mdl_code"].isin(commercial_codes)].copy()
    master_std = master_std.drop_duplicates(subset=["n_number"], keep="first")

    # 2. Read DEREG.txt (uses `MFR-MDL-CODE`, `YEAR-MFR`, `CANCEL-DATE`)
    df_d = pd.read_csv(
        dereg_path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        encoding_errors="replace",
        low_memory=False,
        usecols=lambda c: (
            c.strip() in {"N-NUMBER", "SERIAL-NUMBER", "MFR-MDL-CODE", "YEAR-MFR", "CANCEL-DATE"}
        ),
    )
    df_d.columns = [c.strip() for c in df_d.columns]
    dereg_std = pd.DataFrame(
        {
            "n_number": df_d["N-NUMBER"].astype(str).str.strip().str.upper(),
            "serial_number": df_d["SERIAL-NUMBER"].astype(str).str.strip(),
            "mfr_mdl_code": df_d["MFR-MDL-CODE"].astype(str).str.strip(),
            "year_mfr": df_d["YEAR-MFR"].astype(str).str.strip(),
            "registry_source": "faa_dereg",
            "cancel_date": df_d["CANCEL-DATE"].astype(str).str.strip(),
        }
    )
    dereg_std = dereg_std[(dereg_std["n_number"] != "") & (dereg_std["mfr_mdl_code"] != "")].copy()
    if commercial_codes is not None:
        dereg_std = dereg_std[dereg_std["mfr_mdl_code"].isin(commercial_codes)].copy()

    # Keep EVERY deregistration record. The "latest cancel_date per n_number" choice must be made
    # only after the ACFTREF seat floor is applied (AircraftResolver / int_aircraft_enriched);
    # collapsing here would let a later-cancelled sub-20-seat record (e.g. a 2-seat Boeing Stearman,
    # which passes the BOEING prefix filter) shadow an earlier commercial airliner on the same N-number.
    dereg_std = dereg_std.sort_values(
        ["n_number", "cancel_date", "serial_number"],
        ascending=[True, False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    combined = pd.concat([master_std, dereg_std], ignore_index=True)
    logger.info(
        f"Ingested {len(combined):,} FAA registry rows "
        f"(master={len(master_std):,}, dereg={len(dereg_std):,}, commercial_only={commercial_only})"
    )
    return combined
