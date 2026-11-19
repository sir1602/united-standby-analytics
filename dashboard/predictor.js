/* United Standby Day Predictor Engine — 1:1 Parity Port of src/standby/prediction/simulator.py */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.StandbyPredictor = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const HOUR_BLOCK_NAMES = ["early", "morning", "midday", "afternoon", "evening", "late"];
  const HOUR_BLOCK_TO_IDX = {
    early: 0,
    morning: 1,
    midday: 2,
    afternoon: 3,
    evening: 4,
    late: 5,
  };
  const HOUR_BLOCK_LABELS = {
    early: "Early (05:00–07:59)",
    morning: "Morning (08:00–10:59)",
    midday: "Midday (11:00–13:59)",
    afternoon: "Afternoon (14:00–16:59)",
    evening: "Evening (17:00–19:59)",
    late: "Late (20:00–04:59)",
  };
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  class Mulberry32 {
    constructor(seed) {
      this._state = seed >>> 0;
      this._spare = null;
    }

    random() {
      this._state = (this._state + 0x6d2b79f5) >>> 0;
      let t = this._state;
      t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0;
      t = (t ^ ((t + Math.imul(t ^ (t >>> 7), t | 61)) >>> 0)) >>> 0;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296.0;
    }

    normal() {
      if (this._spare !== null) {
        const val = this._spare;
        this._spare = null;
        return val;
      }
      const u1 = Math.max(1e-12, this.random());
      const u2 = this.random();
      const mag = Math.sqrt(-2.0 * Math.log(u1));
      const angle = 2.0 * Math.PI * u2;
      this._spare = mag * Math.sin(angle);
      return mag * Math.cos(angle);
    }

    poisson(lam) {
      if (lam <= 0.0) return 0;
      if (lam > 30.0) {
        return Math.max(0, Math.round(lam + Math.sqrt(lam) * this.normal()));
      }
      const limit = Math.exp(-lam);
      let k = 0;
      let prod = 1.0;
      while (true) {
        k += 1;
        prod *= Math.max(1e-12, this.random());
        if (prod <= limit) return k - 1;
      }
    }
  }

  function hashSeed(baseSeed, key) {
    let h = ((baseSeed >>> 0) ^ 0x811c9dc5) >>> 0;
    for (let i = 0; i < key.length; i += 1) {
      h = (h ^ key.charCodeAt(i)) >>> 0;
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h >>> 0;
  }

  function parseIsoDateUTC(isoStr) {
    const s = String(isoStr).trim().slice(0, 10);
    const parts = s.split("-");
    return new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
  }

  function formatIsoDateUTC(d) {
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, "0");
    const day = String(d.getUTCDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function addDaysUTC(d, days) {
    return new Date(d.getTime() + days * 86400000);
  }

  // ISO day of week: 1=Mon .. 7=Sun (matches Python date.isoweekday())
  function isoWeekdayUTC(d) {
    const w = d.getUTCDay();
    return w === 0 ? 7 : w;
  }

  // Python weekday: 0=Mon .. 6=Sun
  function pyWeekdayUTC(d) {
    return isoWeekdayUTC(d) - 1;
  }

  function thanksgivingDateUTC(year) {
    const nov1 = new Date(Date.UTC(year, 10, 1));
    const offset = (((3 - pyWeekdayUTC(nov1)) % 7) + 7) % 7 + 21;
    return addDaysUTC(nov1, offset);
  }

  function memorialDayDateUTC(year) {
    const may31 = new Date(Date.UTC(year, 4, 31));
    return addDaysUTC(may31, -pyWeekdayUTC(may31));
  }

  function laborDayDateUTC(year) {
    const sep1 = new Date(Date.UTC(year, 8, 1));
    const offset = (((0 - pyWeekdayUTC(sep1)) % 7) + 7) % 7;
    return addDaysUTC(sep1, offset);
  }

  function holidayName(d) {
    const y = d.getUTCFullYear();
    const m = d.getUTCMonth() + 1;
    const day = d.getUTCDate();
    const t = d.getTime();

    if ((m === 12 && day >= 20) || (m === 1 && day <= 3)) {
      return "Christmas / New Year";
    }
    const tg = thanksgivingDateUTC(y);
    if (t >= addDaysUTC(tg, -1).getTime() && t <= addDaysUTC(tg, 4).getTime()) {
      return "Thanksgiving";
    }
    const mem = memorialDayDateUTC(y);
    if (t >= addDaysUTC(mem, -3).getTime() && t <= addDaysUTC(mem, 1).getTime()) {
      return "Memorial Day";
    }
    if (m === 7 && day >= 1 && day <= 7) {
      return "Independence Day";
    }
    const lab = laborDayDateUTC(y);
    if (t >= addDaysUTC(lab, -3).getTime() && t <= addDaysUTC(lab, 1).getTime()) {
      return "Labor Day";
    }
    if ((m === 3 && day >= 10) || (m === 4 && day <= 7)) {
      return "Spring Break";
    }
    return null;
  }

  function classifyHorizonKey(hoursOut) {
    if (hoursOut == null || Number.isNaN(hoursOut) || hoursOut > 72.0) return "gt_72h";
    if (hoursOut > 24.0) return "h24_72";
    if (hoursOut > 6.0) return "h6_24";
    return "lt_6h";
  }

  function riskTier(prob) {
    if (prob >= 0.80) return "Strong Odds";
    if (prob >= 0.60) return "Favorable";
    if (prob >= 0.40) return "Moderate / Watch Closely";
    if (prob >= 0.20) return "Tight";
    return "High Risk";
  }

  function holidayAdjustment(d, hname, priors) {
    if (!hname) return { openMult: 1.0, cancelMult: 1.0, note: null };
    const base = (priors.holiday_deltas && priors.holiday_deltas[hname]) || {
      open_mult: 0.9,
      cancel_mult: 1.05,
    };
    let openMult = Number(base.open_mult);
    const cancelMult = Number(base.cancel_mult);
    let note = `${hname} travel window`;

    if (hname === "Thanksgiving") {
      const tg = thanksgivingDateUTC(d.getUTCFullYear());
      if (d.getTime() === addDaysUTC(tg, 3).getTime()) {
        openMult = Math.min(openMult, 0.62);
        note = "Thanksgiving Sunday (historically tightest travel day of year)";
      } else if (d.getTime() === tg.getTime()) {
        openMult = Math.max(openMult, 1.02);
        note = "Thanksgiving Day (lighter mid-day loads than Wed/Sun peaks)";
      }
    } else if (hname === "Christmas / New Year") {
      const m = d.getUTCMonth() + 1;
      const day = d.getUTCDate();
      if (m === 12 && (day === 24 || day === 25)) {
        openMult = Math.max(openMult, 1.04);
        note = "Christmas Eve/Day (historically lighter than Dec 20–23 & 26–30 peaks)";
      } else {
        openMult = Math.min(openMult, 0.78);
        note = "Christmas / New Year peak travel window";
      }
    }
    return { openMult, cancelMult, note };
  }

  function resolveHourBlockSlots(dest, d, priors, carrierGroup = "ALL") {
    const destU = String(dest).trim().toUpperCase();
    let cg = String(carrierGroup || "ALL").trim().toUpperCase();
    if (cg !== "ALL" && cg !== "MAINLINE" && cg !== "EXPRESS") cg = "ALL";

    const routeInfo = priors.routes[destU] || null;
    const isIntl = Boolean(routeInfo && routeInfo.intl === 1);
    const m = d.getUTCMonth() + 1;
    const dow = isoWeekdayUTC(d);
    const hname = holidayName(d);
    const { openMult: holOpenMult, cancelMult: holCancMult, note: holNote } = holidayAdjustment(
      d,
      hname,
      priors
    );
    const shrinkM = Number((priors.meta && priors.meta.shrinkage_pseudo_count) || 10.0);

    let t3 = priors.tier3[`${destU}|${m}`];
    if (!t3) {
      if (routeInfo) {
        const spd = Number(routeInfo.spd);
        const lf = Number(routeInfo.lf);
        t3 = [
          Math.max(1, Math.round(routeInfo.deps)),
          Math.max(0.0, spd * (1.0 - lf)),
          Math.max(4.0, spd * 0.1),
          spd,
          lf,
          isIntl ? 0.015 : 0.02,
          Math.max(0.5, Number(routeInfo.avg_daily)),
        ];
      } else {
        t3 = [10, 16.0, 10.0, 120.0, 0.82, 0.02, 2.0];
      }
    }

    const dailyFlightsTotal = Math.max(0.25, Number(t3[6]));

    let cgShare = 1.0;
    let cgOpenRatio = 1.0;
    if (!isIntl && cg !== "ALL") {
      const rcf =
        priors.route_carrier_factors &&
        priors.route_carrier_factors[destU] &&
        priors.route_carrier_factors[destU][cg];
      if (rcf) {
        cgShare = Math.max(0.05, Number(rcf[0]));
        cgOpenRatio = Number(rcf[1]);
      } else {
        cgShare = 0.0;
      }
    }

    if (isIntl) {
      const dowMult = Number((priors.dow_multipliers && priors.dow_multipliers[String(dow)]) || 1.0);
      const intlDowMult = 1.0 + 0.35 * (dowMult - 1.0);
      const meanOpen = Math.max(0.0, Number(t3[1]) * intlDowMult * holOpenMult);
      const stdOpen = Math.max(5.0, Number(t3[2]) * 1.15);
      const canc = Math.min(0.25, Math.max(0.003, Number(t3[5]) * holCancMult));
      return {
        slots: [
          {
            hb: "all_day",
            label: "All Scheduled Departures (T-100 Route-Month Prior v1)",
            expected_flights: Math.round(dailyFlightsTotal * 100) / 100,
            mean_open: meanOpen,
            std_open: stdOpen,
            mean_seats: Number(t3[3]),
            cancel_rate: canc,
            n_eff: Math.max(1.0, Number(t3[0])),
            prior_tier: "TIER3_INTL_T100_V1",
          },
        ],
        isIntl: true,
        effDaily: dailyFlightsTotal,
        holNote,
      };
    }

    const slots = [];
    const rawShares = [];
    const dowMult = Number((priors.dow_multipliers && priors.dow_multipliers[String(dow)]) || 1.0);

    let hasAnyT2 = false;
    for (let i = 0; i < 6; i += 1) {
      if (priors.tier2[`${destU}|${m}|${i}`]) {
        hasAnyT2 = true;
        break;
      }
    }

    let t4Sum = 0.0;
    let t4Cnt = 0;
    for (let i = 0; i < 6; i += 1) {
      const x = priors.tier4[`ALL|${m}|${i}`];
      if (x) {
        t4Sum += Number(x[1]);
        t4Cnt += 1;
      }
    }
    const t4MeanOpenM = t4Sum / Math.max(1, t4Cnt);

    for (let hbIdx = 0; hbIdx < HOUR_BLOCK_NAMES.length; hbIdx += 1) {
      const hbName = HOUR_BLOCK_NAMES[hbIdx];
      const t4 =
        priors.tier4[`${cg}|${m}|${hbIdx}`] ||
        priors.tier4[`ALL|${m}|${hbIdx}`] ||
        [100, 16.0, 12.0, 120.0, 0.82, 0.02, 1.0 / 6.0];
      const t2 = priors.tier2[`${destU}|${m}|${hbIdx}`] || null;
      const t1 = priors.tier1[`${destU}|${m}|${dow}|${hbIdx}`] || null;

      const hbRel = Number(t4[1]) / Math.max(1.0, t4MeanOpenM);
      const parentOpen = Number(t3[1]) * hbRel;
      const parentStd = Math.max(Number(t3[2]), Number(t4[2]) * 0.75);
      const parentSeats = Number(t3[3]);
      const parentCanc = Number(t3[5]);

      let meanOpen;
      let stdOpen;
      let meanSeats;
      let cancRate;
      let share;
      let nEff;
      let tierUsed = "TIER3_ROUTE_MONTH";

      if (t2) {
        const n2 = Number(t2[0]);
        const w2 = n2 / (n2 + shrinkM);
        meanOpen = w2 * Number(t2[1]) + (1.0 - w2) * parentOpen;
        stdOpen = w2 * Number(t2[2]) + (1.0 - w2) * parentStd;
        meanSeats = w2 * Number(t2[3]) + (1.0 - w2) * parentSeats;
        cancRate = w2 * Number(t2[5]) + (1.0 - w2) * parentCanc;
        share = Number(t2[6]);
        nEff = n2;
        tierUsed = "TIER2_ROUTE_MONTH_HB";
      } else {
        meanOpen = parentOpen;
        stdOpen = parentStd;
        meanSeats = parentSeats;
        cancRate = parentCanc;
        share = hasAnyT2 ? Number(t4[6]) * 0.25 : Number(t4[6]);
        nEff = Math.max(1.0, Number(t3[0]) / 6.0);
      }

      if (t1) {
        const n1 = Number(t1[0]);
        const w1 = n1 / (n1 + shrinkM);
        meanOpen = w1 * Number(t1[1]) + (1.0 - w1) * (meanOpen * dowMult);
        stdOpen = w1 * Number(t1[2]) + (1.0 - w1) * stdOpen;
        meanSeats = w1 * Number(t1[3]) + (1.0 - w1) * meanSeats;
        cancRate = w1 * Number(t1[5]) + (1.0 - w1) * cancRate;
        if (n1 >= 8) {
          share = 0.65 * Number(t1[6]) + 0.35 * share;
        }
        nEff = n1;
        tierUsed = "TIER1_ROUTE_MONTH_DOW_HB";
      } else {
        meanOpen *= dowMult;
      }

      meanOpen = Math.max(0.0, meanOpen * holOpenMult * cgOpenRatio);
      cancRate = Math.min(0.35, Math.max(0.002, cancRate * holCancMult));

      rawShares.push(Math.max(0.0, share));
      slots.push({
        hb: hbName,
        label: HOUR_BLOCK_LABELS[hbName],
        mean_open: meanOpen,
        std_open: Math.max(2.0, stdOpen),
        mean_seats: Math.max(50.0, meanSeats),
        cancel_rate: cancRate,
        n_eff: nEff,
        prior_tier: tierUsed,
      });
    }

    const totalShare = rawShares.reduce((a, b) => a + b, 0.0);
    const effDaily = dailyFlightsTotal * cgShare;
    for (let i = 0; i < slots.length; i += 1) {
      const normSh = totalShare > 0 ? rawShares[i] / totalShare : 1.0 / 6.0;
      slots[i].expected_flights = effDaily * normSh;
    }

    return { slots, isIntl: false, effDaily, holNote };
  }

  function runDayMonteCarlo(opts) {
    const {
      slots,
      partySize,
      queueMean,
      queueIsExactOverride,
      openOverride,
      targetHb,
      volScale,
      isIntl,
      hubIrropsCorr,
      spilloverSeats,
      spilloverDecay,
      draws,
      seed,
    } = opts;

    const rng = new Mulberry32(seed);
    const nBatches = 40;
    const drawsPerBatch = Math.max(10, Math.floor(draws / nBatches));
    const totalDraws = nBatches * drawsPerBatch;

    let activeSlots = slots.filter((s) => s.expected_flights >= 0.08);
    if (activeSlots.length === 0 && slots.length > 0) {
      let best = slots[0];
      for (const s of slots) {
        if (s.expected_flights > best.expected_flights) best = s;
      }
      activeSlots = [best];
    }

    const hbClearHits = {};
    const hbSeatHits = {};
    const hbFlightTrials = {};
    for (const s of slots) {
      hbClearHits[s.hb] = 0;
      hbSeatHits[s.hb] = 0;
      hbFlightTrials[s.hb] = 0;
    }

    const batchClearRates = [];
    let dayClearTotal = 0;
    let daySeatTotal = 0;

    const dayCorr = 0.58;
    const indWeight = Math.sqrt(
      Math.max(0.04, 1.0 - hubIrropsCorr * hubIrropsCorr - dayCorr * dayCorr)
    );

    for (let b = 0; b < nBatches; b += 1) {
      const epistemicZ = rng.normal();
      let batchCleared = 0;

      for (let dIdx = 0; dIdx < drawsPerBatch; dIdx += 1) {
        const zHub = rng.normal();
        const zDay = rng.normal();
        let spillPool = 0.0;
        let clearedToday = false;
        let seatOnlyToday = false;

        for (let si = 0; si < slots.length; si += 1) {
          const slot = slots[si];
          const expF = Number(slot.expected_flights);
          if (expF < 0.08 && !activeSlots.includes(slot)) {
            continue;
          }

          const baseF = Math.floor(expF);
          const fracF = expF - baseF;
          let nFlights = baseF + (rng.random() < fracF ? 1 : 0);
          if (nFlights === 0 && slot === activeSlots[0] && activeSlots.length === 1) {
            nFlights = 1;
          }
          if (nFlights === 0) {
            spillPool *= spilloverDecay;
            continue;
          }

          const isTargeted =
            openOverride != null && (targetHb == null || slot.hb === targetHb || isIntl);

          const mu = isTargeted ? Number(openOverride) : Number(slot.mean_open);
          const sigma = Math.max(1.5, Number(slot.std_open) * (isTargeted ? volScale : 1.0));
          const seatsCap = Number(slot.mean_seats);

          let baseEpSe = sigma / Math.sqrt(Math.max(3.0, Math.min(60.0, Number(slot.n_eff))));
          if (isIntl) {
            baseEpSe = Math.max(baseEpSe, seatsCap * 0.045);
          }
          if (isTargeted) {
            baseEpSe *= volScale * 0.65;
          }
          const muEff = Math.max(0.0, mu + epistemicZ * baseEpSe);

          const m1 = Math.max(0.5, muEff + 1.0);
          const s2Log = Math.log(1.0 + (sigma * sigma) / (m1 * m1));
          const sLog = Math.sqrt(s2Log);
          const mLog = Math.log(m1) - 0.5 * s2Log;

          let pCanc = Number(slot.cancel_rate);
          if (zHub > 1.2) {
            pCanc = Math.min(0.55, pCanc * (1.0 + 0.9 * (zHub - 1.2)));
          } else if (zHub < -0.5) {
            pCanc = Math.max(0.002, pCanc * 0.75);
          }

          let bankCancels = 0;
          let bankCleared = false;
          let bankSeatOk = false;
          hbFlightTrials[slot.hb] += 1;

          for (let f = 0; f < nFlights; f += 1) {
            if (rng.random() < pCanc) {
              bankCancels += 1;
              continue;
            }

            const zInd = rng.normal();
            const zTot = -hubIrropsCorr * zHub + dayCorr * zDay + indWeight * zInd;
            const rawOpen = isTargeted
              ? muEff + sigma * zTot
              : Math.exp(mLog + sLog * zTot) - 1.0;
            const openDraw = Math.min(seatsCap, Math.max(0.0, rawOpen - spillPool));

            let qDraw;
            if (queueIsExactOverride) {
              const qChurn = volScale > 0.2 ? rng.normal() * (0.8 * volScale) : 0.0;
              qDraw = Math.max(0.0, queueMean + qChurn);
            } else {
              const qDayMult = Math.max(0.55, 1.0 - 0.18 * zDay + 0.12 * Math.max(0.0, zHub));
              qDraw = rng.poisson(Math.max(0.0, queueMean * qDayMult));
            }

            if (openDraw >= partySize) bankSeatOk = true;
            if (openDraw - qDraw >= partySize) bankCleared = true;
          }

          if (bankCleared) {
            hbClearHits[slot.hb] += 1;
            clearedToday = true;
          }
          if (bankSeatOk) {
            hbSeatHits[slot.hb] += 1;
            seatOnlyToday = true;
          }

          spillPool = spillPool * spilloverDecay + bankCancels * spilloverSeats;
        }

        if (clearedToday) {
          dayClearTotal += 1;
          batchCleared += 1;
        }
        if (seatOnlyToday) {
          daySeatTotal += 1;
        }
      }

      batchClearRates.push(batchCleared / drawsPerBatch);
    }

    batchClearRates.sort((a, b) => a - b);
    const pLow = batchClearRates[Math.floor(0.1 * (nBatches - 1))];
    const pHigh = batchClearRates[Math.floor(0.9 * (nBatches - 1))];
    const wholeDayProb = dayClearTotal / totalDraws;
    const seatOnlyProb = daySeatTotal / totalDraws;

    const hbProbs = {};
    const hbSeatProbs = {};
    for (const s of slots) {
      hbProbs[s.hb] = hbFlightTrials[s.hb] > 0 ? hbClearHits[s.hb] / hbFlightTrials[s.hb] : 0.0;
      hbSeatProbs[s.hb] = hbFlightTrials[s.hb] > 0 ? hbSeatHits[s.hb] / hbFlightTrials[s.hb] : 0.0;
    }

    return {
      whole_day_prob: Math.round(wholeDayProb * 10000) / 10000,
      ci_low_80: Math.round(Math.min(wholeDayProb, pLow) * 10000) / 10000,
      ci_high_80: Math.round(Math.max(wholeDayProb, pHigh) * 10000) / 10000,
      seat_only_whole_day_prob: Math.round(seatOnlyProb * 10000) / 10000,
      hb_probs: hbProbs,
      hb_seat_probs: hbSeatProbs,
    };
  }

  function predictStandbyDay(request, priors, includeNeighbors = true) {
    const meta = priors.meta || {};
    const destU = String(request.dest || "").trim().toUpperCase();
    const routeInfo = priors.routes[destU];
    if (!routeInfo) {
      throw new Error(`Unknown destination '${destU}'.`);
    }

    const d = parseIsoDateUTC(request.flight_date);
    const isoStr = formatIsoDateUTC(d);
    const partySize = Math.max(1, Math.min(6, Math.round(Number(request.party_size || 1))));
    const passQueueMap = meta.pass_tier_queue_means || {
      SA1P: 0.8,
      SA2: 2.5,
      SA3: 5.0,
      BUDDY: 8.5,
    };
    let priority = String(request.priority || "SA2").trim().toUpperCase();
    if (!(priority in passQueueMap)) priority = "SA2";

    let cg = String(request.carrier_group || "ALL").trim().toUpperCase();
    if (cg !== "ALL" && cg !== "MAINLINE" && cg !== "EXPRESS") cg = "ALL";

    const rawTargetHb = request.target_hour_block
      ? String(request.target_hour_block).trim().toLowerCase()
      : null;
    const targetHb = rawTargetHb && rawTargetHb in HOUR_BLOCK_TO_IDX ? rawTargetHb : null;

    const isHubToHub = routeInfo.hub_to_hub === 1;
    const isSharedSpoke = routeInfo.sha === 1;
    const hname = holidayName(d);

    const openOverride =
      request.open_seats_override != null && request.open_seats_override !== ""
        ? Number(request.open_seats_override)
        : null;
    const qOverride =
      request.standbys_ahead_override != null && request.standbys_ahead_override !== ""
        ? Number(request.standbys_ahead_override)
        : null;
    const hoursOut =
      request.hours_until_departure != null && request.hours_until_departure !== ""
        ? Number(request.hours_until_departure)
        : null;

    const h2hMult = Number(meta.hub_to_hub_queue_multiplier || 1.45);
    let queueMean;
    let queueIsExact;
    let queueSource;
    if (qOverride != null && !Number.isNaN(qOverride)) {
      queueMean = Math.max(0.0, qOverride);
      queueIsExact = true;
      queueSource = "EmployeeRES live input";
    } else {
      const baseQ = Number(passQueueMap[priority] ?? 2.5);
      queueMean = baseQ * (isHubToHub ? h2hMult : 1.0);
      if (hname === "Thanksgiving" || hname === "Christmas / New Year") {
        queueMean *= 1.35;
      }
      queueIsExact = false;
      queueSource = `Assumed ${priority} queue prior${isHubToHub ? " (hub-to-hub uplift)" : ""}`;
    }

    const hKey = classifyHorizonKey(hoursOut);
    const volMap = meta.horizon_volatility_scale || {
      gt_72h: 1.0,
      h24_72: 0.72,
      h6_24: 0.45,
      lt_6h: 0.25,
    };
    const volScale = Number(volMap[hKey] ?? 1.0);
    const hasLive = openOverride != null || qOverride != null;
    const mode = hasLive ? "live_employeeres" : "advance_prior";

    const { slots, isIntl, effDaily, holNote } = resolveHourBlockSlots(destU, d, priors, cg);
    const draws = Number(request.draws || meta.simulation_draws || 2000);
    const baseSeed = Number(request.seed != null ? request.seed : meta.simulation_seed || 202604);
    const hubIrropsCorr = Number(meta.hub_irrops_corr ?? 0.35);
    const spilloverSeats = Number(meta.spillover_seats_per_cancel ?? 18.0);
    const spilloverDecay = Number(meta.spillover_decay ?? 0.50);

    const openKey = openOverride == null ? "NONE" : Number(openOverride).toFixed(1);
    const qKey = qOverride == null ? "NONE" : String(Math.trunc(qOverride));
    const hbKey = targetHb != null ? targetHb : "NONE";
    const seed = hashSeed(
      baseSeed,
      `${destU}|${isoStr}|${partySize}|${priority}|${cg}|${openKey}|${qKey}|${hbKey}`
    );

    const sim = runDayMonteCarlo({
      slots,
      partySize,
      queueMean,
      queueIsExactOverride: queueIsExact,
      openOverride,
      targetHb,
      volScale,
      isIntl,
      hubIrropsCorr,
      spilloverSeats,
      spilloverDecay,
      draws,
      seed,
    });

    const hbPreds = [];
    let weightedOpenSum = 0.0;
    let weightedCancSum = 0.0;
    let weightTotal = 0.0;
    for (const s of slots) {
      const ef = Number(s.expected_flights);
      const hbOpen =
        openOverride != null && (targetHb == null || s.hb === targetHb || isIntl)
          ? openOverride
          : Number(s.mean_open);
      hbPreds.push({
        hour_block: s.hb,
        label: s.label,
        expected_flights: Math.round(ef * 100) / 100,
        expected_open_seats: Math.round(hbOpen * 10) / 10,
        open_seats_std: Math.round(Number(s.std_open) * 10) / 10,
        cancel_rate: Math.round(Number(s.cancel_rate) * 10000) / 10000,
        clearance_prob: Math.round(Number(sim.hb_probs[s.hb]) * 10000) / 10000,
        seat_avail_prob: Math.round(Number(sim.hb_seat_probs[s.hb]) * 10000) / 10000,
        prior_tier: s.prior_tier,
      });
      if (ef > 0) {
        weightedOpenSum += hbOpen * ef;
        weightedCancSum += Number(s.cancel_rate) * ef;
        weightTotal += ef;
      }
    }

    const avgOpen = weightTotal > 0 ? weightedOpenSum / weightTotal : 0.0;
    const avgCanc = weightTotal > 0 ? weightedCancSum / weightTotal : 0.0;

    const viableHbs = hbPreds.filter((h) => h.expected_flights >= 0.2);
    const bestPool = viableHbs.length > 0 ? viableHbs : hbPreds;
    let bestHb = bestPool.length > 0 ? bestPool[0] : null;
    for (const h of bestPool) {
      if (h.clearance_prob > bestHb.clearance_prob) bestHb = h;
    }

    const neighbors = [];
    if (includeNeighbors) {
      for (let delta = -3; delta <= 3; delta += 1) {
        const nd = addDaysUTC(d, delta);
        const ndIso = formatIsoDateUTC(nd);
        const ndDow = DAY_NAMES[isoWeekdayUTC(nd) - 1];
        if (delta === 0) {
          neighbors.push({
            flight_date: ndIso,
            day_of_week: ndDow,
            holiday_name: hname,
            whole_day_prob: sim.whole_day_prob,
            expected_open_seats: Math.round(avgOpen * 10) / 10,
            delta_vs_selected: 0.0,
          });
        } else {
          const nRes = resolveHourBlockSlots(destU, nd, priors, cg);
          const nHname = holidayName(nd);
          let nQ = queueMean;
          if (!queueIsExact) {
            const baseQ = Number(passQueueMap[priority] ?? 2.5);
            nQ = baseQ * (isHubToHub ? h2hMult : 1.0);
            if (nHname === "Thanksgiving" || nHname === "Christmas / New Year") {
              nQ *= 1.35;
            }
          }
          const nSim = runDayMonteCarlo({
            slots: nRes.slots,
            partySize,
            queueMean: nQ,
            queueIsExactOverride: queueIsExact,
            openOverride: null,
            targetHb: null,
            volScale: 1.0,
            isIntl: nRes.isIntl,
            hubIrropsCorr,
            spilloverSeats,
            spilloverDecay,
            draws: 600,
            seed: hashSeed(baseSeed, `nbr|${destU}|${ndIso}|${partySize}|${priority}|${cg}`),
          });
          const nWt = nRes.slots.reduce((a, s) => a + Number(s.expected_flights), 0.0);
          const nOpen =
            nWt > 0
              ? nRes.slots.reduce(
                  (a, s) => a + Number(s.mean_open) * Number(s.expected_flights),
                  0.0
                ) / nWt
              : 0.0;
          neighbors.push({
            flight_date: ndIso,
            day_of_week: ndDow,
            holiday_name: nHname,
            whole_day_prob: nSim.whole_day_prob,
            expected_open_seats: Math.round(nOpen * 10) / 10,
            delta_vs_selected: Math.round((nSim.whole_day_prob - sim.whole_day_prob) * 10000) / 10000,
          });
        }
      }
    }

    const dowIdx = isoWeekdayUTC(d);
    const drivers = [];
    if (isIntl) {
      drivers.push(
        `International route (v1): derived from BTS T-100 monthly load factor (${(
          routeInfo.lf * 100
        ).toFixed(1)}%) and ~${effDaily.toFixed(1)} daily departures; no flight-level OTP hour blocks.`
      );
    } else {
      drivers.push(
        `Backup depth: ~${effDaily.toFixed(
          1
        )} scheduled daily departures across ORD → ${destU} give multiple fallback chances if an earlier bank fills or disrupts.`
      );
      if (bestHb) {
        drivers.push(
          `Best window: ${bestHb.label} (${Math.round(
            bestHb.clearance_prob * 100
          )}% single-bank odds, ~${bestHb.expected_open_seats.toFixed(1)} expected open seats).`
        );
      }
    }
    if (holNote) {
      drivers.push(`Calendar regime: ${holNote}.`);
    } else if (dowIdx === 2 || dowIdx === 3 || dowIdx === 6) {
      drivers.push(
        `${DAY_NAMES[dowIdx - 1]} historically offers lighter TSA demand and more open seats than Thu/Fri/Sun peaks.`
      );
    } else if (dowIdx === 4 || dowIdx === 5 || dowIdx === 7) {
      drivers.push(
        `${DAY_NAMES[dowIdx - 1]} historically sees elevated leisure/business demand out of ORD.`
      );
    }
    if (hasLive) {
      drivers.push(
        `Sharpened with EmployeeRES input (horizon scale ${Math.round(
          volScale * 100
        )}% remaining volatility).`
      );
    } else {
      drivers.push(
        `Queue overlay: modeled ~${queueMean.toFixed(
          1
        )} competing standbys ahead (${queueSource}); pure open-seat availability odds = ${Math.round(
          sim.seat_only_whole_day_prob * 100
        )}%.`
      );
    }
    if (isSharedSpoke) {
      drivers.push(
        "Shared regional CPA spoke (OO/YX/2023 ZW): T-100 baseline blends multi-brand regional traffic."
      );
    }

    return {
      hub: String(meta.hub || "ORD"),
      dest: destU,
      dest_city: String(routeInfo.cty),
      dest_state_or_country: String(routeInfo.st),
      flight_date: isoStr,
      day_of_week: DAY_NAMES[dowIdx - 1],
      holiday_name: hname,
      party_size: partySize,
      priority,
      carrier_group: cg,
      mode,
      is_international_v1: isIntl,
      is_shared_regional_spoke: isSharedSpoke,
      is_hub_to_hub: isHubToHub,
      whole_day_prob: sim.whole_day_prob,
      ci_low_80: sim.ci_low_80,
      ci_high_80: sim.ci_high_80,
      seat_only_whole_day_prob: sim.seat_only_whole_day_prob,
      risk_tier: riskTier(sim.whole_day_prob),
      expected_daily_flights: Math.round(effDaily * 100) / 100,
      expected_open_seats_avg: Math.round(avgOpen * 10) / 10,
      expected_cancel_rate: Math.round(avgCanc * 10000) / 10000,
      estimated_queue_ahead: Math.round(queueMean * 10) / 10,
      queue_source: queueSource,
      best_hour_block: bestHb ? bestHb.label : null,
      best_hour_block_prob: bestHb ? bestHb.clearance_prob : null,
      hour_blocks: hbPreds,
      neighbor_days: neighbors,
      drivers,
      disclaimer:
        "Modeled standby seat likelihood combining empirically calibrated historical open-seat priors (BTS OTP, T-100, FAA, TSA) with an assumed non-rev pass-tier queue overlay (or your EmployeeRES override). Not an official United Airlines clearance guarantee.",
    };
  }

  return {
    Mulberry32,
    hashSeed,
    holidayName,
    resolveHourBlockSlots,
    predictStandbyDay,
    HOUR_BLOCK_NAMES,
    HOUR_BLOCK_LABELS,
  };
});
