"""Seeded correlated day-level Monte Carlo standby clearance simulator.

Uses a 32-bit Mulberry32 PRNG + Box-Muller normal draws so Python CLI and
client-side JavaScript produce deterministic, matching probability estimates.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from standby.config import PredictionConfig, StandbyConfig
from standby.prediction.priors import HOUR_BLOCK_NAMES, HOUR_BLOCK_TO_IDX, PredictorPriors
from standby.scoring.holidays import holiday_name, thanksgiving_date

HOUR_BLOCK_LABELS: dict[str, str] = {
    "early": "Early (05:00–07:59)",
    "morning": "Morning (08:00–10:59)",
    "midday": "Midday (11:00–13:59)",
    "afternoon": "Afternoon (14:00–16:59)",
    "evening": "Evening (17:00–19:59)",
    "late": "Late (20:00–04:59)",
}

DAY_NAMES: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class Mulberry32:
    """Deterministic 32-bit PRNG matching JS bitwise implementation 1:1."""

    def __init__(self, seed: int) -> None:
        self._state = int(seed) & 0xFFFFFFFF
        self._spare: float | None = None

    def random(self) -> float:
        self._state = (self._state + 0x6D2B79F5) & 0xFFFFFFFF
        t = self._state
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ ((t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    def normal(self) -> float:
        if self._spare is not None:
            val = self._spare
            self._spare = None
            return val
        u1 = max(1e-12, self.random())
        u2 = self.random()
        mag = math.sqrt(-2.0 * math.log(u1))
        angle = 2.0 * math.pi * u2
        self._spare = mag * math.sin(angle)
        return mag * math.cos(angle)

    def poisson(self, lam: float) -> int:
        if lam <= 0.0:
            return 0
        if lam > 30.0:
            return max(0, int(round(lam + math.sqrt(lam) * self.normal())))
        limit = math.exp(-lam)
        k = 0
        prod = 1.0
        while True:
            k += 1
            prod *= max(1e-12, self.random())
            if prod <= limit:
                return k - 1


def _hash_seed(base_seed: int, key: str) -> int:
    h = (int(base_seed) ^ 0x811C9DC5) & 0xFFFFFFFF
    for ch in key:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _coerce_iso_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _classify_horizon_key(hours_out: float | None) -> str:
    if hours_out is None or hours_out > 72.0:
        return "gt_72h"
    if hours_out > 24.0:
        return "h24_72"
    if hours_out > 6.0:
        return "h6_24"
    return "lt_6h"


def _risk_tier(prob: float) -> str:
    if prob >= 0.80:
        return "Strong Odds"
    if prob >= 0.60:
        return "Favorable"
    if prob >= 0.40:
        return "Moderate / Watch Closely"
    if prob >= 0.20:
        return "Tight"
    return "High Risk"


@dataclass(frozen=True)
class PredictionRequest:
    """Input specification for a day-based standby seat likelihood forecast."""

    dest: str
    flight_date: date | str
    party_size: int = 1
    priority: str = "SA2"
    carrier_group: str = "ALL"
    open_seats_override: float | None = None
    standbys_ahead_override: int | None = None
    hours_until_departure: float | None = None
    target_hour_block: str | None = None
    draws: int | None = None
    seed: int | None = None


@dataclass(frozen=True)
class HourBlockPrediction:
    """Predicted standby clearance outlook for one departure hour block."""

    hour_block: str
    label: str
    expected_flights: float
    expected_open_seats: float
    open_seats_std: float
    cancel_rate: float
    clearance_prob: float
    seat_avail_prob: float
    prior_tier: str


@dataclass(frozen=True)
class DayComparisonItem:
    """Lightweight flexible-date comparison entry (±3 days around target date)."""

    flight_date: str
    day_of_week: str
    holiday_name: str | None
    whole_day_prob: float
    expected_open_seats: float
    delta_vs_selected: float


@dataclass(frozen=True)
class DayPredictionResult:
    """Complete standby seat likelihood forecast for a route and target date."""

    hub: str
    dest: str
    dest_city: str
    dest_state_or_country: str
    flight_date: str
    day_of_week: str
    holiday_name: str | None
    party_size: int
    priority: str
    carrier_group: str
    mode: str
    is_international_v1: bool
    is_shared_regional_spoke: bool
    is_hub_to_hub: bool
    whole_day_prob: float
    ci_low_80: float
    ci_high_80: float
    seat_only_whole_day_prob: float
    risk_tier: str
    expected_daily_flights: float
    expected_open_seats_avg: float
    expected_cancel_rate: float
    estimated_queue_ahead: float
    queue_source: str
    best_hour_block: str | None
    best_hour_block_prob: float | None
    hour_blocks: list[HourBlockPrediction]
    neighbor_days: list[DayComparisonItem]
    drivers: list[str]
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _holiday_adjustment(
    d: date,
    hname: str | None,
    priors: PredictorPriors,
) -> tuple[float, float, str | None]:
    """Return (open_seat_multiplier, cancel_rate_multiplier, special_day_note)."""
    if not hname:
        return 1.0, 1.0, None

    base = priors.holiday_deltas.get(hname, {"open_mult": 0.90, "cancel_mult": 1.05})
    open_mult = float(base["open_mult"])
    cancel_mult = float(base["cancel_mult"])
    note: str | None = f"{hname} travel window"

    # Preserve empirical Pillar 2 bimodal nuances (Thanksgiving Sunday peak vs Christmas Eve/Day lull)
    if hname == "Thanksgiving":
        tg = thanksgiving_date(d.year)
        if d == tg + timedelta(days=3):  # Thanksgiving Sunday
            open_mult = min(open_mult, 0.62)
            note = "Thanksgiving Sunday (historically tightest travel day of year)"
        elif d == tg:
            open_mult = max(open_mult, 1.02)
            note = "Thanksgiving Day (lighter mid-day loads than Wed/Sun peaks)"
    elif hname == "Christmas / New Year":
        if d.month == 12 and d.day in (24, 25):
            open_mult = max(open_mult, 1.04)
            note = "Christmas Eve/Day (historically lighter than Dec 20–23 & 26–30 peaks)"
        else:
            open_mult = min(open_mult, 0.78)
            note = "Christmas / New Year peak travel window"

    return open_mult, cancel_mult, note


def resolve_hour_block_slots(
    dest: str,
    d: date,
    priors: PredictorPriors,
    carrier_group: str = "ALL",
) -> tuple[list[dict[str, Any]], bool, float, str | None]:
    """Resolve hierarchical empirical priors for every active hour block on ``(dest, d)``."""
    dest_u = dest.strip().upper()
    cg = carrier_group.strip().upper()
    if cg not in {"ALL", "MAINLINE", "EXPRESS"}:
        cg = "ALL"

    route_info = priors.routes.get(dest_u)
    is_intl = bool(route_info and route_info.get("intl", 0) == 1)
    m = d.month
    dow = d.isoweekday()
    hname = holiday_name(d)
    hol_open_mult, hol_canc_mult, hol_note = _holiday_adjustment(d, hname, priors)
    shrink_m = float(priors.meta.get("shrinkage_pseudo_count", 10.0))

    # Resolve Tier 3 (route x month) with fallback to annual route average
    t3 = priors.tier3.get(f"{dest_u}|{m}")
    if t3 is None:
        if route_info is not None:
            spd = float(route_info["spd"])
            lf = float(route_info["lf"])
            t3 = [
                max(1, int(route_info["deps"])),
                max(0.0, spd * (1.0 - lf)),
                max(4.0, spd * 0.10),
                spd,
                lf,
                0.015 if is_intl else 0.020,
                max(0.5, float(route_info["avg_daily"])),
            ]
        else:
            t3 = [10, 16.0, 10.0, 120.0, 0.82, 0.02, 2.0]

    daily_flights_total = max(0.25, float(t3[6]))

    # Carrier group adjustment for domestic routes
    cg_share = 1.0
    cg_open_ratio = 1.0
    if not is_intl and cg != "ALL":
        rcf = priors.route_carrier_factors.get(dest_u, {}).get(cg)
        if rcf is not None:
            cg_share = max(0.05, float(rcf[0]))
            cg_open_ratio = float(rcf[1])
        else:
            # Route has no observed history for this carrier group; fall back gently
            cg_share = 0.0

    # International v1: single consolidated route-month bank
    if is_intl:
        dow_mult = float(priors.dow_multipliers.get(str(dow), 1.0))
        # Dampen domestic DOW signal on long-haul international segments
        intl_dow_mult = 1.0 + 0.35 * (dow_mult - 1.0)
        mean_open = max(0.0, float(t3[1]) * intl_dow_mult * hol_open_mult)
        std_open = max(5.0, float(t3[2]) * 1.15)
        canc = min(0.25, max(0.003, float(t3[5]) * hol_canc_mult))
        slot = {
            "hb": "all_day",
            "label": "All Scheduled Departures (T-100 Route-Month Prior v1)",
            "expected_flights": round(daily_flights_total, 2),
            "mean_open": mean_open,
            "std_open": std_open,
            "mean_seats": float(t3[3]),
            "cancel_rate": canc,
            "n_eff": max(1.0, float(t3[0])),
            "prior_tier": "TIER3_INTL_T100_V1",
        }
        return [slot], True, daily_flights_total, hol_note

    # Domestic: resolve all 6 hour blocks with 4-tier Empirical Bayes shrinkage
    slots: list[dict[str, Any]] = []
    raw_shares: list[float] = []
    dow_mult = float(priors.dow_multipliers.get(str(dow), 1.0))

    for hb_idx, hb_name in enumerate(HOUR_BLOCK_NAMES):
        t4 = (
            priors.tier4.get(f"{cg}|{m}|{hb_idx}")
            or priors.tier4.get(f"ALL|{m}|{hb_idx}")
            or [100, 16.0, 12.0, 120.0, 0.82, 0.02, 1.0 / 6.0]
        )
        t2 = priors.tier2.get(f"{dest_u}|{m}|{hb_idx}")
        t1 = priors.tier1.get(f"{dest_u}|{m}|{dow}|{hb_idx}")

        # Base parent: blend Tier 3 route-month level with Tier 4 hour-block pattern
        t4_all_m = [priors.tier4.get(f"ALL|{m}|{i}") for i in range(6)]
        t4_mean_open_m = sum(x[1] for x in t4_all_m if x) / max(1, sum(1 for x in t4_all_m if x))
        hb_rel = float(t4[1]) / max(1.0, t4_mean_open_m)
        parent_open = float(t3[1]) * hb_rel
        parent_std = max(float(t3[2]), float(t4[2]) * 0.75)
        parent_seats = float(t3[3])
        parent_canc = float(t3[5])
        tier_used = "TIER3_ROUTE_MONTH"

        if t2 is not None:
            n2 = float(t2[0])
            w2 = n2 / (n2 + shrink_m)
            mean_open = w2 * float(t2[1]) + (1.0 - w2) * parent_open
            std_open = w2 * float(t2[2]) + (1.0 - w2) * parent_std
            mean_seats = w2 * float(t2[3]) + (1.0 - w2) * parent_seats
            canc_rate = w2 * float(t2[5]) + (1.0 - w2) * parent_canc
            share = float(t2[6])
            n_eff = n2
            tier_used = "TIER2_ROUTE_MONTH_HB"
        else:
            mean_open = parent_open
            std_open = parent_std
            mean_seats = parent_seats
            canc_rate = parent_canc
            share = (
                float(t4[6]) * 0.25
                if any(f"{dest_u}|{m}|{i}" in priors.tier2 for i in range(6))
                else float(t4[6])
            )
            n_eff = max(1.0, float(t3[0]) / 6.0)

        if t1 is not None:
            n1 = float(t1[0])
            w1 = n1 / (n1 + shrink_m)
            mean_open = w1 * float(t1[1]) + (1.0 - w1) * (mean_open * dow_mult)
            std_open = w1 * float(t1[2]) + (1.0 - w1) * std_open
            mean_seats = w1 * float(t1[3]) + (1.0 - w1) * mean_seats
            canc_rate = w1 * float(t1[5]) + (1.0 - w1) * canc_rate
            if n1 >= 8:
                share = 0.65 * float(t1[6]) + 0.35 * share
            n_eff = n1
            tier_used = "TIER1_ROUTE_MONTH_DOW_HB"
        else:
            mean_open *= dow_mult

        mean_open = max(0.0, mean_open * hol_open_mult * cg_open_ratio)
        canc_rate = min(0.35, max(0.002, canc_rate * hol_canc_mult))

        raw_shares.append(max(0.0, share))
        slots.append(
            {
                "hb": hb_name,
                "label": HOUR_BLOCK_LABELS[hb_name],
                "mean_open": mean_open,
                "std_open": max(2.0, std_open),
                "mean_seats": max(50.0, mean_seats),
                "cancel_rate": canc_rate,
                "n_eff": n_eff,
                "prior_tier": tier_used,
            }
        )

    total_share = sum(raw_shares)
    eff_daily = daily_flights_total * cg_share
    for slot, sh in zip(slots, raw_shares, strict=True):
        norm_sh = (sh / total_share) if total_share > 0 else (1.0 / 6.0)
        slot["expected_flights"] = eff_daily * norm_sh

    return slots, False, eff_daily, hol_note


def _run_day_monte_carlo(
    slots: list[dict[str, Any]],
    party_size: int,
    queue_mean: float,
    queue_is_exact_override: bool,
    open_override: float | None,
    target_hb: str | None,
    vol_scale: float,
    is_intl: bool,
    hub_irrops_corr: float,
    spillover_seats: float,
    spillover_decay: float,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    """Simulate ``draws`` correlated flight-days across chronological hour blocks."""
    rng = Mulberry32(seed)
    n_batches = 40
    draws_per_batch = max(10, draws // n_batches)
    total_draws = n_batches * draws_per_batch

    active_slots = [s for s in slots if s["expected_flights"] >= 0.08]
    if not active_slots and slots:
        active_slots = [max(slots, key=lambda s: s["expected_flights"])]

    hb_clear_hits = {s["hb"]: 0 for s in slots}
    hb_seat_hits = {s["hb"]: 0 for s in slots}
    hb_flight_trials = {s["hb"]: 0 for s in slots}

    batch_clear_rates: list[float] = []
    day_clear_total = 0
    day_seat_total = 0

    for _ in range(n_batches):
        # Outer epistemic uncertainty draw (wider on sparse / international v1 routes, narrower near departure)
        epistemic_z = rng.normal()
        batch_cleared = 0

        for _ in range(draws_per_batch):
            # Shared ORD hub disruption shock (positive = worse weather/ATC day)
            # and shared route-day demand shock (positive = lighter demand / more open seats)
            z_hub = rng.normal()
            z_day = rng.normal()
            spill_pool = 0.0
            cleared_today = False
            seat_only_today = False
            day_corr = 0.58
            ind_weight = math.sqrt(
                max(0.04, 1.0 - hub_irrops_corr * hub_irrops_corr - day_corr * day_corr)
            )

            for slot in slots:
                exp_f = float(slot["expected_flights"])
                if exp_f < 0.08 and slot not in active_slots:
                    continue

                # Determine number of scheduled departures in this bank
                base_f = int(math.floor(exp_f))
                frac_f = exp_f - base_f
                n_flights = base_f + (1 if rng.random() < frac_f else 0)
                if n_flights == 0 and slot is active_slots[0] and len(active_slots) == 1:
                    n_flights = 1
                if n_flights == 0:
                    spill_pool *= spillover_decay
                    continue

                is_targeted = open_override is not None and (
                    target_hb is None or slot["hb"] == target_hb or is_intl
                )

                mu = float(open_override) if is_targeted else float(slot["mean_open"])
                sigma = float(slot["std_open"]) * (vol_scale if is_targeted else 1.0)
                sigma = max(1.5, sigma)
                seats_cap = float(slot["mean_seats"])

                # Epistemic standard error shrinks with sample size and live EmployeeRES closeness
                base_ep_se = sigma / math.sqrt(max(3.0, min(60.0, float(slot["n_eff"]))))
                if is_intl:
                    base_ep_se = max(base_ep_se, seats_cap * 0.045)
                if is_targeted:
                    base_ep_se *= vol_scale * 0.65
                mu_eff = max(0.0, mu + epistemic_z * base_ep_se)

                # Precompute shifted Log-Normal parameters for non-negative right-skewed advance priors
                m1 = max(0.5, mu_eff + 1.0)
                s2_log = math.log(1.0 + (sigma * sigma) / (m1 * m1))
                s_log = math.sqrt(s2_log)
                m_log = math.log(m1) - 0.5 * s2_log

                # Hub-coupled cancellation probability
                p_canc = float(slot["cancel_rate"])
                if z_hub > 1.2:
                    p_canc = min(0.55, p_canc * (1.0 + 0.9 * (z_hub - 1.2)))
                elif z_hub < -0.5:
                    p_canc = max(0.002, p_canc * 0.75)

                bank_cancels = 0
                bank_cleared = False
                bank_seat_ok = False
                hb_flight_trials[slot["hb"]] += 1

                for _f in range(n_flights):
                    if rng.random() < p_canc:
                        bank_cancels += 1
                        continue

                    # Correlated seat draw: hub IRROPS + same-day route demand + flight noise
                    z_ind = rng.normal()
                    z_tot = -hub_irrops_corr * z_hub + day_corr * z_day + ind_weight * z_ind
                    if is_targeted:
                        raw_open = mu_eff + sigma * z_tot
                    else:
                        raw_open = math.exp(m_log + s_log * z_tot) - 1.0
                    open_draw = min(seats_cap, max(0.0, raw_open - spill_pool))

                    if queue_is_exact_override:
                        # Small residual churn on live standby list when >6h out
                        q_churn = rng.normal() * (0.8 * vol_scale) if vol_scale > 0.2 else 0.0
                        q_draw = max(0.0, queue_mean + q_churn)
                    else:
                        # Competing standby list swells slightly on high-demand / IRROPS days
                        q_day_mult = max(0.55, 1.0 - 0.18 * z_day + 0.12 * max(0.0, z_hub))
                        q_draw = float(rng.poisson(max(0.0, queue_mean * q_day_mult)))

                    if open_draw >= party_size:
                        bank_seat_ok = True
                    if (open_draw - q_draw) >= party_size:
                        bank_cleared = True

                if bank_cleared:
                    hb_clear_hits[slot["hb"]] += 1
                    cleared_today = True
                if bank_seat_ok:
                    hb_seat_hits[slot["hb"]] += 1
                    seat_only_today = True

                # Spillover from cancelled flights compresses seats in the next bank
                spill_pool = spill_pool * spillover_decay + bank_cancels * spillover_seats

            if cleared_today:
                day_clear_total += 1
                batch_cleared += 1
            if seat_only_today:
                day_seat_total += 1

        batch_clear_rates.append(batch_cleared / draws_per_batch)

    batch_clear_rates.sort()
    p_low = batch_clear_rates[int(0.10 * (n_batches - 1))]
    p_high = batch_clear_rates[int(0.90 * (n_batches - 1))]
    whole_day_prob = day_clear_total / total_draws
    seat_only_prob = day_seat_total / total_draws

    hb_probs = {
        s["hb"]: (
            hb_clear_hits[s["hb"]] / hb_flight_trials[s["hb"]]
            if hb_flight_trials[s["hb"]] > 0
            else 0.0
        )
        for s in slots
    }
    hb_seat_probs = {
        s["hb"]: (
            hb_seat_hits[s["hb"]] / hb_flight_trials[s["hb"]]
            if hb_flight_trials[s["hb"]] > 0
            else 0.0
        )
        for s in slots
    }
    return {
        "whole_day_prob": round(whole_day_prob, 4),
        "ci_low_80": round(min(whole_day_prob, p_low), 4),
        "ci_high_80": round(max(whole_day_prob, p_high), 4),
        "seat_only_whole_day_prob": round(seat_only_prob, 4),
        "hb_probs": hb_probs,
        "hb_seat_probs": hb_seat_probs,
    }


def predict_standby_day(
    request: PredictionRequest,
    priors: PredictorPriors,
    config: StandbyConfig | None = None,
    include_neighbors: bool = True,
) -> DayPredictionResult:
    """Predict whole-day and per-hour-block standby seat clearance likelihood."""
    pred_cfg = config.prediction if config is not None else PredictionConfig()
    dest_u = request.dest.strip().upper()
    if dest_u not in priors.routes:
        raise ValueError(
            f"Unknown destination {dest_u!r}. Expected one of {len(priors.routes)} ORD routes."
        )

    d = _coerce_iso_date(request.flight_date)
    party_size = max(1, min(6, int(request.party_size)))
    priority = request.priority.strip().upper()
    if priority not in pred_cfg.pass_tier_queue_means:
        priority = "SA2"
    cg = request.carrier_group.strip().upper()
    if cg not in {"ALL", "MAINLINE", "EXPRESS"}:
        cg = "ALL"

    target_hb = (
        request.target_hour_block.strip().lower()
        if request.target_hour_block
        and request.target_hour_block.strip().lower() in HOUR_BLOCK_TO_IDX
        else None
    )

    route_info = priors.routes[dest_u]
    is_hub_to_hub = bool(route_info.get("hub_to_hub", 0) == 1)
    is_shared_spoke = bool(route_info.get("sha", 0) == 1)
    hname = holiday_name(d)

    # Queue prior vs live EmployeeRES override
    if request.standbys_ahead_override is not None:
        queue_mean = max(0.0, float(request.standbys_ahead_override))
        queue_is_exact = True
        queue_source = "EmployeeRES live input"
    else:
        base_q = float(
            priors.meta.get("pass_tier_queue_means", pred_cfg.pass_tier_queue_means).get(
                priority, 2.5
            )
        )
        h2h_mult = float(
            priors.meta.get("hub_to_hub_queue_multiplier", pred_cfg.hub_to_hub_queue_multiplier)
        )
        queue_mean = base_q * (h2h_mult if is_hub_to_hub else 1.0)
        if hname in {"Thanksgiving", "Christmas / New Year"}:
            queue_mean *= 1.35
        queue_is_exact = False
        queue_source = f"Assumed {priority} queue prior" + (
            " (hub-to-hub uplift)" if is_hub_to_hub else ""
        )

    h_key = _classify_horizon_key(request.hours_until_departure)
    vol_scale = float(
        priors.meta.get("horizon_volatility_scale", pred_cfg.horizon_volatility_scale).get(
            h_key, 1.0
        )
    )
    has_live = (
        request.open_seats_override is not None or request.standbys_ahead_override is not None
    )
    mode = "live_employeeres" if has_live else "advance_prior"

    slots, is_intl, eff_daily, hol_note = resolve_hour_block_slots(
        dest=dest_u, d=d, priors=priors, carrier_group=cg
    )

    draws = int(request.draws or priors.meta.get("simulation_draws", pred_cfg.simulation_draws))
    base_seed = int(
        request.seed
        if request.seed is not None
        else priors.meta.get("simulation_seed", pred_cfg.simulation_seed)
    )
    open_key = (
        "NONE"
        if request.open_seats_override is None
        else f"{float(request.open_seats_override):.1f}"
    )
    q_key = (
        "NONE"
        if request.standbys_ahead_override is None
        else str(int(request.standbys_ahead_override))
    )
    hb_key = target_hb if target_hb is not None else "NONE"
    seed = _hash_seed(
        base_seed,
        f"{dest_u}|{d.isoformat()}|{party_size}|{priority}|{cg}|{open_key}|{q_key}|{hb_key}",
    )

    sim = _run_day_monte_carlo(
        slots=slots,
        party_size=party_size,
        queue_mean=queue_mean,
        queue_is_exact_override=queue_is_exact,
        open_override=request.open_seats_override,
        target_hb=target_hb,
        vol_scale=vol_scale,
        is_intl=is_intl,
        hub_irrops_corr=float(priors.meta.get("hub_irrops_corr", pred_cfg.hub_irrops_corr)),
        spillover_seats=float(
            priors.meta.get("spillover_seats_per_cancel", pred_cfg.spillover_seats_per_cancel)
        ),
        spillover_decay=float(priors.meta.get("spillover_decay", pred_cfg.spillover_decay)),
        draws=draws,
        seed=seed,
    )

    hb_preds: list[HourBlockPrediction] = []
    weighted_open_sum = 0.0
    weighted_canc_sum = 0.0
    weight_total = 0.0
    for s in slots:
        ef = float(s["expected_flights"])
        hb_open = (
            float(request.open_seats_override)
            if (
                request.open_seats_override is not None
                and (target_hb is None or s["hb"] == target_hb or is_intl)
            )
            else float(s["mean_open"])
        )
        hb_preds.append(
            HourBlockPrediction(
                hour_block=s["hb"],
                label=s["label"],
                expected_flights=round(ef, 2),
                expected_open_seats=round(hb_open, 1),
                open_seats_std=round(float(s["std_open"]), 1),
                cancel_rate=round(float(s["cancel_rate"]), 4),
                clearance_prob=round(float(sim["hb_probs"][s["hb"]]), 4),
                seat_avail_prob=round(float(sim["hb_seat_probs"][s["hb"]]), 4),
                prior_tier=s["prior_tier"],
            )
        )
        if ef > 0:
            weighted_open_sum += hb_open * ef
            weighted_canc_sum += float(s["cancel_rate"]) * ef
            weight_total += ef

    avg_open = weighted_open_sum / weight_total if weight_total > 0 else 0.0
    avg_canc = weighted_canc_sum / weight_total if weight_total > 0 else 0.0

    viable_hbs = [h for h in hb_preds if h.expected_flights >= 0.20] or hb_preds
    best_hb = max(viable_hbs, key=lambda h: h.clearance_prob) if viable_hbs else None

    # ±3 day flexible-date comparison strip (advance-prior baseline across nearby days)
    neighbors: list[DayComparisonItem] = []
    if include_neighbors:
        for delta in range(-3, 4):
            nd = d + timedelta(days=delta)
            if delta == 0:
                neighbors.append(
                    DayComparisonItem(
                        flight_date=nd.isoformat(),
                        day_of_week=DAY_NAMES[nd.isoweekday() - 1],
                        holiday_name=hname,
                        whole_day_prob=sim["whole_day_prob"],
                        expected_open_seats=round(avg_open, 1),
                        delta_vs_selected=0.0,
                    )
                )
            else:
                n_slots, n_intl, _, _ = resolve_hour_block_slots(
                    dest=dest_u, d=nd, priors=priors, carrier_group=cg
                )
                n_hname = holiday_name(nd)
                n_q = queue_mean
                if not queue_is_exact:
                    base_q = float(
                        priors.meta.get(
                            "pass_tier_queue_means", pred_cfg.pass_tier_queue_means
                        ).get(priority, 2.5)
                    )
                    h2h_mult = float(
                        priors.meta.get(
                            "hub_to_hub_queue_multiplier", pred_cfg.hub_to_hub_queue_multiplier
                        )
                    )
                    n_q = base_q * (h2h_mult if is_hub_to_hub else 1.0)
                    if n_hname in {"Thanksgiving", "Christmas / New Year"}:
                        n_q *= 1.35
                n_sim = _run_day_monte_carlo(
                    slots=n_slots,
                    party_size=party_size,
                    queue_mean=n_q,
                    queue_is_exact_override=queue_is_exact,
                    open_override=None,
                    target_hb=None,
                    vol_scale=1.0,
                    is_intl=n_intl,
                    hub_irrops_corr=float(
                        priors.meta.get("hub_irrops_corr", pred_cfg.hub_irrops_corr)
                    ),
                    spillover_seats=float(
                        priors.meta.get(
                            "spillover_seats_per_cancel", pred_cfg.spillover_seats_per_cancel
                        )
                    ),
                    spillover_decay=float(
                        priors.meta.get("spillover_decay", pred_cfg.spillover_decay)
                    ),
                    draws=600,
                    seed=_hash_seed(
                        base_seed, f"nbr|{dest_u}|{nd.isoformat()}|{party_size}|{priority}|{cg}"
                    ),
                )
                n_wt = sum(float(s["expected_flights"]) for s in n_slots)
                n_open = (
                    sum(float(s["mean_open"]) * float(s["expected_flights"]) for s in n_slots)
                    / n_wt
                    if n_wt > 0
                    else 0.0
                )
                neighbors.append(
                    DayComparisonItem(
                        flight_date=nd.isoformat(),
                        day_of_week=DAY_NAMES[nd.isoweekday() - 1],
                        holiday_name=n_hname,
                        whole_day_prob=n_sim["whole_day_prob"],
                        expected_open_seats=round(n_open, 1),
                        delta_vs_selected=round(n_sim["whole_day_prob"] - sim["whole_day_prob"], 4),
                    )
                )

    # Plain-English drivers
    drivers: list[str] = []
    if is_intl:
        drivers.append(
            f"International route (v1): derived from BTS T-100 monthly load factor ({route_info['lf'] * 100:.1f}%) and ~{eff_daily:.1f} daily departures; no flight-level OTP hour blocks."
        )
    else:
        drivers.append(
            f"Backup depth: ~{eff_daily:.1f} scheduled daily departures across ORD → {dest_u} give multiple fallback chances if an earlier bank fills or disrupts."
        )
        if best_hb is not None:
            drivers.append(
                f"Best window: {best_hb.label} ({best_hb.clearance_prob * 100:.0f}% single-bank odds, ~{best_hb.expected_open_seats:.1f} expected open seats)."
            )
    if hol_note:
        drivers.append(f"Calendar regime: {hol_note}.")
    elif d.isoweekday() in (2, 3, 6):
        drivers.append(
            f"{DAY_NAMES[d.isoweekday() - 1]} historically offers lighter TSA demand and more open seats than Thu/Fri/Sun peaks."
        )
    elif d.isoweekday() in (4, 5, 7):
        drivers.append(
            f"{DAY_NAMES[d.isoweekday() - 1]} historically sees elevated leisure/business demand out of ORD."
        )
    if has_live:
        drivers.append(
            f"Sharpened with EmployeeRES input (horizon scale {vol_scale * 100:.0f}% remaining volatility)."
        )
    else:
        drivers.append(
            f"Queue overlay: modeled ~{queue_mean:.1f} competing standbys ahead ({queue_source}); pure open-seat availability odds = {sim['seat_only_whole_day_prob'] * 100:.0f}%."
        )
    if is_shared_spoke:
        drivers.append(
            "Shared regional CPA spoke (OO/YX/2023 ZW): T-100 baseline blends multi-brand regional traffic."
        )

    disclaimer = (
        "Modeled standby seat likelihood combining empirically calibrated historical open-seat priors "
        "(BTS OTP, T-100, FAA, TSA) with an assumed non-rev pass-tier queue overlay (or your EmployeeRES "
        "override). Not an official United Airlines clearance guarantee."
    )

    return DayPredictionResult(
        hub=str(priors.meta.get("hub", "ORD")),
        dest=dest_u,
        dest_city=str(route_info["cty"]),
        dest_state_or_country=str(route_info["st"]),
        flight_date=d.isoformat(),
        day_of_week=DAY_NAMES[d.isoweekday() - 1],
        holiday_name=hname,
        party_size=party_size,
        priority=priority,
        carrier_group=cg,
        mode=mode,
        is_international_v1=is_intl,
        is_shared_regional_spoke=is_shared_spoke,
        is_hub_to_hub=is_hub_to_hub,
        whole_day_prob=sim["whole_day_prob"],
        ci_low_80=sim["ci_low_80"],
        ci_high_80=sim["ci_high_80"],
        seat_only_whole_day_prob=sim["seat_only_whole_day_prob"],
        risk_tier=_risk_tier(sim["whole_day_prob"]),
        expected_daily_flights=round(eff_daily, 2),
        expected_open_seats_avg=round(avg_open, 1),
        expected_cancel_rate=round(avg_canc, 4),
        estimated_queue_ahead=round(queue_mean, 1),
        queue_source=queue_source,
        best_hour_block=best_hb.label if best_hb else None,
        best_hour_block_prob=best_hb.clearance_prob if best_hb else None,
        hour_blocks=hb_preds,
        neighbor_days=neighbors,
        drivers=drivers,
        disclaimer=disclaimer,
    )
