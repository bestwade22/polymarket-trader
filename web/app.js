/** Trade history dashboard — loads data/analysis/trade_history.json */

const DATA_URL = new URL("../data/analysis/trade_history.json", window.location.href).href;
const RESOLUTIONS_URL = new URL("../data/analysis/resolutions_cache.json", window.location.href).href;
const TZ_URL = new URL("city_timezones.json", window.location.href).href;
const U = window.DashUtils;

const TZ_LABELS = {
  "Asia/Shanghai": "China (UTC+8)",
  "Asia/Hong_Kong": "Hong Kong (UTC+8)",
  "Asia/Taipei": "Taiwan (UTC+8)",
  "Asia/Singapore": "Singapore (UTC+8)",
  "Asia/Kuala_Lumpur": "Malaysia (UTC+8)",
  "Asia/Manila": "Philippines (UTC+8)",
  "Asia/Tokyo": "Japan (UTC+9)",
  "Asia/Seoul": "Korea (UTC+9)",
  "Asia/Kolkata": "India (UTC+5:30)",
  "Asia/Karachi": "Pakistan (UTC+5)",
  "Asia/Riyadh": "Arabia (UTC+3)",
  "Asia/Jerusalem": "Israel (UTC+2/+3)",
  "Europe/London": "UK (UTC+0/+1)",
  "Europe/Paris": "Central EU (UTC+1/+2)",
  "Europe/Berlin": "Central EU (UTC+1/+2)",
  "Europe/Rome": "Central EU (UTC+1/+2)",
  "Europe/Madrid": "Central EU (UTC+1/+2)",
  "Europe/Amsterdam": "Central EU (UTC+1/+2)",
  "Europe/Helsinki": "Eastern EU (UTC+2/+3)",
  "Europe/Istanbul": "Turkey (UTC+3)",
  "Europe/Moscow": "Russia (UTC+3)",
  "Europe/Warsaw": "Poland (UTC+1/+2)",
  "America/New_York": "US East (UTC-5/-4)",
  "America/Chicago": "US Central (UTC-6/-5)",
  "America/Denver": "US Mountain (UTC-7/-6)",
  "America/Los_Angeles": "US West (UTC-8/-7)",
  "America/Toronto": "Canada East (UTC-5/-4)",
  "America/Mexico_City": "Mexico (UTC-6)",
  "America/Panama": "Panama (UTC-5)",
  "America/Argentina/Buenos_Aires": "Argentina (UTC-3)",
  "America/Sao_Paulo": "Brazil (UTC-3)",
  "Pacific/Auckland": "NZ (UTC+12/+13)",
  "Africa/Johannesburg": "South Africa (UTC+2)",
};

let allRecords = [];
let cityTimezones = {};
let filterSweepData = null;
let skippedAnalysisData = null;
/** Live stack thresholds used for timezone skip (from denylist or shipped defaults). */
let skipStackFilters = { yes_price_min: 0.45, yes_price_max: 0.6, spread_max: 0.05, bottom_n: 7 };
let filterSweepSort = { key: "oos_pass_60", asc: false };
let sortKey = "bought_at";
let sortAsc = false;
const insightSortState = {};
const skippedSortState = {};

const SURVIVING_TZ_TITLE = "By city timezone (surviving pool — used for skip)";

function survivingRecordsForSkip(records) {
  const yesMin = Number(skipStackFilters.yes_price_min) || 0;
  const yesMax = Number(skipStackFilters.yes_price_max) || 0.6;
  const spreadMax = Number(skipStackFilters.spread_max) || 0.15;
  return (records || []).filter((rec) => {
    const buy = rec.buy_price;
    if (buy == null || !Number.isFinite(buy)) return false;
    if (buy >= yesMax) return false;
    if (yesMin > 0 && buy < yesMin) return false;
    if (rec.spread != null && Number.isFinite(rec.spread) && rec.spread >= spreadMax) {
      return false;
    }
    return true;
  });
}

function timezoneGroup(city) {
  const tz = cityTimezones[city];
  if (!tz) return "Unknown";
  return TZ_LABELS[tz] || tz;
}

function buyPriceBand(price) {
  if (price < 0.3) return "<0.30";
  if (price > 0.6) return ">0.60";
  const idx = Math.min(Math.floor((price - 0.3) / 0.05), 5);
  const lo = 0.3 + idx * 0.05;
  const hi = lo + 0.05;
  return `${lo.toFixed(2)}–${hi.toFixed(2)}`;
}

function recordPnl(r) {
  return U.recordPnl(r);
}

function isSoldWin(r) {
  return U.isSoldWin(r);
}

function isSoldLose(r) {
  return U.isSoldLose(r);
}

function isSoldWouldWin(r) {
  return U.isSoldWouldWin(r);
}

function isSoldWouldLose(r) {
  return U.isSoldWouldLose(r);
}

function countsInWinSummary(r) {
  return U.countsInWinSummary(r);
}

function countsInWinSummaryDenom(r) {
  return U.countsInWinSummaryDenom(r);
}

function outcomeValue(r) {
  if (r.outcome_value_usd != null) return Number(r.outcome_value_usd);
  if (r.would_win_value_usd != null) return Number(r.would_win_value_usd);
  const pnl = recordPnl(r);
  if (pnl == null) return null;
  if (r.result === "loss") return pnl;
  return (r.cost_basis_usd || 0) + pnl;
}

function extractTempLabel(text) {
  return U.extractTempLabel(text);
}

function tempUnitFromLabel(text) {
  const t = String(text || "");
  if (/°\s*F\b/i.test(t) || /\dF\b/.test(t) || /°F/.test(t)) return "F";
  if (/°\s*C\b/i.test(t) || /\dC\b/.test(t) || /°C/.test(t)) return "C";
  return null;
}

function recordTempUnit(r) {
  return (
    tempUnitFromLabel(r.bought_temp) ||
    tempUnitFromLabel(r.temp) ||
    tempUnitFromLabel(r.group_item_title) ||
    "C"
  );
}

function fmtForecastTemp(r, { wu = false } = {}) {
  const unit = recordTempUnit(r);
  const cKey = wu ? "forecast_wu_temp_c" : "forecast_temp_c";
  const fKey = wu ? "forecast_wu_temp_f" : "forecast_temp_f";
  const c = r[cKey];
  const f = r[fKey];
  if (unit === "F") {
    if (f != null && Number.isFinite(Number(f))) {
      return `${Math.round(Number(f))}°F`;
    }
    if (c != null && Number.isFinite(Number(c))) {
      return `${Math.round((Number(c) * 9) / 5 + 32)}°F`;
    }
  } else {
    if (c != null && Number.isFinite(Number(c))) {
      return `${Math.round(Number(c))}°C`;
    }
    if (f != null && Number.isFinite(Number(f))) {
      return `${Math.round(((Number(f) - 32) * 5) / 9)}°C`;
    }
  }
  return "—";
}

