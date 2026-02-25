/**
 * Data-fetching layer for the MLBPredicts dashboard.
 * Queries Supabase directly using the anon key + RLS.
 * Falls back to bundled demo data when Supabase is not configured.
 */
import { supabase } from "./supabase.js";
import { DEMO } from "./demo-data.js";

// ── helpers ────────────────────────────────────────────────────
function today() {
  return new Date().toISOString().slice(0, 10);
}

// ── Market Explorer picks ──────────────────────────────────────
export async function fetchPicks(gameDate) {
  if (!supabase) return DEMO.picks;

  const { data, error } = await supabase
    .from("model_scores")
    .select(
      `
      id, market, game_id, game_date, player_id, player_name,
      team_abbr, opponent_team_abbr, side, bet_type, line,
      model_score, model_prob, book_implied_prob, edge, signal,
      confidence_band, visibility_tier, factors_json, reasons_json,
      risk_flags_json, lineup_confirmed, entity_type
    `
    )
    .eq("game_date", gameDate || today())
    .eq("is_active", 1)
    .in("signal", ["BET", "LEAN", "FADE"])
    .order("model_score", { ascending: false });

  if (error) {
    console.error("fetchPicks error:", error);
    return DEMO.picks;
  }

  // Enrich with game info (matchup, game_time)
  const gameIds = [...new Set(data.map((r) => r.game_id))];
  const { data: games } = await supabase
    .from("games")
    .select("game_id, game_time, home_team, away_team")
    .in("game_id", gameIds);

  const gameMap = Object.fromEntries(
    (games || []).map((g) => [g.game_id, g])
  );

  // Enrich with sportsbook odds
  const { data: odds } = await supabase
    .from("market_odds")
    .select("game_id, market, player_id, team_abbr, sportsbook, price_american")
    .eq("game_date", gameDate || today())
    .eq("is_best_available", 1);

  const oddsMap = {};
  (odds || []).forEach((o) => {
    const key = `${o.game_id}:${o.market}:${o.player_id || ""}:${o.team_abbr || ""}`;
    oddsMap[key] = o;
  });

  return data.map((row) => {
    const game = gameMap[row.game_id] || {};
    const oddsKey = `${row.game_id}:${row.market}:${row.player_id || ""}:${row.team_abbr || ""}`;
    const bestOdds = oddsMap[oddsKey];

    const matchup = game.home_team && game.away_team
      ? `${game.away_team} @ ${game.home_team}`
      : "";

    return {
      id: row.id,
      game_date: row.game_date,
      market: row.market,
      sportsbook: bestOdds?.sportsbook || "—",
      game_time: game.game_time || "",
      matchup,
      team: row.team_abbr || "",
      player: row.player_name || row.team_abbr || "",
      side: row.side || "",
      line: row.line,
      odds: bestOdds ? formatOdds(bestOdds.price_american) : "—",
      model_probability: row.model_prob,
      implied_probability: row.book_implied_prob,
      edge_pct: row.edge,
      model_score: row.model_score,
      signal: row.signal,
      confidence_band: row.confidence_band,
      lineup_confirmed: !!row.lineup_confirmed,
      weather_flag: "OK",
      visibility_tier: row.visibility_tier || "FREE",
      reasons: safeJsonParse(row.reasons_json, []),
      risk: safeJsonParse(row.risk_flags_json, []),
      context: "",
      factors: safeJsonParse(row.factors_json, {}),
    };
  });
}

// ── Performance summary ────────────────────────────────────────
export async function fetchPerformance() {
  if (!supabase) return DEMO.performance;

  const { data: bets, error } = await supabase
    .from("bets")
    .select("signal, result, profit, units, market")
    .not("result", "eq", "pending");

  if (error || !bets || bets.length === 0) return DEMO.performance;

  const total = bets.length;
  const wins = bets.filter((b) => b.result === "win").length;
  const totalProfit = bets.reduce((s, b) => s + (b.profit || 0), 0);
  const totalUnits = bets.reduce((s, b) => s + (b.units || 0), 0);

  // Signal breakdown
  const signalMap = {};
  bets.forEach((b) => {
    const sig = b.signal || "UNKNOWN";
    if (!signalMap[sig]) signalMap[sig] = { bets: 0, wins: 0, profit: 0 };
    signalMap[sig].bets++;
    if (b.result === "win") signalMap[sig].wins++;
    signalMap[sig].profit += b.profit || 0;
  });

  const signal = Object.entries(signalMap).map(([name, v]) => ({
    name,
    bets: v.bets,
    win: v.bets ? ((v.wins / v.bets) * 100).toFixed(1) : "0",
    roi: totalUnits ? ((v.profit / totalUnits) * 100).toFixed(1) : "0",
  }));

  // Score bucket breakdown (using model_score on bets)
  const bucketMap = {};
  bets.forEach((b) => {
    const s = b.model_score || 0;
    const label =
      s >= 80 ? "80-89" : s >= 70 ? "70-79" : s >= 60 ? "60-69" : "< 60";
    if (!bucketMap[label])
      bucketMap[label] = { bets: 0, profit: 0, units: 0 };
    bucketMap[label].bets++;
    bucketMap[label].profit += b.profit || 0;
    bucketMap[label].units += b.units || 0;
  });
  const buckets = Object.entries(bucketMap).map(([label, v]) => ({
    label,
    bets: v.bets,
    roi: v.units ? ((v.profit / v.units) * 100).toFixed(1) : "0",
  }));

  return {
    roi: totalUnits ? ((totalProfit / totalUnits) * 100).toFixed(1) : 0,
    winRate: total ? ((wins / total) * 100).toFixed(1) : 0,
    units: totalProfit.toFixed(1),
    bets: total,
    signal,
    buckets,
  };
}

