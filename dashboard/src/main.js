import { supabase } from "./supabase.js";
import {
  fetchPicks,
  fetchPerformance,
  fetchClv,
  fetchBankroll,
  fetchHealth,
} from "./api.js";

// ── config ─────────────────────────────────────────────────────
const APP_CONFIG = {
  featureFlags: {
    showAdvanced: true,
    showCLV: true,
    showReasons: true,
    showFactorDetail: true,
  },
};

const MARKETS = [
  "HR", "K", "HITS_1P", "HITS_LINE", "TB_LINE",
  "OUTS_RECORDED", "ML", "TOTAL", "F5_ML", "F5_TOTAL", "TEAM_TOTAL",
];

// ── state ──────────────────────────────────────────────────────
let currentPicks = [];
const favorites = JSON.parse(localStorage.getItem("favoriteMarkets") || "[]");
const saved = JSON.parse(localStorage.getItem("savedFilters") || "{}");

// ── init ───────────────────────────────────────────────────────
function init() {
  // Show demo banner when Supabase is not connected
  if (!supabase) {
    document.getElementById("demoBanner").classList.remove("hidden");
  }

  // Populate market dropdown
  const marketSel = document.getElementById("market");
  MARKETS.forEach((m) => marketSel.add(new Option(m, m)));

  // Set default date to today
  document.getElementById("date").value =
    new Date().toISOString().slice(0, 10);

  // Restore saved filters
  if (saved.market) marketSel.value = saved.market;
  if (saved.signal) document.getElementById("signal").value = saved.signal;
  if (!marketSel.value) marketSel.value = "HR";

  // Tab navigation
  document.querySelectorAll(".tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
      document.getElementById(btn.dataset.tab).classList.remove("hidden");
    })
  );

  // Filter change listeners
  ["date", "market", "signal", "book", "confidence", "edge", "lineup", "showAdvanced"].forEach(
    (id) => {
      document.getElementById(id).addEventListener("change", loadExplorer);
    }
  );

  // Load all tabs
  loadExplorer();
  loadPerformance();
  loadHealth();
  loadClv();
  loadBankroll();
}