function fmtForecastDelta(r) {
  if (r.forecast_delta_c == null || !Number.isFinite(Number(r.forecast_delta_c))) {
    return "—";
  }
  const unit = recordTempUnit(r);
  let v = Number(r.forecast_delta_c);
  if (unit === "F") v = (v * 9) / 5;
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}°${unit}`;
}

function parseTempBucket(label) {
  const t = String(label || "").trim();
  if (!t) return null;
  const unit = /°?\s*F\b/i.test(t) || /°F/.test(t) ? "F" : "C";
  let m = t.match(/(\d+)[°]?[FC]?\s+or\s+below/i);
  if (m) return { low: Number(m[1]), high: null, unit, kind: "below" };
  m = t.match(/(\d+)[°]?[FC]?\s+or\s+higher/i);
  if (m) return { low: Number(m[1]), high: null, unit, kind: "higher" };
  m = t.match(/(\d+)\s*-\s*(\d+)[°]?[FC]?/i);
  if (m) return { low: Number(m[1]), high: Number(m[2]), unit, kind: "range" };
  m = t.match(/(\d+)[°]?[FC]?\s*$/i);
  if (m) return { low: Number(m[1]), high: Number(m[1]), unit, kind: "single" };
  return null;
}

function forecastMatchesWinning(r, { wu = false } = {}) {
  const win = r.winning_temp;
  if (!win) return null;
  const bucket = parseTempBucket(win);
  if (!bucket) return null;
  const cKey = wu ? "forecast_wu_temp_c" : "forecast_temp_c";
  const fKey = wu ? "forecast_wu_temp_f" : "forecast_temp_f";
  let value = null;
  if (bucket.unit === "F") {
    if (r[fKey] != null && Number.isFinite(Number(r[fKey]))) value = Math.round(Number(r[fKey]));
    else if (r[cKey] != null && Number.isFinite(Number(r[cKey])))
      value = Math.round((Number(r[cKey]) * 9) / 5 + 32);
  } else {
    if (r[cKey] != null && Number.isFinite(Number(r[cKey]))) value = Math.round(Number(r[cKey]));
    else if (r[fKey] != null && Number.isFinite(Number(r[fKey])))
      value = Math.round(((Number(r[fKey]) - 32) * 5) / 9);
  }
  if (value == null) return null;
  if (bucket.kind === "below") return value <= bucket.low;
  if (bucket.kind === "higher") return value >= bucket.low;
  if (bucket.high != null) return value >= bucket.low && value <= bucket.high;
  return value === bucket.low;
}

function fmtForecastVsWin(r, { wu = false } = {}) {
  const match = forecastMatchesWinning(r, { wu });
  if (match === true) return '<span class="pnl-pos">match</span>';
  if (match === false) {
    const win = r.winning_temp ? extractTempLabel(r.winning_temp) : "?";
    return `<span class="pnl-neg">miss→${win}</span>`;
  }
  return "—";
}

function forecastVsWinSortValue(r, { wu = false } = {}) {
  const match = forecastMatchesWinning(r, { wu });
  if (match === true) return 2;
  if (match === false) return 1;
  return 0;
}

function fmtHk(iso, fallback) {
  if (fallback) return fallback;
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    timeZone: "Asia/Hong_Kong",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).replace(",", "");
}

function cityLocalMinutes(iso, city, fallbackLocal) {
  if (fallbackLocal) {
    const [h, m] = fallbackLocal.split(":").map(Number);
    if (!Number.isNaN(h) && !Number.isNaN(m)) return h * 60 + m;
  }
  const tz = cityTimezones[city];
  if (!tz || !iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: tz,
      hour: "numeric",
      minute: "numeric",
      hour12: false,
    })
      .formatToParts(d)
      .filter((p) => p.type !== "literal")
      .map((p) => [p.type, p.value])
  );
  return Number(parts.hour) * 60 + Number(parts.minute);
}

function fmtLocal(iso, city, fallback) {
  if (fallback) return fallback;
  const mins = cityLocalMinutes(iso, city);
  if (mins == null) return "—";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function parseRangeMinutes(range) {
  const [start, end] = range.split("-");
  const [sh, sm] = start.split(":").map(Number);
  const [eh, em] = end.split(":").map(Number);
  return [sh * 60 + sm, eh * 60 + em];
}

function inLocalTimeRange(mins, band) {
  if (mins == null) return false;
  if (band === "before-12:00") return mins < 12 * 60;
  if (band === "after-16:00") return mins >= 16 * 60;
  const [lo, hi] = parseRangeMinutes(band);
  return mins >= lo && mins < hi;
}

function localTimeBandSortKey(label) {
  if (label === "before 12:00") return -1;
  if (label === "after 16:00") return 24 * 60;
  if (label === "unknown") return 9999;
  const [start] = label.split("-");
  const [h, m] = start.split(":").map(Number);
  return h * 60 + m;
}

function fmtMoney(v) {
  if (v == null) return "—";
  const n = Number(v);
  const cls = n >= 0 ? "pnl-pos" : "pnl-neg";
  return `<span class="${cls}">${n >= 0 ? "+" : ""}${n.toFixed(2)}</span>`;
}

function resultBadge(result) {
  return `<span class="badge badge-${result}">${result}</span>`;
}

function vsBoughtLabel(r) {
  return U.vsBoughtLabel(r);
}

function soldOutcomeKey(r) {
  if (r.result !== "sold") return "";
  if (isSoldWouldWin(r)) return "would_win";
  if (isSoldWouldLose(r)) return "would_lose";
  if (isSoldWin(r)) return "sold_win";
  if (isSoldLose(r)) return "sold_lose";
  return "sold";
}

function soldOutcomeLabel(r) {
  if (r.result !== "sold") return "—";
  if (isSoldWouldWin(r)) {
    const bought = extractTempLabel(r.bought_temp);
    const won = r.winning_temp || "?";
    return `<span class="regret-yes">Would win (${bought}=${won})</span>`;
  }
  if (isSoldWouldLose(r)) {
    const bought = extractTempLabel(r.bought_temp);
    const won = r.winning_temp || "?";
    return `<span class="sold-win">Would lose (${bought}→${won})</span>`;
  }
  if (isSoldWin(r)) {
    return `<span class="sold-win">Sold win</span>`;
  }
  if (isSoldLose(r)) {
    return `<span class="regret-no">Sold lose</span>`;
  }
  return `<span class="regret-no">Sold</span>`;
}

function getFilters() {
  return {
    result: document.getElementById("filter-result").value,
    timezone: document.getElementById("filter-timezone").value,
    city: document.getElementById("filter-city").value,
    localTime: document.getElementById("filter-local-time").value,
    vs: document.getElementById("filter-vs").value,
    soldOutcome: document.getElementById("filter-sold-outcome").value,
    price: document.getElementById("filter-price").value,
    dateFrom: document.getElementById("filter-date-from").value,
    dateTo: document.getElementById("filter-date-to").value,
  };
}

function applyFilters(records) {
  const f = getFilters();
  return records.filter((r) => {
    if (f.result && r.result !== f.result) return false;
    if (f.timezone && timezoneGroup(r.city) !== f.timezone) return false;
    if (f.city && r.city !== f.city) return false;
    if (f.localTime) {
      const mins = cityLocalMinutes(r.bought_at, r.city, r.bought_at_local);
      if (!inLocalTimeRange(mins, f.localTime)) return false;
    }
    if (f.vs && r.win_temp_vs_bought !== f.vs) return false;
    if (f.soldOutcome === "not_sold" && r.result === "sold") return false;
    if (f.soldOutcome && f.soldOutcome !== "not_sold" && soldOutcomeKey(r) !== f.soldOutcome) {
      return false;
    }
    if (f.price && buyPriceBand(r.buy_price) !== f.price) return false;
    if (f.dateFrom && r.date < f.dateFrom) return false;
    if (f.dateTo && r.date > f.dateTo) return false;
    return true;
  });
}

function sortRecords(records) {
  return [...records].sort((a, b) => {
    let av = a[sortKey];
    let bv = b[sortKey];
    if (sortKey === "bought_at_hk" || sortKey === "sold_at_hk") {
      av = a[sortKey] || a.bought_at || a.sold_at;
      bv = b[sortKey] || b.bought_at || b.sold_at;
    }
    if (sortKey === "bought_at_local") {
      av = cityLocalMinutes(a.bought_at, a.city, a.bought_at_local) ?? "";
      bv = cityLocalMinutes(b.bought_at, b.city, b.bought_at_local) ?? "";
    }
    if (sortKey === "sold_outcome") {
      av = soldOutcomeKey(a);
      bv = soldOutcomeKey(b);
    }
    if (sortKey === "outcome_value_usd") {
      av = outcomeValue(a) ?? "";
      bv = outcomeValue(b) ?? "";
    }
    if (sortKey === "forecast_wu_temp_c") {
      av = a.forecast_wu_temp_c ?? a.forecast_wu_temp_f ?? "";
      bv = b.forecast_wu_temp_c ?? b.forecast_wu_temp_f ?? "";
    }
    if (sortKey === "forecast_vs_win") {
      av = forecastVsWinSortValue(a);
      bv = forecastVsWinSortValue(b);
    }
    if (sortKey === "forecast_wu_vs_win") {
      av = forecastVsWinSortValue(a, { wu: true });
      bv = forecastVsWinSortValue(b, { wu: true });
    }
    if (av == null) av = "";
    if (bv == null) bv = "";
    if (typeof av === "number" && typeof bv === "number") {
      return sortAsc ? av - bv : bv - av;
    }
    if (typeof av === "boolean") av = av ? 1 : 0;
    if (typeof bv === "boolean") bv = bv ? 1 : 0;
    const cmp = String(av).localeCompare(String(bv));
    return sortAsc ? cmp : -cmp;
  });
}

function avgHkMinutes(records) {
  const mins = [];
  for (const r of records) {
    const iso = r.bought_at;
    if (!iso) continue;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) continue;
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Hong_Kong",
        hour: "numeric",
        minute: "numeric",
        hour12: false,
      })
        .formatToParts(d)
        .filter((p) => p.type !== "literal")
        .map((p) => [p.type, p.value])
    );
    mins.push(Number(parts.hour) * 60 + Number(parts.minute));
  }
  if (!mins.length) return null;
  const avg = mins.reduce((a, b) => a + b, 0) / mins.length;
  const h = Math.floor(avg / 60);
  const m = Math.round(avg % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")} HKT`;
}

function computeFilteredSummary(records) {
  const s = {
    total_count: records.length,
    win_count: 0,
    loss_count: 0,
    sold_count: 0,
    open_count: 0,
    sold_win_count: 0,
    sold_lose_count: 0,
    total_cost_basis_usd: 0,
    total_realized_pnl_usd: 0,
    sold_but_would_have_won_count: 0,
    sold_would_lose_count: 0,
    pnl_count: 0,
    buy_price_total: 0,
    spread_total: 0,
    spread_count: 0,
    outcome_total: 0,
    outcome_count: 0,
  };
  for (const r of records) {
    if (r.result === "win") s.win_count++;
    else if (r.result === "loss") s.loss_count++;
    else if (r.result === "sold") {
      s.sold_count++;
      if (isSoldWin(r)) s.sold_win_count++;
      else if (isSoldLose(r)) s.sold_lose_count++;
    } else if (r.result === "open") s.open_count++;
    s.total_cost_basis_usd += r.cost_basis_usd || 0;
    s.buy_price_total += r.buy_price || 0;
    if (r.spread != null && Number.isFinite(r.spread)) {
      s.spread_total += r.spread;
      s.spread_count += 1;
    }
    const pnl = recordPnl(r);
    if (pnl != null) {
      s.total_realized_pnl_usd += pnl;
      s.pnl_count += 1;
    }
    if (isSoldWouldWin(r)) s.sold_but_would_have_won_count++;
    if (isSoldWouldLose(r)) s.sold_would_lose_count++;

    const outcome = outcomeValue(r);
    if (outcome != null) {
      s.outcome_total += outcome;
      s.outcome_count += 1;
    }
  }
  const settledClassic = s.win_count + s.loss_count + s.sold_count;
  const settled = records.filter(countsInWinSummaryDenom).length;
  s.win_pct = settledClassic ? Math.round((s.win_count / settledClassic) * 1000) / 10 : 0;
  s.win_plus_sold_win_count = records.filter(countsInWinSummary).length;
  s.win_plus_sold_win_pct = settled
    ? Math.round((s.win_plus_sold_win_count / settled) * 1000) / 10
    : 0;
  s.avg_buy_usd = records.length ? s.total_cost_basis_usd / records.length : 0;
  s.avg_buy_price = records.length ? s.buy_price_total / records.length : 0;
  s.avg_spread = s.spread_count ? s.spread_total / s.spread_count : 0;
  s.avg_pnl_usd = s.pnl_count ? s.total_realized_pnl_usd / s.pnl_count : 0;
  s.avg_bought_time_hk = avgHkMinutes(records);
  s.total_outcome_value_usd = s.outcome_total;
  s.avg_outcome_value_usd = s.outcome_count ? s.outcome_total / s.outcome_count : 0;
  return s;
}

