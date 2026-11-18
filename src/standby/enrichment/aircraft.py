"""Step 1 of Phase 2 enrichment: tail-number normalization and 3-tier aircraft/seat resolution."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from standby.ingest.faa import COMMERCIAL_MFR_PREFIXES

logger = logging.getLogger("standby.enrichment.aircraft")

VALID_US_N_NUMBER_RE = re.compile(r"^N[1-9][0-9A-Z]{0,4}$")

SEAT_CONFIG_REQUIRED_COLS: tuple[str, ...] = (
    "subtype",
    "op_carrier_group",
    "seats_total",
    "seats_premium",
    "seats_economy",
    "source_url",
    "as_of",
)
CROSSWALK_REQUIRED_COLS: tuple[str, ...] = (
    "faa_mfr",
    "faa_model",
    "op_carrier",
    "subtype",
    "bts_aircraft_type",
)
TAIL_OVERRIDE_REQUIRED_COLS: tuple[str, ...] = (
    "tail_num",
    "op_carrier",
    "subtype",
    "bts_aircraft_type",
    "seats_total",
    "seats_premium",
    "seats_economy",
    "source_url",
    "as_of",
    "override_reason",
)


def normalize_tail_num(raw_tail: object) -> tuple[str | None, str | None]:
    """
    Normalize a raw OTP `Tail_Number` to `(tail_num_normalized, faa_n_number_key)`.

    Rules:
      1. None / NaN / empty / 'NAN' / 'NONE' -> `(None, None)`.
      2. Strip whitespace and uppercase.
      3. Ensure single leading `'N'` for `tail_num_normalized`.
      4. Validate US N-number pattern (`^N[1-9][0-9A-Z]{0,4}$`); reject dispatcher
         placeholder typos like `'NTCR2'` by returning `(tail_num_normalized, None)`.
    """
    if raw_tail is None:
        return None, None
    try:
        if pd.isna(raw_tail):
            return None, None
    except TypeError:
        pass

    s = str(raw_tail).strip().upper()
    if not s or s in {"NAN", "NONE"}:
        return None, None

    tail_norm = s if s.startswith("N") else f"N{s}"
    if not VALID_US_N_NUMBER_RE.fullmatch(tail_norm):
        return tail_norm, None
    return tail_norm, tail_norm[1:]


def load_seat_config(reference_dir: Path) -> pd.DataFrame:
    """Load and validate `reference/seat_config.csv`."""
    path = reference_dir / "seat_config.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing seat config reference file: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in SEAT_CONFIG_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"seat_config.csv missing required columns: {missing}")

    df["subtype"] = df["subtype"].str.strip()
    df["op_carrier_group"] = df["op_carrier_group"].str.strip().str.upper()
    for col in ("seats_total", "seats_premium", "seats_economy"):
        df[col] = pd.to_numeric(df[col], errors="raise").astype("int64")
    if (df["seats_total"] != df["seats_premium"] + df["seats_economy"]).any():
        raise ValueError(
            "seat_config.csv invariant failed: seats_total != seats_premium + seats_economy"
        )
    if df["subtype"].duplicated().any():
        raise ValueError("seat_config.csv contains duplicate subtype keys")
    return df


def load_faa_t100_crosswalk(reference_dir: Path) -> pd.DataFrame:
    """Load and validate `reference/faa_t100_crosswalk.csv`."""
    path = reference_dir / "faa_t100_crosswalk.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing FAA-to-T100 crosswalk file: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in CROSSWALK_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"faa_t100_crosswalk.csv missing required columns: {missing}")

    df["faa_mfr"] = df["faa_mfr"].str.strip().str.upper()
    df["faa_model"] = df["faa_model"].str.strip().str.upper()
    df["op_carrier"] = df["op_carrier"].str.strip().str.upper()
    df["subtype"] = df["subtype"].str.strip()
    df["bts_aircraft_type"] = df["bts_aircraft_type"].str.strip()
    return df


def load_tail_overrides(reference_dir: Path) -> pd.DataFrame:
    """Load and validate `reference/tail_overrides.csv`."""
    path = reference_dir / "tail_overrides.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing tail overrides reference file: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in TAIL_OVERRIDE_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"tail_overrides.csv missing required columns: {missing}")

    df["tail_num"] = df["tail_num"].str.strip().str.upper()
    df["op_carrier"] = df["op_carrier"].str.strip().str.upper()
    df["subtype"] = df["subtype"].str.strip()
    df["bts_aircraft_type"] = df["bts_aircraft_type"].str.strip()
    for col in ("seats_total", "seats_premium", "seats_economy"):
        df[col] = pd.to_numeric(df[col], errors="raise").astype("int64")
    if (df["seats_total"] != df["seats_premium"] + df["seats_economy"]).any():
        raise ValueError(
            "tail_overrides.csv invariant failed: seats_total != seats_premium + seats_economy"
        )
    return df


@dataclass(frozen=True)
class FaaMatchMetrics:
    """Empirical FAA registry and post-override tail resolution match metrics."""

    total_flights: int
    performed_flights: int
    cancelled_flights: int
    non_null_tail_flights: int
    null_tail_cancelled_flights: int
    null_tail_performed_flights: int
    raw_faa_master_matched_non_null: int
    raw_faa_dereg_matched_non_null: int
    raw_faa_master_only_unmatched_rate_non_null: float
    raw_faa_master_plus_dereg_commercial_unmatched_rate_non_null: float
    raw_faa_master_plus_dereg_commercial_unmatched_rate_performed: float
    tail_override_count: int
    faa_master_count: int
    faa_dereg_count: int
    unassigned_cancelled_count: int
    unmatched_typo_count: int
    unmatched_other_count: int
    post_override_unmatched_performed: int
    post_override_unmatched_rate_performed: float
    post_override_unmatched_non_null: int
    post_override_unmatched_rate_all_non_null: float
    stearman_collisions_rejected: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics to plain JSON-compatible dict."""
        return asdict(self)


