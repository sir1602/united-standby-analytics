"""Scripted data acquisition utilities for BTS OTP, BTS T-100, BTS Lookups, FAA Registry, and TSA."""

from __future__ import annotations

import csv
import http.cookiejar
import io
import logging
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from standby.manifest import LOOKUP_SOURCE_URLS

logger = logging.getLogger("standby.acquire")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

T100_FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4"
ASPNET_HIDDEN_FIELDS: tuple[str, ...] = (
    "affiliate",
    "__EVENTTARGET",
    "__EVENTARGUMENT",
    "__LASTFOCUS",
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
)
TSA_VOLUME_URLS: dict[int, str] = {
    2019: "https://www.tsa.gov/travel/passenger-volumes/2019",
    2023: "https://www.tsa.gov/travel/passenger-volumes/2023",
    2024: "https://www.tsa.gov/travel/passenger-volumes/2024",
    2025: "https://www.tsa.gov/travel/passenger-volumes/2025",
    2026: "https://www.tsa.gov/travel/passenger-volumes",
}


class _TranstatsFormParser(HTMLParser):
    """Parse ASP.NET hidden inputs and column checkboxes from TranStats `DL_SelectFields.aspx`."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden_fields: dict[str, str] = {}
        self.data_checkboxes: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        input_type = attr_map.get("type", "").lower()
        name = attr_map.get("name", "").strip()
        if not name:
            return
        if input_type == "hidden":
            self.hidden_fields[name] = attr_map.get("value", "")
        elif input_type == "checkbox" and not name.lower().startswith("chk"):
            # Strictly exclude control checkboxes (`chkAllVars`, `chkAllGroups`, `chkDownloadZip`)
            self.data_checkboxes.append((name, attr_map.get("value", "on") or "on"))


def parse_transtats_aspnet_form(html_text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Extract hidden ASP.NET fields and data column checkboxes from TranStats HTML."""
    parser = _TranstatsFormParser()
    parser.feed(html_text)
    hidden = {k: parser.hidden_fields.get(k, "") for k in ASPNET_HIDDEN_FIELDS}
    for k, v in parser.hidden_fields.items():
        hidden.setdefault(k, v)
    return hidden, parser.data_checkboxes


def download_bytes(url: str, timeout: int = 120) -> bytes:
    """Download binary content from a public URL using a standard browser User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def acquire_bts_month_otp(
    year: int,
    month: int,
    output_dir: Path,
    hub: str = "ORD",
    marketing_carrier: str = "UA",
) -> Path:
    """Download a monthly BTS Marketing Carrier PREZIP archive and filter to hub & carrier."""
    hub_norm = hub.strip().upper()
    carrier_norm = marketing_carrier.strip().upper()
    if not hub_norm:
        raise ValueError("hub must not be empty")
    if not carrier_norm:
        raise ValueError("marketing_carrier must not be empty")

    url = (
        "https://transtats.bts.gov/PREZIP/"
        f"On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{year}_{month}.zip"
    )
    logger.info(f"Downloading BTS Marketing OTP PREZIP for {year}-{month:02d} from {url}")
    raw_zip = download_bytes(url, timeout=300)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = (
        output_dir / f"otp_mkt_{hub_norm.lower()}_{carrier_norm.lower()}_{year}_{month:02d}.csv"
    )

    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"No CSV found inside {url}")
        with zf.open(csv_names[0], "r") as raw_stream:
            text_stream = io.TextIOWrapper(
                raw_stream, encoding="utf-8", errors="replace", newline=""
            )
            reader = csv.reader(text_stream)
            header = next(reader, None)
            if not header:
                raise RuntimeError(f"Empty OTP CSV header inside {url}")
            clean_header = [h.strip() for h in header if h.strip()]
            n_cols = len(clean_header)
            col_idx = {name: i for i, name in enumerate(clean_header)}
            required_cols = {"Origin", "Marketing_Airline_Network", "Duplicate"}
            missing_cols = required_cols - set(col_idx)
            if missing_cols:
                raise RuntimeError(
                    f"Missing required OTP columns {sorted(missing_cols)} inside {url}"
                )

            with open(out_file, "w", encoding="utf-8", newline="") as out_f:
                writer = csv.writer(out_f)
                writer.writerow(clean_header)
                for row in reader:
                    row_trimmed = row[:n_cols]
                    if len(row_trimmed) < n_cols:
                        continue
                    if (
                        row_trimmed[col_idx["Origin"]].strip().upper() == hub_norm
                        and row_trimmed[col_idx["Marketing_Airline_Network"]].strip().upper()
                        == carrier_norm
                        and row_trimmed[col_idx["Duplicate"]].strip().upper() == "N"
                    ):
                        writer.writerow(row_trimmed)

    return out_file


def acquire_t100_year(
    year: int,
    output_dir: Path,
    geography: str = "All",
    period: str = "All",
    timeout: int = 300,
    opener: urllib.request.OpenerDirector | None = None,
) -> Path:
    """Download one annual BTS T-100 Segment (`FMG`) CSV via the ASP.NET GET -> POST protocol."""
    http_opener = opener or urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    get_req = urllib.request.Request(T100_FORM_URL, headers={"User-Agent": USER_AGENT})
    logger.info(f"Fetching TranStats T-100 FMG ASP.NET form for year={year}")
    with http_opener.open(get_req, timeout=timeout) as get_resp:
        html_text = get_resp.read().decode("utf-8", errors="replace")

    hidden_fields, data_checkboxes = parse_transtats_aspnet_form(html_text)
    if not data_checkboxes:
        raise RuntimeError(f"No T-100 data column checkboxes parsed from {T100_FORM_URL}")

    form_pairs: list[tuple[str, str]] = list(hidden_fields.items())
    form_pairs.extend(data_checkboxes)
    form_pairs.extend(
        [
            ("cboGeography", geography),
            ("cboYear", str(year)),
            ("cboPeriod", period),
            ("btnDownload", "Download"),
        ]
    )
    encoded_body = urllib.parse.urlencode(form_pairs).encode("utf-8")
    post_req = urllib.request.Request(
        T100_FORM_URL,
        data=encoded_body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": T100_FORM_URL,
        },
        method="POST",
    )
    logger.info(f"Submitting TranStats T-100 FMG download POST for year={year}")
    with http_opener.open(post_req, timeout=timeout) as post_resp:
        zip_bytes = post_resp.read()

    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"t100_segment_all_{year}.csv"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"No CSV archive member returned for T-100 year={year}")
        with zf.open(csv_names[0], "r") as src, open(out_file, "wb") as dst:
            dst.write(src.read())

    return out_file


def acquire_bts_lookups(output_dir: Path) -> list[Path]:
    """Download the 5 BTS Lookup CSVs via TranStats Download_Lookup.asp URLs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, url in LOOKUP_SOURCE_URLS.items():
        logger.info(f"Downloading BTS lookup {filename} from {url}")
        data = download_bytes(url, timeout=60)
        target = output_dir / filename
        target.write_bytes(data)
        written.append(target)
    return written


