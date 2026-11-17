"""Section 7 automated sanity checks (`SC-1` through `SC-5`) for Phase 2 enrichment."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import pandas as pd

from standby.config import DEFAULT_UA_FAMILY_CARRIERS, SanityCheckConfig, StandbyConfig

logger = logging.getLogger("standby.validation.sanity")

CheckStatus = Literal["PASS", "WARN", "FAIL"]


class SanityCheckError(RuntimeError):
    """Raised when one or more blocking Section 7 sanity checks fail."""


@dataclass(frozen=True)
class SanityCheckResult:
    """Outcome of a single Section 7 sanity check."""

    check_id: str
    name: str
    status: CheckStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize result to plain dictionary."""
        return asdict(self)


@dataclass
class SanityCheckReport:
    """Aggregated Section 7 sanity check report."""

    results: list[SanityCheckResult]

    @property
    def failures_count(self) -> int:
        """Number of checks with `status == 'FAIL'`."""
        return sum(1 for r in self.results if r.status == "FAIL")

    @property
    def warnings_count(self) -> int:
        """Number of checks with `status == 'WARN'`."""
        return sum(1 for r in self.results if r.status == "WARN")

    @property
    def passed(self) -> bool:
        """True iff zero checks failed (`WARN` does not fail the pipeline)."""
        return self.failures_count == 0

    def raise_if_failed(self) -> None:
        """Raise `SanityCheckError` if any check has `status == 'FAIL'`."""
        failed = [r for r in self.results if r.status == "FAIL"]
        if failed:
            summary = "; ".join(f"{r.check_id} ({r.name}): {r.message}" for r in failed)
            raise SanityCheckError(f"Section 7 sanity checks failed: {summary}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize full report to JSON-compatible dictionary."""
        return {
            "passed": self.passed,
            "failures_count": self.failures_count,
            "warnings_count": self.warnings_count,
            "checks": [r.to_dict() for r in self.results],
        }


def check_sc1_passenger_and_lf_bounds(enriched_df: pd.DataFrame) -> SanityCheckResult:
    """SC-1: Enforce `0 <= est_load_factor <= 1` and `0 <= est_passengers <= seats_config`."""
    lf = enriched_df["est_load_factor"].dropna()
    lf_adj = enriched_df["est_lf_adjusted"].dropna()

    bad_lf = int(((lf < 0.0) | (lf > 1.0)).sum())
    bad_lf_adj = int(((lf_adj < 0.0) | (lf_adj > 1.0)).sum())

    base_mask = enriched_df["seats_config"].notna() & enriched_df["est_passengers"].notna()
    bad_base_pax = 0
    bad_base_open = 0
    if base_mask.any():
        seats_b = enriched_df.loc[base_mask, "seats_config"].astype("int64")
        pax_b = enriched_df.loc[base_mask, "est_passengers"].astype("int64")
        open_b_raw = enriched_df.loc[base_mask, "est_open_seats"]
        open_b_na = open_b_raw.isna()
        bad_base_pax = int(((pax_b < 0) | (pax_b > seats_b)).sum())
        bad_base_open = int(open_b_na.sum())
        valid_open_b = ~open_b_na
        if valid_open_b.any():
            open_b = open_b_raw.loc[valid_open_b].astype("int64")
            bad_base_open += int(
                (open_b != (seats_b.loc[valid_open_b] - pax_b.loc[valid_open_b])).sum()
            )

    adj_mask = enriched_df["seats_config"].notna() & enriched_df["est_passengers_adjusted"].notna()
    bad_adj_pax = 0
    bad_adj_open = 0
    if adj_mask.any():
        seats_a = enriched_df.loc[adj_mask, "seats_config"].astype("int64")
        pax_a = enriched_df.loc[adj_mask, "est_passengers_adjusted"].astype("int64")
        open_a_raw = enriched_df.loc[adj_mask, "est_open_seats_adjusted"]
        open_a_na = open_a_raw.isna()
        bad_adj_pax = int(((pax_a < 0) | (pax_a > seats_a)).sum())
        bad_adj_open = int(open_a_na.sum())
        valid_open_a = ~open_a_na
        if valid_open_a.any():
            open_a = open_a_raw.loc[valid_open_a].astype("int64")
            bad_adj_open += int(
                (open_a != (seats_a.loc[valid_open_a] - pax_a.loc[valid_open_a])).sum()
            )

    total_violations = (
        bad_lf + bad_lf_adj + bad_base_pax + bad_base_open + bad_adj_pax + bad_adj_open
    )
    status: CheckStatus = "FAIL" if total_violations > 0 else "PASS"
    msg = (
        f"Checked {int(base_mask.sum()):,} base-estimated and {int(adj_mask.sum()):,} adjusted rows; "
        f"violations={total_violations}"
    )
    return SanityCheckResult(
        check_id="SC-1",
        name="Passenger & Load Factor Bounds",
        status=status,
        message=msg,
        details={
            "checked_base_rows": int(base_mask.sum()),
            "checked_adjusted_rows": int(adj_mask.sum()),
            "bad_lf": bad_lf,
            "bad_lf_adj": bad_lf_adj,
            "bad_base_pax": bad_base_pax,
            "bad_base_open": bad_base_open,
            "bad_adj_pax": bad_adj_pax,
            "bad_adj_open": bad_adj_open,
            "total_violations": total_violations,
        },
    )


def check_sc2_t100_otp_departure_reconciliation(
    enriched_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    hub: str = "ORD",
    tolerance: float = 0.10,
    mainline_carriers: tuple[str, ...] = ("UA",),
    dedicated_express_carriers: tuple[str, ...] = ("G7", "EV", "AX", "C5"),
    shared_cpa_carriers: tuple[str, ...] = ("OO", "YX", "YV", "ZW"),
    sample_mode: bool = False,
    sample_min_t100_rows: int = 500,
) -> SanityCheckResult:
    """SC-2: Reconcile OTP performed flight counts against T-100 domestic `DEPARTURES_PERFORMED` (`±10%`)."""
    hub_norm = hub.strip().upper()
    perf = enriched_df[~enriched_df["is_cancelled"].astype(bool)].copy()
    perf["op_carrier"] = perf["op_carrier"].astype(str).str.strip().str.upper()
    perf["origin"] = perf["origin"].astype(str).str.strip().str.upper()
    perf["dest"] = perf["dest"].astype(str).str.strip().str.upper()

    dom = t100_df.copy()
    dom["service_class"] = dom["service_class"].astype(str).str.strip().str.upper()
    dom["data_source"] = dom["data_source"].astype(str).str.strip().str.upper()
    dom["origin"] = dom["origin"].astype(str).str.strip().str.upper()
    dom["dest"] = dom["dest"].astype(str).str.strip().str.upper()
    dom["op_carrier"] = dom["op_carrier"].astype(str).str.strip().str.upper()
    dom = dom[
        (dom["service_class"] == "F")
        & (dom["data_source"].isin(["DU", "DF"]))
        & (dom["origin"] == hub_norm)
    ]

    otp_cells = (
        perf.groupby(["op_carrier", "origin", "dest", "year", "month"], as_index=False)
        .size()
        .rename(columns={"size": "otp_deps"})
    )
    t100_cells = (
        dom.groupby(["op_carrier", "origin", "dest", "year", "month"], as_index=False)[
            "departures_performed"
        ]
        .sum()
        .rename(columns={"departures_performed": "t100_deps"})
    )
    merged = otp_cells.merge(
        t100_cells,
        on=["op_carrier", "origin", "dest", "year", "month"],
        how="left",
    )
    merged["t100_deps"] = merged["t100_deps"].fillna(0.0)
    merged["rel_diff"] = (merged["t100_deps"] - merged["otp_deps"]) / merged["otp_deps"].clip(
        lower=1
    )

    mainline_set = {c.upper() for c in mainline_carriers}
    ded_exp_set = {c.upper() for c in dedicated_express_carriers}
    shared_set = {c.upper() for c in shared_cpa_carriers}

    is_mainline = merged["op_carrier"].isin(mainline_set)
    is_ded_express = merged["op_carrier"].isin(ded_exp_set) | (
        (merged["op_carrier"] == "ZW") & (merged["year"] == 2019)
    )
    is_multi_brand = merged["op_carrier"].isin(shared_set) & ~(
        (merged["op_carrier"] == "ZW") & (merged["year"] == 2019)
    )

    def _agg_metrics(sub: pd.DataFrame) -> dict[str, Any]:
        otp_sum = int(sub["otp_deps"].sum())
        t100_sum = float(sub["t100_deps"].sum())
        diff = ((t100_sum - otp_sum) / otp_sum) if otp_sum > 0 else 0.0
        within = int((sub["rel_diff"].abs() <= tolerance).sum()) if not sub.empty else 0
        return {
            "otp_performed": otp_sum,
            "t100_departures_performed": round(t100_sum, 2),
            "aggregate_rel_diff": round(diff, 6),
            "cells_total": int(len(sub)),
            "cells_within_tolerance": within,
            "cells_outside_tolerance": int(len(sub) - within),
        }

    mainline_stats = _agg_metrics(merged[is_mainline])
    ded_exp_stats = _agg_metrics(merged[is_ded_express])
    multi_df = merged[is_multi_brand]
    multi_otp_sum = int(multi_df["otp_deps"].sum())
    multi_t100_sum = float(multi_df["t100_deps"].sum())
    multi_one_sided_ok = multi_t100_sum >= (1.0 - tolerance) * multi_otp_sum

    details: dict[str, Any] = {
        "sample_mode": sample_mode,
        "tolerance": tolerance,
        "mainline": mainline_stats,
        "dedicated_express": ded_exp_stats,
        "multi_brand_cpa": {
            "otp_performed": multi_otp_sum,
            "t100_departures_performed": round(multi_t100_sum, 2),
            "one_sided_check_passed": bool(multi_one_sided_ok),
        },
    }

    if sample_mode or len(perf) < sample_min_t100_rows:
        return SanityCheckResult(
            check_id="SC-2",
            name="T-100 vs OTP Departure Reconciliation (±10%)",
            status="WARN",
            message=(
                f"Sample/subset mode ({len(perf):,} performed OTP rows vs full monthly T-100); "
                "strict aggregate drift gate skipped"
            ),
            details=details,
        )

    mainline_drift = abs(float(mainline_stats["aggregate_rel_diff"]))
    ded_exp_drift = abs(float(ded_exp_stats["aggregate_rel_diff"]))
    outside_cells = int(
        mainline_stats["cells_outside_tolerance"] + ded_exp_stats["cells_outside_tolerance"]
    )

    if mainline_drift > tolerance:
        return SanityCheckResult(
            check_id="SC-2",
            name="T-100 vs OTP Departure Reconciliation (±10%)",
            status="FAIL",
            message=(
                f"Mainline UA aggregate departure drift {mainline_drift:.2%} exceeds ±{tolerance:.0%}"
            ),
            details=details,
        )

    if ded_exp_drift > tolerance or not multi_one_sided_ok or outside_cells > 0:
        return SanityCheckResult(
            check_id="SC-2",
            name="T-100 vs OTP Departure Reconciliation (±10%)",
            status="WARN",
            message=(
                f"Aggregate reconciliation passed (mainline={mainline_stats['aggregate_rel_diff']:+.2%}, "
                f"dedicated_express={ded_exp_stats['aggregate_rel_diff']:+.2%}); "
                f"{outside_cells} dedicated route-month cells outside ±{tolerance:.0%}"
            ),
            details=details,
        )

    return SanityCheckResult(
        check_id="SC-2",
        name="T-100 vs OTP Departure Reconciliation (±10%)",
        status="PASS",
        message=(
            f"All carriers and route-month cells within ±{tolerance:.0%} "
            f"(mainline={mainline_stats['aggregate_rel_diff']:+.2%})"
        ),
        details=details,
    )


def check_sc3_seats_per_dep_vs_config(
    t100_df: pd.DataFrame,
    seat_config_df: pd.DataFrame,
    crosswalk_df: pd.DataFrame,
    tail_overrides_df: pd.DataFrame | None = None,
    hub: str = "ORD",
    ua_family_carriers: tuple[str, ...] = tuple(DEFAULT_UA_FAMILY_CARRIERS),
    warn_tolerance: float = 0.10,
    fail_tolerance: float = 0.15,
) -> SanityCheckResult:
    """SC-3: Verify T-100 seats-per-departure by `bts_aircraft_type` aligns with `seats_config`."""
    hub_norm = hub.strip().upper()
    family_set = {c.strip().upper() for c in ua_family_carriers}

    # Map bts_aircraft_type -> [min_seats_config, max_seats_config]
    seat_by_subtype = dict(
        zip(
            seat_config_df["subtype"].astype(str).str.strip(),
            seat_config_df["seats_total"].astype("int64"),
            strict=True,
        )
    )
    type_seat_sets: dict[str, list[int]] = {}
    for row in crosswalk_df.itertuples(index=False):
        bts_t = str(row.bts_aircraft_type).strip()
        sub = str(row.subtype).strip()
        if sub in seat_by_subtype:
            type_seat_sets.setdefault(bts_t, []).append(int(seat_by_subtype[sub]))
    if tail_overrides_df is not None and not tail_overrides_df.empty:
        for row in tail_overrides_df.itertuples(index=False):
            bts_t = str(row.bts_aircraft_type).strip()
            type_seat_sets.setdefault(bts_t, []).append(int(row.seats_total))

    seat_bounds: dict[str, tuple[int, int]] = {
        bts_t: (min(vals), max(vals)) for bts_t, vals in type_seat_sets.items() if vals
    }

    dom = t100_df.copy()
    dom["service_class"] = dom["service_class"].astype(str).str.strip().str.upper()
    dom["data_source"] = dom["data_source"].astype(str).str.strip().str.upper()
    dom["origin"] = dom["origin"].astype(str).str.strip().str.upper()
    dom["op_carrier"] = dom["op_carrier"].astype(str).str.strip().str.upper()
    dom["bts_aircraft_type"] = dom["bts_aircraft_type"].astype(str).str.strip()
    dom = dom[
        (dom["service_class"] == "F")
        & (dom["data_source"].isin(["DU", "DF"]))
        & (dom["origin"] == hub_norm)
        & (dom["op_carrier"].isin(family_set))
    ]

    grouped = dom.groupby("bts_aircraft_type", as_index=False)[
        ["seats", "departures_performed"]
    ].sum()
    grouped = grouped[grouped["departures_performed"] > 0].copy()

    warn_types: list[dict[str, Any]] = []
    fail_types: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []

    for row in grouped.itertuples(index=False):
        bts_t = str(row.bts_aircraft_type)
        if bts_t not in seat_bounds:
            continue
        lo, hi = seat_bounds[bts_t]
        spd = float(row.seats) / float(row.departures_performed)
        if spd < lo:
            delta = (lo - spd) / lo
        elif spd > hi:
            delta = (spd - hi) / hi
        else:
            delta = 0.0

        rec = {
            "bts_aircraft_type": bts_t,
            "t100_seats_per_dep": round(spd, 2),
            "config_min_seats": lo,
            "config_max_seats": hi,
            "bound_diff": round(delta, 6),
            "departures_performed": float(row.departures_performed),
        }
        checked.append(rec)
        if delta > fail_tolerance:
            fail_types.append(rec)
        elif delta > warn_tolerance:
            warn_types.append(rec)

    max_diff = max((r["bound_diff"] for r in checked), default=0.0)
    status: CheckStatus
    if fail_types:
        status = "FAIL"
        msg = f"{len(fail_types)} aircraft type(s) exceed {fail_tolerance:.0%} seat-config tolerance (max={max_diff:.2%})"
    elif warn_types:
        status = "WARN"
        msg = f"{len(warn_types)} aircraft type(s) exceed {warn_tolerance:.0%} warning tolerance (max={max_diff:.2%})"
    else:
        status = "PASS"
        msg = f"All {len(checked)} mapped BTS aircraft types within {warn_tolerance:.0%} of configured seats_config (max_diff={max_diff:.2%})"

    return SanityCheckResult(
        check_id="SC-3",
        name="T-100 Seats-per-Departure vs Configured Seats",
        status=status,
        message=msg,
        details={
            "types_checked": len(checked),
            "warn_tolerance": warn_tolerance,
            "fail_tolerance": fail_tolerance,
            "max_bound_diff": max_diff,
            "warn_types": warn_types,
            "fail_types": fail_types,
            "per_type": checked,
        },
    )


def check_sc4_system_wide_ua_load_factor(
    t100_df: pd.DataFrame,
    system_summary_df: pd.DataFrame | None = None,
    warn_bounds: tuple[float, float] = (0.80, 0.86),
    fail_bounds: tuple[float, float] = (0.75, 0.90),
    sample_fail_bounds: tuple[float, float] = (0.70, 0.92),
    sample_min_t100_rows: int = 500,
    sample_mode: bool = False,
) -> SanityCheckResult:
    """SC-4: Verify system-wide `UA` T-100 (`CLASS = 'F'`) load factor aligns with ~80-85%."""
    if system_summary_df is not None and not system_summary_df.empty:
        annual = system_summary_df.copy()
    else:
        sub = t100_df.copy()
        sub["op_carrier"] = sub["op_carrier"].astype(str).str.strip().str.upper()
        sub["service_class"] = sub["service_class"].astype(str).str.strip().str.upper()
        sub = sub[(sub["op_carrier"] == "UA") & (sub["service_class"] == "F")]
        if sub.empty:
            return SanityCheckResult(
                check_id="SC-4",
                name="System-Wide UA Load Factor (~80-85%)",
                status="FAIL",
                message="No UA Class 'F' T-100 rows found for system-wide LF check",
                details={},
            )
        annual = (
            sub.groupby("year", as_index=False)[["seats", "passengers", "departures_performed"]]
            .sum()
            .sort_values("year")
        )
        annual["load_factor"] = annual["passengers"] / annual["seats"].clip(lower=1.0)

    total_seats = float(annual["seats"].sum())
    total_pax = float(annual["passengers"].sum())
    overall_lf = (total_pax / total_seats) if total_seats > 0 else 0.0
    by_year = {
        str(int(r.year)): round(float(r.load_factor), 6) for r in annual.itertuples(index=False)
    }

    is_sample = sample_mode or (len(t100_df) < sample_min_t100_rows)
    active_fail_bounds = sample_fail_bounds if is_sample else fail_bounds

    outside_fail = [
        y for y, lf in by_year.items() if lf < active_fail_bounds[0] or lf > active_fail_bounds[1]
    ]
    outside_warn = [y for y, lf in by_year.items() if lf < warn_bounds[0] or lf > warn_bounds[1]]
    if overall_lf < active_fail_bounds[0] or overall_lf > active_fail_bounds[1]:
        if "overall" not in outside_fail:
            outside_fail.append("overall")
    if overall_lf < warn_bounds[0] or overall_lf > warn_bounds[1]:
        if "overall" not in outside_warn:
            outside_warn.append("overall")

    if outside_fail:
        status: CheckStatus = "FAIL"
        msg = (
            f"UA load factor outside fail bounds {active_fail_bounds}: "
            f"overall={overall_lf:.4%}, by_year={by_year}"
        )
    elif outside_warn:
        status = "WARN"
        msg = (
            f"UA load factor within fail bounds {active_fail_bounds} but outside target "
            f"{warn_bounds} ({'sample' if is_sample else 'full'} mode): overall={overall_lf:.4%}"
        )
    else:
        status = "PASS"
        msg = f"All UA annual & overall load factors inside target {warn_bounds}: overall={overall_lf:.4%}"

    return SanityCheckResult(
        check_id="SC-4",
        name="System-Wide UA Load Factor (~80-85%)",
        status=status,
        message=msg,
        details={
            "sample_mode": is_sample,
            "overall_load_factor": round(overall_lf, 6),
            "by_year_load_factor": by_year,
            "warn_bounds": list(warn_bounds),
            "fail_bounds": list(active_fail_bounds),
        },
    )


def check_sc5_missing_t100_null_invariant(enriched_df: pd.DataFrame) -> SanityCheckResult:
    """SC-5: Enforce zero silent imputation when T-100 match is missing (`NULL` estimates & SOI)."""
    unmatched = (~enriched_df["t100_available"].astype(bool)) | (
        enriched_df["lf_source"].astype(str) == "UNMATCHED"
    )
    unmatched_count = int(unmatched.sum())

    est_cols = (
        "est_load_factor",
        "est_passengers",
        "est_open_seats",
        "est_lf_adjusted",
        "est_passengers_adjusted",
        "est_open_seats_adjusted",
    )
    violations: dict[str, int] = {}
    if unmatched_count > 0:
        sub = enriched_df.loc[unmatched]
        for col in est_cols:
            if col in sub.columns:
                non_null = int(sub[col].notna().sum())
                if non_null > 0:
                    violations[col] = non_null
        for soi_col in (
            "s_open_hist",
            "s_disp_hist",
            "s_backup_hist",
            "s_demand_hist",
            "soi_historical",
            "s_open_plan",
            "s_disp_plan",
            "s_backup_plan",
            "s_demand_plan",
            "soi_planning",
        ):
            if soi_col in sub.columns:
                non_null = int(sub[soi_col].notna().sum())
                if non_null > 0:
                    violations[soi_col] = non_null

    canc_mask = enriched_df["is_cancelled"].astype(bool)
    for hist_col in (
        "s_open_hist",
        "s_disp_hist",
        "s_backup_hist",
        "s_demand_hist",
        "soi_historical",
    ):
        if hist_col in enriched_df.columns:
            canc_val = int(enriched_df.loc[canc_mask, hist_col].notna().sum())
            if canc_val > 0:
                violations[f"{hist_col}_cancelled"] = canc_val

    total_violations = sum(violations.values())
    status: CheckStatus = "FAIL" if total_violations > 0 else "PASS"
    msg = (
        f"Checked {unmatched_count:,} unmatched T-100 row(s); "
        f"silent imputation violations={total_violations}"
    )
    return SanityCheckResult(
        check_id="SC-5",
        name="Missing T-100 Null Invariant (Zero Silent Imputation)",
        status=status,
        message=msg,
        details={
            "unmatched_t100_rows": unmatched_count,
            "violations_by_column": violations,
            "total_violations": total_violations,
        },
    )


def check_soi_bounds(scored_df: pd.DataFrame) -> SanityCheckResult:
    """Verify all non-null SOI composite and sub-score columns lie strictly within `[0.0, 100.0]`."""
    score_cols = (
        "s_open_hist",
        "s_disp_hist",
        "s_backup_hist",
        "s_demand_hist",
        "soi_historical",
        "s_open_plan",
        "s_disp_plan",
        "s_backup_plan",
        "s_demand_plan",
        "soi_planning",
    )
    violations: dict[str, int] = {}
    checked_cols = 0
    for col in score_cols:
        if col not in scored_df.columns:
            continue
        checked_cols += 1
        vals = pd.to_numeric(scored_df[col], errors="coerce").dropna()
        if vals.empty:
            continue
        bad = int(((vals < 0.0) | (vals > 100.0)).sum())
        if bad > 0:
            violations[col] = bad

    total_violations = sum(violations.values())
    status: CheckStatus = "FAIL" if total_violations > 0 else "PASS"
    msg = (
        f"Checked {checked_cols} SOI score/sub-score columns; "
        f"out-of-bounds violations={total_violations}"
    )
    return SanityCheckResult(
        check_id="SOI-BOUNDS",
        name="Standby Opportunity Index [0, 100] Bounds",
        status=status,
        message=msg,
        details={
            "checked_columns": checked_cols,
            "violations_by_column": violations,
            "total_violations": total_violations,
        },
    )


def run_all_sanity_checks(
    enriched_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    seat_config_df: pd.DataFrame,
    crosswalk_df: pd.DataFrame,
    tail_overrides_df: pd.DataFrame | None = None,
    system_summary_df: pd.DataFrame | None = None,
    config: StandbyConfig | None = None,
    raise_on_failure: bool = True,
) -> SanityCheckReport:
    """Execute all 5 Section 7 sanity checks (`SC-1`..`SC-5`) and optionally raise on failure."""
    sc_cfg = config.sanity_checks if config is not None else SanityCheckConfig()
    hub = config.active_hub if config is not None else "ORD"
    sample_mode = config.sample_mode if config is not None else False
    mainline = tuple(config.mainline_carriers) if config is not None else ("UA",)
    ded_express = (
        config.enrichment.dedicated_express_carriers
        if config is not None
        else ("G7", "EV", "AX", "C5")
    )
    shared_cpa = (
        config.enrichment.shared_cpa_express_carriers
        if config is not None
        else ("OO", "YX", "YV", "ZW")
    )
    ua_family = (
        tuple(sorted(config.all_operating_carriers))
        if config is not None
        else tuple(sorted(DEFAULT_UA_FAMILY_CARRIERS))
    )

    results = [
        check_sc1_passenger_and_lf_bounds(enriched_df),
        check_sc2_t100_otp_departure_reconciliation(
            enriched_df=enriched_df,
            t100_df=t100_df,
            hub=hub,
            tolerance=sc_cfg.departure_reconciliation_tolerance,
            mainline_carriers=mainline,
            dedicated_express_carriers=ded_express,
            shared_cpa_carriers=shared_cpa,
            sample_mode=sample_mode,
            sample_min_t100_rows=sc_cfg.sample_min_t100_rows,
        ),
        check_sc3_seats_per_dep_vs_config(
            t100_df=t100_df,
            seat_config_df=seat_config_df,
            crosswalk_df=crosswalk_df,
            tail_overrides_df=tail_overrides_df,
            hub=hub,
            ua_family_carriers=ua_family,
            warn_tolerance=sc_cfg.seats_per_dep_warn_tolerance,
            fail_tolerance=sc_cfg.seats_per_dep_fail_tolerance,
        ),
        check_sc4_system_wide_ua_load_factor(
            t100_df=t100_df,
            system_summary_df=system_summary_df,
            warn_bounds=sc_cfg.system_lf_warn_bounds,
            fail_bounds=sc_cfg.system_lf_fail_bounds,
            sample_fail_bounds=sc_cfg.sample_lf_fail_bounds,
            sample_min_t100_rows=sc_cfg.sample_min_t100_rows,
            sample_mode=sample_mode,
        ),
        check_sc5_missing_t100_null_invariant(enriched_df),
    ]

    report = SanityCheckReport(results=results)
    for res in results:
        if res.status == "FAIL":
            logger.error(f"[{res.check_id}] {res.name}: {res.message}")
        elif res.status == "WARN":
            logger.warning(f"[{res.check_id}] {res.name}: {res.message}")
        else:
            logger.info(f"[{res.check_id}] {res.name}: {res.message}")

    if raise_on_failure:
        report.raise_if_failed()
    return report