function renderSummary(records) {
  const fs = computeFilteredSummary(records);
  const parts = U.computeWinSummaryParts(records);
  const breakdown = U.winSummaryBreakdownLabel(parts);
  document.getElementById("summary-content").innerHTML = `
    <div class="summary-grid">
      <div><span class="summary-label">Total</span><span class="summary-value">${fs.total_count}</span></div>
      <div><span class="summary-label">Win</span><span class="summary-value">${fs.win_count}</span></div>
      <div><span class="summary-label">Sold win</span><span class="summary-value">${fs.sold_win_count}</span></div>
      <div><span class="summary-label">Win summary</span><span class="summary-value">${fs.win_plus_sold_win_count}</span></div>
      <div><span class="summary-label">Win summary%</span><span class="summary-value">${fs.win_plus_sold_win_pct}%</span></div>
      <div class="summary-breakdown" style="grid-column: 1 / -1; font-size: 0.85rem; color: var(--muted);">Win summary = ${breakdown}</div>
      <div><span class="summary-label">Loss</span><span class="summary-value">${fs.loss_count}</span></div>
      <div><span class="summary-label">Sold lose</span><span class="summary-value">${fs.sold_lose_count}</span></div>
      <div><span class="summary-label">Sold</span><span class="summary-value">${fs.sold_count}</span></div>
      <div><span class="summary-label">Open</span><span class="summary-value">${fs.open_count}</span></div>
      <div><span class="summary-label">Win%</span><span class="summary-value">${fs.win_pct}%</span></div>
      <div><span class="summary-label">Avg buy price</span><span class="summary-value">${fs.avg_buy_price.toFixed(3)}</span></div>
      <div><span class="summary-label">Avg spread</span><span class="summary-value">${fs.spread_count ? fs.avg_spread.toFixed(3) : "—"}</span></div>
      <div><span class="summary-label">Avg buy $</span><span class="summary-value">$${fs.avg_buy_usd.toFixed(2)}</span></div>
      <div><span class="summary-label">Avg P&amp;L</span><span class="summary-value">$${fs.avg_pnl_usd.toFixed(2)}</span></div>
      <div><span class="summary-label">Avg bought time</span><span class="summary-value">${fs.avg_bought_time_hk || "—"}</span></div>
      <div><span class="summary-label">Total cost</span><span class="summary-value">$${fs.total_cost_basis_usd.toFixed(2)}</span></div>
      <div><span class="summary-label">Total P&amp;L</span><span class="summary-value">$${fs.total_realized_pnl_usd.toFixed(2)}</span></div>
      <div><span class="summary-label">Sold→would win</span><span class="summary-value">${fs.sold_but_would_have_won_count}</span></div>
      <div><span class="summary-label">Total outcome</span><span class="summary-value">$${(fs.total_outcome_value_usd ?? 0).toFixed(2)}</span></div>
      <div><span class="summary-label">Avg outcome</span><span class="summary-value">$${(fs.avg_outcome_value_usd ?? 0).toFixed(2)}</span></div>
    </div>`;
}

const INSIGHT_COLUMNS = [
  { key: "group", label: "Group", type: "string" },
  { key: "count", label: "Count", type: "number" },
  { key: "settled", label: "Settled", type: "number" },
  { key: "win_rate_pct", label: "Win%", type: "number" },
  { key: "win_plus_sold_win_pct", label: "Win summary%", type: "number" },
  { key: "avg_buy_price", label: "Avg buy", type: "number" },
  { key: "avg_spread", label: "Avg spread", type: "number" },
  { key: "avg_pnl_usd", label: "Avg P&amp;L", type: "number" },
  { key: "total_pnl_usd", label: "Total P&amp;L", type: "number" },
  { key: "avg_outcome_value_usd", label: "Avg outcome", type: "number" },
];

function insightColumnsFor(_title) {
  return INSIGHT_COLUMNS;
}

function localBuyTimeBand(localTime) {
  if (!localTime || !String(localTime).includes(":")) return "unknown";
  const [hour, minute] = String(localTime).split(":").map(Number);
  const total = hour * 60 + minute;
  const start = 12 * 60;
  const end = 16 * 60;
  if (total < start) return "before 12:00";
  if (total >= end) return "after 16:00";
  const bandStart = start + Math.floor((total - start) / 15) * 15;
  const bandEnd = bandStart + 15;
  const fmt = (m) =>
    `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  return `${fmt(bandStart)}-${fmt(bandEnd)}`;
}

function weekdayLabel(dateStr) {
  if (!dateStr) return "Unknown";
  const d = new Date(`${dateStr}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return "Unknown";
  return d.toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
}

