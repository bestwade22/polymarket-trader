/** Shared dashboard helpers: resolution enrichment, temp compare, win summary. */

window.DashUtils = (function () {
  function parseTemperatureBucket(title) {
    const raw = (title || "").trim();
    if (!raw) return null;
    const unit = /°F|[^a-z]F\b/i.test(raw) ? "F" : "C";

    let m = raw.match(/(\d+)[°]?[FC]\s+or\s+below/i);
    if (m) return { low: parseInt(m[1], 10), high: null, unit };

    m = raw.match(/(\d+)[°]?[FC]\s+or\s+higher/i);
    if (m) return { low: parseInt(m[1], 10), high: null, unit };

    m = raw.match(/(\d+)-(\d+)[°]?[FC]/i);
    if (m) return { low: parseInt(m[1], 10), high: parseInt(m[2], 10), unit };

    m = raw.match(/(\d+)[°]?[FC]\s*$/i);
    if (m) {
      const t = parseInt(m[1], 10);
      return { low: t, high: t, unit };
    }

    m = raw.match(/be\s+(\d+)[°]?([FC])\b/i);
    if (m) return { low: parseInt(m[1], 10), high: parseInt(m[1], 10), unit: m[2].toUpperCase() };

    return null;
  }

  function compareTempBuckets(boughtTitle, winningTitle) {
    const bought = parseTemperatureBucket(boughtTitle);
    const winning = parseTemperatureBucket(winningTitle);
    if (!bought || !winning) return "unknown";
    if (bought.low === winning.low) return "same";
    return winning.low > bought.low ? "higher" : "lower";
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

  function enrichRecordsWithResolutions(records, resolutionCache) {
    if (!resolutionCache?.events) return records;
    const events = resolutionCache.events;
    return records.map((r) => {
      if (r.win_temp_vs_bought !== "unknown" && r.winning_temp) return r;
      const res = events[r.event_slug];
      if (!res?.winning_temp) return r;
      const winning_temp = res.winning_temp;
      const win_temp_vs_bought = compareTempBuckets(r.bought_temp, winning_temp);
      return { ...r, winning_temp, win_temp_vs_bought, _resolution_enriched: true };
    });
  }

  function recordPnl(r) {
    if (r.realized_pnl_usd != null) return r.realized_pnl_usd;
    return r.final_value_usd;
  }

  function effectiveVs(r) {
    return r.win_temp_vs_bought || "unknown";
  }

  function isSoldWin(r) {
    if (r.result !== "sold") return false;
    const pnl = recordPnl(r);
    if (pnl == null || pnl < 0) return false;
    return effectiveVs(r) === "same";
  }

  function isSoldLose(r) {
    if (r.result !== "sold") return false;
    const pnl = recordPnl(r);
    if (pnl == null || pnl >= 0) return false;
    const vs = effectiveVs(r);
    return vs === "higher" || vs === "lower";
  }

  function isSoldWouldWin(r) {
    if (r.result !== "sold") return false;
    const pnl = recordPnl(r);
    if (pnl == null || pnl >= 0) return false;
    return effectiveVs(r) === "same";
  }

  function isSoldWouldLose(r) {
    if (r.result !== "sold") return false;
    const pnl = recordPnl(r);
    if (pnl == null || pnl < 0) return false;
    const vs = effectiveVs(r);
    return vs === "higher" || vs === "lower";
  }

  function isPnlInferredWin(r) {
    if (r.result !== "sold") return false;
    if (effectiveVs(r) !== "unknown") return false;
    const pnl = recordPnl(r);
    return pnl != null && pnl >= 0;
  }

  function isUnknownPnlInferredLose(r) {
    // Sold + unknown win vs bought + unknown/null P&L → lose in win summary.
    // Opens are ignored (not in win-summary denom).
    if (r.result === "open") return false;
    if (effectiveVs(r) !== "unknown") return false;
    return recordPnl(r) == null;
  }

  function countsInWinSummary(r) {
    if (r.result === "win") return true;
    if (r.result !== "sold") return false;
    if (isSoldWin(r) || isSoldWouldLose(r) || isPnlInferredWin(r)) return true;
    return false;
  }

  function countsInWinSummaryDenom(r) {
    // Same as classic settled: ignore opens.
    return r.result === "win" || r.result === "loss" || r.result === "sold";
  }

  function computeWinSummaryParts(records) {
    let wins = 0;
    let soldWins = 0;
    let wouldLose = 0;
    let pnlInferred = 0;
    let unknownLose = 0;
    for (const r of records) {
      if (r.result === "win") wins += 1;
      else if (r.result === "sold") {
        if (isSoldWin(r)) soldWins += 1;
        else if (isSoldWouldLose(r)) wouldLose += 1;
        else if (isPnlInferredWin(r)) pnlInferred += 1;
        else if (isUnknownPnlInferredLose(r)) unknownLose += 1;
      }
    }
    const total = wins + soldWins + wouldLose + pnlInferred;
    return { wins, soldWins, wouldLose, pnlInferred, unknownLose, total };
  }

  function winSummaryBreakdownLabel(parts) {
    const bits = [];
    if (parts.wins) bits.push(`win ${parts.wins}`);
    if (parts.soldWins) bits.push(`sold win ${parts.soldWins}`);
    if (parts.wouldLose) bits.push(`would lose ${parts.wouldLose}`);
    if (parts.pnlInferred) bits.push(`pnl+ ${parts.pnlInferred}`);
    const winBits = bits.length ? bits.join(" + ") : "0";
    const loseNote = parts.unknownLose
      ? ` · unknown/no-P&L as lose ${parts.unknownLose}`
      : "";
    return `${winBits}${loseNote}`;
  }

  function vsBoughtLabel(r) {
    const bought = extractTempLabel(r.bought_temp);
    const won = r.winning_temp || "?";
    const vs = effectiveVs(r);
    const map = {
      higher: `higher (${bought}→${won})`,
      lower: `lower (${bought}→${won})`,
      same: `same (${bought})`,
      unknown: "unknown",
    };
    let label = map[vs] || vs;
    if (vs === "unknown" && isPnlInferredWin(r)) {
      label += " (pnl+→win)";
    } else if (isUnknownPnlInferredLose(r)) {
      label += " (→lose)";
    }
    return label;
  }

  function fmtSellLocalHHMM(iso, city, cityTimezones, fallbackLocal) {
    if (fallbackLocal) {
      const compact = fallbackLocal.replace(":", "");
      if (/^\d{4}$/.test(compact)) return compact;
    }
    if (!iso || !cityTimezones) return "";
    const tz = cityTimezones[city];
    if (!tz) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
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
    const h = String(parts.hour).padStart(2, "0");
    const m = String(parts.minute).padStart(2, "0");
    return `${h}${m}`;
  }

  function fmtBuySoldLocalTimes(r, cityTimezones) {
    const buy = r.sample_time_local || r.bought_at_local || "";
    if (!buy) return "—";
    let sell = "";
    if (r.sold_at) {
      sell = fmtSellLocalHHMM(r.sold_at, r.city, cityTimezones, r.sold_at_local);
    }
    return sell ? `${buy},${sell}` : buy;
  }

  function buildRealTradeIndex(records) {
    const map = {};
    for (const r of records || []) {
      if (r.event_slug) map[r.event_slug] = r;
    }
    return map;
  }

  function realBoughtLabel(simRec, realTradesByEvent) {
    const real = realTradesByEvent[simRec.event_slug];
    if (!real) return "not";
    const simTemp = extractTempLabel(simRec.bought_temp);
    const realTemp = extractTempLabel(real.bought_temp);
    if (simTemp === realTemp) return "same";
    return realTemp;
  }

  return {
    parseTemperatureBucket,
    compareTempBuckets,
    extractTempLabel,
    enrichRecordsWithResolutions,
    recordPnl,
    effectiveVs,
    isSoldWin,
    isSoldLose,
    isSoldWouldWin,
    isSoldWouldLose,
    isPnlInferredWin,
    isUnknownPnlInferredLose,
    countsInWinSummary,
    countsInWinSummaryDenom,
    computeWinSummaryParts,
    winSummaryBreakdownLabel,
    vsBoughtLabel,
    fmtSellLocalHHMM,
    fmtBuySoldLocalTimes,
    buildRealTradeIndex,
    realBoughtLabel,
  };
})();
