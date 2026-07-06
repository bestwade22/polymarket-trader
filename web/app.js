/** Trade history dashboard — loads data/analysis/trade_history.json */

const DATA_URL = new URL("../data/analysis/trade_history.json", window.location.href).href;
const TZ_URL = new URL("city_timezones.json", window.location.href).href;

let allRecords = [];
let cityTimezones = {};
let sortKey = "bought_at";
let sortAsc = false;

function buyPriceBand(price) {
  if (price < 0.4) return "<0.40";
  if (price < 0.5) return "0.40–0.50";
  if (price < 0.55) return "0.50–0.55";
  if (price < 0.6) return "0.55–0.60";
  if (price <= 0.7) return "0.60–0.70";
  return ">0.70";
}

function extractTempLabel(text) {
  if (!text) return "—";
  const range = text.match(/(\d+-\d+°[CF])/i);
  if (range) return range[1];
  const open = text.match(/(\d+°[CF]\s+or\s+(?:below|higher))/i);
  if (open) return open[1];
  const single = text.match(/(\d+°[CF])/i);
  return single ? single[1] : text;
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
  if (band === "after-15:00") return mins > 15 * 60;
  const [lo, hi] = parseRangeMinutes(band);
  return mins >= lo && mins <= hi;
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
  const bought = extractTempLabel(r.bought_temp);
  const won = r.winning_temp || "?";
  const map = {
    higher: `higher (${bought}→${won})`,
    lower: `lower (${bought}→${won})`,
    same: `same (${bought})`,
    unknown: "unknown",
  };
  return map[r.win_temp_vs_bought] || r.win_temp_vs_bought;
}

function soldWouldWinLabel(r) {
  if (r.result !== "sold") return "—";
  const bought = extractTempLabel(r.bought_temp);
  const won = r.winning_temp;
  if (r.sold_but_would_have_won) {
    return `<span class="regret-yes">Yes (${bought} = ${won || "?"})</span>`;
  }
  if (won) {
    return `<span class="regret-no">No (${bought} vs ${won})</span>`;
  }
  return `<span class="regret-no">No</span>`;
}

function getFilters() {
  return {
    result: document.getElementById("filter-result").value,
    city: document.getElementById("filter-city").value,
    localTime: document.getElementById("filter-local-time").value,
    vs: document.getElementById("filter-vs").value,
    regret: document.getElementById("filter-regret").value,
    price: document.getElementById("filter-price").value,
    dateFrom: document.getElementById("filter-date-from").value,
    dateTo: document.getElementById("filter-date-to").value,
  };
}

