"""Tests for Phase 2 curated reference CSV tables (`reference/*.csv`)."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from standby.enrichment.aircraft import (
    load_faa_t100_crosswalk,
    load_seat_config,
    load_tail_overrides,
)


def test_seat_config_csv_schema_and_invariants(repo_root: Path) -> None:
    """`reference/seat_config.csv` has 31 valid subtypes with `seats_total == premium + economy`."""
    df = load_seat_config(repo_root / "reference")
    assert len(df) == 31
    assert df["subtype"].is_unique
    assert set(df["op_carrier_group"].unique()) == {"MAINLINE", "EXPRESS"}
    assert (df["seats_total"] > 0).all()
    assert (df["seats_total"] == df["seats_premium"] + df["seats_economy"]).all()
    assert df["source_url"].str.startswith("https://").all()
    assert df["as_of"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all()
    assert (df["op_carrier_group"] == "MAINLINE").sum() == 21
    assert (df["op_carrier_group"] == "EXPRESS").sum() == 10


def test_faa_t100_crosswalk_referential_integrity(repo_root: Path) -> None:
    """Every crosswalk row maps to a valid `seat_config` subtype and `L_AIRCRAFT_TYPE` code."""
    ref_dir = repo_root / "reference"
    sc = load_seat_config(ref_dir)
    cw = load_faa_t100_crosswalk(ref_dir)
    assert len(cw) == 56
    assert not cw.duplicated(subset=["faa_mfr", "faa_model", "op_carrier"]).any()

    valid_subtypes = set(sc["subtype"])
    assert set(cw["subtype"]).issubset(valid_subtypes)

    lkp_sample = repo_root / "data" / "sample" / "lookups" / "L_AIRCRAFT_TYPE.csv"
    with open(lkp_sample, encoding="utf-8-sig", newline="") as f:
        valid_bts_codes = {row["Code"].strip() for row in csv.DictReader(f)}
    assert set(cw["bts_aircraft_type"]).issubset(valid_bts_codes)

    lkp_raw = repo_root / "data" / "raw" / "lookups" / "L_AIRCRAFT_TYPE.csv"
    if lkp_raw.is_file():
        with open(lkp_raw, encoding="utf-8-sig", newline="") as f:
            raw_bts_codes = {row["Code"].strip() for row in csv.DictReader(f)}
        assert set(cw["bts_aircraft_type"]).issubset(raw_bts_codes)


def test_tail_overrides_csv_schema_and_referential_integrity(repo_root: Path) -> None:
    """`reference/tail_overrides.csv` contains 152 overrides (72 mainline + 80 regional) matching `seat_config`."""
    ref_dir = repo_root / "reference"
    sc = load_seat_config(ref_dir)
    to = load_tail_overrides(ref_dir)
    assert len(to) == 152
    assert to["tail_num"].is_unique
    assert (to["op_carrier"] == "UA").sum() == 72
    assert (to["op_carrier"] != "UA").sum() == 80
    assert to["tail_num"].str.match(re.compile(r"^N[1-9][0-9A-Z]{0,4}$")).all()
    assert (to["seats_total"] == to["seats_premium"] + to["seats_economy"]).all()

    sc_totals = dict(zip(sc["subtype"], sc["seats_total"], strict=True))
    for row in to.itertuples(index=False):
        assert row.subtype in sc_totals
        assert int(row.seats_total) == int(sc_totals[row.subtype])
        assert str(row.source_url).startswith("https://")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.as_of))
        assert str(row.override_reason).strip() != ""