// ── helpers ────────────────────────────────────────────────────
function saveFilters() {
  localStorage.setItem(
    "savedFilters",
    JSON.stringify({
      market: document.getElementById("market").value,
      signal: document.getElementById("signal").value,
    })
  );
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Market Explorer ────────────────────────────────────────────
async function loadExplorer() {
  APP_CONFIG.featureFlags.showAdvanced = document.getElementById("showAdvanced").checked;
  const date = document.getElementById("date").value;
  const market = document.getElementById("market").value || "HR";
  const signal = document.getElementById("signal").value;
  const book = document.getElementById("book").value;
  const confidence = document.getElementById("confidence").value;
  const minEdge = Number(document.getElementById("edge").value || 0) / 100;
  const lineupOnly = document.getElementById("lineup").checked;

  const loader = document.getElementById("explorerLoading");
  loader.classList.remove("hidden");

  try {
    currentPicks = await fetchPicks(date);
  } catch (err) {
    console.error("Failed to load picks:", err);
  }
  loader.classList.add("hidden");

  // Populate book dropdown from actual data
  const bookSel = document.getElementById("book");
  const currentBook = bookSel.value;
  const uniqueBooks = [...new Set(currentPicks.map((r) => r.sportsbook).filter(Boolean))];
  bookSel.innerHTML = '<option value="ALL">ALL</option>';
  uniqueBooks.forEach((b) => bookSel.add(new Option(b, b)));
  if (currentBook && currentBook !== "ALL") bookSel.value = currentBook;

  const filtered = currentPicks
    .filter((r) => r.market === market)
    .filter((r) => signal === "ALL" || r.signal === signal)
    .filter((r) => book === "ALL" || r.sportsbook === book)
    .filter((r) => confidence === "ALL" || r.confidence_band === confidence)
    .filter((r) => (r.edge_pct || 0) >= minEdge)
    .filter((r) => !lineupOnly || r.lineup_confirmed)
    .sort((a, b) => (b.model_score || 0) - (a.model_score || 0) || (b.edge_pct || 0) - (a.edge_pct || 0));

  // Exposure warnings
  const teamExposure = {};
  filtered.forEach((r) => {
    teamExposure[r.team] = (teamExposure[r.team] || 0) + 1;
  });
  const exposure = Object.entries(teamExposure).filter(([, n]) => n >= 2);
  const warn = document.getElementById("exposureWarnings");
  if (exposure.length) {
    warn.textContent = `Exposure warning: ${exposure.map(([team, n]) => `${team} x${n}`).join(", ")}.`;
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }

  // Render rows
  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="muted" style="text-align:center;padding:24px;">No picks for this filter combination</td></tr>';
    saveFilters();
    return;
  }

  filtered.forEach((row) => {
    const corrFlag =
      row.market === "ML"
        ? '<span class="tag">corr:team-total</span>'
        : '<span class="tag">corr:none</span>';
    const tr = document.createElement("tr");
    const playerDisplay = escapeHtml(row.player || "");
    const matchupDisplay = escapeHtml(row.matchup || "");
    const gameTimeDisplay = escapeHtml(row.game_time || "");
    const modelProbPct = row.model_probability != null ? Math.round(row.model_probability * 100) : "—";
    const impliedPct = row.implied_probability != null ? Math.round(row.implied_probability * 100) : "—";
    const edgePct = row.edge_pct != null ? (row.edge_pct * 100).toFixed(1) : "—";

    tr.innerHTML = `<td>${playerDisplay} <span class="tag">tier:${escapeHtml(row.visibility_tier || "FREE")}</span><div class="muted">${matchupDisplay} · ${gameTimeDisplay}</div>${corrFlag}</td>
      <td>${escapeHtml(row.side || "")} ${row.line ?? ""} (${escapeHtml(row.market)})</td>
      <td>${escapeHtml(String(row.odds || "—"))} <span class="muted">${escapeHtml(row.sportsbook || "")}</span></td>
      <td>${modelProbPct}% <span class="muted">score ${row.model_score || "—"}</span></td>
      <td>${impliedPct}%</td>
      <td>${edgePct}%</td>
      <td>${escapeHtml(row.signal || "")} <span class="muted">${escapeHtml(row.confidence_band || "")}</span></td>
      <td>${row.lineup_confirmed ? "Lineup ✅" : "Lineup ⚠️"} · ${escapeHtml(row.weather_flag || "")}</td>
      <td><button data-id="${row.id}" class="details-btn">Details</button> <button data-id="${row.id}" class="log-btn">Log Bet</button> <button data-id="${row.market}" class="fav-btn">☆</button></td>`;

    const details = document.createElement("tr");
    details.className = "hidden";
    details.id = `details-${row.id}`;

    let factorHtml = "";
    if (APP_CONFIG.featureFlags.showFactorDetail && row.factors) {
      factorHtml = Object.entries(row.factors)
        .map(
          ([k, v]) =>
            `<div>${escapeHtml(k)}: ${v}<div class="bar-wrap"><div class="bar" style="width:${Math.min(Number(v) || 0, 100)}%"></div></div></div>`
        )
        .join("");
    }
    const reasons = APP_CONFIG.featureFlags.showReasons
      ? (row.reasons || []).map((r) => escapeHtml(r)).join(" · ")
      : "PRO feature hidden";
    const risks = (row.risk || []).map((r) => escapeHtml(r)).join(" · ");

    details.innerHTML = `<td colspan="9"><strong>Reasons:</strong> ${reasons}<br/><strong>Risk:</strong> ${risks}<br/><strong>Context:</strong> ${escapeHtml(row.context || "")}${APP_CONFIG.featureFlags.showAdvanced ? `<hr/>${factorHtml}` : ""}</td>`;
    tbody.appendChild(tr);
    tbody.appendChild(details);
  });

  // Event handlers
  document.querySelectorAll(".details-btn").forEach((btn) => {
    btn.onclick = () => {
      document.getElementById(`details-${btn.dataset.id}`).classList.toggle("hidden");
    };
  });
  document.querySelectorAll(".log-btn").forEach((btn) => {
    btn.onclick = () => {
      const msg = document.getElementById("message");
      msg.textContent = `Queued bet log for row ${btn.dataset.id} (integration hook for /api/bets).`;
      msg.classList.remove("hidden");
    };
  });
  document.querySelectorAll(".fav-btn").forEach((btn) => {
    btn.onclick = () => {
      if (!favorites.includes(btn.dataset.id)) favorites.push(btn.dataset.id);
      localStorage.setItem("favoriteMarkets", JSON.stringify(favorites));
    };
  });

  saveFilters();
}