class AircraftResolver:
    """
    Resolves OTP flights to FAA manufacturer/model, fleet subtype, BTS aircraft type, and cabin seats.

    Strict precedence (1:1 cardinality preserved — never Cartesian merge across `MASTER + DEREG`):
      0. Null `tail_num` -> `UNASSIGNED_CANCELLED` (or `UNMATCHED` if performed)
      1. `reference/tail_overrides.csv` -> `TAIL_OVERRIDE`
      2. FAA `MASTER.txt` (commercial airliner + `faa_tc_max_seats >= min_faa_tc_max_seats` + crosswalk) -> `FAA_MASTER`
      3. FAA `DEREG.txt` (latest `cancel_date`, commercial + `faa_tc_max_seats >= min_faa_tc_max_seats` + crosswalk) -> `FAA_DEREG`
      4. Invalid N-number format -> `UNMATCHED_TYPO`; otherwise -> `UNMATCHED`
    """

    def __init__(
        self,
        seat_config_df: pd.DataFrame,
        crosswalk_df: pd.DataFrame,
        tail_overrides_df: pd.DataFrame,
        faa_registry_df: pd.DataFrame,
        faa_acftref_df: pd.DataFrame,
        min_faa_tc_max_seats: int = 20,
        commercial_mfr_prefixes: tuple[str, ...] = COMMERCIAL_MFR_PREFIXES,
    ) -> None:
        self.min_faa_tc_max_seats = int(min_faa_tc_max_seats)
        self.commercial_mfr_prefixes = tuple(p.strip().upper() for p in commercial_mfr_prefixes)

        # 1. Seat configuration map: subtype -> (seats_total, seats_premium, seats_economy)
        self.seat_map: dict[str, tuple[int, int, int]] = {
            row.subtype: (int(row.seats_total), int(row.seats_premium), int(row.seats_economy))
            for row in seat_config_df.itertuples(index=False)
        }

        # 2. Crosswalk maps: (op_carrier, faa_mfr, faa_model) and generic (faa_mfr, faa_model)
        self.crosswalk_carrier_map: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.crosswalk_generic_map: dict[tuple[str, str], tuple[str, str]] = {}
        self.reverse_crosswalk_carrier_map: dict[tuple[str, str], tuple[str, str]] = {}
        self.reverse_crosswalk_generic_map: dict[str, tuple[str, str]] = {}
        for row in crosswalk_df.itertuples(index=False):
            mfr = str(row.faa_mfr).strip().upper()
            mdl = str(row.faa_model).strip().upper()
            carrier = str(row.op_carrier).strip().upper()
            subtype = str(row.subtype).strip()
            bts_type = str(row.bts_aircraft_type).strip()
            if carrier and carrier != "*":
                self.crosswalk_carrier_map[(carrier, mfr, mdl)] = (subtype, bts_type)
                self.reverse_crosswalk_carrier_map.setdefault((carrier, subtype), (mfr, mdl))
            self.crosswalk_generic_map.setdefault((mfr, mdl), (subtype, bts_type))
            self.reverse_crosswalk_generic_map.setdefault(subtype, (mfr, mdl))

        # 3. Tail override map: (tail_num, op_carrier) -> list of override specs (supports optional date bounds)
        self.override_carrier_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.override_wildcard_map: dict[str, list[dict[str, Any]]] = {}
        has_eff_from = "effective_from" in tail_overrides_df.columns
        has_eff_to = "effective_to" in tail_overrides_df.columns
        for row in tail_overrides_df.itertuples(index=False):
            tail = str(row.tail_num).strip().upper()
            carrier = str(row.op_carrier).strip().upper()
            raw_eff_from = getattr(row, "effective_from", "") if has_eff_from else ""
            raw_eff_to = getattr(row, "effective_to", "") if has_eff_to else ""
            eff_from = "" if pd.isna(raw_eff_from) else str(raw_eff_from).strip()[:10]
            eff_to = "" if pd.isna(raw_eff_to) else str(raw_eff_to).strip()[:10]
            spec = {
                "subtype": str(row.subtype).strip(),
                "bts_aircraft_type": str(row.bts_aircraft_type).strip(),
                "seats_total": int(row.seats_total),
                "seats_premium": int(row.seats_premium),
                "seats_economy": int(row.seats_economy),
                "effective_from": eff_from,
                "effective_to": eff_to,
            }
            if carrier and carrier != "*":
                self.override_carrier_map.setdefault((tail, carrier), []).append(spec)
            else:
                self.override_wildcard_map.setdefault(tail, []).append(spec)

        # 4. Build strictly separated MASTER and DEREG lookups joined to ACFTREF
        acft_clean = faa_acftref_df.copy()
        acft_clean["code"] = acft_clean["code"].astype(str).str.strip()
        acft_clean["faa_manufacturer"] = (
            acft_clean["faa_manufacturer"].astype(str).str.strip().str.upper()
        )
        acft_clean["faa_model"] = acft_clean["faa_model"].astype(str).str.strip().str.upper()
        acft_clean["faa_tc_max_seats"] = pd.to_numeric(
            acft_clean["faa_tc_max_seats"], errors="coerce"
        ).fillna(0)

        mfr_series = acft_clean["faa_manufacturer"]
        is_comm = pd.Series(False, index=acft_clean.index)
        for prefix in self.commercial_mfr_prefixes:
            is_comm = is_comm | mfr_series.str.startswith(prefix)
        acft_clean["is_comm_filtered"] = is_comm

        reg_joined = faa_registry_df.merge(
            acft_clean[
                ["code", "faa_manufacturer", "faa_model", "faa_tc_max_seats", "is_comm_filtered"]
            ],
            left_on="mfr_mdl_code",
            right_on="code",
            how="inner",
        )
        reg_joined["n_number"] = reg_joined["n_number"].astype(str).str.strip().str.upper()

        # Count commercial-prefix records rejected by the seat floor (e.g. 2-seat Stearman A75N1/E75)
        stearman_rejected_mask = reg_joined["is_comm_filtered"] & (
            reg_joined["faa_tc_max_seats"] < self.min_faa_tc_max_seats
        )
        self.stearman_collisions_rejected = int(stearman_rejected_mask.sum())

        valid_faa = reg_joined[
            reg_joined["is_comm_filtered"]
            & (reg_joined["faa_tc_max_seats"] >= self.min_faa_tc_max_seats)
        ].copy()

        # Deterministic tie-break shared with dbt `int_aircraft_enriched`:
        # latest cancel_date first, then lowest serial_number (applied AFTER the seat floor).
        for col in ("cancel_date", "serial_number"):
            if col not in valid_faa.columns:
                valid_faa[col] = ""
        valid_faa["_cancel_sort"] = valid_faa["cancel_date"].fillna("").astype(str).str.strip()
        valid_faa["_serial_sort"] = valid_faa["serial_number"].fillna("").astype(str).str.strip()
        valid_faa = valid_faa.sort_values(
            ["n_number", "_cancel_sort", "_serial_sort"],
            ascending=[True, False, True],
            kind="mergesort",
        )

        # Tier 2: MASTER lookup (deduplicated 1:1 per n_number)
        master_df = valid_faa[valid_faa["registry_source"] == "faa_master"].drop_duplicates(
            subset=["n_number"], keep="first"
        )
        self.master_map: dict[str, tuple[str, str, int]] = {
            row.n_number: (
                str(row.faa_manufacturer),
                str(row.faa_model),
                int(row.faa_tc_max_seats),
            )
            for row in master_df.itertuples(index=False)
        }

        # Tier 3: DEREG lookup (latest seat-floor-valid cancel_date per n_number)
        dereg_df = valid_faa[valid_faa["registry_source"] == "faa_dereg"].drop_duplicates(
            subset=["n_number"], keep="first"
        )
        self.dereg_map: dict[str, tuple[str, str, int]] = {
            row.n_number: (
                str(row.faa_manufacturer),
                str(row.faa_model),
                int(row.faa_tc_max_seats),
            )
            for row in dereg_df.itertuples(index=False)
        }

    @classmethod
    def from_reference_dir(
        cls,
        reference_dir: Path,
        faa_registry_df: pd.DataFrame,
        faa_acftref_df: pd.DataFrame,
        min_faa_tc_max_seats: int = 20,
        commercial_mfr_prefixes: tuple[str, ...] = COMMERCIAL_MFR_PREFIXES,
    ) -> AircraftResolver:
        """Construct an `AircraftResolver` from `reference/*.csv` and ingested FAA tables."""
        return cls(
            seat_config_df=load_seat_config(reference_dir),
            crosswalk_df=load_faa_t100_crosswalk(reference_dir),
            tail_overrides_df=load_tail_overrides(reference_dir),
            faa_registry_df=faa_registry_df,
            faa_acftref_df=faa_acftref_df,
            min_faa_tc_max_seats=min_faa_tc_max_seats,
            commercial_mfr_prefixes=commercial_mfr_prefixes,
        )

    def _lookup_override(
        self, tail_norm: str, op_carrier: str, flight_date: str
    ) -> dict[str, Any] | None:
        candidates = self.override_carrier_map.get(
            (tail_norm, op_carrier), []
        ) + self.override_wildcard_map.get(tail_norm, [])
        for spec in candidates:
            eff_from = spec["effective_from"]
            eff_to = spec["effective_to"]
            if eff_from and flight_date < eff_from:
                continue
            if eff_to and flight_date > eff_to:
                continue
            return spec
        return None

    def _lookup_crosswalk(
        self, op_carrier: str, faa_mfr: str, faa_model: str
    ) -> tuple[str, str] | None:
        res = self.crosswalk_carrier_map.get((op_carrier, faa_mfr, faa_model))
        if res is not None:
            return res
        return self.crosswalk_generic_map.get((faa_mfr, faa_model))

    def resolve(self, otp_df: pd.DataFrame) -> tuple[pd.DataFrame, FaaMatchMetrics]:
        """
        Resolve aircraft & seat configuration for every row in `otp_df`.

        Preserves exact row count (`len(result) == len(otp_df)`) and index.
        """
        out = otp_df.copy()

        # Normalize tails consistently
        raw_tails = (
            out["tail_num"] if "tail_num" in out.columns else pd.Series(None, index=out.index)
        )
        op_carriers = out["op_carrier"].astype(str).str.strip().str.upper()
        flight_dates = out["flight_date"].astype(str).str.strip().str.slice(0, 10)
        is_cancelled_arr = out["is_cancelled"].astype(bool).tolist()

        n_rows = len(out)
        tail_num_out: list[str | None] = [None] * n_rows
        tail_clean_out: list[str | None] = [None] * n_rows
        aircraft_source_out: list[str] = [""] * n_rows
        faa_mfr_out: list[str | None] = [None] * n_rows
        faa_model_out: list[str | None] = [None] * n_rows
        faa_tc_seats_out: list[int | None] = [None] * n_rows
        subtype_out: list[str | None] = [None] * n_rows
        bts_type_out: list[str | None] = [None] * n_rows
        seat_source_out: list[str] = [""] * n_rows
        seats_total_out: list[int | None] = [None] * n_rows
        seats_prem_out: list[int | None] = [None] * n_rows
        seats_econ_out: list[int | None] = [None] * n_rows

        raw_master_matched = 0
        raw_dereg_matched = 0
        raw_comm_matched_performed = 0
        non_null_tail_flights = 0
        null_tail_cancelled = 0
        null_tail_performed = 0

        for idx, (raw_t, carrier, fdate, is_canc) in enumerate(
            zip(
                raw_tails.tolist(),
                op_carriers.tolist(),
                flight_dates.tolist(),
                is_cancelled_arr,
                strict=True,
            )
        ):
            tail_norm, faa_key = normalize_tail_num(raw_t)
            tail_num_out[idx] = tail_norm
            tail_clean_out[idx] = faa_key

            if tail_norm is None:
                if is_canc:
                    null_tail_cancelled += 1
                    aircraft_source_out[idx] = "UNASSIGNED_CANCELLED"
                    seat_source_out[idx] = "UNASSIGNED_CANCELLED"
                else:
                    null_tail_performed += 1
                    aircraft_source_out[idx] = "UNMATCHED"
                    seat_source_out[idx] = "UNMATCHED"
                continue

            non_null_tail_flights += 1

            # Track raw FAA commercial match metrics (independent of overrides)
            master_hit = self.master_map.get(faa_key) if faa_key is not None else None
            dereg_hit = (
                self.dereg_map.get(faa_key)
                if (faa_key is not None and master_hit is None)
                else None
            )
            if master_hit is not None:
                raw_master_matched += 1
                if not is_canc:
                    raw_comm_matched_performed += 1
            elif dereg_hit is not None:
                raw_dereg_matched += 1
                if not is_canc:
                    raw_comm_matched_performed += 1

            # Tier 1: Tail overrides
            ov = self._lookup_override(tail_norm, carrier, fdate)
            if ov is not None:
                aircraft_source_out[idx] = "TAIL_OVERRIDE"
                seat_source_out[idx] = "TAIL_OVERRIDE"
                subtype = ov["subtype"]
                subtype_out[idx] = subtype
                bts_type_out[idx] = ov["bts_aircraft_type"]
                seats_total_out[idx] = ov["seats_total"]
                seats_prem_out[idx] = ov["seats_premium"]
                seats_econ_out[idx] = ov["seats_economy"]
                faa_ref = master_hit or dereg_hit
                if faa_ref is not None:
                    faa_mfr_out[idx], faa_model_out[idx], faa_tc_seats_out[idx] = faa_ref
                else:
                    rev = self.reverse_crosswalk_carrier_map.get(
                        (carrier, subtype)
                    ) or self.reverse_crosswalk_generic_map.get(subtype)
                    if rev is not None:
                        faa_mfr_out[idx], faa_model_out[idx] = rev
                continue

            if faa_key is None:
                aircraft_source_out[idx] = "UNMATCHED_TYPO"
                seat_source_out[idx] = "UNMATCHED"
                continue

            # Tier 2: FAA MASTER (commercial-filtered + seat floor)
            if master_hit is not None:
                mfr, mdl, tc_seats = master_hit
                cw_hit = self._lookup_crosswalk(carrier, mfr, mdl)
                if cw_hit is not None and cw_hit[0] in self.seat_map:
                    subtype, bts_type = cw_hit
                    s_tot, s_prem, s_econ = self.seat_map[subtype]
                    aircraft_source_out[idx] = "FAA_MASTER"
                    seat_source_out[idx] = "SUBTYPE_DEFAULT"
                    faa_mfr_out[idx] = mfr
                    faa_model_out[idx] = mdl
                    faa_tc_seats_out[idx] = tc_seats
                    subtype_out[idx] = subtype
                    bts_type_out[idx] = bts_type
                    seats_total_out[idx] = s_tot
                    seats_prem_out[idx] = s_prem
                    seats_econ_out[idx] = s_econ
                    continue

            # Tier 3: FAA DEREG fallback (commercial-filtered + seat floor)
            if dereg_hit is not None:
                mfr, mdl, tc_seats = dereg_hit
                cw_hit = self._lookup_crosswalk(carrier, mfr, mdl)
                if cw_hit is not None and cw_hit[0] in self.seat_map:
                    subtype, bts_type = cw_hit
                    s_tot, s_prem, s_econ = self.seat_map[subtype]
                    aircraft_source_out[idx] = "FAA_DEREG"
                    seat_source_out[idx] = "SUBTYPE_DEFAULT"
                    faa_mfr_out[idx] = mfr
                    faa_model_out[idx] = mdl
                    faa_tc_seats_out[idx] = tc_seats
                    subtype_out[idx] = subtype
                    bts_type_out[idx] = bts_type
                    seats_total_out[idx] = s_tot
                    seats_prem_out[idx] = s_prem
                    seats_econ_out[idx] = s_econ
                    continue

            # Unmatched fallback (e.g., commercial model not in crosswalk or absent from FAA)
            aircraft_source_out[idx] = "UNMATCHED"
            seat_source_out[idx] = "UNMATCHED"
            if master_hit is not None or dereg_hit is not None:
                faa_mfr_out[idx], faa_model_out[idx], faa_tc_seats_out[idx] = (
                    master_hit or dereg_hit
                )

        out["tail_num"] = pd.Series(tail_num_out, index=out.index, dtype=object)
        out["tail_num_clean"] = pd.Series(tail_clean_out, index=out.index, dtype=object)
        out["aircraft_source"] = pd.Series(aircraft_source_out, index=out.index, dtype=object)
        out["faa_manufacturer"] = pd.Series(faa_mfr_out, index=out.index, dtype=object)
        out["faa_model"] = pd.Series(faa_model_out, index=out.index, dtype=object)
        out["faa_tc_max_seats"] = pd.array(faa_tc_seats_out, dtype="Int64")
        out["fleet_subtype"] = pd.Series(subtype_out, index=out.index, dtype=object)
        out["bts_aircraft_type"] = pd.Series(bts_type_out, index=out.index, dtype=object)
        out["seat_config_source"] = pd.Series(seat_source_out, index=out.index, dtype=object)
        out["seats_config"] = pd.array(seats_total_out, dtype="Int64")
        out["seats_premium"] = pd.array(seats_prem_out, dtype="Int64")
        out["seats_economy"] = pd.array(seats_econ_out, dtype="Int64")

        performed_flights = int((~out["is_cancelled"].astype(bool)).sum())
        cancelled_flights = int(out["is_cancelled"].astype(bool).sum())
        src_series = out["aircraft_source"]
        tail_override_count = int((src_series == "TAIL_OVERRIDE").sum())
        faa_master_count = int((src_series == "FAA_MASTER").sum())
        faa_dereg_count = int((src_series == "FAA_DEREG").sum())
        unassigned_cancelled_count = int((src_series == "UNASSIGNED_CANCELLED").sum())
        unmatched_typo_count = int((src_series == "UNMATCHED_TYPO").sum())
        unmatched_other_count = int((src_series == "UNMATCHED").sum())

        unresolved_mask = out["seats_config"].isna()
        post_override_unmatched_performed = int(
            (unresolved_mask & (~out["is_cancelled"].astype(bool))).sum()
        )
        post_override_unmatched_non_null = int((unresolved_mask & out["tail_num"].notna()).sum())

        denom_nn = non_null_tail_flights if non_null_tail_flights > 0 else 1
        denom_perf = performed_flights if performed_flights > 0 else 1
        raw_comm_matched_nn = raw_master_matched + raw_dereg_matched

        metrics = FaaMatchMetrics(
            total_flights=n_rows,
            performed_flights=performed_flights,
            cancelled_flights=cancelled_flights,
            non_null_tail_flights=non_null_tail_flights,
            null_tail_cancelled_flights=null_tail_cancelled,
            null_tail_performed_flights=null_tail_performed,
            raw_faa_master_matched_non_null=raw_master_matched,
            raw_faa_dereg_matched_non_null=raw_dereg_matched,
            raw_faa_master_only_unmatched_rate_non_null=round(
                (non_null_tail_flights - raw_master_matched) / denom_nn, 6
            ),
            raw_faa_master_plus_dereg_commercial_unmatched_rate_non_null=round(
                (non_null_tail_flights - raw_comm_matched_nn) / denom_nn, 6
            ),
            raw_faa_master_plus_dereg_commercial_unmatched_rate_performed=round(
                (performed_flights - raw_comm_matched_performed) / denom_perf, 6
            ),
            tail_override_count=tail_override_count,
            faa_master_count=faa_master_count,
            faa_dereg_count=faa_dereg_count,
            unassigned_cancelled_count=unassigned_cancelled_count,
            unmatched_typo_count=unmatched_typo_count,
            unmatched_other_count=unmatched_other_count,
            post_override_unmatched_performed=post_override_unmatched_performed,
            post_override_unmatched_rate_performed=round(
                post_override_unmatched_performed / denom_perf, 6
            ),
            post_override_unmatched_non_null=post_override_unmatched_non_null,
            post_override_unmatched_rate_all_non_null=round(
                post_override_unmatched_non_null / denom_nn, 6
            ),
            stearman_collisions_rejected=self.stearman_collisions_rejected,
        )

        logger.info(
            "Resolved aircraft configs",
            extra={
                "extra_fields": {
                    "total_flights": n_rows,
                    "raw_master_unmatched_rate": metrics.raw_faa_master_only_unmatched_rate_non_null,
                    "raw_faa_comm_unmatched_rate": metrics.raw_faa_master_plus_dereg_commercial_unmatched_rate_non_null,
                    "post_override_unmatched_performed": metrics.post_override_unmatched_performed,
                    "stearman_collisions_rejected": metrics.stearman_collisions_rejected,
                }
            },
        )
        assert len(out) == len(otp_df), "Row count invariant violated in AircraftResolver.resolve"
        return out, metrics
