"""Step 2 of Phase 2 enrichment: 3-tier T-100 domestic load factor hierarchy & fallback join."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any

import pandas as pd

from standby.config import DEFAULT_UA_FAMILY_CARRIERS

logger = logging.getLogger("standby.enrichment.load_factor")

LF_SOURCE_L1 = "CARRIER_ROUTE_MONTH_TYPE"
LF_SOURCE_L2 = "CARRIER_ROUTE_MONTH"
LF_SOURCE_L3 = "ROUTE_MONTH"
LF_SOURCE_UNMATCHED = "UNMATCHED"

DEFAULT_SHARED_CPA_CARRIERS: frozenset[str] = frozenset({"OO", "YX", "YV", "ZW"})


class T100LoadFactorJoiner:
    """
    Aggregates domestic BTS T-100 (`CLASS = 'F'`, `DATA_SOURCE IN ('DU', 'DF')`) segments
    and resolves flight-level load factors via the 3-tier fallback hierarchy:

      1. `CARRIER_ROUTE_MONTH_TYPE`: `(op_carrier, origin, dest, year, month, bts_aircraft_type)`
      2. `CARRIER_ROUTE_MONTH`: `(op_carrier, origin, dest, year, month)`
      3. `ROUTE_MONTH`: `(origin, dest, year, month)` across the UA carrier family
      4. `UNMATCHED`: `t100_available == False` or no match -> `est_load_factor = NaN` (never imputed)
    """

    def __init__(
        self,
        t100_df: pd.DataFrame,
        hub: str = "ORD",
        ua_family_carriers: Collection[str] | None = None,
        shared_cpa_carriers: Collection[str] | None = None,
        shared_spoke_tolerance: float = 0.10,
    ) -> None:
        self.hub = hub.strip().upper()
        self.ua_family = (
            {str(c).strip().upper() for c in ua_family_carriers}
            if ua_family_carriers is not None
            else set(DEFAULT_UA_FAMILY_CARRIERS)
        )
        self.shared_cpa = (
            {str(c).strip().upper() for c in shared_cpa_carriers}
            if shared_cpa_carriers is not None
            else set(DEFAULT_SHARED_CPA_CARRIERS)
        )
        self.shared_spoke_tolerance = float(shared_spoke_tolerance)

        if t100_df.empty:
            self.domestic_t100 = t100_df.copy()
            self.available_months: set[tuple[int, int]] = set()
            self.l1_map: dict[tuple[str, str, str, int, int, str], float] = {}
            self.l2_map: dict[tuple[str, str, str, int, int], float] = {}
            self.l2_deps_map: dict[tuple[str, str, str, int, int], float] = {}
            self.l3_map: dict[tuple[str, str, int, int], float] = {}
            return

        dom = t100_df.copy()
        dom["service_class"] = dom["service_class"].astype(str).str.strip().str.upper()
        dom["data_source"] = dom["data_source"].astype(str).str.strip().str.upper()
        dom["origin"] = dom["origin"].astype(str).str.strip().str.upper()
        dom["dest"] = dom["dest"].astype(str).str.strip().str.upper()
        dom["op_carrier"] = dom["op_carrier"].astype(str).str.strip().str.upper()
        dom["bts_aircraft_type"] = dom["bts_aircraft_type"].astype(str).str.strip()

        dom = dom[
            (dom["service_class"] == "F")
            & (dom["data_source"].isin(["DU", "DF"]))
            & (dom["origin"] == self.hub)
        ].copy()
        self.domestic_t100 = dom

        if dom.empty:
            self.available_months = set()
            self.l1_map = {}
            self.l2_map = {}
            self.l2_deps_map = {}
            self.l3_map = {}
            return

        self.available_months = {
            (int(y), int(m))
            for y, m in zip(dom["year"].tolist(), dom["month"].tolist(), strict=True)
        }

        # Level 1: (op_carrier, origin, dest, year, month, bts_aircraft_type)
        g1 = dom.groupby(
            ["op_carrier", "origin", "dest", "year", "month", "bts_aircraft_type"],
            as_index=False,
        )[["passengers", "seats", "departures_performed"]].sum()
        g1 = g1[(g1["seats"] > 0) & (g1["departures_performed"] > 0)].copy()
        g1["lf"] = (g1["passengers"] / g1["seats"]).clip(0.0, 1.0)
        self.l1_map = {
            (
                str(r.op_carrier),
                str(r.origin),
                str(r.dest),
                int(r.year),
                int(r.month),
                str(r.bts_aircraft_type),
            ): float(r.lf)
            for r in g1.itertuples(index=False)
        }

        # Level 2: (op_carrier, origin, dest, year, month)
        g2 = dom.groupby(["op_carrier", "origin", "dest", "year", "month"], as_index=False)[
            ["passengers", "seats", "departures_performed"]
        ].sum()
        g2_valid = g2[(g2["seats"] > 0) & (g2["departures_performed"] > 0)].copy()
        g2_valid["lf"] = (g2_valid["passengers"] / g2_valid["seats"]).clip(0.0, 1.0)
        self.l2_map = {
            (str(r.op_carrier), str(r.origin), str(r.dest), int(r.year), int(r.month)): float(r.lf)
            for r in g2_valid.itertuples(index=False)
        }
        self.l2_deps_map = {
            (str(r.op_carrier), str(r.origin), str(r.dest), int(r.year), int(r.month)): float(
                r.departures_performed
            )
            for r in g2.itertuples(index=False)
        }

        # Level 3: (origin, dest, year, month) across UA family carriers
        dom_ua = dom[dom["op_carrier"].isin(self.ua_family)]
        g3 = dom_ua.groupby(["origin", "dest", "year", "month"], as_index=False)[
            ["passengers", "seats", "departures_performed"]
        ].sum()
        g3 = g3[(g3["seats"] > 0) & (g3["departures_performed"] > 0)].copy()
        g3["lf"] = (g3["passengers"] / g3["seats"]).clip(0.0, 1.0)
        self.l3_map = {
            (str(r.origin), str(r.dest), int(r.year), int(r.month)): float(r.lf)
            for r in g3.itertuples(index=False)
        }

    def join(self, flights_df: pd.DataFrame) -> pd.DataFrame:
        """Attach `t100_available`, `lf_source`, `is_shared_regional_spoke`, and `est_load_factor`."""
        out = flights_df.copy()
        n_rows = len(out)

        years = out["year"].astype("int64").tolist()
        months = out["month"].astype("int64").tolist()
        carriers = out["op_carrier"].astype(str).str.strip().str.upper().tolist()
        origins = out["origin"].astype(str).str.strip().str.upper().tolist()
        dests = out["dest"].astype(str).str.strip().str.upper().tolist()
        bts_types_raw = (
            out["bts_aircraft_type"].tolist()
            if "bts_aircraft_type" in out.columns
            else [None] * n_rows
        )
        is_cancelled_list = out["is_cancelled"].astype(bool).tolist()

        # Precompute OTP performed counts per (op_carrier, origin, dest, year, month) for shared spoke detection
        otp_perf_counts: dict[tuple[str, str, str, int, int], int] = {}
        for c, o, d, y, m, is_c in zip(
            carriers, origins, dests, years, months, is_cancelled_list, strict=True
        ):
            if not is_c:
                key = (c, o, d, y, m)
                otp_perf_counts[key] = otp_perf_counts.get(key, 0) + 1

        t100_avail_out: list[bool] = [False] * n_rows
        lf_source_out: list[str] = [LF_SOURCE_UNMATCHED] * n_rows
        est_lf_out: list[float] = [float("nan")] * n_rows
        shared_spoke_out: list[bool] = [False] * n_rows

        l1_map = self.l1_map
        l2_map = self.l2_map
        l3_map = self.l3_map
        l2_deps_map = self.l2_deps_map
        avail_months = self.available_months
        shared_cpa = self.shared_cpa
        tol = self.shared_spoke_tolerance

        for i in range(n_rows):
            y = years[i]
            m = months[i]
            avail = (y, m) in avail_months
            t100_avail_out[i] = avail
            if not avail:
                lf_source_out[i] = LF_SOURCE_UNMATCHED
                est_lf_out[i] = float("nan")
                shared_spoke_out[i] = False
                continue

            c = carriers[i]
            o = origins[i]
            d = dests[i]
            bts_raw = bts_types_raw[i]
            bts = (
                str(bts_raw).strip()
                if bts_raw is not None and not pd.isna(bts_raw) and str(bts_raw).strip()
                else None
            )

            # Multi-brand shared spoke detection
            is_multi_brand_carrier = (c in shared_cpa) and not (c == "ZW" and y == 2019)
            if is_multi_brand_carrier:
                cell_key = (c, o, d, y, m)
                otp_cnt = otp_perf_counts.get(cell_key, 0)
                t100_deps = l2_deps_map.get(cell_key, 0.0)
                if otp_cnt > 0 and t100_deps > (1.0 + tol) * otp_cnt:
                    shared_spoke_out[i] = True

            if bts is not None:
                l1_val = l1_map.get((c, o, d, y, m, bts))
                if l1_val is not None:
                    lf_source_out[i] = LF_SOURCE_L1
                    est_lf_out[i] = l1_val
                    continue

            l2_val = l2_map.get((c, o, d, y, m))
            if l2_val is not None:
                lf_source_out[i] = LF_SOURCE_L2
                est_lf_out[i] = l2_val
                continue

            l3_val = l3_map.get((o, d, y, m))
            if l3_val is not None:
                lf_source_out[i] = LF_SOURCE_L3
                est_lf_out[i] = l3_val
                continue

            lf_source_out[i] = LF_SOURCE_UNMATCHED
            est_lf_out[i] = float("nan")

        out["t100_available"] = pd.Series(t100_avail_out, index=out.index, dtype=bool)
        out["lf_source"] = pd.Series(lf_source_out, index=out.index, dtype=object)
        out["is_shared_regional_spoke"] = pd.Series(shared_spoke_out, index=out.index, dtype=bool)
        out["est_load_factor"] = pd.Series(est_lf_out, index=out.index, dtype="float64")

        counts: dict[str, Any] = out["lf_source"].value_counts().to_dict()
        logger.info(
            "Joined T-100 load factors",
            extra={
                "extra_fields": {
                    "rows": n_rows,
                    "t100_available_true": int(out["t100_available"].sum()),
                    "lf_source_counts": counts,
                }
            },
        )
        assert len(out) == len(flights_df), "Row count invariant violated in T100LoadFactorJoiner"
        return out