function weekLabel(dateStr) {
  if (!dateStr) return "Unknown";
  const d = new Date(`${dateStr}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return "Unknown";
  // ISO week
  const tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dayNum = tmp.getUTCDay() || 7;
  tmp.setUTCDate(tmp.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((tmp - yearStart) / 86400000 + 1) / 7);
  return `${tmp.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function monthLabel(dateStr) {
  if (!dateStr) return "Unknown";
  return dateStr.length >= 7 ? dateStr.slice(0, 7) : "Unknown";
}

function dayLabel(dateStr) {
  if (!dateStr) return "Unknown";
  return dateStr.length >= 10 ? dateStr.slice(0, 10) : dateStr;
}

function roiBand(r) {
  if (r.roi_pct == null) return "unknown";
  const roi = r.roi_pct;
  if (roi < -50) return "<-50%";
  if (roi < 0) return "-50–0%";
  if (roi < 50) return "0–50%";
  if (roi < 100) return "50–100%";
  return ">100%";
}

function spreadBand(spread) {
  if (spread == null || !Number.isFinite(spread) || spread < 0) return "unknown";
  const idx = Math.floor(spread / 0.05);
  const lo = idx * 0.05;
  const hi = lo + 0.05;
  return `${lo.toFixed(2)}–${hi.toFixed(2)}`;
}

function edgeLabel(onEdge) {
  if (onEdge == null) return "unknown";
  return onEdge ? "Yes" : "No";
}

function competitiveBand(score) {
  if (score == null || !Number.isFinite(score)) return "unknown";
  if (score >= 0.98) return "0.98–1.00";
  if (score < 0.8) return "<0.80";
  const idx = Math.floor((score - 0.8) / 0.02);
  const lo = 0.8 + idx * 0.02;
  const hi = lo + 0.02;
  return `${lo.toFixed(2)}–${hi.toFixed(2)}`;
}

function competitiveBandSortKey(label) {
  if (label === "unknown") return -2;
  if (label === "<0.80") return -1;
  if (label === "0.98–1.00") return 1.0;
  const m = /^(\d\.\d+)–/.exec(label);
  return m ? parseFloat(m[1]) : 0;
}

function openInterestBand(openInterest) {
  if (openInterest == null || !Number.isFinite(openInterest) || openInterest < 0) {
    return "unknown";
  }
  const step = 2000;
  const idx = Math.floor(openInterest / step);
  const lo = idx * step;
  if (lo >= 30000) return "≥30000";
  const hi = lo + step;
  return `${lo}–${hi}`;
}

function yesGapBand(gap) {
  if (gap == null || !Number.isFinite(gap) || gap < 0) return "unknown";
  if (gap >= 0.3) return "≥0.30";
  const idx = Math.floor(gap / 0.05);
  const lo = idx * 0.05;
  const hi = lo + 0.05;
  return `${lo.toFixed(2)}–${hi.toFixed(2)}`;
}

function yesGapBandSortKey(label) {
  if (label === "unknown") return -1;
  if (label.startsWith("≥")) return 0.3;
  const m = /^(\d\.\d+)–/.exec(label);
  return m ? parseFloat(m[1]) : 0;
}

function openInterestBandSortKey(label) {
  if (label === "unknown") return -1;
  if (label.startsWith("≥")) return 30000;
  const m = /^(\d+)–/.exec(label);
  return m ? parseInt(m[1], 10) : 0;
}

function soldOutcomeInsightKey(r) {
  if (r.result !== "sold") return "not_sold";
  return soldOutcomeKey(r) || "sold";
}

function groupInsightMetrics(records, keyFn) {
  const grouped = new Map();
  for (const rec of records) {
    const key = keyFn(rec);
    if (!grouped.has(key)) {
      grouped.set(key, {
        count: 0,
        wins: 0,
        sold_wins: 0,
        sold_loses: 0,
        win_summary: 0,
        settled: 0,
        win_summary_denom: 0,
        pnl_usd: 0,
        buy_usd: 0,
        buy_price: 0,
        spread: 0,
        spread_count: 0,
        outcome_usd: 0,
        outcome_count: 0,
      });
    }
    const stats = grouped.get(key);
    stats.count += 1;
    stats.buy_usd += rec.cost_basis_usd || 0;
    stats.buy_price += rec.buy_price || 0;
    if (rec.spread != null && Number.isFinite(rec.spread)) {
      stats.spread += rec.spread;
      stats.spread_count += 1;
    }
    const pnl = recordPnl(rec);
    if (pnl != null) stats.pnl_usd += pnl;
    const outcome = outcomeValue(rec);
    if (outcome != null) {
      stats.outcome_usd += outcome;
      stats.outcome_count += 1;
    }
    if (rec.result === "win" || rec.result === "loss" || rec.result === "sold") {
      stats.settled += 1;
    }
    if (countsInWinSummaryDenom(rec)) {
      stats.win_summary_denom += 1;
    }
    if (rec.result === "win") stats.wins += 1;
    if (isSoldWin(rec)) stats.sold_wins += 1;
    if (isSoldLose(rec)) stats.sold_loses += 1;
    if (countsInWinSummary(rec)) stats.win_summary += 1;
  }

  const result = {};
  for (const [key, stats] of grouped.entries()) {
    const { count, settled, wins, win_summary } = stats;
    const winSummaryDenom = stats.win_summary_denom;
    result[key] = {
      count,
      wins,
      sold_wins: stats.sold_wins,
      sold_loses: stats.sold_loses,
      win_plus_sold_win: win_summary,
      settled,
      win_rate_pct: settled ? Math.round((wins / settled) * 1000) / 10 : 0,
      win_plus_sold_win_pct: winSummaryDenom
        ? Math.round((win_summary / winSummaryDenom) * 1000) / 10
        : 0,
      avg_buy_usd: count ? Math.round((stats.buy_usd / count) * 100) / 100 : 0,
      avg_buy_price: count ? Math.round((stats.buy_price / count) * 1000) / 1000 : 0,
      avg_spread: stats.spread_count
        ? Math.round((stats.spread / stats.spread_count) * 10000) / 10000
        : 0,
      avg_pnl_usd: count ? Math.round((stats.pnl_usd / count) * 100) / 100 : 0,
      total_pnl_usd: Math.round(stats.pnl_usd * 100) / 100,
      avg_outcome_value_usd: stats.outcome_count
        ? Math.round((stats.outcome_usd / stats.outcome_count) * 100) / 100
        : 0,
      total_outcome_value_usd: Math.round(stats.outcome_usd * 100) / 100,
    };
  }
  return result;
}

function computeInsights(records, { skipPoolRecords = null } = {}) {
  let soldCount = 0;
  let soldRegret = 0;
  let soldWouldLose = 0;
  const sellValuePcts = [];
  const pnlByResult = {};

  for (const rec of records) {
    if (rec.result === "sold") {
      soldCount += 1;
      if (isSoldWouldWin(rec)) soldRegret += 1;
      if (isSoldWouldLose(rec)) soldWouldLose += 1;
      if (rec.sell_value_pct != null) sellValuePcts.push(rec.sell_value_pct);
    }
    const pnl = recordPnl(rec);
    if (pnl != null) {
      if (!pnlByResult[rec.result]) pnlByResult[rec.result] = [];
      pnlByResult[rec.result].push(pnl);
    }
  }

  const avgPnlByResult = {};
  for (const [result, vals] of Object.entries(pnlByResult)) {
    avgPnlByResult[result] = vals.length
      ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100
      : 0;
  }

  // Match Lambda: rank from all history + live buy/spread stack (ignore page filters).
  const surviving = survivingRecordsForSkip(skipPoolRecords || records);

  return {
    summary_by_city: groupInsightMetrics(records, (r) => r.city || "Unknown"),
    summary_by_buy_price_band: groupInsightMetrics(records, (r) => buyPriceBand(r.buy_price)),
    summary_by_local_buy_time_band: groupInsightMetrics(records, (r) =>
      localBuyTimeBand(r.bought_at_local || fmtLocal(r.bought_at, r.city))
    ),
    summary_by_win_temp_vs_bought: groupInsightMetrics(
      records,
      (r) => r.win_temp_vs_bought || "unknown"
    ),
    summary_by_weekday: groupInsightMetrics(records, (r) => weekdayLabel(r.date)),
    summary_by_day: groupInsightMetrics(records, (r) => dayLabel(r.date)),
    summary_by_week: groupInsightMetrics(records, (r) => weekLabel(r.date)),
    summary_by_month: groupInsightMetrics(records, (r) => monthLabel(r.date)),
    summary_by_result: groupInsightMetrics(records, (r) => r.result || "unknown"),
    summary_by_sold_outcome: groupInsightMetrics(records, (r) => soldOutcomeInsightKey(r)),
    summary_by_trade_window: groupInsightMetrics(records, (r) => r.trade_window || "unknown"),
    summary_by_roi_band: groupInsightMetrics(records, (r) => roiBand(r)),
    summary_by_spread_band: groupInsightMetrics(records, (r) => spreadBand(r.spread)),
    summary_by_edge: groupInsightMetrics(records, (r) => edgeLabel(r.on_edge)),
    summary_by_competitive_band: groupInsightMetrics(records, (r) =>
      competitiveBand(r.competitive)
    ),
    summary_by_open_interest_band: groupInsightMetrics(records, (r) =>
      openInterestBand(r.open_interest)
    ),
    summary_by_yes_gap_band: groupInsightMetrics(records, (r) => yesGapBand(r.yes_gap)),
    summary_by_loss_autopsy: groupInsightMetrics(records, (r) => r.loss_autopsy || "n/a"),
    summary_by_city_timezone: groupInsightMetrics(records, (r) => timezoneGroup(r.city)),
    summary_by_city_timezone_surviving: groupInsightMetrics(surviving, (r) =>
      timezoneGroup(r.city)
    ),
    surviving_pool_n: surviving.length,
    stop_loss_regret_rate_pct: soldCount
      ? Math.round((soldRegret / soldCount) * 1000) / 10
      : 0,
    sold_would_lose_rate_pct: soldCount
      ? Math.round((soldWouldLose / soldCount) * 1000) / 10
      : 0,
    avg_pnl_by_result: avgPnlByResult,
    avg_sell_value_pct: sellValuePcts.length
      ? Math.round((sellValuePcts.reduce((a, b) => a + b, 0) / sellValuePcts.length) * 100) / 100
      : null,
  };
}

function sortInsightEntries(title, data, limit) {
  const state = insightSortState[title] || { key: "group", asc: true };
  if (title === "By local buy time" && !insightSortState[title]) {
    state.key = "group";
    state.asc = true;
    state.groupSort = "time";
  }

  let entries = Object.entries(data || {});
  if (title === "By local buy time" && state.groupSort === "time") {
    entries.sort((a, b) => localTimeBandSortKey(a[0]) - localTimeBandSortKey(b[0]));
    if (limit) entries = entries.slice(0, limit);
    return entries;
  }

  // Daily summary: newest days first by default (group desc).
  if (title === "By day" && !insightSortState[title]) {
    entries.sort((a, b) => String(b[0]).localeCompare(String(a[0])));
    if (limit) entries = entries.slice(0, limit);
    return entries;
  }

  if (
    (title === "By competitive band" ||
      title === "By open interest band" ||
      title === "By yes gap band") &&
    state.key === "group" &&
    state.asc
  ) {
    const sortKey =
      title === "By competitive band"
        ? competitiveBandSortKey
        : title === "By yes gap band"
          ? yesGapBandSortKey
          : openInterestBandSortKey;
    entries.sort((a, b) => sortKey(a[0]) - sortKey(b[0]));
    if (limit) entries = entries.slice(0, limit);
    return entries;
  }

  entries.sort((a, b) => {
    let av;
    let bv;
    if (state.key === "group") {
      av = a[0];
      bv = b[0];
    } else {
      av = a[1][state.key] ?? 0;
      bv = b[1][state.key] ?? 0;
    }
    if (typeof av === "number" && typeof bv === "number") {
      return state.asc ? av - bv : bv - av;
    }
    const cmp = String(av).localeCompare(String(bv));
    return state.asc ? cmp : -cmp;
  });
  if (limit) entries = entries.slice(0, limit);
  return entries;
}

function renderGroupTable(title, data, options = {}) {
  const { limit = null, defaultSort = null, description = null } = options;
  const columns = insightColumnsFor(title);
  if (!insightSortState[title]) {
    insightSortState[title] = defaultSort || { key: "count", asc: false };
    if (title === "By local buy time") {
      insightSortState[title] = { key: "group", asc: true, groupSort: "time" };
    }
    if (title === "By day") {
      insightSortState[title] = { key: "group", asc: false };
    }
    if (title === "By competitive band" || title === "By open interest band" || title === "By yes gap band") {
      insightSortState[title] = { key: "group", asc: true };
    }
    if (title === SURVIVING_TZ_TITLE) {
      // Worst win summary first — same order as Lambda timezone-skip log.
      insightSortState[title] = { key: "win_plus_sold_win_pct", asc: true };
    }
  }
  const state = insightSortState[title];
  const entries = sortInsightEntries(title, data, limit);
  const rows = entries.length
    ? entries
        .map(
          ([key, stats]) => `
            <tr>
              <td class="sticky-city">${key}</td>
              ${columns.slice(1).map((col) => {
                const val = stats[col.key] ?? 0;
                if (col.key === "win_rate_pct" || col.key === "win_plus_sold_win_pct") {
                  return `<td>${Number(val).toFixed(1)}%</td>`;
                }
                if (col.key === "avg_buy_price" || col.key === "avg_spread") {
                  return `<td>${Number(val).toFixed(3)}</td>`;
                }
                if (col.key === "avg_pnl_usd" || col.key === "total_pnl_usd") {
                  return `<td>${fmtMoney(val)}</td>`;
                }
                if (col.key.startsWith("avg_") || col.key.startsWith("total_")) {
                  return `<td>$${Number(val).toFixed(2)}</td>`;
                }
                return `<td>${val}</td>`;
              }).join("")}
            </tr>`
        )
        .join("")
    : `<tr><td colspan="${columns.length}">No data</td></tr>`;
  const header = columns.map(
    (col, idx) =>
      `<th class="insight-sort${idx === 0 ? " sticky-city" : ""}" data-insight="${title}" data-key="${col.key}">${col.label}${state.key === col.key ? (state.asc ? " ▲" : " ▼") : ""}</th>`
  ).join("");
  return `
    <section class="insight-card" data-insight-title="${title}">
      <h3>${title}${description ? `<span class="insight-desc">${description}</span>` : ""}</h3>
      <div class="mini-table-wrap">
        <table class="mini-table">
          <thead><tr>${header}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>`;
}

function median(vals) {
  if (!vals.length) return null;
  const sorted = [...vals].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function mean(vals) {
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function localHour(r) {
  const local = r.bought_at_local || fmtLocal(r.bought_at, r.city);
  if (!local || !local.includes(":")) return null;
  const h = parseInt(local.split(":")[0], 10);
  return Number.isFinite(h) ? h : null;
}

function computeWinLossFingerprint(records) {
  const wins = records.filter((r) => countsInWinSummary(r));
  const losses = records.filter(
    (r) => countsInWinSummaryDenom(r) && !countsInWinSummary(r)
  );
  const fields = [
    { key: "yes_gap", label: "Yes gap", pick: (r) => r.yes_gap },
    { key: "buy_price", label: "Buy $", pick: (r) => r.buy_price },
    { key: "spread", label: "Spread", pick: (r) => r.spread },
    { key: "open_interest", label: "Open interest", pick: (r) => r.open_interest },
    {
      key: "on_edge",
      label: "On edge rate",
      pick: (r) => (r.on_edge == null ? null : r.on_edge ? 1 : 0),
    },
    { key: "local_hour", label: "Local buy hour", pick: (r) => localHour(r) },
    {
      key: "minutes_into_window",
      label: "Min into window",
      pick: (r) => r.minutes_into_window,
    },
  ];
  return {
    win_n: wins.length,
    loss_n: losses.length,
    rows: fields.map((f) => {
      const wv = wins.map(f.pick).filter((v) => v != null && Number.isFinite(v));
      const lv = losses.map(f.pick).filter((v) => v != null && Number.isFinite(v));
      return {
        label: f.label,
        win_mean: mean(wv),
        win_median: median(wv),
        loss_mean: mean(lv),
        loss_median: median(lv),
        win_n: wv.length,
        loss_n: lv.length,
      };
    }),
  };
}

function fmtFinger(v, label) {
  if (v == null || !Number.isFinite(v)) return "—";
  if (label === "Open interest") return `$${Math.round(v).toLocaleString()}`;
  if (label === "On edge rate") return `${(v * 100).toFixed(0)}%`;
  if (label === "Local buy hour" || label === "Min into window") return v.toFixed(1);
  return v.toFixed(3);
}

function renderWinLossFingerprint(records) {
  const container = document.getElementById("fingerprint-panel");
  if (!container) return;
  const fp = computeWinLossFingerprint(records);
  const rows = fp.rows
    .map(
      (r) => `
      <tr>
        <td>${r.label}</td>
        <td>${fmtFinger(r.win_mean, r.label)}</td>
        <td>${fmtFinger(r.win_median, r.label)}</td>
        <td>${fmtFinger(r.loss_mean, r.label)}</td>
        <td>${fmtFinger(r.loss_median, r.label)}</td>
      </tr>`
    )
    .join("");
  container.innerHTML = `
    <section class="insight-card fingerprint-card">
      <h3>Win vs loss fingerprint
        <span class="insight-desc">Mean/median on the current filtered set (win summary wins vs losses)</span>
      </h3>
      <p class="muted">Wins n=${fp.win_n} · Losses n=${fp.loss_n}</p>
      <div class="mini-table-wrap">
        <table class="mini-table">
          <thead>
            <tr>
              <th>Field</th>
              <th>Win mean</th>
              <th>Win median</th>
              <th>Loss mean</th>
              <th>Loss median</th>
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="5">No settled trades in filter</td></tr>`}</tbody>
        </table>
      </div>
    </section>`;
}

function renderInsights(data) {
  const container = document.getElementById("insights-content");
  const insightSections = [
    [
      "By day",
      data.summary_by_day,
      {
        limit: 10,
        defaultSort: { key: "group", asc: false },
        description: "Last 10 days (newest first); respects active filters",
      },
    ],
    ["By city", data.summary_by_city, { limit: null }],
    ["By local buy time", data.summary_by_local_buy_time_band, { limit: null }],
    ["By buy price band", data.summary_by_buy_price_band, { limit: null }],
    [
      "By spread band",
      data.summary_by_spread_band,
      {
        limit: null,
        defaultSort: { key: "group", asc: true },
        description: "Bid–ask spread at order time in 0.05 steps (0.00–0.05, 0.05–0.10, …)",
      },
    ],
    [
      "By edge",
      data.summary_by_edge,
      {
        limit: null,
        defaultSort: { key: "group", asc: true },
        description: "On edge = all cooler temp buckets had Yes &lt; 1% at order time",
      },
    ],
    [
      "By competitive band",
      data.summary_by_competitive_band,
      {
        limit: null,
        defaultSort: { key: "group", asc: true },
        description: "Polymarket competitiveness score at order time (0.98–1.00, 0.96–0.98, …, &lt;0.80)",
      },
    ],
    [
      "By open interest band",
      data.summary_by_open_interest_band,
      {
        limit: null,
        defaultSort: { key: "group", asc: true },
        description: "Event open interest (USD) at order time in $2k bands",
      },
    ],
    [
      "By yes gap band",
      data.summary_by_yes_gap_band,
      {
        limit: null,
        defaultSort: { key: "group", asc: true },
        description:
          "Top Yes% − 2nd Yes% (0.05 bands). Larger gap = clearer highest-yes leader",
      },
    ],
    [
      "By loss autopsy",
      data.summary_by_loss_autopsy,
      {
        limit: null,
        description:
          "Loss tags: wrong_bucket / sold_too_early / never_led / gap_collapsed (n/a = win or open)",
      },
    ],
    ["By sold outcome", data.summary_by_sold_outcome, { limit: null }],
    ["By result", data.summary_by_result, { limit: null }],
    ["By win temp vs bought", data.summary_by_win_temp_vs_bought, { limit: null }],
    ["By trade window", data.summary_by_trade_window, { limit: null }],
    ["By weekday", data.summary_by_weekday, { limit: null }],
    ["By week", data.summary_by_week, { limit: null }],
    ["By month", data.summary_by_month, { limit: null }],
    [
      "By return % (ROI)",
      data.summary_by_roi_band,
      {
        limit: null,
        description: "P&amp;L ÷ cost basis: &lt;-50%, -50–0%, 0–50%, 50–100%, &gt;100%",
      },
    ],
    ["By city timezone", data.summary_by_city_timezone, { limit: null }],
    [
      SURVIVING_TZ_TITLE,
      data.summary_by_city_timezone_surviving,
      {
        limit: null,
        description:
          `Trades that pass the live stack (buy ≥ ${skipStackFilters.yes_price_min}, buy &lt; ${skipStackFilters.yes_price_max}, spread &lt; ${skipStackFilters.spread_max} when known). ` +
          `n=${data.surviving_pool_n ?? "—"} · bottom ${skipStackFilters.bottom_n ?? 7} by Win summary% are skipped on trade-hourly. ` +
          `Uses full history (not page filters), same as Lambda.`,
      },
    ],
  ];
  const cards = insightSections
    .map(([title, stats, opts]) => renderGroupTable(title, stats, opts))
    .join("");
  const highlights = `
    <section class="insight-card insight-highlights">
      <h3>Highlights</h3>
      <div class="summary-grid">
        <div><span class="summary-label">Sold regret rate</span><span class="summary-value">${(data.stop_loss_regret_rate_pct ?? 0).toFixed(1)}%</span></div>
        <div><span class="summary-label">Sold would-lose rate</span><span class="summary-value">${(data.sold_would_lose_rate_pct ?? 0).toFixed(1)}%</span></div>
        <div><span class="summary-label">Avg sell %</span><span class="summary-value">${data.avg_sell_value_pct != null ? `${data.avg_sell_value_pct.toFixed(1)}%` : "—"}</span></div>
        <div><span class="summary-label">Avg win P&amp;L</span><span class="summary-value">$${((data.avg_pnl_by_result || {}).win ?? 0).toFixed(2)}</span></div>
        <div><span class="summary-label">Avg loss P&amp;L</span><span class="summary-value">$${((data.avg_pnl_by_result || {}).loss ?? 0).toFixed(2)}</span></div>
        <div><span class="summary-label">Avg sold P&amp;L</span><span class="summary-value">$${((data.avg_pnl_by_result || {}).sold ?? 0).toFixed(2)}</span></div>
      </div>
    </section>`;
  container.innerHTML = `${highlights}<div class="insight-grid">${cards}</div>`;
  container.querySelectorAll(".insight-sort").forEach((th) => {
    th.addEventListener("click", () => {
      const title = th.dataset.insight;
      const key = th.dataset.key;
      const state = insightSortState[title] || { key: "count", asc: false };
      if (state.key === key) state.asc = !state.asc;
      else {
        state.key = key;
        state.asc = key === "group";
      }
      if (title === "By local buy time") {
        state.groupSort = state.key === "group" && state.asc ? "time" : "value";
      }
      insightSortState[title] = state;
      renderInsights(data);
    });
  });
}

function renderTable(records) {
  const body = document.getElementById("trade-body");
  body.innerHTML = records
    .map((r) => {
      const temp = extractTempLabel(r.bought_temp);
      const hk = fmtHk(r.bought_at, r.bought_at_hk);
      const soldHk = fmtHk(r.sold_at, r.sold_at_hk);
      const local = fmtLocal(r.bought_at, r.city, r.bought_at_local);
      const sharesCls = r.shares_over_target ? "shares-warn" : "";
      const sharesTitle = r.shares_over_target
        ? ` title="Over target ${r.share_count_target ?? 10}"`
        : "";
      const outcome = outcomeValue(r);
      return `
    <tr>
      <td>${r.date}</td>
      <td class="sticky-city">${r.city}</td>
      <td><a class="event-link" href="https://polymarket.com/event/${r.event_slug}" target="_blank" rel="noopener">${temp}</a></td>
      <td>${fmtForecastTemp(r)}</td>
      <td>${fmtForecastTemp(r, { wu: true })}</td>
      <td>${fmtForecastDelta(r)}</td>
      <td>${fmtForecastVsWin(r)}</td>
      <td>${r.trade_window || "—"}</td>
      <td>${hk}</td>
      <td>${soldHk}</td>
      <td>${local}</td>
      <td>$${(r.cost_basis_usd ?? 0).toFixed(2)}</td>
      <td class="${sharesCls}"${sharesTitle}>${r.shares}</td>
      <td>${r.buy_price?.toFixed(2) ?? "—"}</td>
      <td>${r.spread != null ? Number(r.spread).toFixed(3) : "—"}</td>
      <td>${r.yes_gap != null ? Number(r.yes_gap).toFixed(3) : "—"}</td>
      <td>${r.on_edge == null ? "—" : r.on_edge ? "Yes" : "No"}</td>
      <td>${resultBadge(r.result)}</td>
      <td>${fmtMoney(recordPnl(r))}</td>
      <td>${outcome != null ? fmtMoney(outcome) : "—"}</td>
      <td>${vsBoughtLabel(r)}</td>
      <td>${soldOutcomeLabel(r)}</td>
      <td>${r.sell_value_pct != null ? r.sell_value_pct.toFixed(1) + "%" : "—"}</td>
    </tr>`;
    })
    .join("");
}

function renderFilterSweep(data) {
  const container = document.getElementById("filter-sweep-content");
  if (!container) return;
  if (!data || !Array.isArray(data.stacks)) {
    container.innerHTML = `<p class="muted">No filter_sweep in trade_history.json — run <code>python -m src.main enrich-trade-history</code>.</p>`;
    return;
  }
  const rec = data.recommended;
  const train = data.train_dates || {};
  const oos = data.oos_dates || {};
  const target = data.target_win_summary_pct ?? 60;

  const glossary = `
    <div class="insight-card" style="margin-bottom:1rem">
      <h3>What these numbers mean</h3>
      <ul class="glossary-list">
        <li><strong>OOS</strong> = <em>out-of-sample</em>: trades on later dates held out of the “fit” window. A stack “passes ≥${target}% OOS” if win summary on that later period is still ≥${target}% (so it isn’t just overfitting the past).</li>
        <li><strong>Train</strong> = earlier dates used to rank timezone skips / judge the stack historically (about first 70% of distinct trade days).</li>
        <li><strong>denom</strong> = win-summary denominator: settled trades with ≥1 share (wins + losses + sold). Opens and dust (&lt;1 share) are excluded. <strong>n</strong> is all matching rows including opens.</li>
        <li><strong>Win summary%</strong> = win-summary wins ÷ denom (same rules as the main dashboard).</li>
        <li><strong>skip_bottom7_tz</strong> = bottom 7 timezones by win summary on <em>all</em> train trades (legacy research skip).</li>
        <li><strong>skip_bottom7_tz_surviving</strong> = bottom 7 on the <em>surviving</em> train pool (buy/spread stack), same ranking as live Lambda timezone skip.</li>
        <li><strong>spread_live / buy_live</strong> = live rules: missing spread allowed; buy in [YES_PRICE_MIN, YES_PRICE_MAX). The shipped mirror is <code>skip_bottom7_tz_surviving + spread_live + buy_live</code>.</li>
      </ul>
      <p class="insight-desc">
        Example split: <strong>Train</strong> ${train.from || "?"}→${train.to || "?"} (${train.n || 0} days)
        · <strong>OOS</strong> ${oos.from || "?"}→${oos.to || "?"} (${oos.n || 0} days).
        Filters are scored on train+all, then checked on OOS so a “good” stack still works on unseen days.
      </p>
    </div>`;

  const highlight = rec
    ? `<div class="insight-card insight-highlights">
        <h3>Recommended (loosest ≥${target}% OOS)</h3>
        <div class="summary-grid">
          <div><span class="summary-label">Stack</span><span class="summary-value">${rec.stack}</span></div>
          <div><span class="summary-label">All win summary</span><span class="summary-value">${rec.win_summary_pct}%</span></div>
          <div><span class="summary-label">All P&amp;L</span><span class="summary-value">$${Number(rec.pnl_usd).toFixed(2)}</span></div>
          <div><span class="summary-label">All n / denom</span><span class="summary-value">${rec.n} / ${rec.denom}</span></div>
          <div><span class="summary-label">OOS win summary</span><span class="summary-value">${rec.oos_win_summary_pct}%</span></div>
          <div><span class="summary-label">OOS P&amp;L</span><span class="summary-value">$${Number(rec.oos_pnl_usd).toFixed(2)}</span></div>
          <div><span class="summary-label">OOS denom</span><span class="summary-value">${rec.oos_denom}</span></div>
        </div>
        <p class="insight-desc">Loosest = most OOS denom among stacks that still clear ≥${target}% win summary on the OOS dates.</p>
      </div>`
    : `<p class="muted">No stack cleared ≥${target}% OOS with enough trades.</p>`;

  const columns = [
    { key: "stack", label: "Stack" },
    { key: "win_summary_pct", label: "Win summary%" },
    { key: "pnl_usd", label: "P&amp;L" },
    { key: "denom", label: "denom" },
    { key: "n", label: "n" },
    { key: "oos_win_summary_pct", label: "OOS win%" },
    { key: "oos_pnl_usd", label: "OOS P&amp;L" },
    { key: "oos_denom", label: "OOS denom" },
    { key: "oos_pass_60", label: `≥${target}% OOS` },
  ];

  const sorted = [...data.stacks].sort((a, b) => {
    const key = filterSweepSort.key;
    let av = a[key];
    let bv = b[key];
    if (key === "stack") {
      av = String(av || "");
      bv = String(bv || "");
      const cmp = av.localeCompare(bv);
      return filterSweepSort.asc ? cmp : -cmp;
    }
    if (typeof av === "boolean") av = av ? 1 : 0;
    if (typeof bv === "boolean") bv = bv ? 1 : 0;
    av = Number(av);
    bv = Number(bv);
    if (!Number.isFinite(av)) av = filterSweepSort.asc ? Infinity : -Infinity;
    if (!Number.isFinite(bv)) bv = filterSweepSort.asc ? Infinity : -Infinity;
    if (av === bv) return String(a.stack || "").localeCompare(String(b.stack || ""));
    return filterSweepSort.asc ? av - bv : bv - av;
  });

  const rows = sorted
    .slice(0, 40)
    .map((r) => {
      const pass = r.oos_pass_60 ? "pass" : "";
      return `<tr class="${pass}">
        <td>${r.stack}</td>
        <td>${r.win_summary_pct}%</td>
        <td>$${Number(r.pnl_usd).toFixed(1)}</td>
        <td>${r.denom}</td>
        <td>${r.n}</td>
        <td>${r.oos_win_summary_pct}%</td>
        <td>$${Number(r.oos_pnl_usd).toFixed(1)}</td>
        <td>${r.oos_denom}</td>
        <td>${r.oos_pass_60 ? "yes" : ""}</td>
      </tr>`;
    })
    .join("");

  const head = columns
    .map((col) => {
      const mark =
        filterSweepSort.key === col.key ? (filterSweepSort.asc ? " ▲" : " ▼") : "";
      return `<th class="insight-sort" data-sweep-key="${col.key}">${col.label}${mark}</th>`;
    })
    .join("");

  container.innerHTML = `${glossary}${highlight}
    <div class="table-wrap" style="padding:0">
      <table class="insight-table">
        <thead><tr>${head}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  container.querySelectorAll("th[data-sweep-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sweepKey;
      if (filterSweepSort.key === key) filterSweepSort.asc = !filterSweepSort.asc;
      else {
        filterSweepSort.key = key;
        filterSweepSort.asc = key === "stack";
      }
      renderFilterSweep(filterSweepData);
    });
  });
}

function skippedSortMark(tableId, key) {
  const state = skippedSortState[tableId];
  if (!state || state.key !== key) return "";
  return state.asc ? " ▲" : " ▼";
}

function sortSkippedRows(rows, tableId, defaultKey = "count") {
  if (!skippedSortState[tableId]) {
    skippedSortState[tableId] = { key: defaultKey, asc: defaultKey === "group" || defaultKey === "reason" };
  }
  const state = skippedSortState[tableId];
  return [...rows].sort((a, b) => {
    let av = a[state.key];
    let bv = b[state.key];
    if (av == null) av = "";
    if (bv == null) bv = "";
    if (typeof av === "number" && typeof bv === "number") {
      return state.asc ? av - bv : bv - av;
    }
    if (typeof av === "boolean") av = av ? 1 : 0;
    if (typeof bv === "boolean") bv = bv ? 1 : 0;
    const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
    return state.asc ? cmp : -cmp;
  });
}

function bindSkippedSortHeaders(container) {
  container.querySelectorAll("th[data-skipped-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const tableId = th.dataset.skippedTable;
      const key = th.dataset.skippedSort;
      const state = skippedSortState[tableId] || { key, asc: false };
      if (state.key === key) state.asc = !state.asc;
      else {
        state.key = key;
        state.asc = key === "group" || key === "reason" || key === "run_at" || key === "city";
      }
      skippedSortState[tableId] = state;
      renderSkippedAnalysis(skippedAnalysisData);
    });
  });
}

