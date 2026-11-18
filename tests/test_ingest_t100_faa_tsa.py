"""Tests for T-100, BTS Lookups (incl. CP1252 fallback), FAA Registry, TSA, and acquisition protocols."""

from __future__ import annotations

import csv
import io
import re
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from standby.acquire import (
    acquire_bts_month_otp,
    acquire_t100_year,
    acquire_tsa_volumes,
    parse_transtats_aspnet_form,
    parse_tsa_html_table,
)
from standby.ingest.faa import ingest_faa_acftref, ingest_faa_registry
from standby.ingest.lookups import LOOKUP_FILES, ingest_lookups
from standby.ingest.t100 import SUMMARY_COLUMNS, ingest_t100, ingest_t100_system_carrier_summary
from standby.ingest.tsa import ingest_tsa


def test_ingest_t100_filters_class_f_and_tags_international(sample_dir: Path) -> None:
    """`ingest_t100` excludes non-`F` rows and derives `is_international` from `DATA_SOURCE`."""
    df = ingest_t100(sample_dir)
    assert len(df) == 1472
    assert (df["service_class"] == "F").all()
    assert (df["origin"] == "ORD").all()
    intl_mask = df["data_source"].isin(["IU", "IF"])
    assert (df["is_international"] == intl_mask).all()
    assert intl_mask.any()
    assert (~intl_mask).any()


def test_ingest_lookups_reads_all_five_and_handles_cp1252(sample_dir: Path) -> None:
    """`ingest_lookups` loads all 5 lookup tables, includes all UA/Express carriers, and decodes CP1252 `0xcd`."""
    df = ingest_lookups(sample_dir)
    assert set(df["lookup_table"].unique()) == {Path(name).stem for name in LOOKUP_FILES}

    airports = df[df["lookup_table"] == "L_AIRPORT"]
    rih_desc = airports.loc[airports["code"] == "RIH", "description"].iloc[0]
    assert "Scarlett MartÍnez" in rih_desc

    airport_ids = df[df["lookup_table"] == "L_AIRPORT_ID"]
    id_desc = airport_ids.loc[airport_ids["code"] == "17214", "description"].iloc[0]
    assert "Scarlett MartÍnez" in id_desc

    expected_carriers = {"UA", "OO", "YX", "G7", "ZW", "EV", "AX", "C5", "YV"}
    for table_name in ("L_CARRIER_HISTORY", "L_UNIQUE_CARRIERS"):
        codes = set(df.loc[df["lookup_table"] == table_name, "code"])
        assert expected_carriers.issubset(codes)