def acquire_faa_registry(output_dir: Path) -> list[Path]:
    """Download FAA ReleasableAircraft.zip and extract MASTER.txt, DEREG.txt, and ACFTREF.txt."""
    url = "https://registry.faa.gov/database/ReleasableAircraft.zip"
    logger.info(f"Downloading FAA Aircraft Registry from {url}")
    raw_zip = download_bytes(url, timeout=300)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        for member in ("MASTER.txt", "DEREG.txt", "ACFTREF.txt"):
            target = output_dir / member
            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            written.append(target)
    return written


class _TsaTableParser(HTMLParser):
    """Extract `<td>` cell text per `<tr>` row, handling nested tags inside `<td>...</td>`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_tr = False
        self._in_td = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "tr":
            if self._in_tr and self._current_row:
                self.rows.append(self._current_row)
            self._in_tr = True
            self._current_row = []
        elif t == "td" and self._in_tr:
            if self._in_td:
                self._current_row.append("".join(self._current_cell).strip())
            self._in_td = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "td" and self._in_td:
            self._current_row.append("".join(self._current_cell).strip())
            self._in_td = False
            self._current_cell = []
        elif t == "tr" and self._in_tr:
            if self._in_td:
                self._current_row.append("".join(self._current_cell).strip())
                self._in_td = False
                self._current_cell = []
            if self._current_row:
                self.rows.append(self._current_row)
            self._in_tr = False
            self._current_row = []

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._current_cell.append(data)


def parse_tsa_html_table(html_text: str, page_year: str, source_url: str) -> list[dict[str, str]]:
    """Extract `(date, tsa_throughput, source_page_year, source_url)` rows from TSA HTML."""
    parser = _TsaTableParser()
    parser.feed(html_text)
    parser.close()

    records: list[dict[str, str]] = []
    for row in parser.rows:
        if len(row) < 2:
            continue
        clean_date = " ".join(row[0].split())
        clean_num = re.sub(r"[^\d]", "", row[1])
        if not clean_num:
            continue
        try:
            dt = datetime.strptime(clean_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        records.append(
            {
                "date": dt,
                "tsa_throughput": clean_num,
                "source_page_year": page_year,
                "source_url": source_url,
            }
        )
    return records


def acquire_tsa_volumes(
    output_dir: Path,
    years: list[int] | None = None,
    current_year: int = 2026,
    timeout: int = 60,
) -> Path:
    """Download TSA daily checkpoint volume pages, deduplicate, sort ascending, and write CSV."""
    target_years = years if years is not None else sorted(TSA_VOLUME_URLS.keys())
    by_date: dict[str, dict[str, str]] = {}

    for year in target_years:
        url = TSA_VOLUME_URLS.get(
            year,
            "https://www.tsa.gov/travel/passenger-volumes"
            if year == current_year
            else f"https://www.tsa.gov/travel/passenger-volumes/{year}",
        )
        logger.info(f"Downloading TSA checkpoint volumes for {year} from {url}")
        html_text = download_bytes(url, timeout=timeout).decode("utf-8", errors="replace")
        for rec in parse_tsa_html_table(html_text, page_year=str(year), source_url=url):
            by_date.setdefault(rec["date"], rec)

    if not by_date:
        raise RuntimeError(f"No TSA checkpoint records parsed for years={target_years}")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "tsa_checkpoint_volumes.csv"
    with open(out_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "tsa_throughput", "source_page_year", "source_url"],
        )
        writer.writeheader()
        for dt in sorted(by_date.keys()):
            writer.writerow(by_date[dt])

    logger.info(f"Wrote {len(by_date):,} TSA daily checkpoint rows to {out_file}")
    return out_file