function renderSkippedSortableTable({
  tableId,
  title,
  description,
  columns,
  rows,
  emptyText,
  defaultSortKey = "count",
  stickyCity = false,
  cityColKey = "city",
}) {
  const sorted = sortSkippedRows(rows || [], tableId, defaultSortKey);
  const head = columns
    .map((col) => {
      const sticky =
        stickyCity && col.key === cityColKey
          ? ' class="insight-sort sticky-city"'
          : ' class="insight-sort"';
      return `<th${sticky} data-skipped-table="${tableId}" data-skipped-sort="${col.key}">${col.label}${skippedSortMark(tableId, col.key)}</th>`;
    })
    .join("");
  const body = sorted.length
    ? sorted
        .map((r) => {
          const cells = columns
            .map((col) => {
              const sticky =
                stickyCity && col.key === cityColKey ? ' class="sticky-city"' : "";
              const raw = r[col.key];
              let text = raw;
              if (col.fmt) text = col.fmt(raw, r);
              else if (raw == null || raw === "") text = "—";
              return `<td${sticky}>${text}</td>`;
            })
            .join("");
          const tag = r.filter_costly ? "costly" : r.filter_helpful ? "helpful" : "";
          return `<tr class="${tag}">${cells}</tr>`;
        })
        .join("")
    : `<tr><td colspan="${columns.length}">${emptyText || "No data"}</td></tr>`;
  return `
    <h3 style="margin:1rem 0 0.5rem;font-size:0.95rem;color:var(--muted)">${title}</h3>
    ${description ? `<p class="muted" style="margin:0 0 0.5rem">${description}</p>` : ""}
    <div class="table-wrap" style="padding:0">
      <table class="insight-table">
        <thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function fmtPctCell(v) {
  return v != null && Number.isFinite(Number(v)) ? `${Number(v)}%` : "—";
}

function renderSkippedAnalysis(data) {
  const container = document.getElementById("skipped-content");
  if (!container) return;
  if (!data || !Array.isArray(data.by_reason)) {
    container.innerHTML = `<p class="muted">No skipped_analysis in trade_history.json — run <code>python -m src.main enrich-trade-history</code>.</p>`;
    return;
  }
  const shares = data.share_count_assumed ?? 10;
  const fmtPnl = (v) => {
    if (v == null || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}$${n.toFixed(2)}`;
  };
  const fmtPrice = (v) =>
    v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(3) : "—";

  const fc = data.forecast_compare || {};
  const overall = fc.overall || {};

  const header = `
    <div class="insight-card insight-highlights">
      <h3>Skip overview</h3>
      <div class="summary-grid">
        <div><span class="summary-label">Total skips</span><span class="summary-value">${data.total_skips ?? 0}</span></div>
        <div><span class="summary-label">With event slug</span><span class="summary-value">${data.with_slug ?? "—"}</span></div>
        <div><span class="summary-label">With price</span><span class="summary-value">${data.with_price ?? "—"}</span></div>
        <div><span class="summary-label">Resolved outcomes</span><span class="summary-value">${data.resolved_skips ?? 0}</span></div>
        <div><span class="summary-label">Would-have-won</span><span class="summary-value">${data.would_have_won_total ?? 0}</span></div>
        <div><span class="summary-label">Would-have-won %</span><span class="summary-value">${data.would_have_won_pct != null ? `${data.would_have_won_pct}%` : "—"}</span></div>
        <div><span class="summary-label">Total P&amp;L if bought</span><span class="summary-value">${fmtPnl(data.total_pnl_if_bought)}</span></div>
        <div><span class="summary-label">P&amp;L sample n</span><span class="summary-value">${data.pnl_n ?? 0}</span></div>
        <div><span class="summary-label">Resolutions fetched</span><span class="summary-value">${data.resolutions_fetched ?? 0}</span></div>
      </div>
      <p class="muted">Joins skips → event_slug (row / event_id / city+date reconstruct) → resolutions_cache + trade_history winners.</p>
      <p class="insight-desc">P&amp;L if bought assumes ${shares} shares held to resolution: win = shares×(1−price), loss = −shares×price. Avg price uses logged selection_price when present, else nearest selection snapshot / events file Yes %. Costly = skip reason often would have won (≥50% among resolved). Helpful = usually would have lost (&lt;40%).</p>
    </div>`;

  const reasonSection = renderSkippedSortableTable({
    tableId: "skip-by-reason",
    title: "By skip reason",
    description: null,
    defaultSortKey: "count",
    columns: [
      { key: "reason", label: "Reason" },
      { key: "count", label: "Count" },
      { key: "with_temp", label: "With temp" },
      { key: "resolved", label: "Resolved" },
      { key: "would_have_won", label: "Would win" },
      { key: "would_have_lost", label: "Would lose" },
      { key: "would_have_won_pct", label: "Would-win%", fmt: fmtPctCell },
      { key: "avg_price", label: "Avg price", fmt: (v) => fmtPrice(v) },
      { key: "total_pnl_if_bought", label: "Total P&L if bought", fmt: (v) => fmtPnl(v) },
      {
        key: "note",
        label: "Note",
        fmt: (_v, r) => (r.filter_costly ? "costly" : r.filter_helpful ? "helpful" : ""),
      },
    ],
    rows: (data.by_reason || []).map((r) => ({ ...r, note: r.filter_costly ? 1 : r.filter_helpful ? 0 : -1 })),
    emptyText: "No skip reasons",
  });

  const bandCols = [
    { key: "reason", label: "Buy $ band" },
    { key: "count", label: "Count" },
    { key: "resolved", label: "Resolved" },
    { key: "would_have_won", label: "Would win" },
    { key: "would_have_lost", label: "Would lose" },
    { key: "would_have_won_pct", label: "Would-win%", fmt: fmtPctCell },
    { key: "avg_price", label: "Avg price", fmt: (v) => fmtPrice(v) },
    { key: "total_pnl_if_bought", label: "Total P&L if bought", fmt: (v) => fmtPnl(v) },
  ];

  const bandSection = renderSkippedSortableTable({
    tableId: "skip-ypm-all",
    title: "yes_price_max by buy $ (0.05 band) — all skips",
    description:
      "Every yes_price_max skip, including the same market re-skipped later in the day (e.g. London 27°C at 14:15 and again at 14:45 both count).",
    defaultSortKey: "reason",
    columns: bandCols,
    rows: data.yes_price_max_by_buy_band || [],
    emptyText: "No yes_price_max skips with price",
  });

  const firstSkipRows = Array.isArray(data.yes_price_max_by_buy_band_first_skip)
    ? data.yes_price_max_by_buy_band_first_skip
    : null;
  const firstBandSection = renderSkippedSortableTable({
    tableId: "skip-ypm-first",
    title: "yes_price_max by buy $ (0.05 band) — first skip only",
    description:
      "Same columns as all-skips, but only the earliest yes_price_max skip per market. Example: London 27°C first skipped at 14:15 counts; the same market skipped again at 14:45 does not. (Other skip reasons do not block the first yes_price_max slot.)",
    defaultSortKey: "reason",
    columns: bandCols,
    rows: firstSkipRows || [],
    emptyText: firstSkipRows == null
      ? "Missing in trade_history.json — hard-refresh the page (or re-run enrich-trade-history / wait for hourly sync)."
      : "No first-skip yes_price_max rows",
  });

  const fcCols = [
    { key: "group", label: "Group" },
    { key: "count", label: "Count" },
    { key: "resolved", label: "Resolved" },
    { key: "would_have_won_pct", label: "Would-win% (result)", fmt: fmtPctCell },
    { key: "om_match_pct", label: "OM match win%", fmt: fmtPctCell },
    { key: "om_match_resolved", label: "OM n" },
    { key: "wu_match_pct", label: "WU match win%", fmt: fmtPctCell },
    { key: "wu_match_resolved", label: "WU n" },
    { key: "avg_price", label: "Avg price", fmt: (v) => fmtPrice(v) },
    { key: "total_pnl_if_bought", label: "P&L if bought", fmt: (v) => fmtPnl(v) },
  ];

  const fcSlot = Array.isArray(fc.by_local_slot) ? fc.by_local_slot : null;
  const fcReason = Array.isArray(fc.by_reason) ? fc.by_reason : null;
  const fcPrice = Array.isArray(fc.by_price_band) ? fc.by_price_band : null;
  const fcSpread = Array.isArray(fc.by_spread_band) ? fc.by_spread_band : null;
  const fcMissingHint =
    "Missing in trade_history.json — hard-refresh (or re-run enrich-trade-history / wait for hourly sync).";

  const forecastSection = `
    <h3 style="margin:1rem 0 0.5rem;font-size:0.95rem;color:var(--muted)">Forecast compare (skipped)</h3>
    <div class="insight-card">
      <div class="summary-grid">
        <div><span class="summary-label">With Open-Meteo / primary</span><span class="summary-value">${fc.with_forecast ?? 0}</span></div>
        <div><span class="summary-label">With WU scrape</span><span class="summary-value">${fc.with_wu ?? 0}</span></div>
        <div><span class="summary-label">Would-win% (result)</span><span class="summary-value">${fmtPctCell(overall.would_have_won_pct)}</span></div>
        <div><span class="summary-label">OM match win%</span><span class="summary-value">${fmtPctCell(overall.om_match_pct)}</span></div>
        <div><span class="summary-label">WU match win%</span><span class="summary-value">${fmtPctCell(overall.wu_match_pct)}</span></div>
        <div><span class="summary-label">OM↔WU agree ≤1°C</span><span class="summary-value">${fc.om_wu_agree_within_1c_pct != null ? `${fc.om_wu_agree_within_1c_pct}%` : "—"}</span></div>
        <div><span class="summary-label">Avg |Δ| would-win</span><span class="summary-value">${fc.avg_abs_delta_would_win_c != null ? `${fc.avg_abs_delta_would_win_c}°C` : "—"}</span></div>
        <div><span class="summary-label">Avg |Δ| would-lose</span><span class="summary-value">${fc.avg_abs_delta_would_lose_c != null ? `${fc.avg_abs_delta_would_lose_c}°C` : "—"}</span></div>
      </div>
      <p class="muted" style="margin:0.5rem 0 0">
        Uses only skips that have a recorded forecast (Open-Meteo and/or WU).
        <strong>Would-win% (result)</strong> = skipped temp bucket equals the event’s actual winning temp.
        <strong>OM / WU match win%</strong> = that forecast falls in the winning bucket.
      </p>
    </div>
    ${renderSkippedSortableTable({
      tableId: "fc-slot",
      title: "Forecast match by local time slot",
      description: "Skip run_at converted to city local time (15-min bands in the trade window).",
      defaultSortKey: "group",
      columns: fcCols,
      rows: fcSlot || [],
      emptyText: fcSlot == null ? fcMissingHint : "No local-slot groups",
    })}
    ${renderSkippedSortableTable({
      tableId: "fc-reason",
      title: "Forecast match by skip reason",
      defaultSortKey: "count",
      columns: fcCols,
      rows: fcReason || [],
      emptyText: fcReason == null ? fcMissingHint : "No reason groups",
    })}
    ${renderSkippedSortableTable({
      tableId: "fc-price",
      title: "Forecast match by buy $ band",
      defaultSortKey: "group",
      columns: fcCols,
      rows: fcPrice || [],
      emptyText: fcPrice == null ? fcMissingHint : "No price-band groups",
    })}
    ${renderSkippedSortableTable({
      tableId: "fc-spread",
      title: "Forecast match by spread band",
      defaultSortKey: "group",
      columns: fcCols,
      rows: fcSpread || [],
      emptyText: fcSpread == null ? fcMissingHint : "No spread-band groups",
    })}`;

  const recent = (data.recent_skips || data.samples || []).slice(0, 15).map((s) => {
    const rowForFmt = { ...s, bought_temp: s.temp || "" };
    return {
      ...s,
      run_at_short: (s.run_at || "").slice(0, 16),
      forecast_fmt: fmtForecastTemp(rowForFmt),
      wu_fmt: fmtForecastTemp(rowForFmt, { wu: true }),
      delta_fmt: fmtForecastDelta(rowForFmt),
      whw_label:
        s.would_have_won === true ? "won" : s.would_have_won === false ? "lost" : "—",
      om_vs_win: fmtForecastVsWin({ ...rowForFmt, winning_temp: s.winning_temp }),
    };
  });

  const recentSection = renderSkippedSortableTable({
    tableId: "skip-recent",
    title: "Last 15 skipped trades",
    defaultSortKey: "run_at",
    stickyCity: true,
    columns: [
      { key: "run_at", label: "Run at", fmt: (_v, r) => r.run_at_short || "—" },
      { key: "city", label: "City" },
      { key: "reason", label: "Reason" },
      { key: "temp", label: "Temp" },
      { key: "forecast_fmt", label: "Forecast", fmt: (_v, r) => r.forecast_fmt },
      { key: "wu_fmt", label: "WU forecast", fmt: (_v, r) => r.wu_fmt },
      { key: "delta_fmt", label: "Δ forecast", fmt: (_v, r) => r.delta_fmt },
      { key: "om_vs_win", label: "Forecast vs win", fmt: (_v, r) => r.om_vs_win },
      { key: "selection_price", label: "Price", fmt: (v) => fmtPrice(v) },
      { key: "whw_label", label: "Would have", fmt: (_v, r) => r.whw_label },
      { key: "pnl_if_bought", label: "P&L if bought", fmt: (v) => fmtPnl(v) },
    ],
    rows: recent,
    emptyText: "No skipped rows",
  });

  container.innerHTML = `${header}
    ${reasonSection}
    ${bandSection}
    ${firstBandSection}
    ${forecastSection}
    ${recentSection}`;
  bindSkippedSortHeaders(container);
}