def test_ingest_faa_handles_bom_commercial_filter_and_dereg_dedup(
    sample_dir: Path, tmp_path: Path
) -> None:
    """FAA ingestion strips BOM/padding, excludes GA/drone collisions, and keeps latest `CANCEL-DATE`."""
    acftref = ingest_faa_acftref(sample_dir)
    assert len(acftref) == 19
    assert not acftref.loc[acftref["code"] == "06102EQ", "is_commercial_airliner"].iloc[0]
    assert not acftref.loc[acftref["code"] == "06102JZ", "is_commercial_airliner"].iloc[0]
    assert acftref.loc[acftref["code"] == "13848A3", "is_commercial_airliner"].iloc[0]

    reg = ingest_faa_registry(sample_dir, acftref_df=acftref, commercial_only=True)
    # 14 active commercial tails in MASTER + 3 commercial/retired tails in DEREG (424UA, 884AS, 903SW)
    assert len(reg) == 17
    master_tails = set(reg.loc[reg["registry_source"] == "faa_master", "n_number"])
    dereg_tails = set(reg.loc[reg["registry_source"] == "faa_dereg", "n_number"])
    assert "884AS" in dereg_tails
    assert "424UA" in dereg_tails
    assert "903SW" in dereg_tails
    assert "75432" in master_tails

    # Synthetic collision & DEREG deduplication check:
    # - 151GJ (Hylio drone in MASTER) excluded when commercial_only=True, kept when False
    # - 884AS (EA Vision drone in MASTER vs CRJ-200 in DEREG) keeps only CRJ-200 DEREG when commercial_only=True
    # - 75432 (Boeing 737-924ER in MASTER vs Cessna AT-17 in DEREG) keeps only Boeing MASTER when commercial_only=True
    # - 54552 (Airbus A321-271NX in MASTER vs Boeing A75N1 Stearman in DEREG) preserves separate registry_source rows
    # - 999TEST keeps both DEREG rows, ordered latest YYYYMMDD cancel_date first
    faa_dir = tmp_path / "faa"
    faa_dir.mkdir(parents=True)
    (faa_dir / "ACFTREF.txt").write_text(
        "\ufeffCODE,MFR,MODEL,NO-SEATS,\n"
        "06102EQ,HYLIO INC                      ,AG-272              ,000,\n"
        "06102JZ,EA VISION                      ,EA J100             ,000,\n"
        "13848A3,BOEING                         ,737-924ER           ,222,\n"
        "1390008,BOMBARDIER INC                 ,CL-600-2B19         ,055,\n"
        "2071304,CESSNA                         ,AT-17               ,005,\n"
        "3940001,AIRBUS S A S                   ,A321-271NX          ,246,\n"
        "1150101,BOEING                         ,A75N1               ,002,\n",
        encoding="utf-8",
    )
    (faa_dir / "MASTER.txt").write_text(
        "\ufeffN-NUMBER,SERIAL NUMBER,MFR MDL CODE,YEAR MFR,\n"
        "151GJ,DRONE-1,06102EQ,2024,\n"
        "884AS,DRONE-2,06102JZ,2024,\n"
        "75432,32835,13848A3,2009,\n"
        "54552,11001,3940001,2023,\n",
        encoding="utf-8",
    )
    (faa_dir / "DEREG.txt").write_text(
        "\ufeffN-NUMBER,SERIAL-NUMBER,MFR-MDL-CODE,YEAR-MFR,CANCEL-DATE,\n"
        "884AS,7513,1390008,2001,20191126,\n"
        "75432,2919,2071304,1943,19490517,\n"
        "54552,75-1234,1150101,1941,19551130,\n"
        "999TEST,S1,1390008,2001,20100101,\n"
        "999TEST,S2,1390008,2005,20220515,\n",
        encoding="utf-8",
    )

    syn_comm = ingest_faa_registry(tmp_path, commercial_only=True)
    comm_master = set(syn_comm.loc[syn_comm["registry_source"] == "faa_master", "n_number"])
    comm_dereg = set(syn_comm.loc[syn_comm["registry_source"] == "faa_dereg", "n_number"])
    assert comm_master == {"75432", "54552"}
    assert comm_dereg == {"884AS", "54552", "999TEST"}
    assert "151GJ" not in comm_master
    assert "884AS" not in comm_master
    assert "75432" not in comm_dereg
    # DEREG keeps every record (latest cancel_date first); the per-tail choice is made after the
    # seat floor in AircraftResolver / int_aircraft_enriched.
    rows_999 = syn_comm.loc[syn_comm["n_number"] == "999TEST"]
    assert rows_999["serial_number"].tolist() == ["S2", "S1"]
    assert rows_999["cancel_date"].tolist() == ["20220515", "20100101"]

    syn_all = ingest_faa_registry(tmp_path, commercial_only=False)
    all_master = set(syn_all.loc[syn_all["registry_source"] == "faa_master", "n_number"])
    all_dereg = set(syn_all.loc[syn_all["registry_source"] == "faa_dereg", "n_number"])
    assert all_master == {"151GJ", "884AS", "75432", "54552"}
    assert all_dereg == {"884AS", "75432", "54552", "999TEST"}


def test_ingest_tsa_parses_dates_and_filters_years(sample_dir: Path) -> None:
    """`ingest_tsa` parses sorted daily rows and filters by requested years."""
    df_all = ingest_tsa(sample_dir)
    assert len(df_all) == 62
    assert (df_all["tsa_throughput"] > 0).all()

    df_2024 = ingest_tsa(sample_dir, years=[2024])
    assert len(df_2024) == 31
    assert df_2024["date_day"].str.startswith("2024-").all()


def test_tsa_duplicate_dates_keep_max_throughput_like_dbt(tmp_path: Path) -> None:
    """Conflicting duplicate TSA dates keep the highest throughput (parity with dbt `stg_tsa_throughput`)."""
    from standby.enrichment.calculations import compute_tsa_daily_multipliers

    tsa_dir = tmp_path / "tsa"
    tsa_dir.mkdir()
    (tsa_dir / "tsa_checkpoint_volumes.csv").write_text(
        "date,tsa_throughput,source_page_year\n"
        "2024-01-15,1500000,2024\n"
        "2024-01-15,2000000,2025\n"
        "2024-01-16,1000000,2024\n",
        encoding="utf-8",
    )
    ingested = ingest_tsa(tmp_path, years=[2024])
    assert ingested["date_day"].tolist() == ["2024-01-15", "2024-01-16"]
    assert ingested["tsa_throughput"].tolist() == [2_000_000, 1_000_000]

    # The multiplier step applies the same rule regardless of input order
    raw_dups = pd.DataFrame(
        {
            "date_day": ["2024-01-15", "2024-01-16", "2024-01-15"],
            "tsa_throughput": [1_500_000, 1_000_000, 2_000_000],
        }
    )
    mults = compute_tsa_daily_multipliers(raw_dups, alpha=0.35, bounds=(0.80, 1.20))
    by_day = dict(zip(mults["date_day"], mults["tsa_throughput"], strict=True))
    assert by_day == {"2024-01-15": 2_000_000, "2024-01-16": 1_000_000}