// ── CLV summary ────────────────────────────────────────────────
export async function fetchClv() {
  if (!supabase) return DEMO.clv;

  const { data, error } = await supabase
    .from("bets")
    .select("market, sportsbook, clv_open_to_close")
    .not("clv_open_to_close", "is", null);

  if (error || !data || data.length === 0) return DEMO.clv;

  const avg =
    data.reduce((s, b) => s + (b.clv_open_to_close || 0), 0) / data.length;

  // Group by market + sportsbook
  const groups = {};
  data.forEach((b) => {
    const key = `${b.market}:${b.sportsbook}`;
    if (!groups[key]) groups[key] = { market: b.market, book: b.sportsbook, total: 0, n: 0 };
    groups[key].total += b.clv_open_to_close;
    groups[key].n++;
  });

  const by = Object.values(groups).map((g) => [
    g.market,
    g.book,
    g.n ? g.total / g.n : 0,
    g.n,
  ]);

  return { avg, by };
}

// ── Bankroll / Bet log ─────────────────────────────────────────
export async function fetchBankroll() {
  if (!supabase) return DEMO.bankroll;

  const { data, error } = await supabase
    .from("bets")
    .select(
      "game_date, market, player_name, team_id, odds, units, result, profit"
    )
    .order("game_date", { ascending: false })
    .limit(100);

  if (error || !data || data.length === 0) return DEMO.bankroll;

  let running = 0;
  let peak = 0;
  let streak = "";
  let lastResult = null;
  let streakCount = 0;

  // Walk in chronological order for running total
  const sorted = [...data].reverse();
  sorted.forEach((b) => {
    running += b.profit || 0;
    if (running > peak) peak = running;
    if (b.result === lastResult) {
      streakCount++;
    } else {
      lastResult = b.result;
      streakCount = 1;
    }
  });
  streak = lastResult === "win" ? `W${streakCount}` : `L${streakCount}`;
  const drawdown = peak ? (((running - peak) / peak) * 100).toFixed(1) : 0;

  const bets = data.slice(0, 50).map((b) => [
    b.game_date,
    b.market,
    b.player_name || b.team_id || "—",
    formatOdds(b.odds),
    b.units,
    b.result,
    b.profit,
  ]);

  return {
    current: running.toFixed(1),
    peak: peak.toFixed(1),
    drawdown,
    streak,
    bets,
  };
}

// ── Model Health ───────────────────────────────────────────────
export async function fetchHealth() {
  if (!supabase) return DEMO.health;

  // Check latest score run timestamps for staleness
  const { data: runs } = await supabase
    .from("score_runs")
    .select("run_type, status, started_at, finished_at")
    .order("started_at", { ascending: false })
    .limit(20);

  const stale = [];
  const missing = [];

  if (runs) {
    const byType = {};
    runs.forEach((r) => {
      if (!byType[r.run_type]) byType[r.run_type] = r;
    });

    const now = Date.now();
    Object.entries(byType).forEach(([type, r]) => {
      const age = now - new Date(r.started_at).getTime();
      const hours = age / 3600000;
      if (r.status !== "finished" && r.status !== "completed") {
        missing.push(`${type}: last run ${r.status}`);
      } else if (hours > 24) {
        stale.push(`${type}: ${Math.round(hours)}h old`);
      }
    });
  }

  return {
    stale_sources: stale.length ? stale : ["All sources fresh"],
    missing_sources: missing.length ? missing : ["None"],
    calibration: DEMO.health.calibration, // Calibration needs graded data; placeholder for now
    factors: DEMO.health.factors,
  };
}

// ── utils ──────────────────────────────────────────────────────
function formatOdds(american) {
  if (american == null) return "—";
  return american > 0 ? `+${american}` : `${american}`;
}

function safeJsonParse(val, fallback) {
  if (!val) return fallback;
  if (typeof val === "object") return val;
  try {
    return JSON.parse(val);
  } catch {
    return fallback;
  }
}