// ── Performance ────────────────────────────────────────────────
async function loadPerformance() {
  const perf = await fetchPerformance();
  document.getElementById("perfCards").innerHTML = [
    ["ROI", `${perf.roi}%`],
    ["Win %", `${perf.winRate}%`],
    ["Units", perf.units],
    ["Bets", perf.bets],
  ]
    .map(([k, v]) => `<div class='card'><div class='muted'>${k}</div><div>${v}</div></div>`)
    .join("");
  document.getElementById("signalRows").innerHTML = (perf.signal || [])
    .map((s) => `<tr><td>${escapeHtml(s.name)}</td><td>${s.bets}</td><td>${s.win}%</td><td>${s.roi}%</td></tr>`)
    .join("");
  document.getElementById("bucketRows").innerHTML = (perf.buckets || [])
    .map((b) => `<tr><td>${escapeHtml(b.label)}</td><td>${b.bets}</td><td>${b.roi}%</td></tr>`)
    .join("");
}

// ── Model Health ───────────────────────────────────────────────
async function loadHealth() {
  const h = await fetchHealth();
  document.getElementById("healthCards").innerHTML = [
    ["Stale Sources", (h.stale_sources || []).join(", ")],
    ["Missing Sources", (h.missing_sources || []).join(", ")],
    ["Data Freshness", "odds: 8m · weather: 22m · lineups: 11m"],
    ["Feature Flag", "advanced metrics toggleable"],
  ]
    .map(([k, v]) => `<div class='card'><div class='muted'>${k}</div><div>${v}</div></div>`)
    .join("");
  document.getElementById("calRows").innerHTML = (h.calibration || [])
    .map((r) => `<tr><td>${r[0]}</td><td>${r[1]}%</td><td>${r[2]}%</td></tr>`)
    .join("");
  document.getElementById("factorDiag").innerHTML = (h.factors || [])
    .map(
      (f) =>
        `<div>${escapeHtml(f[0])}<div class='bar-wrap'><div class='bar' style='width:${Math.round(f[1] * 100)}%'></div></div></div>`
    )
    .join("");
}

// ── CLV ────────────────────────────────────────────────────────
async function loadClv() {
  const c = await fetchClv();
  document.getElementById("clvCards").innerHTML = [
    ["Avg CLV", (c.avg * 100).toFixed(2) + "%"],
    ["Visibility", APP_CONFIG.featureFlags.showCLV ? "enabled" : "hidden"],
    ["Market Count", (c.by || []).length],
    ["Status", "tracking open\u2192close"],
  ]
    .map(([k, v]) => `<div class='card'><div class='muted'>${k}</div><div>${v}</div></div>`)
    .join("");
  document.getElementById("clvRows").innerHTML = (c.by || [])
    .map(
      (r) => `<tr><td>${escapeHtml(String(r[0]))}</td><td>${escapeHtml(String(r[1]))}</td><td>${(r[2] * 100).toFixed(2)}%</td><td>${r[3]}</td></tr>`
    )
    .join("");
}

// ── Bankroll ───────────────────────────────────────────────────
async function loadBankroll() {
  const b = await fetchBankroll();
  document.getElementById("bankrollCards").innerHTML = [
    ["Current Bankroll", b.current],
    ["Peak", b.peak],
    ["Max Drawdown", b.drawdown + "%"],
    ["Streak", b.streak],
  ]
    .map(([k, v]) => `<div class='card'><div class='muted'>${k}</div><div>${v}</div></div>`)
    .join("");
  document.getElementById("betRows").innerHTML = (b.bets || [])
    .map(
      (row) =>
        `<tr><td>${escapeHtml(String(row[0]))}</td><td>${escapeHtml(String(row[1]))}</td><td>${escapeHtml(String(row[2]))}</td><td>${escapeHtml(String(row[3]))}</td><td>${row[4]}</td><td>${escapeHtml(String(row[5]))}</td><td>${row[6]}</td></tr>`
    )
    .join("");
}

// ── boot ───────────────────────────────────────────────────────
init();