def test_acquire_t100_and_tsa_protocols_mocked(tmp_path: Path, monkeypatch) -> None:
    """Verify ASP.NET T-100 GET->POST protocol (excluding `chk*`) and TSA HTML multi-year aggregation."""
    sample_form_html = (
        "<form>"
        '<input type="hidden" name="__VIEWSTATE" value="VS123" />'
        '<input type="hidden" name="__EVENTVALIDATION" value="EV456" />'
        '<input type="checkbox" name="chkAllVars" value="on" />'
        '<input type="checkbox" name="chkAllGroups" value="on" />'
        '<input type="checkbox" name="chkDownloadZip" value="on" />'
        '<input type="checkbox" name="YEAR" value="on" />'
        '<input type="checkbox" name="ORIGIN" value="on" />'
        "</form>"
    )
    hidden, checkboxes = parse_transtats_aspnet_form(sample_form_html)
    assert hidden["__VIEWSTATE"] == "VS123"
    assert hidden["__EVENTVALIDATION"] == "EV456"
    assert checkboxes == [("YEAR", "on"), ("ORIGIN", "on")]

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("T_T100_SEGMENT_ALL_CARRIER.csv", "YEAR,ORIGIN\n2024,ORD\n")
    zip_bytes = zip_buf.getvalue()

    captured_requests: list[urllib.request.Request] = []

    class FakeOpener:
        def open(self, req: urllib.request.Request, timeout: int = 300):
            captured_requests.append(req)
            resp = MagicMock()
            payload = sample_form_html.encode("utf-8") if req.get_method() == "GET" else zip_bytes
            resp.read.return_value = payload
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *args: None
            return resp

    t100_out = acquire_t100_year(2024, output_dir=tmp_path / "t100", opener=FakeOpener())
    assert t100_out.is_file()
    assert "2024,ORD" in t100_out.read_text(encoding="utf-8")
    assert len(captured_requests) == 2
    post_body = captured_requests[1].data.decode("utf-8")
    assert "chkAllVars" not in post_body
    assert "chkAllGroups" not in post_body
    assert "chkDownloadZip" not in post_body
    assert "cboYear=2024" in post_body
    assert "YEAR=on" in post_body

    html_2019 = "<table><tr><td>1/2/2019</td><td>2,100,000</td></tr><tr><td>1/1/2019</td><td>2,201,765</td></tr></table>"
    html_2024 = "<table><tr><td>1/1/2024</td><td>2,303,507</td></tr><tr><td>1/1/2024</td><td>2,303,507</td></tr></table>"
    monkeypatch.setattr(
        "standby.acquire.download_bytes",
        lambda url, timeout=60: (html_2019 if "2019" in url else html_2024).encode("utf-8"),
    )
    tsa_out = acquire_tsa_volumes(output_dir=tmp_path / "tsa", years=[2019, 2024])
    tsa_df = pd.read_csv(tsa_out, dtype=str)
    assert tsa_df["date"].tolist() == ["2019-01-01", "2019-01-02", "2024-01-01"]
    assert tsa_df["tsa_throughput"].tolist() == ["2201765", "2100000", "2303507"]


