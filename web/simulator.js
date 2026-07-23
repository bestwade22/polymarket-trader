/** Trade strategy simulator — loads data/analysis/sim_trade_history.json */

const DATA_URL = new URL("../data/analysis/sim_trade_history.json", window.location.href).href;
const REAL_TRADE_URL = new URL("../data/analysis/trade_history.json", window.location.href).href;
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
let realTradesByEvent = {};
let sortKey = "bought_at";
let sortAsc = false;
const insightSortState = {};

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

function realBoughtCell(r) {
  const label = U.realBoughtLabel(r, realTradesByEvent);
  if (label === "same") return '<span class="real-same">same</span>';
  if (label === "not") return '<span class="real-not">not</span>';
  return `<span class="real-other">${label}</span>`;
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
    if (sortKey === "local_times") {
      av = U.fmtBuySoldLocalTimes(a, cityTimezones);
      bv = U.fmtBuySoldLocalTimes(b, cityTimezones);
    }
    if (sortKey === "real_bought") {
      av = U.realBoughtLabel(a, realTradesByEvent);
      bv = U.realBoughtLabel(b, realTradesByEvent);
    }
    if (sortKey === "sold_outcome") {
      av = soldOutcomeKey(a);
      bv = soldOutcomeKey(b);
    }
    if (sortKey === "outcome_value_usd") {
      av = outcomeValue(a) ?? "";
      bv = outcomeValue(b) ?? "";
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

function computeInsights(records) {
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
    summary_by_city_timezone: groupInsightMetrics(records, (r) => timezoneGroup(r.city)),
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
    (title === "By competitive band" || title === "By open interest band") &&
    state.key === "group" &&
    state.asc
  ) {
    const sortKey =
      title === "By competitive band" ? competitiveBandSortKey : openInterestBandSortKey;
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
    if (title === "By competitive band" || title === "By open interest band") {
      insightSortState[title] = { key: "group", asc: true };
    }
  }
  const state = insightSortState[title];
  const entries = sortInsightEntries(title, data, limit);
  const header = columns.map(
    (col) =>
      `<th class="insight-sort" data-insight="${title}" data-key="${col.key}">${col.label}${state.key === col.key ? (state.asc ? " ▲" : " ▼") : ""}</th>`
  ).join("");
  const rows = entries.length
    ? entries
        .map(
          ([key, stats]) => `
            <tr>
              <td>${key}</td>
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
      const localTimes = U.fmtBuySoldLocalTimes(r, cityTimezones);
      const sharesCls = r.shares_over_target ? "shares-warn" : "";
      const sharesTitle = r.shares_over_target
        ? ` title="Over target ${r.share_count_target ?? 10}"`
        : "";
      const outcome = outcomeValue(r);
      return `
    <tr>
      <td>${r.date}</td>
      <td>${r.city}</td>
      <td><a class="event-link" href="https://polymarket.com/event/${r.event_slug}" target="_blank" rel="noopener">${temp}</a></td>
      <td>${localTimes}</td>
      <td>$${(r.cost_basis_usd ?? 0).toFixed(2)}</td>
      <td class="${sharesCls}"${sharesTitle}>${r.shares}</td>
      <td>${r.buy_price?.toFixed(2) ?? "—"}</td>
      <td>${r.gamma_proxy ? '<span class="badge badge-proxy" title="Gamma Yes % proxied from history series">Gamma≈%</span>' : "—"}</td>
      <td>${r.spread != null ? Number(r.spread).toFixed(3) : "—"}</td>
      <td>${r.competitive != null ? Number(r.competitive).toFixed(3) : "—"}</td>
      <td>${r.open_interest != null ? `$${Math.round(r.open_interest).toLocaleString()}` : "—"}</td>
      <td>${resultBadge(r.result)}</td>
      <td>${fmtMoney(recordPnl(r))}</td>
      <td>${outcome != null ? fmtMoney(outcome) : "—"}</td>
      <td>${vsBoughtLabel(r)}</td>
      <td>${realBoughtCell(r)}</td>
      <td>${soldOutcomeLabel(r)}</td>
      <td>${r.sell_value_pct != null ? r.sell_value_pct.toFixed(1) + "%" : "—"}</td>
    </tr>`;
    })
    .join("");
}

function render() {
  const filtered = sortRecords(applyFilters(allRecords));
  renderSummary(filtered);
  renderTable(filtered);
  renderInsights(computeInsights(filtered));
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
  const [dataResp, tzResp, resResp, realResp] = await Promise.all([
    fetch(DATA_URL),
    fetch(TZ_URL).catch(() => null),
    fetch(RESOLUTIONS_URL).catch(() => null),
    fetch(REAL_TRADE_URL).catch(() => null),
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

  if (realResp?.ok) {
    const realData = await realResp.json();
    realTradesByEvent = U.buildRealTradeIndex(realData.records || []);
  } else {
    realTradesByEvent = {};
  }
  const params = data.params || {};
  const paramBits = [
    params.strategy && `strategy=${params.strategy}`,
    params.from_date && params.to_date && `${params.from_date}→${params.to_date}`,
    params.sample_grid,
    params.yes_price_max != null && `YES_PRICE_MAX=${params.yes_price_max}`,
  ].filter(Boolean);
  document.getElementById("sync-meta").textContent =
    `Simulated ${data.synced_at || "?"} · ${allRecords.length} trades` +
    (paramBits.length ? ` · ${paramBits.join(" · ")}` : "") +
    (data.events_scanned != null ? ` · scanned ${data.events_scanned} events` : "");
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