function render() {
  const filtered = sortRecords(applyFilters(allRecords));
  renderSummary(filtered);
  renderTable(filtered);
  renderWinLossFingerprint(filtered);
  renderInsights(computeInsights(filtered, { skipPoolRecords: allRecords }));
  renderFilterSweep(filterSweepData);
  renderSkippedAnalysis(skippedAnalysisData);
}

function populateCityFilter() {
  const cities = [...new Set(allRecords.map((r) => r.city).filter(Boolean))].sort();
  const sel = document.getElementById("filter-city");
  const groups = new Map();
  for (const city of cities) {
    const group = timezoneGroup(city);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(city);
  }
  for (const [group, groupCities] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group;
    for (const city of groupCities) {
      const opt = document.createElement("option");
      opt.value = city;
      opt.textContent = city;
      optgroup.appendChild(opt);
    }
    sel.appendChild(optgroup);
  }
}

function populateTimezoneFilter() {
  const sel = document.getElementById("filter-timezone");
  const zones = [...new Set(allRecords.map((r) => timezoneGroup(r.city)).filter(Boolean))].sort();
  for (const zone of zones) {
    const opt = document.createElement("option");
    opt.value = zone;
    opt.textContent = zone;
    sel.appendChild(opt);
  }
}

function populateLocalTimeFilter() {
  const sel = document.getElementById("filter-local-time");
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All";
  sel.appendChild(all);

  const before = document.createElement("option");
  before.value = "before-12:00";
  before.textContent = "Before 12:00";
  sel.appendChild(before);

  const start = 12 * 60;
  const end = 16 * 60;
  for (let mins = start; mins < end; mins += 15) {
    const next = mins + 15;
    const label = `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}-${String(Math.floor(next / 60)).padStart(2, "0")}:${String(next % 60).padStart(2, "0")}`;
    const opt = document.createElement("option");
    opt.value = label;
    opt.textContent = label.replace("-", "–");
    sel.appendChild(opt);
  }
  const after = document.createElement("option");
  after.value = "after-16:00";
  after.textContent = "After 16:00";
  sel.appendChild(after);
}