def test_sample_data_contains_no_personal_or_non_airline_pii(sample_dir: Path) -> None:
    """Ensure `data/sample/faa/` contains only corporate airline records and zero PII across all sample files."""
    allowed_airlines = {"UNITED AIRLINES INC", "SKYWEST AIRLINES INC", "REPUBLIC AIRWAYS INC"}
    allowed_hq_streets = {
        "444 S RIVER RD",
        "444 S RIVER ROAD",
        "2 BRICKYARD LN",
        "233 S WACKER DR",
        "ATTN: TREASURER WILLIS TOWER",
    }

    with open(sample_dir / "faa" / "MASTER.txt", encoding="utf-8-sig", newline="") as f:
        master_rows = list(csv.DictReader(f))
    assert len(master_rows) == 14
    for row in master_rows:
        assert row["N-NUMBER"].strip() not in {"151GJ", "884AS"}
        assert row["TYPE REGISTRANT"].strip() == "3"
        assert row["NAME"].strip() in allowed_airlines
        assert row["STREET"].strip() in allowed_hq_streets
        for i in range(1, 6):
            assert row[f"OTHER NAMES({i})"].strip() == ""

    with open(sample_dir / "faa" / "DEREG.txt", encoding="utf-8-sig", newline="") as f:
        dereg_rows = list(csv.DictReader(f))
    assert len(dereg_rows) == 3
    for row in dereg_rows:
        assert row["N-NUMBER"].strip() != "75432"
        assert row["INDICATOR-GROUP"].strip() == "3"
        assert row["NAME"].strip() in allowed_airlines
        assert row["STREET-MAIL"].strip() in allowed_hq_streets
        for col in (
            "STREET-PHYSICAL",
            "STREET2-PHYSICAL",
            "CITY-PHYSICAL",
            "STATE-ABBREV-PHYSICAL",
            "ZIP-CODE-PHYSICAL",
            "COUNTY-PHYSICAL",
            "COUNTRY-PHYSICAL",
            "OTHER-NAMES(1)",
            "OTHER-NAMES(2)",
            "OTHER-NAMES(3)",
            "OTHER-NAMES(4)",
            "OTHER-NAMES(5)",
        ):
            assert row[col].strip() == ""

    email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone_re = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
    for path in sorted(sample_dir.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        assert not email_re.findall(text), f"Email-like token in {path}"
        assert not phone_re.findall(text), f"Phone-like token in {path}"


def test_ingest_t100_hardening_and_tsa_empty_years(sample_dir: Path, tmp_path: Path) -> None:
    """`ingest_t100`, `ingest_t100_system_carrier_summary`, and `ingest_tsa` harden hub/headers and `years=[]`."""
    assert len(ingest_t100(sample_dir, hub="ord")) == 1472
    with pytest.raises(RuntimeError, match="No T-100 segments ingested"):
        ingest_t100(sample_dir, years=[])
    assert ingest_tsa(sample_dir, years=[]).empty

    t100_dir = tmp_path / "t100"
    t100_dir.mkdir(parents=True)
    (t100_dir / "t100_segment_all_mixed.csv").write_text(
        " YEAR , UNIQUE_CARRIER , CLASS , SEATS , PASSENGERS , DEPARTURES_PERFORMED \n"
        "2019,UA,F,1000,800,10\n"
        "2024,UA,F,2000,1700,20\n"
        "2024,DL,F,500,450,5\n",
        encoding="utf-8",
    )

    summary_all = ingest_t100_system_carrier_summary(tmp_path, carrier="ua")
    assert summary_all["year"].tolist() == [2019, 2024]
    assert (summary_all["carrier"] == "UA").all()
    assert summary_all["load_factor"].tolist() == [pytest.approx(0.8), pytest.approx(0.85)]

    summary_2024 = ingest_t100_system_carrier_summary(tmp_path, carrier="ua", years=[2024])
    assert summary_2024["year"].tolist() == [2024]

    summary_empty = ingest_t100_system_carrier_summary(tmp_path, carrier="ua", years=[])
    assert summary_empty.empty
    assert tuple(summary_empty.columns) == SUMMARY_COLUMNS


def test_acquire_hardening_short_rows_chk_case_and_nested_tsa_html(
    tmp_path: Path, monkeypatch
) -> None:
    """`acquire` handles case-insensitive `chk*`, short CSV lines, nested TSA HTML tags, and `years=[]`."""
    form_html = (
        "<form>"
        '<input type="checkbox" name="CHKALLVARS" value="on" />'
        '<input type="checkbox" name="ChkDownloadZip" value="on" />'
        '<input type="checkbox" name="YEAR" value="on" />'
        "</form>"
    )
    _, checkboxes = parse_transtats_aspnet_form(form_html)
    assert checkboxes == [("YEAR", "on")]

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr(
            "On_Time_Marketing_2024_1.csv",
            "Origin,Marketing_Airline_Network,Duplicate,FlightDate\n"
            "ORD,UA,N,2024-01-01\n"
            "DEN,UA,N,2024-01-01\n"
            "SHORT_ROW\n",
        )
    monkeypatch.setattr(
        "standby.acquire.download_bytes", lambda url, timeout=300: zip_buf.getvalue()
    )
    otp_out = acquire_bts_month_otp(
        2024, 1, tmp_path / "otp", hub=" ord ", marketing_carrier=" ua "
    )
    with open(otp_out, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows == [
        ["Origin", "Marketing_Airline_Network", "Duplicate", "FlightDate"],
        ["ORD", "UA", "N", "2024-01-01"],
    ]

    nested_html = (
        "<table><tr><th>Date</th><th>Num</th></tr>"
        "<tr><td><span>1/1/2024</span></td><td><strong>2,303,507</strong></td></tr>"
        "</table>"
    )
    assert parse_tsa_html_table(nested_html, "2024", "https://example.com") == [
        {
            "date": "2024-01-01",
            "tsa_throughput": "2303507",
            "source_page_year": "2024",
            "source_url": "https://example.com",
        }
    ]

    with pytest.raises(RuntimeError, match="No TSA checkpoint records parsed"):
        acquire_tsa_volumes(tmp_path / "tsa_empty", years=[])
