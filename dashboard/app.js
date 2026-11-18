/* United Standby Opportunity Explorer — Interactive BI Engine (Zero-Dependency SVG + JS) */
(function () {
  "use strict";

  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const HOUR_BLOCKS = [
    { idx: 0, short: "Early", range: "05:00–07:59" },
    { idx: 1, short: "Morning", range: "08:00–10:59" },
    { idx: 2, short: "Midday", range: "11:00–13:59" },
    { idx: 3, short: "Afternoon", range: "14:00–16:59" },
    { idx: 4, short: "Evening", range: "17:00–19:59" },
    { idx: 5, short: "Late", range: "20:00–04:59" },
  ];

  // Simplified continental & North American coastlines (lon, lat) for crisp zero-CDN SVG maps
  const LAND_POLYGONS = [
    // North America (mainland + Canada + Mexico + Central America)
    [
      [-166, 65], [-162, 70], [-141, 70], [-128, 70], [-110, 68], [-94, 68], [-82, 63],
      [-78, 53], [-64, 60], [-55, 52], [-60, 46], [-66, 45], [-70, 42], [-74, 40],
      [-76, 35], [-81, 31], [-80, 25], [-82, 25], [-84, 30], [-90, 29], [-97, 26],
      [-97, 21], [-90, 21], [-87, 21], [-88, 16], [-83, 15], [-83, 10], [-78, 8],
      [-80, 7], [-85, 10], [-92, 14], [-105, 20], [-110, 24], [-114, 31], [-117, 32],
      [-121, 35], [-124, 41], [-124, 48], [-131, 54], [-138, 59], [-152, 60], [-162, 55],
      [-166, 60],
    ],
    // Greenland
    [[-52, 60], [-42, 60], [-22, 70], [-20, 81], [-50, 82], [-70, 76], [-54, 68]],
    // Cuba / Caribbean hint
    [[-85, 22], [-75, 20], [-74, 21], [-83, 23]],
    // South America
    [
      [-78, 9], [-71, 12], [-61, 10], [-50, 1], [-35, -6], [-38, -14], [-41, -23],
      [-48, -26], [-54, -34], [-65, -42], [-68, -55], [-74, -52], [-72, -37],
      [-71, -18], [-81, -5], [-79, 2],
    ],
    // UK & Ireland
    [[-6, 50], [2, 51], [0, 56], [-5, 58], [-10, 54], [-9, 51]],
    // Europe
    [
      [-9, 37], [-9, 43], [-2, 43], [-4, 48], [2, 51], [8, 54], [10, 57], [5, 61],
      [15, 69], [30, 70], [40, 66], [42, 45], [29, 41], [23, 37], [16, 40],
      [12, 42], [9, 44], [3, 43], [-1, 37], [-6, 36],
    ],
    // Africa
    [
      [-17, 15], [-12, 28], [-6, 36], [10, 37], [25, 32], [33, 31], [43, 12], [51, 11],
      [41, -3], [35, -24], [20, -35], [15, -26], [9, -2], [-8, 5], [-16, 11],
    ],
    // Asia
    [
      [35, 36], [42, 42], [55, 45], [65, 66], [105, 73], [140, 72], [170, 66], [162, 55],
      [142, 53], [135, 43], [128, 35], [122, 31], [114, 22], [108, 12], [100, 13],
      [96, 16], [88, 22], [77, 8], [68, 23], [56, 26], [49, 29], [43, 13], [35, 29],
    ],
    // Japan
    [[130, 31], [136, 34], [141, 36], [145, 43], [140, 41], [133, 35]],
    // Australia
    [[114, -22], [129, -14], [142, -11], [153, -27], [148, -38], [136, -35], [115, -34]],
  ];

  let DATA = null;
  const state = {
    mode: "plan", // "plan" | "hist"
    year: "POST", // "ALL" | "POST" | "2026" | "2025" | "2024" | "2023" | "2019"
    carrierGroup: "ALL", // "ALL" | "MAINLINE" | "EXPRESS"
    tab: "network",
    mapView: "na", // "na" | "global"
    mapScope: "ALL",
    routeSearch: "",
    routeScope: "ALL",
    routeSort: "deps",
    routeSortDir: -1,
    routePage: 1,
    trendScope: "DOM",
    flightSearch: "",
    flightHb: "ALL",
    flightTier: "ALL",
    flightPage: 1,
  };

  // DOM Helpers
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtInt(v) {
    return v == null || Number.isNaN(v) ? "—" : Math.round(v).toLocaleString("en-US");
  }

  function fmtDec(v, d = 1) {
    return v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d);
  }

  function fmtPct(v, d = 1) {
    return v == null || Number.isNaN(v) ? "—" : (Number(v) * 100).toFixed(d) + "%";
  }

  function activeYears() {
    if (state.year === "ALL") return ["2019", "2023", "2024", "2025", "2026"];
    if (state.year === "POST") return ["2023", "2024", "2025", "2026"];
    return [state.year];
  }

  function activeCarrierGroups() {
    if (state.carrierGroup === "ALL") return ["MAINLINE", "EXPRESS"];
    return [state.carrierGroup];
  }

  function soiColor(score) {
    if (score == null) return "var(--soi-null)";
    if (score >= 70) return "var(--soi-high)";
    if (score >= 55) return "var(--soi-mod)";
    if (score >= 45) return "var(--soi-tight)";
    return "var(--soi-low)";
  }

  function lfColor(lf) {
    if (lf == null) return "var(--soi-null)";
    if (lf < 0.78) return "var(--soi-high)";
    if (lf < 0.83) return "var(--soi-mod)";
    if (lf < 0.88) return "var(--soi-tight)";
    return "var(--soi-low)";
  }

  function soiBadge(score) {
    if (score == null) return { cls: "badge-null", text: "Unscored" };
    if (score >= 75) return { cls: "badge-high", text: "High (75–100)" };
    if (score >= 55) return { cls: "badge-mod", text: "Moderate (55–74)" };
    if (score >= 35) return { cls: "badge-tight", text: "Tight (35–54)" };
    return { cls: "badge-low", text: "Constrained (0–34)" };
  }

  function heatBgColor(score) {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    if (score == null) return isDark ? "#1c2738" : "#f1f5f9";
    // Smooth perceptual steps across [38..80]
    const t = Math.max(0, Math.min(1, (score - 40) / 38));
    if (isDark) {
      if (t < 0.35) return `rgba(220, 38, 38, ${0.42 - t * 0.5})`;
      if (t < 0.62) return `rgba(217, 119, 6, ${0.22 + (0.62 - t) * 0.45})`;
      return `rgba(16, 185, 129, ${0.18 + (t - 0.62) * 0.85})`;
    }
    if (t < 0.35) return `rgba(220, 38, 38, ${0.24 - t * 0.32})`;
    if (t < 0.62) return `rgba(217, 119, 6, ${0.12 + (0.62 - t) * 0.32})`;
    return `rgba(4, 120, 87, ${0.12 + (t - 0.62) * 0.52})`;
  }

  // Tooltip controller
  const tooltipEl = $("#tooltip");
  function showTooltip(evt, html) {
    tooltipEl.innerHTML = html;
    tooltipEl.classList.add("visible");
    moveTooltip(evt);
  }
  function moveTooltip(evt) {
    const pad = 14;
    let x = evt.clientX + pad;
    let y = evt.clientY + pad;
    const rect = tooltipEl.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 10) x = evt.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 10) y = evt.clientY - rect.height - pad;
    tooltipEl.style.left = Math.max(8, x) + "px";
    tooltipEl.style.top = Math.max(8, y) + "px";
  }
  function hideTooltip() {
    tooltipEl.classList.remove("visible");
  }

  // Drawer controller
  const drawerBackdrop = $("#drawer-backdrop");
  function openDrawer(title, subtitle, bodyHtml) {
    $("#drawer-title").textContent = title;
    $("#drawer-subtitle").textContent = subtitle || "";
    $("#drawer-body").innerHTML = bodyHtml;
    drawerBackdrop.classList.add("open");
    drawerBackdrop.setAttribute("aria-hidden", "false");
  }
  function closeDrawer() {
    drawerBackdrop.classList.remove("open");
    drawerBackdrop.setAttribute("aria-hidden", "true");
  }

  // Aggregate helper over kpis_by_slice
  function sumKpiSlices(years, cgs) {
    const out = {
      n: 0, perf: 0, canc: 0, d15_n: 0, d15_s: 0, lf_n: 0, lf_s: 0,
      lfa_n: 0, lfa_s: 0, op_n: 0, op_s: 0, opp_n: 0, opp_s: 0,
      bkp_n: 0, bkp_s: 0, sp_n: 0, sp_s: 0, sh_n: 0, sh_s: 0,
    };
    for (const y of years) {
      for (const cg of cgs) {
        const s = DATA.kpis[`${y}|${cg}`];
        if (!s) continue;
        for (const k of Object.keys(out)) out[k] += s[k] || 0;
      }
    }
    return out;
  }

  // ---------------------------------------------------------------------------
  // 1. GLOBAL CONTEXT & KPI CARDS
  // ---------------------------------------------------------------------------
  function renderContextBanner() {
    const banner = $("#context-banner");
    if (state.year === "2019" && state.mode === "plan") {
      banner.innerHTML =
        "<strong>2019 Baseline in Planning Mode:</strong> By design (strict temporal leakage separation, <code>train.year &lt; eval.year</code>), " +
        "<code>soi_planning</code> is <strong>100% NULL in 2019</strong> because no prior training year exists in scope. Switch to <strong>Historical (Post-Hoc)</strong> to inspect 2019 SOI scores.";
      banner.classList.add("visible");
    } else {
      banner.classList.remove("visible");
    }
  }

  function renderKpis() {
    const yrs = activeYears();
    const cgs = activeCarrierGroups();
    const cur = sumKpiSlices(yrs, cgs);
    const base2019 = sumKpiSlices(["2019"], cgs);

    const isPlan = state.mode === "plan";
    const soiVal = isPlan
      ? (cur.sp_n > 0 ? cur.sp_s / cur.sp_n : null)
      : (cur.sh_n > 0 ? cur.sh_s / cur.sh_n : null);
    const baseSoi2019 = base2019.sh_n > 0 ? base2019.sh_s / base2019.sh_n : null;
    const soiDelta = soiVal != null && baseSoi2019 != null && state.year !== "2019"
      ? soiVal - baseSoi2019
      : null;

    const openSeats = cur.op_n > 0 ? cur.op_s / cur.op_n : null;
    const openPct = cur.opp_n > 0 ? cur.opp_s / cur.opp_n : null;
    const baseOpen2019 = base2019.op_n > 0 ? base2019.op_s / base2019.op_n : null;
    const openDelta = openSeats != null && baseOpen2019 != null && state.year !== "2019"
      ? openSeats - baseOpen2019
      : null;

    const baseLf = cur.lf_n > 0 ? cur.lf_s / cur.lf_n : null;
    const adjLf = cur.lfa_n > 0 ? cur.lfa_s / cur.lfa_n : null;
    const baseLf2019 = base2019.lf_n > 0 ? base2019.lf_s / base2019.lf_n : null;
    const lfDelta = baseLf != null && baseLf2019 != null && state.year !== "2019"
      ? (baseLf - baseLf2019) * 100
      : null;

    const delayRate = cur.d15_n > 0 ? cur.d15_s / cur.d15_n : null;
    const cancelRate = cur.n > 0 ? cur.canc / cur.n : null;
    const backups = cur.bkp_n > 0 ? cur.bkp_s / cur.bkp_n : null;

    const deltaTag = (val, unit, invertGood = false) => {
      if (val == null) return `<span class="kpi-delta neutral">2019 base</span>`;
      const sign = val >= 0 ? "+" : "";
      const good = invertGood ? val <= 0 : val >= 0;
      const cls = Math.abs(val) < 0.05 ? "neutral" : (good ? "pos" : "neg");
      return `<span class="kpi-delta ${cls}">${sign}${val.toFixed(1)}${unit} vs '19</span>`;
    };

    // Scale stratified sample count back to full-dataset population count
    const popFactor = DATA.meta.total_flights / 100000;
    const estPop = Math.round(cur.n * popFactor);

    $("#kpi-grid").innerHTML = `
      <div class="kpi-card">
        <div class="kpi-label">${isPlan ? "Avg Planning SOI" : "Avg Historical SOI"} (0–100)</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono" style="color:${soiColor(soiVal)}">${fmtDec(soiVal, 1)}</div>
          ${deltaTag(soiDelta, " pts")}
        </div>
        <div class="kpi-sub">${isPlan ? "Leakage-free ex-ante heuristic" : "Post-hoc realized flights"} · ~${fmtInt(estPop)} flights</div>
        <div class="kpi-bar"><div class="kpi-bar-fill" style="width:${soiVal != null ? Math.min(100, soiVal) : 0}%;background:${soiColor(soiVal)}"></div></div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Est. Adjusted Open Seats</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">${fmtDec(openSeats, 1)}</div>
          ${deltaTag(openDelta, " seats")}
        </div>
        <div class="kpi-sub">Mean open cabin share: <strong class="mono">${fmtPct(openPct, 1)}</strong> · Avg ${fmtDec(backups, 1)} backups</div>
        <div class="kpi-bar"><div class="kpi-bar-fill" style="width:${openSeats != null ? Math.min(100, (openSeats / 35) * 100) : 0}%"></div></div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Base T-100 Load Factor</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">${fmtPct(baseLf, 1)}</div>
          ${deltaTag(lfDelta, " pp", true)}
        </div>
        <div class="kpi-sub">TSA/hour-adjusted LF: <strong class="mono">${fmtPct(adjLf, 1)}</strong> (performed)</div>
        <div class="kpi-bar"><div class="kpi-bar-fill" style="width:${baseLf != null ? Math.min(100, baseLf * 100) : 0}%;background:${lfColor(baseLf)}"></div></div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Dep Delay Rate (15+ min)</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">${fmtPct(delayRate, 1)}</div>
          <span class="kpi-delta neutral">OTP Actual</span>
        </div>
        <div class="kpi-sub">Performed flights departing ≥15 min late</div>
        <div class="kpi-bar"><div class="kpi-bar-fill" style="width:${delayRate != null ? Math.min(100, delayRate * 250) : 0}%;background:var(--soi-tight)"></div></div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Cancellation Rate</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">${fmtPct(cancelRate, 2)}</div>
          <span class="kpi-delta neutral">OTP Actual</span>
        </div>
        <div class="kpi-sub">Drives same-day IRROPS displacement pressure</div>
        <div class="kpi-bar"><div class="kpi-bar-fill" style="width:${cancelRate != null ? Math.min(100, cancelRate * 1000) : 0}%;background:var(--soi-low)"></div></div>
      </div>
    `;
  }

  // ---------------------------------------------------------------------------
  // 2. TAB 1: ORD HUB-AND-SPOKE MAP & ROUTE LEADERBOARD
  // ---------------------------------------------------------------------------
  function projectMap(lat, lon, view, W, H) {
    if (view === "na") {
      // North America / Caribbean focus: lon [-162, -52], lat [14, 64]
      const minLon = -132, maxLon = -60;
      const minLat = 16, maxLat = 56;
      // Inset AK and HI gently inside North America frame
      let pLat = lat, pLon = lon;
      if (lon < -140 && lat > 52) { // Alaska inset
        pLon = -126 + (lon + 160) * 0.45;
        pLat = 51 + (lat - 58) * 0.55;
      } else if (lon < -150 && lat < 26) { // Hawaii inset
        pLon = -124 + (lon + 160) * 0.8;
        pLat = 21 + (lat - 19) * 0.8;
      }
      const x = ((pLon - minLon) / (maxLon - minLon)) * W;
      const y = ((maxLat - pLat) / (maxLat - minLat)) * H;
      return [x, y];
    }
    // Global equirectangular cropped cleanly [-165..155], [-36..70]
    const minLon = -165, maxLon = 155;
    const minLat = -36, maxLat = 70;
    const x = ((lon - minLon) / (maxLon - minLon)) * W;
    const y = ((maxLat - lat) / (maxLat - minLat)) * H;
    return [x, y];
  }

  function renderRouteMap() {
    const container = $("#route-map-container");
    const W = 980, H = 430;
    const view = state.mapView;
    const ord = projectMap(41.9742, -87.9073, view, W, H);

    // Filter routes
    const routes = DATA.routes.filter((r) => {
      if (state.mapScope === "DOM" && r.intl) return false;
      if (state.mapScope === "INTL" && !r.intl) return false;
      if (view === "na") {
        // Keep North America, Mexico, Caribbean, AK/HI
        return r.lon >= -165 && r.lon <= -52 && r.lat >= 12 && r.lat <= 68;
      }
      return true;
    });

    const maxDeps = Math.max(...routes.map((r) => r.deps), 1);

    // Land polygons
    const landPaths = LAND_POLYGONS.map((poly) => {
      const pts = poly
        .map(([lon, lat]) => {
          const [x, y] = projectMap(lat, lon, view, W, H);
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ");
      return `<polygon points="${pts}" fill="var(--map-land)" stroke="var(--map-land-stroke)" stroke-width="1"/>`;
    }).join("");

    // Subtle graticule lines
    let gridLines = "";
    const latStep = view === "na" ? 10 : 20;
    const lonStep = view === "na" ? 15 : 30;
    for (let lat = -20; lat <= 60; lat += latStep) {
      const [, y] = projectMap(lat, 0, view, W, H);
      if (y > 0 && y < H) gridLines += `<line x1="0" y1="${y.toFixed(1)}" x2="${W}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-opacity="0.45" stroke-width="0.6"/>`;
    }
    for (let lon = -150; lon <= 150; lon += lonStep) {
      const [x] = projectMap(30, lon, view, W, H);
      if (x > 0 && x < W) gridLines += `<line x1="${x.toFixed(1)}" y1="0" x2="${x.toFixed(1)}" y2="${H}" stroke="var(--border)" stroke-opacity="0.45" stroke-width="0.6"/>`;
    }

    // Draw top arcs first so bubbles sit cleanly on top
    const arcRoutes = routes.slice(0, view === "na" ? 90 : 120);
    const arcsSvg = arcRoutes.map((r) => {
      const [x2, y2] = projectMap(r.lat, r.lon, view, W, H);
      if (x2 < -10 || x2 > W + 10 || y2 < -10 || y2 > H + 10) return "";
      const mx = (ord[0] + x2) / 2;
      const my = (ord[1] + y2) / 2 - Math.min(55, Math.hypot(x2 - ord[0], y2 - ord[1]) * 0.16);
      const col = r.intl ? lfColor(r.lf) : soiColor(state.mode === "plan" ? r.sp : r.sh);
      const dash = r.intl ? `stroke-dasharray="3 3"` : "";
      const op = r.deps > 8000 ? 0.36 : 0.18;
      return `<path d="M ${ord[0].toFixed(1)} ${ord[1].toFixed(1)} Q ${mx.toFixed(1)} ${my.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}" fill="none" stroke="${col}" stroke-width="${r.deps > 12000 ? 1.4 : 0.85}" stroke-opacity="${op}" ${dash}/>`;
    }).join("");

    // Draw bubbles (smallest first so big hubs remain visible, or largest first so small stations clickable)
    const sortedForDraw = [...routes].sort((a, b) => b.deps - a.deps);
    const bubblesSvg = sortedForDraw.map((r) => {
      const [x, y] = projectMap(r.lat, r.lon, view, W, H);
      if (x < 4 || x > W - 4 || y < 4 || y > H - 4) return "";
      const radius = 3.2 + Math.sqrt(r.deps / maxDeps) * 9.5;
      const activeSoi = state.mode === "plan" ? r.sp : r.sh;
      const fill = r.intl ? lfColor(r.lf) : soiColor(activeSoi);
      const stroke = r.intl ? "var(--text)" : "var(--surface)";
      return `<circle class="map-node" data-route-id="${esc(r.id)}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${radius.toFixed(1)}" fill="${fill}" fill-opacity="0.82" stroke="${stroke}" stroke-width="${r.intl ? 1.1 : 0.9}" style="cursor:pointer"/>`;
    }).join("");

    // Labels for key major destinations
    const labelCodes = view === "na"
      ? new Set(["LGA", "EWR", "DCA", "BOS", "DEN", "SFO", "LAX", "IAH", "SEA", "MCO", "LAS", "PHX", "YYZ", "CUN", "MEX", "YVR", "HNL", "ANC"])
      : new Set(["LHR", "FRA", "CDG", "HND", "GRU", "CUN", "YYZ", "SFO", "EWR", "DEN", "FCO", "ATH", "DEL", "HKG", "LIM", "KEF"]);
    const labelsSvg = routes
      .filter((r) => labelCodes.has(r.dst))
      .map((r) => {
        const [x, y] = projectMap(r.lat, r.lon, view, W, H);
        if (x < 12 || x > W - 20 || y < 12 || y > H - 8) return "";
        return `<text x="${(x + 7).toFixed(1)}" y="${(y + 3).toFixed(1)}" font-size="10" font-weight="600" font-family="var(--font-mono)" fill="var(--text)" stroke="var(--map-ocean)" stroke-width="2.5" paint-order="stroke">${esc(r.dst)}</text>`;
      })
      .join("");

    // ORD Hub Star Marker
    const ordSvg = `
      <circle cx="${ord[0].toFixed(1)}" cy="${ord[1].toFixed(1)}" r="8" fill="#0b2545" stroke="#ffffff" stroke-width="2"/>
      <circle cx="${ord[0].toFixed(1)}" cy="${ord[1].toFixed(1)}" r="3" fill="#38bdf8"/>
      <text x="${(ord[0] + 10).toFixed(1)}" y="${(ord[1] + 4).toFixed(1)}" font-size="11.5" font-weight="800" font-family="var(--font-mono)" fill="var(--text)" stroke="var(--map-ocean)" stroke-width="3" paint-order="stroke">ORD</text>
    `;

    container.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="ORD Hub and Spoke Standby Route Network Map">
        ${gridLines}
        ${landPaths}
        ${arcsSvg}
        ${bubblesSvg}
        ${labelsSvg}
        ${ordSvg}
      </svg>
    `;

    // Bind hover & click on bubbles
    const routeById = new Map(DATA.routes.map((r) => [r.id, r]));
    container.querySelectorAll(".map-node").forEach((el) => {
      const r = routeById.get(el.getAttribute("data-route-id"));
      if (!r) return;
      el.addEventListener("mouseenter", (e) => {
        const activeSoi = state.mode === "plan" ? r.sp : r.sh;
        showTooltip(e, `
          <div class="tt-title">ORD → ${esc(r.dst)} · ${esc(r.cty)}, ${esc(r.st)}</div>
          <div class="tt-grid">
            <span class="tt-k">Scope</span><span class="tt-v">${r.intl ? "International (T-100 v1)" : "Domestic (OTP + T-100)"}</span>
            <span class="tt-k">Performed Deps</span><span class="tt-v">${fmtInt(r.deps)}</span>
            <span class="tt-k">Avg T-100 LF</span><span class="tt-v">${fmtPct(r.lf, 1)}</span>
            <span class="tt-k">Avg Seats/Dep</span><span class="tt-v">${fmtDec(r.spd, 1)}</span>
            ${r.intl ? "" : `
              <span class="tt-k">Est. Open Seats</span><span class="tt-v">${fmtDec(r.open, 1)}</span>
              <span class="tt-k">${state.mode === "plan" ? "SOI Planning" : "SOI Historical"}</span><span class="tt-v" style="color:${soiColor(activeSoi)}">${fmtDec(activeSoi, 1)}</span>
            `}
            ${r.sha ? `<span class="tt-k">CPA Note</span><span class="tt-v" style="color:var(--soi-tight)">Shared Regional Spoke</span>` : ""}
          </div>
        `);
      });
      el.addEventListener("mousemove", moveTooltip);
      el.addEventListener("mouseleave", hideTooltip);
      el.addEventListener("click", () => openRouteDrawer(r));
    });
  }

  function openRouteDrawer(r) {
    const rk = `${r.dst}|${r.intl ? "INTL" : "DOM"}`;
    const series = DATA.route_monthly[rk] || [];
    const activeSoi = state.mode === "plan" ? r.sp : r.sh;

    let chartHtml = "";
    if (series.length >= 2) {
      const W = 490, H = 190, padL = 38, padR = 38, padT = 16, padB = 28;
      const plotW = W - padL - padR, plotH = H - padT - padB;
      const xPos = (i) => padL + (i / Math.max(1, series.length - 1)) * plotW;
      const yLf = (v) => padT + (1 - (Math.max(0.4, Math.min(1, v)) - 0.4) / 0.6) * plotH;
      const ySoi = (v) => padT + (1 - Math.max(0, Math.min(100, v)) / 100) * plotH;

      let lfPath = "", soiPath = "";
      series.forEach((pt, i) => {
        const [, , lf, , sp, sh] = pt;
        const soi = state.mode === "plan" ? sp : sh;
        if (lf != null) lfPath += (lfPath ? " L " : "M ") + `${xPos(i).toFixed(1)} ${yLf(lf).toFixed(1)}`;
        if (soi != null) soiPath += (soiPath ? " L " : "M ") + `${xPos(i).toFixed(1)} ${ySoi(soi).toFixed(1)}`;
      });

      const ticks = [0, Math.floor(series.length / 2), series.length - 1]
        .filter((v, idx, arr) => arr.indexOf(v) === idx)
        .map((i) => `<text x="${xPos(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="var(--text-muted)">${esc(series[i][0])}</text>`)
        .join("");

      chartHtml = `
        <h3 style="font-size:13px;margin:14px 0 6px;">Monthly Load Factor (Red) ${r.intl ? "" : "& Active SOI (Blue)"} (${series.length} active months)</h3>
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;background:var(--surface-subtle);border:1px solid var(--border);border-radius:6px;">
          <line x1="${padL}" y1="${padT}" x2="${W - padR}" y2="${padT}" stroke="var(--border)" stroke-dasharray="2 3"/>
          <line x1="${padL}" y1="${padT + plotH / 2}" x2="${W - padR}" y2="${padT + plotH / 2}" stroke="var(--border)" stroke-dasharray="2 3"/>
          <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="var(--border)"/>
          <text x="${padL - 5}" y="${padT + 4}" text-anchor="end" font-size="9.5" fill="var(--c-lf)">100%</text>
          <text x="${padL - 5}" y="${padT + plotH + 3}" text-anchor="end" font-size="9.5" fill="var(--c-lf)">40%</text>
          ${r.intl ? "" : `
            <text x="${W - padR + 5}" y="${padT + 4}" font-size="9.5" fill="var(--brand)">100</text>
            <text x="${W - padR + 5}" y="${padT + plotH + 3}" font-size="9.5" fill="var(--brand)">0</text>
          `}
          ${lfPath ? `<path d="${lfPath}" fill="none" stroke="var(--c-lf)" stroke-width="1.8" stroke-dasharray="4 2"/>` : ""}
          ${soiPath ? `<path d="${soiPath}" fill="none" stroke="var(--brand)" stroke-width="2.2"/>` : ""}
          ${ticks}
        </svg>
      `;
    } else {
      chartHtml = `<p class="panel-subtitle" style="margin-top:12px;">Monthly sparkline series is pre-packaged for the top 80 ORD stations by departure volume; full route-month data for ${esc(r.dst)} is available in <code>exports/looker/looker_route_month.csv</code> and BigQuery <code>marts.agg_route_month</code>.</p>`;
    }

    const html = `
      <div class="drawer-kpis">
        <div class="drawer-stat">
          <div class="drawer-stat-label">Departures (T-100)</div>
          <div class="drawer-stat-val mono">${fmtInt(r.deps)}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">Avg T-100 LF</div>
          <div class="drawer-stat-val mono" style="color:${lfColor(r.lf)}">${fmtPct(r.lf, 1)}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">${r.intl ? "Seats / Dep" : (state.mode === "plan" ? "SOI Planning" : "SOI Historical")}</div>
          <div class="drawer-stat-val mono" style="color:${r.intl ? "var(--text)" : soiColor(activeSoi)}">${r.intl ? fmtDec(r.spd, 1) : fmtDec(activeSoi, 1)}</div>
        </div>
      </div>

      <div class="eq-box">
        <div class="eq-row"><span>Scope &amp; Granularity</span><span class="badge ${r.intl ? "badge-intl" : "badge-mod"}">${r.intl ? "International · route_month_only_v1" : "Domestic · Flight + Route-Month"}</span></div>
        <div class="eq-row"><span>Distance / Active Months</span><span class="mono">${fmtInt(r.dist)} mi · ${r.mos} / 56 mos</span></div>
        <div class="eq-row"><span>Total Seats / Passengers</span><span class="mono">${fmtInt(r.seats)} / ${fmtInt(r.pax)}</span></div>
        <div class="eq-row"><span>Operating Carriers</span><span class="mono">${r.cars} carrier(s) ${r.sha ? "· Shared Regional CPA" : ""}</span></div>
        ${r.intl ? "" : `
          <div class="eq-row"><span>SOI Planning / Historical</span><span class="mono">${fmtDec(r.sp, 1)} / ${fmtDec(r.sh, 1)}</span></div>
          <div class="eq-row"><span>Est. Adjusted Open Seats</span><span class="mono">${fmtDec(r.open, 1)} seats/flight</span></div>
          <div class="eq-row"><span>OTP Cancellation Rate</span><span class="mono">${fmtPct(r.canc_r, 2)}</span></div>
        `}
      </div>

      ${chartHtml}

      ${r.intl ? `<div class="caveat-box"><strong>International Scope (v1):</strong> BTS On-Time Performance covers U.S. domestic flights only. International segments are evaluated strictly via BTS T-100 monthly load factors and seats per departure.</div>` : ""}
      ${r.sha ? `<div class="caveat-box"><strong>Shared Multi-Brand Regional CPA:</strong> This route includes OO (SkyWest), YX (Republic), or 2023 ZW (Air Wisconsin) segments where BTS T-100 combines UA/AA/DL traffic under <code>UNIQUE_CARRIER</code>.</div>` : ""}
    `;
    openDrawer(`ORD → ${r.dst} (${r.cty}, ${r.st})`, r.name, html);
  }

  function renderRouteTable() {
    const q = state.routeSearch.trim().toLowerCase();
    let rows = DATA.routes.filter((r) => {
      if (state.routeScope === "DOM" && r.intl) return false;
      if (state.routeScope === "INTL" && !r.intl) return false;
      if (state.routeScope === "CPA" && !r.sha) return false;
      if (q) {
        const hay = `${r.dst} ${r.cty} ${r.st} ${r.cc} ${r.name}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const sortKey = state.routeSort;
    const dir = state.routeSortDir;
    rows.sort((a, b) => {
      const getVal = (x) => {
        if (sortKey === "active_soi") return state.mode === "plan" ? x.sp : x.sh;
        return x[sortKey];
      };
      const va = getVal(a), vb = getVal(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "string") return va.localeCompare(vb) * dir;
      return (va - vb) * dir;
    });

    const pageSize = 15;
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    state.routePage = Math.min(state.routePage, totalPages);
    const start = (state.routePage - 1) * pageSize;
    const pageRows = rows.slice(start, start + pageSize);

    const tbody = $("#route-table-body");
    tbody.innerHTML = pageRows.map((r) => {
      const activeSoi = state.mode === "plan" ? r.sp : r.sh;
      const scopeTag = r.intl
        ? `<span class="badge badge-intl">Intl v1</span>`
        : (r.sha ? `<span class="badge badge-cpa">Dom · Shared CPA</span>` : `<span class="badge badge-mod">Domestic</span>`);
      return `
        <tr class="clickable" data-route-id="${esc(r.id)}">
          <td class="mono"><strong>ORD → ${esc(r.dst)}</strong></td>
          <td>${esc(r.cty)}, ${esc(r.st)}</td>
          <td>${scopeTag}</td>
          <td class="num mono">${fmtInt(r.deps)}</td>
          <td class="num mono">
            <span class="mini-bar-wrap">
              <span>${fmtPct(r.lf, 1)}</span>
              <span class="mini-bar"><span style="width:${(r.lf || 0) * 100}%;background:${lfColor(r.lf)}"></span></span>
            </span>
          </td>
          <td class="num mono">${fmtDec(r.spd, 1)}</td>
          <td class="num mono">${r.intl ? "—" : fmtDec(r.open, 1)}</td>
          <td class="num mono">
            ${r.intl ? `<span class="badge badge-null">T-100 only</span>` : `
              <span class="mini-bar-wrap">
                <strong style="color:${soiColor(activeSoi)}">${fmtDec(activeSoi, 1)}</strong>
                <span class="mini-bar"><span style="width:${activeSoi || 0}%;background:${soiColor(activeSoi)}"></span></span>
              </span>
            `}
          </td>
          <td class="num mono">${r.intl ? "—" : fmtPct(r.canc_r, 2)}</td>
        </tr>
      `;
    }).join("");

    const routeById = new Map(DATA.routes.map((r) => [r.id, r]));
    tbody.querySelectorAll("tr").forEach((tr) => {
      const r = routeById.get(tr.getAttribute("data-route-id"));
      if (r) tr.addEventListener("click", () => openRouteDrawer(r));
    });

    $("#route-count-info").textContent = `Showing ${rows.length === 0 ? 0 : start + 1}–${Math.min(start + pageSize, rows.length)} of ${rows.length} destinations`;
    $("#route-page-label").textContent = `Page ${state.routePage} / ${totalPages}`;
    $("#route-prev").disabled = state.routePage <= 1;
    $("#route-next").disabled = state.routePage >= totalPages;
  }

  // ---------------------------------------------------------------------------
  // 3. TAB 2: HEATMAP, SUB-SCORES, HOLIDAYS & 56-MONTH TREND
  // ---------------------------------------------------------------------------
  function renderHeatmapAndSubscores() {
    const yrs = activeYears();
    const cgs = activeCarrierGroups();
    const isPlan = state.mode === "plan";

    // Accumulate 7x6 matrix and 6 hour-block totals
    const hbTotals = HOUR_BLOCKS.map(() => ({ n: 0, sn: 0, ss: 0, op_n: 0, op_s: 0, so: 0, sd: 0, sb: 0, sm: 0 }));

    let html = `<table class="heatmap-table"><thead><tr><th class="row-head">Day</th>`;
    for (const hb of HOUR_BLOCKS) {
      html += `<th>${hb.short}<div style="font-weight:400;font-size:10px;color:var(--text-muted)">${hb.range}</div></th>`;
    }
    html += `</tr></thead><tbody>`;

    const cellMeta = {};
    for (let dow = 1; dow <= 7; dow++) {
      html += `<tr><th class="row-head">${DAYS[dow - 1]}</th>`;
      for (const hb of HOUR_BLOCKS) {
        let n = 0, sn = 0, ss = 0, op_n = 0, op_s = 0, so = 0, sd = 0, sb = 0, sm = 0;
        for (const y of yrs) {
          for (const cg of cgs) {
            const arr = DATA.heatmap[`${y}|${cg}|${dow}|${hb.idx}`];
            if (!arr) continue;
            n += arr[0];
            op_n += arr[5];
            op_s += arr[6];
            if (isPlan) {
              sn += arr[1]; ss += arr[2];
              so += arr[7]; sd += arr[8]; sb += arr[9]; sm += arr[10];
            } else {
              sn += arr[3]; ss += arr[4];
              so += arr[11]; sd += arr[12]; sb += arr[13]; sm += arr[14];
            }
          }
        }
        const t = hbTotals[hb.idx];
        t.n += n; t.sn += sn; t.ss += ss; t.op_n += op_n; t.op_s += op_s;
        t.so += so; t.sd += sd; t.sb += sb; t.sm += sm;

        const score = sn > 0 ? ss / sn : null;
        const open = op_n > 0 ? op_s / op_n : null;
        const cid = `hc-${dow}-${hb.idx}`;
        cellMeta[cid] = {
          day: DAYS[dow - 1], hb: `${hb.short} (${hb.range})`, n, sn, score, open,
          sOpen: sn > 0 ? so / sn : null,
          sDisp: sn > 0 ? sd / sn : null,
          sBkp: sn > 0 ? sb / sn : null,
          sDem: sn > 0 ? sm / sn : null,
        };
        html += `
          <td>
            <div class="heat-cell" data-cid="${cid}" style="background:${heatBgColor(score)}">
              <div class="heat-score mono">${fmtDec(score, 1)}</div>
              <div class="heat-sub mono">${open != null ? fmtDec(open, 1) + " open" : "N/A"}</div>
            </div>
          </td>
        `;
      }
      html += `</tr>`;
    }
    html += `</tbody></table>`;
    const container = $("#heatmap-container");
    container.innerHTML = html;

    container.querySelectorAll(".heat-cell").forEach((el) => {
      const m = cellMeta[el.getAttribute("data-cid")];
      el.addEventListener("mouseenter", (e) => {
        showTooltip(e, `
          <div class="tt-title">${m.day} · ${m.hb}</div>
          <div class="tt-grid">
            <span class="tt-k">${isPlan ? "Planning SOI" : "Historical SOI"}</span><span class="tt-v" style="color:${soiColor(m.score)}">${fmtDec(m.score, 1)}</span>
            <span class="tt-k">Est. Open Seats</span><span class="tt-v">${fmtDec(m.open, 1)}</span>
            <span class="tt-k">S_open (50% wt)</span><span class="tt-v">${fmtDec(m.sOpen, 1)}</span>
            <span class="tt-k">S_disp (20% wt)</span><span class="tt-v">${fmtDec(m.sDisp, 1)}</span>
            <span class="tt-k">S_backup (20% wt)</span><span class="tt-v">${fmtDec(m.sBkp, 1)}</span>
            <span class="tt-k">S_demand (10% wt)</span><span class="tt-v">${fmtDec(m.sDem, 1)}</span>
            <span class="tt-k">Sample Flights</span><span class="tt-v">${fmtInt(m.n)}</span>
          </div>
        `);
      });
      el.addEventListener("mousemove", moveTooltip);
      el.addEventListener("mouseleave", hideTooltip);
    });

    // Render 4-Component Stacked Horizontal Bar Chart
    const W = 470, H = 264, padL = 82, padR = 46, padT = 10, padB = 22;
    const plotW = W - padL - padR;
    const rowH = (H - padT - padB) / HOUR_BLOCKS.length;
    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;" role="img" aria-label="SOI Sub-Score Decomposition by Hour Block">`;
    [0, 25, 50, 75, 100].forEach((tick) => {
      const x = padL + (tick / 100) * plotW;
      svg += `<line x1="${x}" y1="${padT}" x2="${x}" y2="${H - padB}" stroke="var(--border)" stroke-dasharray="${tick === 0 ? "" : "2 3"}"/>`;
      svg += `<text x="${x}" y="${H - 6}" text-anchor="middle" font-size="10" fill="var(--text-muted)">${tick}</text>`;
    });

    HOUR_BLOCKS.forEach((hb, i) => {
      const t = hbTotals[i];
      const y = padT + i * rowH + 6;
      const barH = Math.max(14, rowH - 14);
      const total = t.sn > 0 ? t.ss / t.sn : 0;
      const wOpen = t.sn > 0 ? (t.so / t.sn) * 0.5 : 0;
      const wDisp = t.sn > 0 ? (t.sd / t.sn) * 0.2 : 0;
      const wBkp = t.sn > 0 ? (t.sb / t.sn) * 0.2 : 0;
      const wDem = t.sn > 0 ? (t.sm / t.sn) * 0.1 : 0;
      const segs = [
        { v: wOpen, c: "var(--c-open)" },
        { v: wDisp, c: "var(--c-disp)" },
        { v: wBkp, c: "var(--c-backup)" },
        { v: wDem, c: "var(--c-demand)" },
      ];
      svg += `<text x="${padL - 8}" y="${y + barH / 2 + 4}" text-anchor="end" font-size="11.5" font-weight="600" fill="var(--text)">${hb.short}</text>`;
      let xCur = padL;
      segs.forEach((s) => {
        const sw = (s.v / 100) * plotW;
        if (sw > 0.3) {
          svg += `<rect x="${xCur.toFixed(1)}" y="${y.toFixed(1)}" width="${sw.toFixed(1)}" height="${barH.toFixed(1)}" fill="${s.c}" rx="1"/>`;
          xCur += sw;
        }
      });
      svg += `<text x="${(xCur + 6).toFixed(1)}" y="${y + barH / 2 + 4}" font-size="11" font-weight="700" font-family="var(--font-mono)" fill="var(--text)">${t.sn > 0 ? total.toFixed(1) : "—"}</text>`;
    });
    svg += `</svg>`;
    $("#subscore-chart").innerHTML = svg;
  }

  function renderHolidayChart() {
    const yrs = activeYears();
    const cgs = activeCarrierGroups();
    const isPlan = state.mode === "plan";
    const acc = {};
    for (const [k, s] of Object.entries(DATA.holidays)) {
      const [y, cg, hol] = k.split("|");
      if (!yrs.includes(y) || !cgs.includes(cg)) continue;
      if (!acc[hol]) acc[hol] = { n: 0, sn: 0, ss: 0, op_n: 0, op_s: 0 };
      acc[hol].n += s.n;
      acc[hol].op_n += s.op_n;
      acc[hol].op_s += s.op_s;
      acc[hol].sn += isPlan ? s.sp_n : s.sh_n;
      acc[hol].ss += isPlan ? s.sp_s : s.sh_s;
    }

    const items = Object.entries(acc)
      .map(([name, a]) => ({
        name,
        n: a.n,
        score: a.sn > 0 ? a.ss / a.sn : null,
        open: a.op_n > 0 ? a.op_s / a.op_n : null,
      }))
      .filter((x) => x.n >= 20)
      .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

    const W = 540, rowH = 34, padL = 165, padR = 54, padT = 8, padB = 22;
    const H = padT + padB + Math.max(1, items.length) * rowH;
    const plotW = W - padL - padR;

    const baseline = items.find((x) => x.name === "Non-Holiday Baseline")?.score;

    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;" role="img" aria-label="Holiday Windows vs Baseline SOI Comparison">`;
    [0, 25, 50, 75, 100].forEach((t) => {
      const x = padL + (t / 100) * plotW;
      svg += `<line x1="${x}" y1="${padT}" x2="${x}" y2="${H - padB}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      svg += `<text x="${x}" y="${H - 6}" text-anchor="middle" font-size="10" fill="var(--text-muted)">${t}</text>`;
    });
    if (baseline != null) {
      const bx = padL + (baseline / 100) * plotW;
      svg += `<line x1="${bx.toFixed(1)}" y1="${padT}" x2="${bx.toFixed(1)}" y2="${H - padB}" stroke="var(--brand)" stroke-width="1.4" stroke-dasharray="4 2"/>`;
    }

    items.forEach((it, i) => {
      const y = padT + i * rowH + 6;
      const barH = 20;
      const bw = it.score != null ? (it.score / 100) * plotW : 0;
      const isBase = it.name === "Non-Holiday Baseline";
      const fill = isBase ? "var(--brand)" : soiColor(it.score);
      svg += `<text x="${padL - 8}" y="${y + 14}" text-anchor="end" font-size="11.5" font-weight="${isBase ? 700 : 500}" fill="var(--text)">${esc(it.name)}</text>`;
      svg += `<rect x="${padL}" y="${y}" width="${bw.toFixed(1)}" height="${barH}" rx="4" fill="${fill}" fill-opacity="${isBase ? 0.92 : 0.78}"/>`;
      svg += `<text x="${(padL + bw + 6).toFixed(1)}" y="${y + 14}" font-size="11" font-weight="700" font-family="var(--font-mono)" fill="var(--text)">${fmtDec(it.score, 1)}</text>`;
    });
    svg += `</svg>`;
    $("#holiday-chart").innerHTML = svg;
  }

  function renderMonthlyTrend() {
    const cgs = activeCarrierGroups();
    const scope = state.trendScope;
    const isPlan = state.mode === "plan";

    // Collect distinct sorted year_months
    const yms = Array.from(new Set(Object.keys(DATA.monthly).map((k) => k.split("|")[0]))).sort();
    const pts = yms.map((ym) => {
      let deps = 0, seats = 0, pax = 0, sh_w = 0, sh_n = 0, sp_w = 0, sp_n = 0, op_w = 0, op_n = 0;
      for (const cg of cgs) {
        const m = DATA.monthly[`${ym}|${cg}|${scope}`];
        if (!m) continue;
        deps += m[0]; seats += m[1]; pax += m[2];
        op_w += m[8]; op_n += m[9];
        sh_w += m[10]; sh_n += m[11];
        sp_w += m[12]; sp_n += m[13];
      }
      const lf = seats > 0 ? Math.min(1, pax / seats) : null;
      const soi = scope === "INTL" ? null : (isPlan ? (sp_n > 0 ? sp_w / sp_n : null) : (sh_n > 0 ? sh_w / sh_n : null));
      const open = op_n > 0 ? op_w / op_n : null;
      return { ym, deps, lf, soi, open, tsa: DATA.tsa_by_ym[ym] || null };
    });

    const W = 560, H = 265, padL = 40, padR = 42, padT = 18, padB = 34;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const xPos = (i) => padL + (i / Math.max(1, pts.length - 1)) * plotW;
    const ySoi = (v) => padT + (1 - (Math.max(20, Math.min(90, v)) - 20) / 70) * plotH;
    const yLf = (v) => padT + (1 - (Math.max(0.55, Math.min(1.0, v)) - 0.55) / 0.45) * plotH;

    // 2019 shaded region (first 12 months)
    const idx2019End = pts.findIndex((p) => !p.ym.startsWith("2019"));
    const xGap = idx2019End > 0 ? (xPos(idx2019End - 1) + xPos(idx2019End)) / 2 : padL;

    let lfPath = "", soiPath = "";
    let prevYmYear = "";
    pts.forEach((p, i) => {
      const curYr = p.ym.slice(0, 4);
      const breakGap = prevYmYear === "2019" && curYr === "2023";
      if (p.lf != null) lfPath += (lfPath && !breakGap ? " L " : " M ") + `${xPos(i).toFixed(1)} ${yLf(p.lf).toFixed(1)}`;
      if (p.soi != null) soiPath += (soiPath && !breakGap ? " L " : " M ") + `${xPos(i).toFixed(1)} ${ySoi(p.soi).toFixed(1)}`;
      prevYmYear = curYr;
    });

    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;" role="img" aria-label="56-Month Load Factor and SOI Trend">`;
    if (idx2019End > 0) {
      svg += `<rect x="${padL}" y="${padT}" width="${(xGap - padL).toFixed(1)}" height="${plotH}" fill="var(--brand-soft)" opacity="0.55"/>`;
      svg += `<text x="${((padL + xGap) / 2).toFixed(1)}" y="${padT + 12}" text-anchor="middle" font-size="9.5" font-weight="600" fill="var(--brand)">2019 Baseline</text>`;
      svg += `<line x1="${xGap.toFixed(1)}" y1="${padT}" x2="${xGap.toFixed(1)}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-dasharray="3 3"/>`;
    }

    [30, 50, 70, 90].forEach((v) => {
      const y = ySoi(v);
      svg += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      if (scope === "DOM") svg += `<text x="${padL - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="9.5" fill="var(--brand)">${v}</text>`;
    });
    [0.6, 0.75, 0.9, 1.0].forEach((v) => {
      const y = yLf(v);
      svg += `<text x="${W - padR + 6}" y="${(y + 3).toFixed(1)}" font-size="9.5" fill="var(--c-lf)">${Math.round(v * 100)}%</text>`;
    });

    if (lfPath) svg += `<path d="${lfPath}" fill="none" stroke="var(--c-lf)" stroke-width="1.8" stroke-dasharray="4 2.5"/>`;
    if (soiPath) svg += `<path d="${soiPath}" fill="none" stroke="var(--brand)" stroke-width="2.2"/>`;

    // Year tick labels
    ["2019-01", "2023-01", "2024-01", "2025-01", "2026-01"].forEach((ym) => {
      const idx = pts.findIndex((p) => p.ym === ym);
      if (idx >= 0) {
        svg += `<text x="${xPos(idx).toFixed(1)}" y="${H - 10}" text-anchor="middle" font-size="10" font-weight="600" fill="var(--text-muted)">${ym.slice(0, 4)}</text>`;
      }
    });

    // Interactive invisible hover strips
    const stepW = plotW / Math.max(1, pts.length);
    pts.forEach((p, i) => {
      svg += `<rect class="trend-hit" data-idx="${i}" x="${(xPos(i) - stepW / 2).toFixed(1)}" y="${padT}" width="${stepW.toFixed(1)}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>`;
    });

    svg += `</svg>`;
    const container = $("#monthly-trend-chart");
    container.innerHTML = svg;

    container.querySelectorAll(".trend-hit").forEach((el) => {
      const p = pts[Number(el.getAttribute("data-idx"))];
      el.addEventListener("mouseenter", (e) => {
        showTooltip(e, `
          <div class="tt-title">${p.ym} · ${scope === "DOM" ? "Domestic" : "International v1"}</div>
          <div class="tt-grid">
            <span class="tt-k">Performed Deps</span><span class="tt-v">${fmtInt(p.deps)}</span>
            <span class="tt-k">T-100 Load Factor</span><span class="tt-v" style="color:var(--c-lf)">${fmtPct(p.lf, 1)}</span>
            ${scope === "DOM" ? `
              <span class="tt-k">${isPlan ? "Planning SOI" : "Historical SOI"}</span><span class="tt-v" style="color:${soiColor(p.soi)}">${fmtDec(p.soi, 1)}</span>
              <span class="tt-k">Est. Open Seats</span><span class="tt-v">${fmtDec(p.open, 1)}</span>
            ` : ""}
            <span class="tt-k">Avg Daily TSA</span><span class="tt-v">${p.tsa ? (p.tsa / 1e6).toFixed(2) + "M" : "—"}</span>
          </div>
        `);
      });
      el.addEventListener("mousemove", moveTooltip);
      el.addEventListener("mouseleave", hideTooltip);
    });
  }

  // ---------------------------------------------------------------------------
  // 4. TAB 3: CARRIERS & FLEET SUBTYPES
  // ---------------------------------------------------------------------------
  function renderCarriersAndFleet() {
    const yrs = activeYears();
    const cgs = activeCarrierGroups();
    const isPlan = state.mode === "plan";

    // Carriers Table
    const carrierRows = DATA.carriers.meta
      .filter((c) => cgs.includes(c.cg))
      .map((c) => {
        let deps = 0, seats = 0, pax = 0;
        let n = 0, canc = 0, d15_n = 0, d15_s = 0, op_n = 0, op_s = 0, sn = 0, ss = 0;
        for (const y of yrs) {
          const t = DATA.carriers.t100[`${y}|${c.code}`];
          if (t) { deps += t[0]; seats += t[1]; pax += t[2]; }
          const f = DATA.carriers.flights[`${y}|${c.code}`];
          if (f) {
            n += f.n; canc += f.canc; d15_n += f.d15_n; d15_s += f.d15_s;
            op_n += f.op_n; op_s += f.op_s;
            sn += isPlan ? f.sp_n : f.sh_n;
            ss += isPlan ? f.sp_s : f.sh_s;
          }
        }
        return {
          ...c, deps, lf: seats > 0 ? Math.min(1, pax / seats) : null,
          delayR: d15_n > 0 ? d15_s / d15_n : null,
          cancR: n > 0 ? canc / n : null,
          open: op_n > 0 ? op_s / op_n : null,
          soi: sn > 0 ? ss / sn : null,
        };
      })
      .filter((c) => c.deps > 0)
      .sort((a, b) => b.deps - a.deps);

    $("#carrier-table-body").innerHTML = carrierRows.map((c) => `
      <tr>
        <td><strong class="mono">${esc(c.code)}</strong> · ${esc(c.name)}</td>
        <td><span class="badge ${c.cg === "MAINLINE" ? "badge-mod" : "badge-null"}">${c.cg}</span></td>
        <td>${c.shared_cpa ? `<span class="badge badge-cpa">Shared Multi-Brand CPA</span>` : `<span class="badge badge-pass">UA-Dedicated</span>`}</td>
        <td class="num mono">${fmtInt(c.deps)}</td>
        <td class="num mono">${fmtPct(c.lf, 1)}</td>
        <td class="num mono">${fmtPct(c.delayR, 1)}</td>
        <td class="num mono">${fmtPct(c.cancR, 2)}</td>
        <td class="num mono">${fmtDec(c.open, 1)}</td>
        <td class="num mono"><strong style="color:${soiColor(c.soi)}">${fmtDec(c.soi, 1)}</strong></td>
      </tr>
    `).join("");

    // Fleet Subtypes Scatter + Table
    const fleetRows = DATA.fleet.meta
      .filter((fm) => cgs.includes(fm.cg) && fm.seats)
      .map((fm) => {
        let n = 0, op_n = 0, op_s = 0, sn = 0, ss = 0;
        for (const y of yrs) {
          const f = DATA.fleet.by_year[`${y}|${fm.sub}`];
          if (!f) continue;
          n += f.n; op_n += f.op_n; op_s += f.op_s;
          sn += isPlan ? f.sp_n : f.sh_n;
          ss += isPlan ? f.sp_s : f.sh_s;
        }
        return {
          ...fm, n,
          open: op_n > 0 ? op_s / op_n : null,
          soi: sn > 0 ? ss / sn : null,
        };
      })
      .filter((f) => f.n > 0)
      .sort((a, b) => b.n - a.n);

    // Scatter Plot (Seats Config vs Active SOI)
    const W = 560, H = 320, padL = 46, padR = 24, padT = 18, padB = 38;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const maxN = Math.max(...fleetRows.map((f) => f.n), 1);
    const xPos = (seats) => padL + ((Math.max(40, Math.min(380, seats)) - 40) / 340) * plotW;
    const yPos = (soi) => padT + (1 - (Math.max(30, Math.min(90, soi)) - 30) / 60) * plotH;

    let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;" role="img" aria-label="Fleet Cabin Capacity vs Active SOI">`;
    [40, 50, 60, 70, 80, 90].forEach((v) => {
      const y = yPos(v);
      svg += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      svg += `<text x="${padL - 8}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--text-muted)">${v}</text>`;
    });
    [50, 100, 150, 200, 250, 300, 350].forEach((s) => {
      const x = xPos(s);
      svg += `<line x1="${x.toFixed(1)}" y1="${padT}" x2="${x.toFixed(1)}" y2="${H - padB}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      svg += `<text x="${x.toFixed(1)}" y="${H - 18}" text-anchor="middle" font-size="10" fill="var(--text-muted)">${s}</text>`;
    });
    svg += `<text x="${padL + plotW / 2}" y="${H - 3}" text-anchor="middle" font-size="10.5" font-weight="600" fill="var(--text-secondary)">Physical Cabin Seats (seats_config)</text>`;

    const labelSubs = new Set(["B777_200_HD", "B777_200_INTL", "B737_MAX9", "A320", "E175_76", "CRJ200", "CRJ550", "B787_10", "B757_300"]);
    fleetRows.forEach((f, idx) => {
      if (f.soi == null) return;
      const cx = xPos(f.seats), cy = yPos(f.soi);
      const r = 4.5 + Math.sqrt(f.n / maxN) * 14;
      const fill = f.cg === "MAINLINE" ? "var(--brand)" : "var(--c-express)";
      svg += `<circle class="fleet-dot" data-idx="${idx}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="${fill}" fill-opacity="0.72" stroke="var(--surface)" stroke-width="1.2" style="cursor:pointer"/>`;
      if (labelSubs.has(f.sub)) {
        svg += `<text x="${(cx + r + 4).toFixed(1)}" y="${(cy + 3).toFixed(1)}" font-size="9.5" font-weight="600" font-family="var(--font-mono)" fill="var(--text)">${esc(f.sub)}</text>`;
      }
    });
    svg += `</svg>`;
    const scatterEl = $("#fleet-scatter-chart");
    scatterEl.innerHTML = svg;

    scatterEl.querySelectorAll(".fleet-dot").forEach((el) => {
      const f = fleetRows[Number(el.getAttribute("data-idx"))];
      el.addEventListener("mouseenter", (e) => {
        showTooltip(e, `
          <div class="tt-title">${esc(f.sub)} (${f.seats} seats)</div>
          <div class="tt-grid">
            <span class="tt-k">FAA Model</span><span class="tt-v">${esc(f.mfr)} ${esc(f.mdl)}</span>
            <span class="tt-k">Carrier Group</span><span class="tt-v">${f.cg}</span>
            <span class="tt-k">${isPlan ? "Planning SOI" : "Historical SOI"}</span><span class="tt-v" style="color:${soiColor(f.soi)}">${fmtDec(f.soi, 1)}</span>
            <span class="tt-k">Est. Open Seats</span><span class="tt-v">${fmtDec(f.open, 1)}</span>
            <span class="tt-k">Sample Flights</span><span class="tt-v">${fmtInt(f.n)}</span>
          </div>
        `);
      });
      el.addEventListener("mousemove", moveTooltip);
      el.addEventListener("mouseleave", hideTooltip);
    });

    $("#fleet-table-body").innerHTML = fleetRows.map((f) => `
      <tr>
        <td><strong class="mono">${esc(f.sub)}</strong><div style="font-size:11px;color:var(--text-muted)">${esc(f.mfr)} ${esc(f.mdl)}</div></td>
        <td class="num mono">${f.seats}</td>
        <td class="num mono">${fmtInt(f.n)}</td>
        <td class="num mono">${fmtDec(f.open, 1)}</td>
        <td class="num mono"><strong style="color:${soiColor(f.soi)}">${fmtDec(f.soi, 1)}</strong></td>
      </tr>
    `).join("");
  }

  // ---------------------------------------------------------------------------
  // 5. TAB 4: FLIGHT-LEVEL STANDBY SCORER & FORMULA INSPECTOR
  // ---------------------------------------------------------------------------
  function renderFlightTable() {
    const yrs = activeYears().map(Number);
    const cgCode = state.carrierGroup === "ALL" ? null : state.carrierGroup[0];
    const isPlan = state.mode === "plan";
    const q = state.flightSearch.trim().toLowerCase();

    const rows = DATA.sample_flights.filter((fl) => {
      if (!yrs.includes(fl.y)) return false;
      if (cgCode && fl.cg !== cgCode) return false;
      if (state.flightHb !== "ALL" && String(fl.hb) !== state.flightHb) return false;
      const sc = isPlan ? fl.sp : fl.sh;
      if (state.flightTier !== "ALL") {
        if (state.flightTier === "NULL" && sc != null) return false;
        if (state.flightTier === "HIGH" && (sc == null || sc < 75)) return false;
        if (state.flightTier === "MOD" && (sc == null || sc < 55 || sc >= 75)) return false;
        if (state.flightTier === "TIGHT" && (sc == null || sc < 35 || sc >= 55)) return false;
        if (state.flightTier === "LOW" && (sc == null || sc >= 35)) return false;
      }
      if (q) {
        const hay = `${fl.dst} ${fl.cty} ${fl.st} ${fl.fn} ${fl.c} ${fl.sub} ${fl.d} ${fl.hol}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    const pageSize = 15;
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    state.flightPage = Math.min(state.flightPage, totalPages);
    const start = (state.flightPage - 1) * pageSize;
    const pageRows = rows.slice(start, start + pageSize);

    const tbody = $("#flight-table-body");
    tbody.innerHTML = pageRows.map((fl, i) => {
      const sc = isPlan ? fl.sp : fl.sh;
      const b = soiBadge(sc);
      const statusText = fl.cnc ? "Cancelled" : (sc == null && fl.y === 2019 && isPlan ? "2019 (No Prior)" : b.text);
      return `
        <tr class="clickable" data-idx="${start + i}">
          <td class="mono">${esc(fl.d)}</td>
          <td class="mono">${esc(fl.t)}</td>
          <td class="mono">UA${esc(fl.fn)}</td>
          <td><strong>ORD → ${esc(fl.dst)}</strong> <span style="color:var(--text-muted)">${esc(fl.cty)}</span></td>
          <td class="mono">${esc(fl.c)}${fl.sha ? ` <span class="badge badge-cpa">CPA</span>` : ""}</td>
          <td class="mono">${esc(fl.sub)} (${fl.sc || "—"})</td>
          <td class="num mono">${fmtPct(fl.lf, 1)}</td>
          <td class="num mono">${fl.cnc ? "—" : fmtDec(fl.op, 1)}</td>
          <td class="num mono">${fmtInt(fl.bkp)}</td>
          <td class="num mono"><strong style="color:${soiColor(sc)}">${fmtDec(sc, 1)}</strong></td>
          <td><span class="badge ${fl.cnc ? "badge-low" : b.cls}">${statusText}</span></td>
        </tr>
      `;
    }).join("");

    tbody.querySelectorAll("tr").forEach((tr) => {
      const fl = rows[Number(tr.getAttribute("data-idx"))];
      if (fl) tr.addEventListener("click", () => openFlightDrawer(fl));
    });

    $("#flight-count-info").textContent = `Showing ${rows.length === 0 ? 0 : start + 1}–${Math.min(start + pageSize, rows.length)} of ${rows.length} matching flights`;
    $("#flight-page-label").textContent = `Page ${state.flightPage} / ${totalPages}`;
    $("#flight-prev").disabled = state.flightPage <= 1;
    $("#flight-next").disabled = state.flightPage >= totalPages;
  }

  function openFlightDrawer(fl) {
    const isPlan = state.mode === "plan";
    const sc = isPlan ? fl.sp : fl.sh;
    const s4 = isPlan ? fl.s4p : fl.s4h;
    const hb = HOUR_BLOCKS[fl.hb];

    let eqHtml = "";
    if (s4 && sc != null) {
      const w0 = s4[0] * 0.5, w1 = s4[1] * 0.2, w2 = s4[2] * 0.2, w3 = s4[3] * 0.1;
      eqHtml = `
        <div class="eq-box">
          <div class="eq-row"><span><strong>S_open</strong> (Open Seats, 50% wt)</span><span class="mono">${s4[0].toFixed(1)} × 0.50 = <strong>${w0.toFixed(2)}</strong></span></div>
          <div class="eq-row"><span><strong>S_disp</strong> (Displacement, 20% wt)</span><span class="mono">${s4[1].toFixed(1)} × 0.20 = <strong>${w1.toFixed(2)}</strong></span></div>
          <div class="eq-row"><span><strong>S_backup</strong> (Later Backups, 20% wt)</span><span class="mono">${s4[2].toFixed(1)} × 0.20 = <strong>${w2.toFixed(2)}</strong></span></div>
          <div class="eq-row"><span><strong>S_demand</strong> (TSA / Holiday, 10% wt)</span><span class="mono">${s4[3].toFixed(1)} × 0.10 = <strong>${w3.toFixed(2)}</strong></span></div>
          <div class="eq-row"><span>Total ${isPlan ? "Planning SOI" : "Historical SOI"}</span><span class="mono" style="color:${soiColor(sc)}">${sc.toFixed(1)} / 100</span></div>
        </div>
      `;
    } else {
      eqHtml = `<div class="caveat-box"><strong>Unscored (${isPlan ? "Planning" : "Historical"}):</strong> ${fl.cnc ? "Cancelled flights have <code>soi_historical = NULL</code> by design." : "2019 flights have <code>soi_planning = NULL</code> because no prior training year exists."}</div>`;
    }

    const html = `
      <div class="drawer-kpis">
        <div class="drawer-stat">
          <div class="drawer-stat-label">Planning SOI</div>
          <div class="drawer-stat-val mono" style="color:${soiColor(fl.sp)}">${fmtDec(fl.sp, 1)}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">Historical SOI</div>
          <div class="drawer-stat-val mono" style="color:${soiColor(fl.sh)}">${fmtDec(fl.sh, 1)}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">Est. Open Seats</div>
          <div class="drawer-stat-val mono">${fl.cnc ? "Cancelled" : fmtDec(fl.op, 1)}</div>
        </div>
      </div>

      ${eqHtml}

      <div class="eq-box">
        <div class="eq-row"><span>Scheduled Departure</span><span class="mono">${fl.d} · ${fl.t} (${hb.short})</span></div>
        <div class="eq-row"><span>Operating Carrier / Fleet</span><span class="mono">${fl.c} · ${fl.sub} (${fl.sc} seats)</span></div>
        <div class="eq-row"><span>Base T-100 LF → Adjusted LF</span><span class="mono">${fmtPct(fl.lf, 1)} → ${fmtPct(fl.lfa, 1)}</span></div>
        <div class="eq-row"><span>T-100 Join Provenance (<code>lf_source</code>)</span><span class="mono">${esc(fl.lfs || "UNMATCHED")}</span></div>
        <div class="eq-row"><span>Same-Day Later Backups</span><span class="mono">${fmtInt(fl.bkp)} flights</span></div>
        <div class="eq-row"><span>Observed Dep Delay</span><span class="mono">${fl.cnc ? "Cancelled" : (fl.dd != null ? fl.dd + " min" : "—")}</span></div>
        <div class="eq-row"><span>Holiday Window</span><span>${esc(fl.hol || "Non-Holiday Baseline")}</span></div>
      </div>
    `;
    openDrawer(`UA${fl.fn} · ORD → ${fl.dst} (${fl.cty}, ${fl.st})`, `${fl.d} at ${fl.t} · Operated by ${fl.c}`, html);
  }

  // ---------------------------------------------------------------------------
  // 6. TAB 5: EMPIRICAL VALIDATION & BIGQUERY AUDIT
  // ---------------------------------------------------------------------------
  function renderValidation() {
    const v = DATA.validation;
    const wh = DATA.meta.warehouse;
    const p1f = v.pillar1_fixed;
    const p1c = v.pillar1_wf_combined;
    const minP3 = Math.min(...Object.values(v.pillar3_schemes).map((s) => s.spearman_rho_vs_baseline));

    $("#validation-gate-cards").innerHTML = `
      <div class="kpi-card">
        <div class="kpi-label">Pillar 1 · Fixed Holdout (2025–26)</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">ρ = ${p1f.spearman_rho.toFixed(3)}</div>
          <span class="badge badge-pass">PASS</span>
        </div>
        <div class="kpi-sub">Top-Decile Lift: <strong class="mono">${p1f.top_decile_lift.toFixed(2)}×</strong> · n = ${fmtInt(p1f.paired_flights)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Pillar 2 · Peak vs Off-Peak</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">+${v.pillar2_tests.late_jan_vs_thanksgiving_sunday.soi_historical.delta_mean.toFixed(1)} pts</div>
          <span class="badge badge-pass">PASS</span>
        </div>
        <div class="kpi-sub">Late Jan (77.0) vs T-giving Sun (46.4) · t = 86.3</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Pillar 3 · Weight Sensitivity</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">min ρ = ${minP3.toFixed(3)}</div>
          <span class="badge badge-pass">PASS</span>
        </div>
        <div class="kpi-sub">5 alternative schemes across 814,847 flights (≥ 0.85)</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Live BigQuery &amp; dbt Tests</div>
        <div class="kpi-value-row">
          <div class="kpi-value mono">${wh.dbt_tests_passed} / ${wh.dbt_tests_total}</div>
          <span class="badge badge-pass">PASS</span>
        </div>
        <div class="kpi-sub">${fmtInt(DATA.meta.total_flights)} rows · ${fmtInt(wh.partitions)} partitions · ${wh.storage_gib} GiB</div>
      </div>
    `;

    // Pillar 1 Expanding Walk-Forward Chart
    const wfYears = Object.entries(v.pillar1_wf_by_year).sort((a, b) => Number(a[0]) - Number(b[0]));
    const W1 = 520, H1 = 215, pL = 44, pR = 20, pT = 22, pB = 34;
    const pw1 = W1 - pL - pR, ph1 = H1 - pT - pB;
    const bw1 = pw1 / wfYears.length;
    let svg1 = `<svg viewBox="0 0 ${W1} ${H1}" style="width:100%;height:auto;">`;
    [0.0, 0.25, 0.5, 0.75, 1.0].forEach((t) => {
      const y = pT + (1 - t) * ph1;
      svg1 += `<line x1="${pL}" y1="${y.toFixed(1)}" x2="${W1 - pR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      svg1 += `<text x="${pL - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--text-muted)">${t.toFixed(2)}</text>`;
    });
    // Gate threshold 0.55
    const yGate = pT + (1 - 0.55) * ph1;
    svg1 += `<line x1="${pL}" y1="${yGate.toFixed(1)}" x2="${W1 - pR}" y2="${yGate.toFixed(1)}" stroke="var(--soi-high)" stroke-width="1.3" stroke-dasharray="4 2"/>`;
    svg1 += `<text x="${W1 - pR}" y="${(yGate - 4).toFixed(1)}" text-anchor="end" font-size="9.5" font-weight="600" fill="var(--soi-high)">Gate ρ ≥ 0.55</text>`;

    wfYears.forEach(([yr, m], i) => {
      const cx = pL + i * bw1 + bw1 / 2;
      const barW = Math.min(54, bw1 * 0.45);
      const h = m.spearman_rho * ph1;
      const y = pT + ph1 - h;
      svg1 += `<rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="4" fill="var(--brand)" fill-opacity="0.88"/>`;
      svg1 += `<text x="${cx.toFixed(1)}" y="${(y - 5).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="700" font-family="var(--font-mono)" fill="var(--text)">ρ=${m.spearman_rho.toFixed(3)}</text>`;
      svg1 += `<text x="${cx.toFixed(1)}" y="${H1 - 16}" text-anchor="middle" font-size="11" font-weight="700" fill="var(--text)">${yr}</text>`;
      svg1 += `<text x="${cx.toFixed(1)}" y="${H1 - 4}" text-anchor="middle" font-size="9.5" fill="var(--text-muted)">Lift ${m.top_decile_lift.toFixed(2)}×</text>`;
    });
    svg1 += `</svg>`;
    $("#val-p1-chart").innerHTML = svg1;
    $("#val-caveat-box").innerHTML = `<strong>Mandatory Consistency vs. Ground Truth Caveat:</strong> ${esc(v.caveat)}`;

    // Pillar 2 Face Validity Chart (Historical vs Planning side-by-side)
    const regimes = [
      ["late_jan_midweek", "Late Jan Midweek (Off-Peak)"],
      ["christmas_eve_and_day", "Christmas Eve & Day"],
      ["all_flights_baseline", "All-Flights Baseline"],
      ["spring_break", "Spring Break Peak"],
      ["christmas_peak", "Christmas Peak Window"],
      ["thanksgiving_sunday", "Thanksgiving Sunday"],
    ];
    const W2 = 520, rowH2 = 38, l2 = 178, r2 = 54, t2 = 18, b2 = 22;
    const H2 = t2 + b2 + regimes.length * rowH2;
    const pw2 = W2 - l2 - r2;
    let svg2 = `<svg viewBox="0 0 ${W2} ${H2}" style="width:100%;height:auto;">`;
    svg2 += `<text x="${l2}" y="11" font-size="10" font-weight="600" fill="var(--brand)">■ Historical SOI</text>`;
    svg2 += `<text x="${l2 + 95}" y="11" font-size="10" font-weight="600" fill="var(--c-disp)">■ Planning SOI</text>`;
    [0, 25, 50, 75, 100].forEach((tk) => {
      const x = l2 + (tk / 100) * pw2;
      svg2 += `<line x1="${x}" y1="${t2}" x2="${x}" y2="${H2 - b2}" stroke="var(--border)" stroke-dasharray="2 3"/>`;
      svg2 += `<text x="${x}" y="${H2 - 6}" text-anchor="middle" font-size="10" fill="var(--text-muted)">${tk}</text>`;
    });
    regimes.forEach(([key, label], i) => {
      const y = t2 + i * rowH2 + 6;
      const vh = v.pillar2_hist[key]?.mean ?? 0;
      const vp = v.pillar2_plan[key]?.mean ?? 0;
      svg2 += `<text x="${l2 - 8}" y="${y + 15}" text-anchor="end" font-size="11" font-weight="500" fill="var(--text)">${esc(label)}</text>`;
      svg2 += `<rect x="${l2}" y="${y}" width="${((vh / 100) * pw2).toFixed(1)}" height="11" rx="2" fill="var(--brand)"/>`;
      svg2 += `<text x="${(l2 + (vh / 100) * pw2 + 5).toFixed(1)}" y="${y + 9}" font-size="10" font-family="var(--font-mono)" fill="var(--text)">${vh.toFixed(1)}</text>`;
      svg2 += `<rect x="${l2}" y="${y + 13}" width="${((vp / 100) * pw2).toFixed(1)}" height="11" rx="2" fill="var(--c-disp)"/>`;
      svg2 += `<text x="${(l2 + (vp / 100) * pw2 + 5).toFixed(1)}" y="${y + 22}" font-size="10" font-family="var(--font-mono)" fill="var(--text-muted)">${vp.toFixed(1)}</text>`;
    });
    svg2 += `</svg>`;
    $("#val-p2-chart").innerHTML = svg2;

    // Pillar 3 Weight Sensitivity Chart
    const schemes = Object.entries(v.pillar3_schemes).map(([name, s]) => ({ name, ...s }));
    const W3 = 520, rowH3 = 34, l3 = 132, r3 = 86, t3 = 12, b3 = 22;
    const H3 = t3 + b3 + schemes.length * rowH3;
    const pw3 = W3 - l3 - r3;
    let svg3 = `<svg viewBox="0 0 ${W3} ${H3}" style="width:100%;height:auto;">`;
    [0.5, 0.7, 0.85, 1.0].forEach((tk) => {
      const x = l3 + ((tk - 0.5) / 0.5) * pw3;
      svg3 += `<line x1="${x.toFixed(1)}" y1="${t3}" x2="${x.toFixed(1)}" y2="${H3 - b3}" stroke="${tk === 0.85 ? "var(--soi-high)" : "var(--border)"}" stroke-dasharray="${tk === 0.85 ? "4 2" : "2 3"}"/>`;
      svg3 += `<text x="${x.toFixed(1)}" y="${H3 - 6}" text-anchor="middle" font-size="10" fill="${tk === 0.85 ? "var(--soi-high)" : "var(--text-muted)"}">${tk.toFixed(2)}</text>`;
    });
    schemes.forEach((s, i) => {
      const y = t3 + i * rowH3 + 6;
      const bw = Math.max(2, ((s.spearman_rho_vs_baseline - 0.5) / 0.5) * pw3);
      svg3 += `<text x="${l3 - 8}" y="${y + 13}" text-anchor="end" font-size="11" font-weight="600" fill="var(--text)">${esc(s.name)}</text>`;
      svg3 += `<rect x="${l3}" y="${y}" width="${bw.toFixed(1)}" height="18" rx="3" fill="${s.name === "baseline" ? "var(--brand)" : "var(--soi-high)"}" fill-opacity="0.82"/>`;
      svg3 += `<text x="${(l3 + bw + 6).toFixed(1)}" y="${y + 13}" font-size="10.5" font-family="var(--font-mono)" fill="var(--text)">ρ=${s.spearman_rho_vs_baseline.toFixed(3)} · ${s.top10_overlap_pct.toFixed(0)}%</text>`;
    });
    svg3 += `</svg>`;
    $("#val-p3-chart").innerHTML = svg3;

    // BigQuery & Sanity Checks Audit Table
    const auditRows = [
      ["GCP BigQuery Project", `flight-standby-analytics (US, billing-enabled free tier, partition_expiration_ms = NULL)`, "PASS"],
      ["marts.fct_flights", `830,941 rows · 1,704 DAY partitions · CLUSTER BY (origin, dest, op_carrier) · 469 MiB`, "PASS"],
      ["marts.agg_route_month", `20,877 rows (18,577 domestic + 2,300 international v1) · 4 dims (1,908 tails, 349 airports)`, "PASS"],
      ["dbt build (Live BigQuery)", `PASS=253 WARN=0 ERROR=0 (19 models, 3 seeds, 231/231 data & singular tests)`, "PASS"],
      ["FAA 3-Tier Seat Resolution", `814,847 / 814,847 performed flights resolved (100.0000%; 9,263 GA/Stearman collisions rejected)`, "PASS"],
      ["SC-1 & SOI Bounds", `0 passenger/seat bound violations · 0 SOI [0, 100] violations across 10 score columns`, "PASS"],
      ["SC-2 / SC-3 / SC-4", `Mainline OTP/T-100 +0.16% · Max seat diff 5.68% · System UA LF 82.89% (80–85% target)`, "PASS"],
      ["SC-5 Zero Silent Imputation", `0 violations — missing T-100 rows strictly preserve NULL across est_* and soi_*`, "PASS"],
    ];
    $("#bq-audit-body").innerHTML = auditRows.map(([k, val, st]) => `
      <tr>
        <td><strong>${esc(k)}</strong></td>
        <td style="font-size:12px">${esc(val)}</td>
        <td><span class="badge badge-pass">${st}</span></td>
      </tr>
    `).join("");
  }

  // ---------------------------------------------------------------------------
  // 7. METHODOLOGY & DISCLAIMERS DRAWER
  // ---------------------------------------------------------------------------
  function openMethodologyDrawer() {
    openDrawer(
      "Methodology, Formula & Mandatory Disclaimers",
      "United Standby Opportunity Explorer (ORD Hub · v1)",
      `
        <div class="eq-box">
          <div class="eq-row"><span><strong>SOI Formula (0–100 Heuristic)</strong></span><span class="mono">0.50·S_open + 0.20·S_disp + 0.20·S_backup + 0.10·S_demand</span></div>
          <div class="eq-row"><span><strong>S_open (50%)</strong></span><span>50/50 blend of adjusted open seats (cap 25) &amp; open cabin % (cap 30%)</span></div>
          <div class="eq-row"><span><strong>S_disp (20%)</strong></span><span>Exponentially decayed earlier ORD→DEST disruptions (hist) or prior-year rate × earlier scheduled flights (plan)</span></div>
          <div class="eq-row"><span><strong>S_backup (20%)</strong></span><span>Logarithmic curve ln(1 + N_after)/ln(7) over strictly later same-day departures</span></div>
          <div class="eq-row"><span><strong>S_demand (10%)</strong></span><span>Annual-relative national TSA throughput ratio + 6-window US holiday indicator</span></div>
        </div>

        <h3 style="font-size:13.5px;margin:16px 0 6px;">1. Non-Probability Heuristic Disclaimer</h3>
        <p class="panel-subtitle" style="line-height:1.6"><code>soi_historical</code> and <code>soi_planning</code> are transparent 0–100 relative opportunity heuristics constructed strictly from public U.S. government aviation datasets (BTS OTP, BTS T-100, FAA Aircraft Registry, TSA daily checkpoint throughput) and curated cabin configurations. <strong>They are NOT a probability of boarding.</strong> No public dataset contains United Airlines gate standby lists, pass priorities (SA1P/SA2/buddy pass), hire-date seniority, revenue standby/SDC counts, or weight-and-balance payload limits.</p>

        <h3 style="font-size:13.5px;margin:14px 0 6px;">2. Modeled Intraday &amp; Day-of-Week Adjustments</h3>
        <p class="panel-subtitle" style="line-height:1.6">BTS T-100 reports passenger loads strictly at the monthly <code>carrier × route × aircraft_type</code> grain. All hour-of-day and day-of-week differences come from BTS OTP operational signals (schedules, delays, cancellations, same-day backup banks) and damped TSA / hour-block multipliers (<code>alpha = 0.35</code>, clipped <code>[0.80, 1.20]</code>), <strong>not</strong> observed flight-level passenger loads.</p>

        <h3 style="font-size:13.5px;margin:14px 0 6px;">3. International Scope (v1) &amp; Shared Regional CPA</h3>
        <p class="panel-subtitle" style="line-height:1.6">BTS OTP covers U.S. domestic flights only; international ORD departures (71 stations, 2,300 route-months) are analyzed strictly at the <code>route × month × carrier</code> grain (<code>route_month_only_v1</code>). On shared multi-brand regional spokes, OO (SkyWest), YX (Republic), and 2023 ZW (Air Wisconsin) T-100 loads reflect combined UA/AA/DL operations.</p>

        <h3 style="font-size:13.5px;margin:14px 0 6px;">4. Independent Portfolio Project</h3>
        <p class="panel-subtitle" style="line-height:1.6">This analytics engineering project is independent and is <strong>not affiliated with, endorsed by, or sponsored by United Airlines, Inc.</strong> or its United Express partners.</p>
      `
    );
  }

  // ---------------------------------------------------------------------------
  // 8. FULL RENDER & EVENT BINDINGS
  // ---------------------------------------------------------------------------
  function renderAll() {
    renderContextBanner();
    renderKpis();
    renderRouteMap();
    renderRouteTable();
    renderHeatmapAndSubscores();
    renderHolidayChart();
    renderMonthlyTrend();
    renderCarriersAndFleet();
    renderFlightTable();
    renderValidation();
  }

  function bindEvents() {
    // Mode toggle
    $$("[data-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.mode = btn.getAttribute("data-mode");
        $$("[data-mode]").forEach((b) => b.classList.toggle("active", b === btn));
        renderAll();
      });
    });

    // Global selects
    $("#filter-year").addEventListener("change", (e) => {
      state.year = e.target.value;
      state.flightPage = 1;
      renderAll();
    });
    $("#filter-carrier-group").addEventListener("change", (e) => {
      state.carrierGroup = e.target.value;
      state.flightPage = 1;
      renderAll();
    });

    // Theme toggle
    $("#btn-theme").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      $("#theme-label").textContent = next === "dark" ? "Light" : "Dark";
      renderHeatmapAndSubscores();
    });

    // Tabs
    $$(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const t = btn.getAttribute("data-tab");
        state.tab = t;
        $$(".tab-btn").forEach((b) => {
          const active = b === btn;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", String(active));
        });
        $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${t}`));
      });
    });

    // Map controls
    $$("[data-map-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.mapView = btn.getAttribute("data-map-view");
        $$("[data-map-view]").forEach((b) => b.classList.toggle("active", b === btn));
        renderRouteMap();
      });
    });
    $("#map-scope").addEventListener("change", (e) => {
      state.mapScope = e.target.value;
      renderRouteMap();
    });

    // Route table controls
    $("#route-search").addEventListener("input", (e) => {
      state.routeSearch = e.target.value;
      state.routePage = 1;
      renderRouteTable();
    });
    $("#route-scope-filter").addEventListener("change", (e) => {
      state.routeScope = e.target.value;
      state.routePage = 1;
      renderRouteTable();
    });
    $$("#route-table th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.getAttribute("data-sort");
        if (state.routeSort === key) state.routeSortDir *= -1;
        else { state.routeSort = key; state.routeSortDir = key === "dst" ? 1 : -1; }
        renderRouteTable();
      });
    });
    $("#route-prev").addEventListener("click", () => { state.routePage = Math.max(1, state.routePage - 1); renderRouteTable(); });
    $("#route-next").addEventListener("click", () => { state.routePage += 1; renderRouteTable(); });

    // Trend scope
    $("#trend-scope").addEventListener("change", (e) => {
      state.trendScope = e.target.value;
      renderMonthlyTrend();
    });

    // Flight table controls
    $("#flight-search").addEventListener("input", (e) => { state.flightSearch = e.target.value; state.flightPage = 1; renderFlightTable(); });
    $("#flight-hb-filter").addEventListener("change", (e) => { state.flightHb = e.target.value; state.flightPage = 1; renderFlightTable(); });
    $("#flight-tier-filter").addEventListener("change", (e) => { state.flightTier = e.target.value; state.flightPage = 1; renderFlightTable(); });
    $("#flight-prev").addEventListener("click", () => { state.flightPage = Math.max(1, state.flightPage - 1); renderFlightTable(); });
    $("#flight-next").addEventListener("click", () => { state.flightPage += 1; renderFlightTable(); });

    // Drawers
    $("#btn-methodology").addEventListener("click", openMethodologyDrawer);
    $("#drawer-close").addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", (e) => { if (e.target === drawerBackdrop) closeDrawer(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  }

  async function init() {
    bindEvents();
    try {
      if (window.__DASHBOARD_DATA__) {
        DATA = window.__DASHBOARD_DATA__;
      } else {
        const res = await fetch("data/dashboard_data.json");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        DATA = await res.json();
      }
      renderAll();
    } catch (err) {
      $("#kpi-grid").innerHTML = `<div class="kpi-card" style="grid-column:1/-1">Failed to load dashboard data: ${esc(err.message)}</div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
