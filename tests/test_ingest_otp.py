"""Tests for BTS Marketing Carrier OTP ingestion, normalization, and config-driven filtering."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from standby.config import HourBlockSpec, load_config
from standby.ingest.otp import (
    ingest_otp,
    ingest_single_otp_csv,
    map_dep_hour_to_block,
    normalize_hhmm_series,
    normalize_hhmm_value,
    normalize_tail_series,
)


def test_sample_2019_raw_header_has_trailing_space_and_empty_col(sample_dir: Path) -> None:
    """Verify the raw 2019-01 sample file preserves `'Operating_Airline '` + 120th empty col, and ingestion cleans it."""
    raw_2019 = sample_dir / "otp" / "otp_mkt_ord_ua_2019_01.csv"
    with open(raw_2019, encoding="utf-8", newline="") as f:
        raw_header = next(csv.reader(f))
    assert "Operating_Airline " in raw_header
    assert raw_header[-1] == ""
    assert len(raw_header) == 120

    df = ingest_single_otp_csv(raw_2019)
    assert len(df) == 40
    assert df["op_carrier"].str.strip().eq(df["op_carrier"]).all()
    assert "" not in df.columns


def test_hhmm_2400_and_leading_zero_normalization(sample_dir: Path) -> None:
    """`'2400'` normalizes to `'0000'`, and leading-zero times remain 4-digit strings."""
    assert normalize_hhmm_value("2400") == "0000"
    assert normalize_hhmm_value("645") == "0645"
    assert normalize_hhmm_value(645.0) == "0645"
    assert normalize_hhmm_value("0005") == "0005"
    assert normalize_hhmm_value("") is None
    assert normalize_hhmm_value(None) is None

    s = normalize_hhmm_series(pd.Series(["2400", "645.0", "0735", ""]))
    assert s.tolist() == ["0000", "0645", "0735", None]

    df = ingest_otp(sample_dir)
    assert len(df) == 80
    assert not (df["crs_dep_time"] == "2400").any()
    assert not (df["dep_time"] == "2400").any()
    assert not (df["arr_time"] == "2400").any()
    # Sample includes flights whose raw DepTime or ArrTime was '2400' -> '0000'
    assert (df["dep_time"] == "0000").any()
    assert (df["arr_time"] == "0000").any()
    # Leading-zero times preserved as 4-char strings
    assert (df["crs_dep_time"].str.startswith("0") & (df["crs_dep_time"].str.len() == 4)).any()
    assert (df["crs_dep_time"] == "0600").any()


def test_cancelled_and_diverted_null_semantics(sample_dir: Path) -> None:
    """Sample OTP covers cancelled (null + non-null tail, pushback dep_time) and diverted flights."""
    df = ingest_otp(sample_dir)

    cancelled = df[df["is_cancelled"]]
    assert len(cancelled) >= 4
    assert cancelled["tail_num"].isna().any()
    assert cancelled["tail_num"].notna().any()
    assert cancelled["dep_time"].notna().any()
    assert cancelled["cancellation_code"].notna().all()

    diverted = df[df["is_diverted"]]
    assert len(diverted) >= 2
    assert diverted["arr_delay_min"].isna().all()
    assert diverted["arr_time"].isna().any()
    assert diverted["arr_time"].notna().any()


def test_tail_normalization_handles_n_prefix_and_missing() -> None:
    """`normalize_tail_series` ensures `tail_num` has `'N'` and `tail_num_clean` strips `'N'`."""
    tail, clean = normalize_tail_series(pd.Series(["N75432", "75432", "n424ua", "", None]))
    assert tail.tolist() == ["N75432", "N75432", "N424UA", None, None]
    assert clean.tolist() == ["75432", "75432", "424UA", None, None]


def test_hour_block_mapping_is_yaml_driven(sample_dir: Path, repo_root: Path) -> None:
    """All 6 standard hour blocks appear in sample OTP, and `get_hour_block` reads `self.hour_blocks`."""
    df = ingest_otp(sample_dir)
    assert set(df["hour_block"].unique()) == {
        "early",
        "morning",
        "midday",
        "afternoon",
        "evening",
        "late",
    }

    cfg = load_config(repo_root=repo_root)
    assert cfg.get_hour_block(5) == "early"
    assert cfg.get_hour_block(23) == "late"
    assert cfg.get_hour_block(2) == "late"

    # Mutating hour_blocks boundaries changes both config.get_hour_block and map_dep_hour_to_block
    cfg.hour_blocks["early"] = HourBlockSpec(
        name="early", start_hour=4, end_hour=7, load_multiplier=0.95
    )
    cfg.hour_blocks["late"] = HourBlockSpec(
        name="late", start_hour=20, end_hour=4, load_multiplier=0.93
    )
    assert cfg.get_hour_block(4) == "early"
    assert cfg.get_hour_block(3) == "late"
    mapped = map_dep_hour_to_block(pd.Series([3, 4]), hour_blocks=cfg.hour_blocks)
    assert mapped.tolist() == ["late", "early"]


def test_ingest_otp_honors_year_carrier_and_hub_filters(sample_dir: Path) -> None:
    """`ingest_otp` filters cleanly by years and operating_carriers, and fails on unmatched hub."""
    df_2019 = ingest_otp(sample_dir, years=[2019])
    assert set(df_2019["year"].unique()) == {2019}
    assert len(df_2019) == 40

    df_ua_only = ingest_otp(sample_dir, operating_carriers={"UA"})
    assert set(df_ua_only["op_carrier"].unique()) == {"UA"}

    with pytest.raises(RuntimeError, match="No OTP records ingested"):
        ingest_otp(sample_dir, hub="DEN")


def test_ingest_otp_normalizes_hub_guards_bad_crs_dep_time_and_rejects_empty_years(
    sample_dir: Path,
    tmp_path: Path,
) -> None:
    """`ingest_otp` normalizes hub whitespace/case, rejects bad `CRSDepTime`, and does not bypass `years=[]`."""
    assert len(ingest_otp(sample_dir, hub="ord")) == 80
    assert len(ingest_otp(sample_dir, hub=" ord ")) == 80
    with pytest.raises(ValueError, match="hub must not be empty"):
        ingest_otp(sample_dir, hub="   ")

    assert normalize_hhmm_series(
        pd.Series(["abc", "-100", "", None, "645.0", "2400"])
    ).tolist() == [None, None, None, None, "0645", "0000"]

    sample_csv = sample_dir / "otp" / "otp_mkt_ord_ua_2024_01.csv"
    with open(sample_csv, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    first_row = list(rows[1])
    crs_idx = header.index("CRSDepTime")

    for bad_val in ("", "abc"):
        bad_row = list(first_row)
        bad_row[crs_idx] = bad_val
        bad_csv = tmp_path / f"bad_crs_{bad_val or 'blank'}.csv"
        with open(bad_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerow(bad_row)
        with pytest.raises(ValueError, match="blank or non-digit CRSDepTime"):
            ingest_single_otp_csv(bad_csv)

    with pytest.raises(RuntimeError, match="No OTP records ingested"):
        ingest_otp(sample_dir, years=[])