function applyFilters(records) {
  const f = getFilters();
  return records.filter((r) => {
    if (f.result && r.result !== f.result) return false;
    if (f.city && r.city !== f.city) return false;
    if (f.localTime) {
      const mins = cityLocalMinutes(r.bought_at, r.city, r.bought_at_local);
      if (!inLocalTimeRange(mins, f.localTime)) return false;
    }
    if (f.vs && r.win_temp_vs_bought !== f.vs) return false;
    if (f.regret === "true" && !r.sold_but_would_have_won) return false;
    if (f.regret === "false" && r.sold_but_would_have_won) return false;
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
    if (sortKey === "bought_at_hk") {
      av = a.bought_at_hk || a.bought_at;
      bv = b.bought_at_hk || b.bought_at;
    }
    if (sortKey === "bought_at_local") {
      av = cityLocalMinutes(a.bought_at, a.city, a.bought_at_local) ?? "";
      bv = cityLocalMinutes(b.bought_at, b.city, b.bought_at_local) ?? "";
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
    total_cost_basis_usd: 0,
    total_realized_pnl_usd: 0,
    sold_but_would_have_won_count: 0,
  };
  for (const r of records) {
    if (r.result === "win") s.win_count++;
    else if (r.result === "loss") s.loss_count++;
    else if (r.result === "sold") s.sold_count++;
    else if (r.result === "open") s.open_count++;
    s.total_cost_basis_usd += r.cost_basis_usd || 0;
    const pnl = r.realized_pnl_usd ?? r.final_value_usd;
    if (pnl != null) s.total_realized_pnl_usd += pnl;
    if (r.sold_but_would_have_won) s.sold_but_would_have_won_count++;
  }
  const settled = s.win_count + s.loss_count + s.sold_count;
  s.win_pct = settled ? Math.round((s.win_count / settled) * 1000) / 10 : 0;
  s.avg_bought_value =
    records.length ? s.total_cost_basis_usd / records.length : 0;
  s.avg_bought_time_hk = avgHkMinutes(records);
  return s;
}

function renderSummary(records) {
  const fs = computeFilteredSummary(records);
  document.getElementById("summary-content").innerHTML = `
    <div class="summary-grid">
      <div><span class="summary-label">Total</span><span class="summary-value">${fs.total_count}</span></div>
      <div><span class="summary-label">Win</span><span class="summary-value">${fs.win_count}</span></div>
      <div><span class="summary-label">Loss</span><span class="summary-value">${fs.loss_count}</span></div>
      <div><span class="summary-label">Sold</span><span class="summary-value">${fs.sold_count}</span></div>
      <div><span class="summary-label">Open</span><span class="summary-value">${fs.open_count}</span></div>
      <div><span class="summary-label">Win%</span><span class="summary-value">${fs.win_pct}%</span></div>
      <div><span class="summary-label">Avg bought value</span><span class="summary-value">$${fs.avg_bought_value.toFixed(2)}</span></div>
      <div><span class="summary-label">Avg bought time</span><span class="summary-value">${fs.avg_bought_time_hk || "—"}</span></div>
      <div><span class="summary-label">Total cost</span><span class="summary-value">$${fs.total_cost_basis_usd.toFixed(2)}</span></div>
      <div><span class="summary-label">Total P&amp;L</span><span class="summary-value">$${fs.total_realized_pnl_usd.toFixed(2)}</span></div>
      <div><span class="summary-label">Sold→would win</span><span class="summary-value">${fs.sold_but_would_have_won_count}</span></div>
    </div>`;
}

function renderTable(records) {
  const body = document.getElementById("trade-body");
  body.innerHTML = records
    .map((r) => {
      const temp = extractTempLabel(r.bought_temp);
      const hk = fmtHk(r.bought_at, r.bought_at_hk);
      const local = fmtLocal(r.bought_at, r.city, r.bought_at_local);
      return `
    <tr>
      <td>${r.date}</td>
      <td>${r.city}</td>
      <td><a class="event-link" href="https://polymarket.com/event/${r.event_slug}" target="_blank" rel="noopener">${temp}</a></td>
      <td>${hk}</td>
      <td>${local}</td>
      <td>$${(r.cost_basis_usd ?? 0).toFixed(2)}</td>
      <td>${r.sold_at ? r.sold_at.slice(0, 16).replace("T", " ") : "—"}</td>
      <td>${r.shares}</td>
      <td>${r.buy_price?.toFixed(2) ?? "—"}</td>
      <td>${resultBadge(r.result)}</td>
      <td>${fmtMoney(r.realized_pnl_usd ?? r.final_value_usd)}</td>
      <td>${r.winning_temp ?? "—"}</td>
      <td>${vsBoughtLabel(r)}</td>
      <td>${soldWouldWinLabel(r)}</td>
      <td>${r.sell_value_pct != null ? r.sell_value_pct.toFixed(1) + "%" : "—"}</td>
    </tr>`;
    })
    .join("");
}

function render() {
  const filtered = sortRecords(applyFilters(allRecords));
  renderSummary(filtered);
  renderTable(filtered);
}

function populateCityFilter() {
  const cities = [...new Set(allRecords.map((r) => r.city).filter(Boolean))].sort();
  const sel = document.getElementById("filter-city");
  for (const city of cities) {
    const opt = document.createElement("option");
    opt.value = city;
    opt.textContent = city;
    sel.appendChild(opt);
  }
}

async function loadData() {
  const [dataResp, tzResp] = await Promise.all([
    fetch(DATA_URL),
    fetch(TZ_URL).catch(() => null),
  ]);
  if (!dataResp.ok) throw new Error(`Failed to load ${DATA_URL}: ${dataResp.status}`);
  if (tzResp?.ok) cityTimezones = await tzResp.json();

  const data = await dataResp.json();
  allRecords = data.records || [];
  document.getElementById("sync-meta").textContent =
    `Synced ${data.synced_at || "?"} · ${allRecords.length} trades · wallet ${(data.wallet || "").slice(0, 10)}…`;
  document.getElementById("insights-content").textContent = JSON.stringify(
    data.insights || {},
    null,
    2
  );
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
