#!/usr/bin/env python3
"""Build compact, pre-aggregated JSON for the live GitHub Pages BI dashboard.

Reads the committed full-dataset BI extracts in ``exports/looker/``,
``exports/tableau/dim_date.csv``, and ``docs/phase4_validation_summary.json``
using only the Python standard library, and emits
``dashboard/data/dashboard_data.json``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOKER_DIR = REPO_ROOT / "exports" / "looker"
TABLEAU_DIR = REPO_ROOT / "exports" / "tableau"
P4_VAL_PATH = REPO_ROOT / "docs" / "phase4_validation_summary.json"
OUT_PATH = REPO_ROOT / "dashboard" / "data" / "dashboard_data.json"

# Exact airport coordinates (lat, lon) for ORD, major US airports, and all 71 international stations.
AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    # Origin Hub
    "ORD": (41.9742, -87.9073),
    # Major US Domestic Stations
    "LGA": (40.7769, -73.8740),
    "MKE": (42.9472, -87.8966),
    "EWR": (40.6895, -74.1745),
    "DCA": (38.8512, -77.0402),
    "IAH": (29.9902, -95.3368),
    "DEN": (39.8561, -104.6737),
    "SFO": (37.6213, -122.3790),
    "IND": (39.7173, -86.2944),
    "DTW": (42.2162, -83.3554),
    "BNA": (36.1263, -86.6774),
    "LAX": (33.9416, -118.4085),
    "CLE": (41.4058, -81.8539),
    "BOS": (42.3656, -71.0096),
    "STL": (38.7487, -90.3700),
    "MSP": (44.8848, -93.2223),
    "GRR": (42.8808, -85.5228),
    "MSN": (43.1399, -89.3375),
    "CMH": (39.9980, -82.8919),
    "CVG": (39.0488, -84.6678),
    "SDF": (38.1741, -85.7365),
    "MCI": (39.2976, -94.7139),
    "ATW": (44.2581, -88.5191),
    "DAY": (39.9024, -84.2194),
    "PIT": (40.4915, -80.2329),
    "DSM": (41.5340, -93.6631),
    "TVC": (44.7414, -85.5822),
    "MCO": (28.4312, -81.3081),
    "GRB": (44.4851, -88.1296),
    "LAS": (36.0840, -115.1537),
    "SBN": (41.7087, -86.3173),
    "RDU": (35.8801, -78.7880),
    "DFW": (32.8998, -97.0403),
    "MLI": (41.4485, -90.5075),
    "OMA": (41.3032, -95.8941),
    "CID": (41.8847, -91.7108),
    "OKC": (35.3931, -97.6007),
    "PHX": (33.4373, -112.0078),
    "IAD": (38.9531, -77.4565),
    "ATL": (33.6407, -84.4277),
    "FSD": (43.5820, -96.7419),
    "FWA": (40.9785, -85.1951),
    "TYS": (35.8110, -83.9940),
    "LEX": (38.0365, -84.6059),
    "PHL": (39.8744, -75.2424),
    "PIA": (40.6642, -89.6933),
    "SAN": (32.7338, -117.1933),
    "MEM": (35.0424, -89.9767),
    "RIC": (37.5052, -77.3197),
    "ICT": (37.6499, -97.4331),
    "FNT": (42.9654, -83.7436),
    "SEA": (47.4502, -122.3088),
    "SLC": (40.7899, -111.9791),
    "SYR": (43.1112, -76.1063),
    "BUF": (42.9405, -78.7322),
    "ROC": (43.1189, -77.6724),
    "MDT": (40.1935, -76.7634),
    "ALB": (42.7483, -73.8017),
    "SGF": (37.2457, -93.3886),
    "AUS": (30.1975, -97.6664),
    "RSW": (26.5362, -81.7552),
    "MIA": (25.7959, -80.2870),
    "FLL": (26.0742, -80.1506),
    "TPA": (27.9755, -82.5332),
    "CLT": (35.2140, -80.9431),
    "BWI": (39.1774, -76.6684),
    "BDL": (41.9389, -72.6832),
    "PVD": (41.7240, -71.4282),
    "PDX": (45.5898, -122.5951),
    "SMF": (38.6954, -121.5908),
    "SJC": (37.3639, -121.9289),
    "SNA": (33.6762, -117.8675),
    "ONT": (34.0560, -117.6012),
    "MSY": (29.9934, -90.2580),
    "SAT": (29.5337, -98.4698),
    "JAX": (30.4941, -81.6879),
    "PBI": (26.6832, -80.0956),
    "SRQ": (27.3954, -82.5544),
    "CHS": (32.8986, -80.0405),
    "SAV": (32.1276, -81.2021),
    "ORF": (36.8946, -76.2012),
    "GSP": (34.8957, -82.2189),
    "GSO": (36.0978, -79.9373),
    "TUL": (36.1984, -95.8881),
    "LIT": (34.7294, -92.2243),
    "XNA": (36.2819, -94.3068),
    "BHM": (33.5629, -86.7535),
    "HSV": (34.6372, -86.7751),
    "ABQ": (35.0402, -106.6090),
    "ELP": (31.8072, -106.3778),
    "TUS": (32.1161, -110.9410),
    "BOI": (43.5644, -116.2228),
    "BZN": (45.7772, -111.1530),
    "JAC": (43.6073, -110.7377),
    "ASE": (39.2232, -106.8689),
    "EGE": (39.6426, -106.9177),
    "HDN": (40.4812, -107.2177),
    "MTJ": (38.5098, -107.8942),
    "PSP": (33.8303, -116.5067),
    "HNL": (21.3187, -157.9225),
    "OGG": (20.8986, -156.4305),
    "KOA": (19.7388, -156.0456),
    "ANC": (61.1743, -149.9962),
    "FAI": (64.8151, -147.8563),
    "SJU": (18.4394, -66.0018),
    "STT": (18.3373, -64.9734),
    "MHT": (42.9326, -71.4357),
    "PWM": (43.6462, -70.3093),
    "BTV": (44.4720, -73.1533),
    "BGR": (44.8074, -68.8281),
    "HPN": (41.0670, -73.7076),
    "ABE": (40.6521, -75.4408),
    "AVP": (41.3385, -75.7234),
    "ERI": (42.0831, -80.1739),
    "SCE": (40.8493, -77.8487),
    "CRW": (38.3731, -81.5932),
    "ROA": (37.3255, -79.9754),
    "CHO": (38.1386, -78.4529),
    "AVL": (35.4362, -82.5418),
    "MYR": (33.6797, -78.9283),
    "CAE": (33.9388, -81.1195),
    "PNS": (30.4734, -87.1866),
    "VPS": (30.4832, -86.5254),
    "ECP": (30.3571, -85.7954),
    "EYW": (24.5561, -81.7596),
    "LNK": (40.8510, -96.7592),
    "FAR": (46.9207, -96.8158),
    "BIS": (46.7727, -100.7467),
    "RAP": (44.0453, -103.0574),
    "DLH": (46.8420, -92.1936),
    "RST": (43.9083, -92.5000),
    "CWA": (44.7776, -89.6668),
    "EAU": (44.8658, -91.4843),
    "LSE": (43.8790, -91.2567),
    "MQT": (46.3536, -87.3954),
    "LAN": (42.7787, -84.5874),
    "AZO": (42.2349, -85.5521),
    "MBS": (43.5329, -84.0796),
    "MKG": (43.1695, -86.2382),
    "BMI": (40.4771, -88.9159),
    "CMI": (40.0392, -88.2781),
    "SPI": (39.8441, -89.6779),
    "EVV": (38.0370, -87.5324),
    "CAK": (40.9161, -81.4422),
    "TOL": (41.5868, -83.8078),
    "COS": (38.8058, -104.7008),
    "FAT": (36.7762, -119.7181),
    "RNO": (39.4991, -119.7681),
    "GEG": (47.6199, -117.5338),
    "MSO": (46.9163, -114.0906),
    "FCA": (48.3105, -114.2560),
    "BIL": (45.8077, -108.5429),
    "JFK": (40.6413, -73.7781),
    "OAK": (37.7213, -122.2207),
    # All 71 International Stations
    "YYZ": (43.6777, -79.6248),
    "YUL": (45.4706, -73.7408),
    "LHR": (51.4700, -0.4543),
    "CUN": (21.0365, -86.8771),
    "YOW": (45.3225, -75.6692),
    "FRA": (50.0379, 8.5622),
    "YYC": (51.1215, -114.0076),
    "YVR": (49.1967, -123.1815),
    "MEX": (19.4363, -99.0721),
    "MTY": (25.7785, -100.1069),
    "YWG": (49.9100, -97.2399),
    "GRU": (-23.4356, -46.4731),
    "BRU": (50.9010, 4.4844),
    "MUC": (48.3537, 11.7750),
    "CDG": (49.0097, 2.5479),
    "AMS": (52.3105, 4.7683),
    "PUJ": (18.5674, -68.3634),
    "HND": (35.5494, 139.7798),
    "YQB": (46.7911, -71.3933),
    "SJD": (23.1518, -109.7215),
    "ZRH": (47.4647, 8.5492),
    "PVR": (20.6801, -105.2544),
    "FCO": (41.8003, 12.2389),
    "DUB": (53.4264, -6.2499),
    "MBJ": (18.5037, -77.9134),
    "NAS": (25.0390, -77.4662),
    "MXP": (45.6301, 8.7255),
    "EDI": (55.9508, -3.3615),
    "BCN": (41.2974, 2.0833),
    "AUA": (12.5014, -70.0152),
    "KEF": (63.9850, -22.6056),
    "SNN": (52.7020, -8.9248),
    "LIR": (10.5933, -85.5444),
    "GCM": (19.2928, -81.3577),
    "ATH": (37.9364, 23.9445),
    "PEK": (40.0799, 116.6031),
    "NRT": (35.7720, 140.3929),
    "PVG": (31.1443, 121.8083),
    "SJO": (9.9939, -84.2088),
    "PLS": (21.7736, -72.2659),
    "GUA": (14.5833, -90.5275),
    "YHZ": (44.8808, -63.5086),
    "HKG": (22.3080, 113.9185),
    "TQO": (20.1760, -87.4820),
    "BJX": (20.9935, -101.4808),
    "BZE": (17.5391, -88.3082),
    "TLV": (32.0055, 34.8854),
    "SXM": (18.0410, -63.1089),
    "UVF": (13.7332, -60.9526),
    "CZM": (20.5224, -86.9256),
    "YEG": (53.3097, -113.5797),
    "ZIH": (17.6016, -101.4605),
    "DEL": (28.5562, 77.1000),
    "GDL": (20.5218, -103.3112),
    "YYT": (47.6186, -52.7519),
    "MZT": (23.1614, -106.2661),
    "GIG": (-22.8099, -43.2506),
    "GLA": (55.8719, -4.4331),
    "KIN": (17.9357, -76.7875),
    "LGW": (51.1537, -0.1821),
    "LIM": (-12.0219, -77.1143),
    "MGA": (12.1415, -86.1682),
    "MID": (20.9370, -89.6577),
    "MLM": (19.8499, -101.0255),
    "NCE": (43.6584, 7.2159),
    "POP": (19.7579, -70.5700),
    "PTY": (9.0714, -79.3835),
    "QRO": (20.6173, -100.1857),
    "SAL": (13.4409, -89.0557),
    "YJT": (48.5442, -58.5500),
    "YQX": (48.9369, -54.5681),
}

STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "AL": (32.8, -86.8),
    "AK": (63.5, -152.0),
    "AZ": (34.2, -111.6),
    "AR": (34.9, -92.4),
    "CA": (36.8, -119.5),
    "CO": (39.0, -105.5),
    "CT": (41.6, -72.7),
    "DC": (38.9, -77.0),
    "DE": (39.0, -75.5),
    "FL": (28.1, -81.6),
    "GA": (32.6, -83.4),
    "HI": (20.8, -156.3),
    "ID": (44.3, -114.6),
    "IL": (40.0, -89.2),
    "IN": (39.9, -86.3),
    "IA": (42.0, -93.5),
    "KS": (38.5, -98.3),
    "KY": (37.5, -85.3),
    "LA": (31.0, -92.0),
    "ME": (45.2, -69.2),
    "MD": (39.0, -76.7),
    "MA": (42.3, -71.8),
    "MI": (43.8, -84.7),
    "MN": (46.0, -94.3),
    "MS": (32.7, -89.7),
    "MO": (38.4, -92.5),
    "MT": (47.0, -109.6),
    "NE": (41.5, -99.8),
    "NV": (39.3, -116.6),
    "NH": (43.6, -71.5),
    "NJ": (40.2, -74.7),
    "NM": (34.4, -106.1),
    "NY": (42.8, -75.5),
    "NC": (35.5, -79.4),
    "ND": (47.4, -100.4),
    "OH": (40.3, -82.8),
    "OK": (35.5, -97.5),
    "OR": (44.0, -120.5),
    "PA": (40.9, -77.8),
    "PR": (18.2, -66.5),
    "RI": (41.7, -71.5),
    "SC": (33.9, -80.9),
    "SD": (44.4, -100.2),
    "TN": (35.8, -86.3),
    "TX": (31.4, -99.3),
    "UT": (39.3, -111.7),
    "VT": (44.0, -72.7),
    "VA": (37.5, -78.8),
    "VI": (18.3, -64.9),
    "WA": (47.4, -120.5),
    "WV": (38.6, -80.6),
    "WI": (44.6, -89.7),
    "WY": (43.0, -107.5),
}


def _flt(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
        return None if math.isnan(x) or math.isinf(x) else x
    except ValueError:
        return None


def _int(v: str | None) -> int | None:
    x = _flt(v)
    return int(round(x)) if x is not None else None


def _r(v: float | None, digits: int = 2) -> float | None:
    return round(v, digits) if v is not None else None


def _airport_latlon(code: str, state_or_country: str) -> tuple[float, float, bool]:
    if code in AIRPORT_COORDS:
        lat, lon = AIRPORT_COORDS[code]
        return lat, lon, True
    base = STATE_CENTROIDS.get(state_or_country, (39.5, -98.35))
    h = int(hashlib.md5(code.encode("utf-8")).hexdigest()[:8], 16)
    dlat = ((h % 1000) / 1000.0 - 0.5) * 1.4
    dlon = (((h // 1000) % 1000) / 1000.0 - 0.5) * 1.8
    return round(base[0] + dlat, 4), round(base[1] + dlon, 4), False


def _new_agg() -> dict[str, float]:
    return {
        "n": 0,
        "perf": 0,
        "canc": 0,
        "d15_n": 0,
        "d15_sum": 0,
        "lf_n": 0,
        "lf_sum": 0.0,
        "lfa_n": 0,
        "lfa_sum": 0.0,
        "open_n": 0,
        "open_sum": 0.0,
        "open_pct_n": 0,
        "open_pct_sum": 0.0,
        "bkp_n": 0,
        "bkp_sum": 0.0,
        "sp_n": 0,
        "sp_sum": 0.0,
        "sh_n": 0,
        "sh_sum": 0.0,
        "sop_sum": 0.0,
        "sdp_sum": 0.0,
        "sbp_sum": 0.0,
        "smp_sum": 0.0,
        "soh_sum": 0.0,
        "sdh_sum": 0.0,
        "sbh_sum": 0.0,
        "smh_sum": 0.0,
    }


def _update_agg(a: dict[str, float], row: dict[str, str]) -> None:
    a["n"] += 1
    is_canc = row["is_cancelled"] == "True"
    if is_canc:
        a["canc"] += 1
    else:
        a["perf"] += 1
        dd = _flt(row["dep_delay_min"])
        if dd is not None:
            a["d15_n"] += 1
            if dd >= 15.0:
                a["d15_sum"] += 1
        lf = _flt(row["est_load_factor"])
        if lf is not None:
            a["lf_n"] += 1
            a["lf_sum"] += lf
        lfa = _flt(row["est_lf_adjusted"])
        if lfa is not None:
            a["lfa_n"] += 1
            a["lfa_sum"] += lfa
        op = _flt(row["est_open_seats_adjusted"])
        seats = _flt(row["seats_config"])
        if op is not None:
            a["open_n"] += 1
            a["open_sum"] += op
            if seats and seats > 0:
                a["open_pct_n"] += 1
                a["open_pct_sum"] += op / seats

    bkp = _flt(row["remaining_scheduled_backups"])
    if bkp is not None:
        a["bkp_n"] += 1
        a["bkp_sum"] += bkp

    sp = _flt(row["soi_planning"])
    if sp is not None:
        a["sp_n"] += 1
        a["sp_sum"] += sp
        a["sop_sum"] += _flt(row["s_open_plan"]) or 0.0
        a["sdp_sum"] += _flt(row["s_disp_plan"]) or 0.0
        a["sbp_sum"] += _flt(row["s_backup_plan"]) or 0.0
        a["smp_sum"] += _flt(row["s_demand_plan"]) or 0.0

    sh = _flt(row["soi_historical"])
    if sh is not None:
        a["sh_n"] += 1
        a["sh_sum"] += sh
        a["soh_sum"] += _flt(row["s_open_hist"]) or 0.0
        a["sdh_sum"] += _flt(row["s_disp_hist"]) or 0.0
        a["sbh_sum"] += _flt(row["s_backup_hist"]) or 0.0
        a["smh_sum"] += _flt(row["s_demand_hist"]) or 0.0


def _compact_agg(a: dict[str, float]) -> dict[str, Any]:
    return {
        "n": int(a["n"]),
        "perf": int(a["perf"]),
        "canc": int(a["canc"]),
        "d15_n": int(a["d15_n"]),
        "d15_s": int(a["d15_sum"]),
        "lf_n": int(a["lf_n"]),
        "lf_s": round(a["lf_sum"], 3),
        "lfa_n": int(a["lfa_n"]),
        "lfa_s": round(a["lfa_sum"], 3),
        "op_n": int(a["open_n"]),
        "op_s": round(a["open_sum"], 2),
        "opp_n": int(a["open_pct_n"]),
        "opp_s": round(a["open_pct_sum"], 3),
        "bkp_n": int(a["bkp_n"]),
        "bkp_s": round(a["bkp_sum"], 2),
        "sp_n": int(a["sp_n"]),
        "sp_s": round(a["sp_sum"], 2),
        "sop_s": round(a["sop_sum"], 2),
        "sdp_s": round(a["sdp_sum"], 2),
        "sbp_s": round(a["sbp_sum"], 2),
        "smp_s": round(a["smp_sum"], 2),
        "sh_n": int(a["sh_n"]),
        "sh_s": round(a["sh_sum"], 2),
        "soh_s": round(a["soh_sum"], 2),
        "sdh_s": round(a["sdh_sum"], 2),
        "sbh_s": round(a["sbh_sum"], 2),
        "smh_s": round(a["smh_sum"], 2),
    }


def _compact_heat(a: dict[str, float]) -> list[Any]:
    # [n, sp_n, sp_s, sh_n, sh_s, op_n, op_s, sop_s, sdp_s, sbp_s, smp_s, soh_s, sdh_s, sbh_s, smh_s]
    return [
        int(a["n"]),
        int(a["sp_n"]),
        round(a["sp_sum"], 1),
        int(a["sh_n"]),
        round(a["sh_sum"], 1),
        int(a["open_n"]),
        round(a["open_sum"], 1),
        round(a["sop_sum"], 1),
        round(a["sdp_sum"], 1),
        round(a["sbp_sum"], 1),
        round(a["smp_sum"], 1),
        round(a["soh_sum"], 1),
        round(a["sdh_sum"], 1),
        round(a["sbh_sum"], 1),
        round(a["smh_sum"], 1),
    ]


def build() -> dict[str, Any]:
    # 1. Read TSA monthly means from dim_date.csv
    tsa_month_sum: dict[str, float] = defaultdict(float)
    tsa_month_n: dict[str, int] = defaultdict(int)
    with (TABLEAU_DIR / "dim_date.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ym = row["date_day"][:7]
            val = _flt(row["tsa_throughput"])
            if val is not None:
                tsa_month_sum[ym] += val
                tsa_month_n[ym] += 1
    tsa_by_ym = {ym: round(tsa_month_sum[ym] / tsa_month_n[ym]) for ym in tsa_month_n}

    # 2. Stream looker_flights.csv (100,000 stratified flights)
    kpis_by_slice: dict[str, dict[str, float]] = defaultdict(_new_agg)
    heatmap_slices: dict[str, dict[str, float]] = defaultdict(_new_agg)
    holiday_slices: dict[str, dict[str, float]] = defaultdict(_new_agg)
    fleet_slices: dict[str, dict[str, Any]] = {}
    carrier_flight_slices: dict[str, dict[str, float]] = defaultdict(_new_agg)
    sample_flights_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    hb_order = {"early": 0, "morning": 1, "midday": 2, "afternoon": 3, "evening": 4, "late": 5}

    with (LOOKER_DIR / "looker_flights.csv").open(newline="", encoding="utf-8") as f:
        for idx, row in enumerate(csv.DictReader(f)):
            yr = row["year"]
            cg = row["carrier_group"]
            dow = row["day_of_week"]
            hb = row["hour_block"]
            hol = row["holiday_name"] or "Non-Holiday Baseline"
            car = row["op_carrier"]
            sub = row["fleet_subtype"] or "UNKNOWN"

            _update_agg(kpis_by_slice[f"{yr}|{cg}"], row)
            _update_agg(heatmap_slices[f"{yr}|{cg}|{dow}|{hb_order.get(hb, 5)}"], row)
            _update_agg(holiday_slices[f"{yr}|{cg}|{hol}"], row)
            _update_agg(carrier_flight_slices[f"{yr}|{car}"], row)

            fkey = f"{yr}|{sub}"
            if fkey not in fleet_slices:
                fleet_slices[fkey] = {
                    "agg": _new_agg(),
                    "subtype": sub,
                    "cg": cg,
                    "seats": _int(row["seats_config"]),
                    "mfr": row["faa_manufacturer"],
                    "mdl": row["faa_model"],
                }
            _update_agg(fleet_slices[fkey]["agg"], row)

            # Stratified deterministic sample (~1,400 flights) across (year, hour_block)
            if idx % 71 == 0:
                sp = _flt(row["soi_planning"])
                sh = _flt(row["soi_historical"])
                sample_flights_buckets[(yr, hb)].append(
                    {
                        "d": row["flight_date"],
                        "y": int(yr),
                        "t": row["crs_dep_time"],
                        "fn": row["flight_num_mkt"],
                        "c": car,
                        "cg": cg[0],  # M or E
                        "dst": row["dest"],
                        "cty": row["dest_city_name"],
                        "st": row["dest_state_or_country"],
                        "hb": hb_order.get(hb, 5),
                        "sub": sub,
                        "sc": _int(row["seats_config"]),
                        "lf": _r(_flt(row["est_load_factor"]), 3),
                        "lfa": _r(_flt(row["est_lf_adjusted"]), 3),
                        "op": _r(_flt(row["est_open_seats_adjusted"]), 1),
                        "bkp": _int(row["remaining_scheduled_backups"]),
                        "cnc": 1 if row["is_cancelled"] == "True" else 0,
                        "dd": _r(_flt(row["dep_delay_min"]), 0),
                        "sh": _r(sh, 1),
                        "sp": _r(sp, 1),
                        "s4h": [
                            _r(_flt(row["s_open_hist"]), 1),
                            _r(_flt(row["s_disp_hist"]), 1),
                            _r(_flt(row["s_backup_hist"]), 1),
                            _r(_flt(row["s_demand_hist"]), 1),
                        ]
                        if sh is not None
                        else None,
                        "s4p": [
                            _r(_flt(row["s_open_plan"]), 1),
                            _r(_flt(row["s_disp_plan"]), 1),
                            _r(_flt(row["s_backup_plan"]), 1),
                            _r(_flt(row["s_demand_plan"]), 1),
                        ]
                        if sp is not None
                        else None,
                        "lfs": row["lf_source"],
                        "hol": row["holiday_name"] or "",
                        "sha": 1 if row["is_shared_regional_spoke"] == "True" else 0,
                    }
                )

    sample_flights: list[dict[str, Any]] = []
    for k in sorted(sample_flights_buckets.keys()):
        sample_flights.extend(sample_flights_buckets[k])
    sample_flights.sort(key=lambda x: (x["d"], x["t"]), reverse=True)

    # 3. Read looker_route_summary.csv (348 routes)
    routes: list[dict[str, Any]] = []
    with (LOOKER_DIR / "looker_route_summary.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dest = row["dest"]
            st = row["dest_state_or_country"]
            lat, lon, exact = _airport_latlon(dest, st)
            is_intl = row["is_international"] == "True"
            routes.append(
                {
                    "id": row["route_id"],
                    "dst": dest,
                    "name": row["dest_airport_name"],
                    "cty": row["dest_city_name"],
                    "st": st,
                    "cc": row["dest_country_code"],
                    "intl": 1 if is_intl else 0,
                    "lat": lat,
                    "lon": lon,
                    "exact_geo": 1 if exact else 0,
                    "deps": _int(row["total_departures_performed"]) or 0,
                    "seats": _int(row["total_seats"]) or 0,
                    "pax": _int(row["total_passengers"]) or 0,
                    "lf": _r(_flt(row["avg_t100_load_factor"]), 4),
                    "spd": _r(_flt(row["avg_t100_seats_per_departure"]), 1),
                    "dist": _int(row["distance_miles"]) or 0,
                    "mos": _int(row["months_active"]) or 0,
                    "cars": _int(row["carrier_count"]) or 0,
                    "sha": 1 if row["is_shared_regional_spoke"] == "True" else 0,
                    "otp_n": _int(row["otp_scheduled_flights"]),
                    "canc_r": _r(_flt(row["otp_cancellation_rate"]), 4),
                    "open": _r(_flt(row["otp_avg_est_open_seats_adjusted"]), 1),
                    "sh": _r(_flt(row["otp_avg_soi_historical"]), 2),
                    "sp": _r(_flt(row["otp_avg_soi_planning"]), 2),
                }
            )
    routes.sort(key=lambda r: -r["deps"])

    # 4. Read looker_route_month.csv (20,877 rows) for:
    #    - 56-month series by (year_month, carrier_group, is_intl)
    #    - Carrier summaries by (year, op_carrier)
    #    - Per-route monthly sparkline/drilldown for all routes (compact!)
    monthly_acc: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "deps": 0.0,
            "seats": 0.0,
            "pax": 0.0,
            "otp_n": 0.0,
            "otp_perf": 0.0,
            "otp_canc": 0.0,
            "delay_w": 0.0,
            "delay_n": 0.0,
            "open_w": 0.0,
            "open_n": 0.0,
            "sh_w": 0.0,
            "sh_n": 0.0,
            "sp_w": 0.0,
            "sp_n": 0.0,
        }
    )
    carrier_meta: dict[str, dict[str, Any]] = {}
    carrier_t100: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "deps": 0.0,
            "seats": 0.0,
            "pax": 0.0,
            "dom_deps": 0.0,
            "intl_deps": 0.0,
        }
    )
    route_month_acc: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {
            "deps": 0.0,
            "seats": 0.0,
            "pax": 0.0,
            "open_w": 0.0,
            "open_n": 0.0,
            "sh_w": 0.0,
            "sh_n": 0.0,
            "sp_w": 0.0,
            "sp_n": 0.0,
        }
    )

    with (LOOKER_DIR / "looker_route_month.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ym = row["year_month"]
            yr = row["year"]
            cg = row["carrier_group"]
            intl = "INTL" if row["is_international"] == "True" else "DOM"
            car = row["op_carrier"]
            dest = row["dest"]

            if car not in carrier_meta:
                carrier_meta[car] = {
                    "code": car,
                    "name": row["carrier_name"],
                    "cg": cg,
                    "shared_cpa": 1 if row["is_shared_cpa_carrier"] == "True" else 0,
                }

            deps = _flt(row["departures_performed"]) or 0.0
            seats = _flt(row["seats"]) or 0.0
            pax = _flt(row["passengers"]) or 0.0
            otp_n = _flt(row["otp_scheduled_flights"]) or 0.0
            otp_perf = _flt(row["otp_performed_flights"]) or 0.0
            otp_canc = _flt(row["otp_cancelled_flights"]) or 0.0
            delay = _flt(row["otp_avg_dep_delay_min"])
            op = _flt(row["otp_avg_est_open_seats_adjusted"])
            sh = _flt(row["otp_avg_soi_historical"])
            sp = _flt(row["otp_avg_soi_planning"])

            for m_key in (f"{ym}|{cg}|{intl}",):
                m = monthly_acc[m_key]
                m["deps"] += deps
                m["seats"] += seats
                m["pax"] += pax
                m["otp_n"] += otp_n
                m["otp_perf"] += otp_perf
                m["otp_canc"] += otp_canc
                if delay is not None and otp_perf > 0:
                    m["delay_w"] += delay * otp_perf
                    m["delay_n"] += otp_perf
                if op is not None and otp_perf > 0:
                    m["open_w"] += op * otp_perf
                    m["open_n"] += otp_perf
                if sh is not None and otp_perf > 0:
                    m["sh_w"] += sh * otp_perf
                    m["sh_n"] += otp_perf
                if sp is not None and otp_n > 0:
                    m["sp_w"] += sp * otp_n
                    m["sp_n"] += otp_n

            ct = carrier_t100[f"{yr}|{car}"]
            ct["deps"] += deps
            ct["seats"] += seats
            ct["pax"] += pax
            if intl == "INTL":
                ct["intl_deps"] += deps
            else:
                ct["dom_deps"] += deps

            rm = route_month_acc[(f"{dest}|{intl}", ym)]
            rm["deps"] += deps
            rm["seats"] += seats
            rm["pax"] += pax
            if op is not None and otp_perf > 0:
                rm["open_w"] += op * otp_perf
                rm["open_n"] += otp_perf
            if sh is not None and otp_perf > 0:
                rm["sh_w"] += sh * otp_perf
                rm["sh_n"] += otp_perf
            if sp is not None and otp_n > 0:
                rm["sp_w"] += sp * otp_n
                rm["sp_n"] += otp_n

    # Compact monthly series keyed by "YYYY-MM|CG|SCOPE"
    monthly_series: dict[str, list[Any]] = {}
    for k, m in sorted(monthly_acc.items()):
        # [deps, seats, pax, otp_n, otp_perf, otp_canc, delay_w, delay_n, open_w, open_n, sh_w, sh_n, sp_w, sp_n]
        monthly_series[k] = [
            int(round(m["deps"])),
            int(round(m["seats"])),
            int(round(m["pax"])),
            int(round(m["otp_n"])),
            int(round(m["otp_perf"])),
            int(round(m["otp_canc"])),
            round(m["delay_w"], 1),
            int(round(m["delay_n"])),
            round(m["open_w"], 1),
            int(round(m["open_n"])),
            round(m["sh_w"], 1),
            int(round(m["sh_n"])),
            round(m["sp_w"], 1),
            int(round(m["sp_n"])),
        ]

    # Top 80 routes monthly series for interactive drill-down drawer
    top_route_keys = {f"{r['dst']}|{'INTL' if r['intl'] else 'DOM'}" for r in routes[:80]}
    route_monthly: dict[str, list[list[Any]]] = defaultdict(list)
    for (rk, ym), rm in sorted(route_month_acc.items()):
        if rk not in top_route_keys:
            continue
        lf = round(min(1.0, rm["pax"] / rm["seats"]), 3) if rm["seats"] > 0 else None
        op = round(rm["open_w"] / rm["open_n"], 1) if rm["open_n"] > 0 else None
        sh = round(rm["sh_w"] / rm["sh_n"], 1) if rm["sh_n"] > 0 else None
        sp = round(rm["sp_w"] / rm["sp_n"], 1) if rm["sp_n"] > 0 else None
        route_monthly[rk].append([ym, int(round(rm["deps"])), lf, op, sp, sh])

    # Compact carrier slices
    carriers_data = {
        "meta": sorted(
            carrier_meta.values(), key=lambda c: (0 if c["cg"] == "MAINLINE" else 1, c["code"])
        ),
        "t100": {
            k: [
                int(round(v["deps"])),
                int(round(v["seats"])),
                int(round(v["pax"])),
                int(round(v["dom_deps"])),
                int(round(v["intl_deps"])),
            ]
            for k, v in sorted(carrier_t100.items())
        },
        "flights": {k: _compact_agg(v) for k, v in sorted(carrier_flight_slices.items())},
    }

    # Compact fleet slices
    fleet_meta: dict[str, dict[str, Any]] = {}
    fleet_by_year: dict[str, dict[str, Any]] = {}
    for fkey, entry in sorted(fleet_slices.items()):
        sub = entry["subtype"]
        if sub not in fleet_meta or (entry["seats"] and not fleet_meta[sub]["seats"]):
            fleet_meta[sub] = {
                "sub": sub,
                "cg": entry["cg"],
                "seats": entry["seats"],
                "mfr": entry["mfr"],
                "mdl": entry["mdl"],
            }
        fleet_by_year[fkey] = _compact_agg(entry["agg"])

    # 5. Read Phase 4 Validation JSON
    with P4_VAL_PATH.open(encoding="utf-8") as f:
        p4 = json.load(f)

    # Clean weights float noise for display
    weights_clean = {k: round(v, 2) for k, v in p4["metadata"]["weights"].items()}

    payload: dict[str, Any] = {
        "meta": {
            "title": "United Standby Opportunity Explorer (ORD Hub)",
            "hub": "ORD",
            "years": [2019, 2023, 2024, 2025, 2026],
            "months_count": 56,
            "date_range": "2019-01 to 2026-08",
            "total_flights": p4["metadata"]["total_flights"],
            "performed_flights": p4["metadata"]["performed_flights"],
            "cancelled_flights": p4["metadata"]["cancelled_flights"],
            "route_months_total": 20877,
            "routes_total": len(routes),
            "routes_dom": sum(1 for r in routes if not r["intl"]),
            "routes_intl": sum(1 for r in routes if r["intl"]),
            "weights": weights_clean,
            "warehouse": {
                "project_id": "flight-standby-analytics",
                "location": "US",
                "loaded_date": "2026-11-18",
                "dbt_models": 20,
                "dbt_tests_passed": 231,
                "dbt_tests_total": 231,
                "storage_gib": 0.9,
                "partitions": 1704,
            },
        },
        "tsa_by_ym": tsa_by_ym,
        "kpis": {k: _compact_agg(v) for k, v in sorted(kpis_by_slice.items())},
        "heatmap": {k: _compact_heat(v) for k, v in sorted(heatmap_slices.items())},
        "holidays": {k: _compact_agg(v) for k, v in sorted(holiday_slices.items())},
        "monthly": monthly_series,
        "routes": routes,
        "route_monthly": dict(route_monthly),
        "carriers": carriers_data,
        "fleet": {
            "meta": sorted(fleet_meta.values(), key=lambda x: -(x["seats"] or 0)),
            "by_year": fleet_by_year,
        },
        "sample_flights": sample_flights,
        "validation": {
            "by_year": p4["coverage_and_invariants"]["by_year"],
            "dist_hist": p4["coverage_and_invariants"]["soi_historical_distribution"],
            "dist_plan": p4["coverage_and_invariants"]["soi_planning_distribution"],
            "pillar1_fixed": p4["pillar_1_time_based_backtest"]["fixed_holdout"],
            "pillar1_wf_by_year": p4["pillar_1_time_based_backtest"]["expanding_walk_forward"][
                "by_year"
            ],
            "pillar1_wf_combined": p4["pillar_1_time_based_backtest"]["expanding_walk_forward"][
                "combined"
            ],
            "caveat": p4["pillar_1_time_based_backtest"]["methodology_caveat"],
            "pillar2_hist": p4["pillar_2_face_validity"]["soi_historical"],
            "pillar2_plan": p4["pillar_2_face_validity"]["soi_planning"],
            "pillar2_tests": p4["pillar_2_face_validity"]["statistical_separation_tests"],
            "pillar2_nuances": p4["pillar_2_face_validity"]["nuances"],
            "pillar3_schemes": p4["pillar_3_weight_sensitivity"]["schemes"],
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    OUT_PATH.write_text(raw_json, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(raw_json) / 1024:.1f} KiB)")
    print(
        f"  routes={len(routes)} (dom={payload['meta']['routes_dom']}, intl={payload['meta']['routes_intl']}), "
        f"route_monthly={len(route_monthly)}, sample_flights={len(sample_flights)}, "
        f"fleet={len(fleet_meta)}, heatmap_cells={len(heatmap_slices)}"
    )
    return payload


def build_predictor_priors() -> Path:
    """Fit hierarchical empirical Bayes priors + walk-forward calibration for the Trip Predictor."""
    import sys

    src_dir = str(REPO_ROOT / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    import pandas as pd

    from standby.config import load_config
    from standby.prediction import (
        evaluate_walk_forward_calibration,
        fit_predictor_priors,
        save_priors_json,
    )

    cfg = load_config(repo_root=REPO_ROOT)
    flights_df = pd.read_csv(LOOKER_DIR / "looker_flights.csv", low_memory=False)
    route_month_df = pd.read_csv(LOOKER_DIR / "looker_route_month.csv", low_memory=False)
    route_summary_df = pd.read_csv(LOOKER_DIR / "looker_route_summary.csv", low_memory=False)

    priors = fit_predictor_priors(
        flights_df=flights_df,
        route_month_df=route_month_df,
        route_summary_df=route_summary_df,
        config=cfg,
    )
    cal = evaluate_walk_forward_calibration(
        flights_df=flights_df,
        route_month_df=route_month_df,
        route_summary_df=route_summary_df,
        config=cfg,
    )
    priors.calibration = cal.to_dict()

    priors_out = REPO_ROOT / "dashboard" / "data" / "predictor_priors.json"
    save_priors_json(priors, priors_out)
    size_kb = priors_out.stat().st_size / 1024.0
    print(
        f"Wrote {priors_out} ({size_kb:.1f} KiB) | "
        f"routes={len(priors.routes)}, t1={len(priors.tier1)}, t2={len(priors.tier2)}, t3={len(priors.tier3)}, "
        f"cal_n={cal.n_eval_flights}"
    )
    return priors_out


if __name__ == "__main__":
    build()
    build_predictor_priors()
