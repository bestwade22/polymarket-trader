/** Trade history dashboard — loads data/analysis/trade_history.json */

const DATA_URL = new URL("../data/analysis/trade_history.json", window.location.href).href;

let allRecords = [];
let summary = {};
let insights = {};
let sortKey = "bought_at";
let sortAsc = false;

function buyPriceBand(price) {
  if (price < 0.5) return "<0.50";
  if (price <= 0.6) return "0.50–0.60";
  return ">0.60";
}

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.slice(0, 16).replace("T", " ");
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

function getFilters() {
  return {
    result: document.getElementById("filter-result").value,
    city: document.getElementById("filter-city").value,
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
    if (av == null) av = "";
    if (bv == null) bv = "";
    if (typeof av === "number" && typeof bv === "number") {
      return sortAsc ? av - bv : bv - av;
    }
    const cmp = String(av).localeCompare(String(bv));
    return sortAsc ? cmp : -cmp;
  });
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
  return s;
}

function renderTable(records) {
  const body = document.getElementById("trade-body");
  body.innerHTML = records
    .map(
      (r) => `
    <tr>
      <td>${r.date}</td>
      <td>${r.city}</td>
      <td><a class="event-link" href="https://polymarket.com/event/${r.event_slug}" target="_blank" rel="noopener">${r.bought_temp}</a></td>
      <td>${fmtDate(r.bought_at)}</td>
      <td>${fmtDate(r.sold_at)}</td>
      <td>${r.shares}</td>
      <td>${r.buy_price?.toFixed(2) ?? "—"}</td>
      <td>${resultBadge(r.result)}</td>
      <td>${fmtMoney(r.realized_pnl_usd ?? r.final_value_usd)}</td>
      <td>${r.winning_temp ?? "—"}</td>
      <td>${r.win_temp_vs_bought}</td>
      <td>${r.sell_value_pct != null ? r.sell_value_pct.toFixed(1) + "%" : "—"}</td>
    </tr>`
    )
    .join("");

  const fs = computeFilteredSummary(records);
  document.getElementById("summary-row").innerHTML = `
    <td colspan="12">
      Total: ${fs.total_count} |
      Win: ${fs.win_count} | Loss: ${fs.loss_count} | Sold: ${fs.sold_count} | Open: ${fs.open_count} |
      Win%: ${fs.win_pct}% |
      Cost: $${fs.total_cost_basis_usd.toFixed(2)} |
      P&amp;L: $${fs.total_realized_pnl_usd.toFixed(2)} |
      Sold-but-won: ${fs.sold_but_would_have_won_count}
    </td>`;
}

function render() {
  const filtered = sortRecords(applyFilters(allRecords));
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
  const resp = await fetch(DATA_URL);
  if (!resp.ok) throw new Error(`Failed to load ${DATA_URL}: ${resp.status}`);
  const data = await resp.json();
  allRecords = data.records || [];
  summary = data.summary || {};
  insights = data.insights || {};
  document.getElementById("sync-meta").textContent =
    `Synced ${data.synced_at || "?"} · ${allRecords.length} trades · wallet ${(data.wallet || "").slice(0, 10)}…`;
  document.getElementById("insights-content").textContent = JSON.stringify(insights, null, 2);
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