async function loadData() {
  const [dataResp, tzResp, resResp] = await Promise.all([
    fetch(DATA_URL, { cache: "no-store" }),
    fetch(TZ_URL, { cache: "no-store" }).catch(() => null),
    fetch(RESOLUTIONS_URL, { cache: "no-store" }).catch(() => null),
  ]);
  if (!dataResp.ok) throw new Error(`Failed to load ${DATA_URL}: ${dataResp.status}`);
  if (tzResp?.ok) cityTimezones = await tzResp.json();

  const data = await dataResp.json();
  let records = data.records || [];
  if (resResp?.ok) {
    const resolutions = await resResp.json();
    records = U.enrichRecordsWithResolutions(records, resolutions);
  }
  allRecords = records;
  filterSweepData = data.filter_sweep || null;
  skippedAnalysisData = data.skipped_analysis || null;
  const denylist = data.timezone_skip_denylist || null;
  if (denylist) {
    skipStackFilters = {
      yes_price_min: denylist.yes_price_min ?? 0.45,
      yes_price_max: denylist.yes_price_max ?? 0.6,
      spread_max: denylist.spread_max ?? 0.05,
      bottom_n: denylist.bottom_n ?? 7,
    };
  }
  document.getElementById("sync-meta").textContent =
    `Synced ${data.synced_at || "?"} · ${allRecords.length} trades · wallet ${(data.wallet || "").slice(0, 10)}…`;
  populateLocalTimeFilter();
  populateTimezoneFilter();
  populateCityFilter();
  render();
}

document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (sortKey === key) sortAsc = !sortAsc;
    else {
      sortKey = key;
      sortAsc = true;
    }
    render();
  });
});

document.querySelectorAll(".filters select, .filters input").forEach((el) => {
  el.addEventListener("change", render);
});

loadData().catch((err) => {
  document.getElementById("sync-meta").textContent = `Error: ${err.message}`;
});
