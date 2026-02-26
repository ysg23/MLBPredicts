# MLBPredicts — Data Schema Spec for Lovable

> **Purpose:** This document describes the exact Supabase tables, columns, data types,
> enums, relationships, and access patterns your UI should be built around.
> The dashboard connects to Supabase using the **anon key** with read-only RLS policies.
> All writes are handled by a backend pipeline — the frontend is **read-only**.

---

## Connection

| Setting | Value |
|---------|-------|
| Client library | `@supabase/supabase-js` |
| Auth mode | Anonymous (anon key), no user login required |
| Access | Read-only via RLS (Row Level Security) |
| Env vars | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` |

```js
import { createClient } from "@supabase/supabase-js";
const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
);
```

---

## Tables Accessible from the Dashboard (RLS-enabled, read-only)

These 6 tables have RLS SELECT policies for `anon`:

| Table | Primary Use |
|-------|-------------|
| `model_scores` | All predictions — the main data source |
| `games` | Game schedule, matchups, scores |
| `market_odds` | Sportsbook odds (lines, prices) |
| `market_outcomes` | Actual results for grading |
| `bets` | Tracked/placed bets with P&L |
| `score_runs` | Pipeline run audit log (health monitoring) |

---

## Table Schemas

### 1. `model_scores` — Primary Dashboard Table

The main prediction output. Every row is one scored selection (e.g., "Aaron Judge HR YES").

| Column | Type | Description |
|--------|------|-------------|
| `id` | `bigint` | Primary key (auto) |
| `score_run_id` | `bigint` | FK → `score_runs.id` |
| `market` | `text` | Market type (see Market enum below) |
| `game_id` | `bigint` | FK → `games.game_id` |
| `game_date` | `date` | Game date (`YYYY-MM-DD`) |
| `event_id` | `text` | Optional external event identifier |
| `entity_type` | `text` | `'batter'`, `'pitcher'`, `'team'`, or `'game'` |
| `player_id` | `bigint` | MLB player ID (nullable for game-level markets) |
| `player_name` | `text` | Display name |
| `team_id` | `text` | Team key |
| `opponent_team_id` | `text` | Opponent team key |
| `team_abbr` | `text` | 3-letter abbreviation (e.g., `'NYY'`) |
| `opponent_team_abbr` | `text` | Opponent abbreviation |
| `selection_key` | `text` | Unique selection identifier |
| `side` | `text` | `'OVER'`, `'UNDER'`, `'YES'`, `'NO'`, `'HOME'`, `'AWAY'` |
| `bet_type` | `text` | Bet type string (e.g., `'hr_yes'`, `'k_over'`, `'ml_home'`) |
| `line` | `double precision` | The line/threshold (e.g., `0.5`, `5.5`, `8.5`) |
| `model_score` | `double precision` | **Model confidence score (0–100)**. Higher = stronger. |
| `model_prob` | `double precision` | Model's estimated probability (0.0–1.0) |
| `model_projection` | `double precision` | Projected stat value (e.g., 6.2 Ks) |
| `book_implied_prob` | `double precision` | Sportsbook implied probability (0.0–1.0) |
| `edge` | `double precision` | Percentage edge vs. book (e.g., `8.5` = 8.5% edge) |
| `signal` | `text` | **`'BET'`**, **`'LEAN'`**, `'SKIP'`, `'FADE'` |
| `confidence_band` | `text` | `'HIGH'`, `'MEDIUM'`, `'LOW'` |
| `visibility_tier` | `text` | `'FREE'` or `'PRO'` (for paywall gating) |
| `factors_json` | `text` | JSON string — top factor scores (e.g., `{"barrel_score": 22.5, "park_weather_score": 18.3}`) |
| `reasons_json` | `text` | JSON string — human-readable reasons array (e.g., `["barrel pct 7d elite", "pitcher HR-prone"]`) |
| `risk_flags_json` | `text` | JSON string — warning flags (e.g., `["lineup_pending", "weather_pending"]`) |
| `lineup_confirmed` | `smallint` | `1` = confirmed, `0` = pending |
| `weather_final` | `smallint` | `1` = final weather data, `0` = forecast |
| `is_active` | `smallint` | `1` = current/active, `0` = superseded by rescore |
| `created_at` | `timestamptz` | Row creation time |
| `updated_at` | `timestamptz` | Last update time |

**Key filters for dashboard queries:**
```
.eq("game_date", date)
.eq("is_active", 1)
.in("signal", ["BET", "LEAN", "FADE"])
.order("model_score", { ascending: false })
```

---

### 2. `games` — Game Schedule & Results

| Column | Type | Description |
|--------|------|-------------|
| `game_id` | `bigint` | Primary key (MLB game PK) |
| `game_date` | `date` | Game date |
| `game_time` | `text` | Scheduled time (ET string, e.g., `"19:05"`) |
| `home_team` | `text` | Home team abbreviation |
| `away_team` | `text` | Away team abbreviation |
| `stadium_id` | `bigint` | FK → stadiums |
| `home_pitcher_id` | `bigint` | Starting pitcher MLB ID |
| `away_pitcher_id` | `bigint` | Starting pitcher MLB ID |
| `home_pitcher_name` | `text` | Display name |
| `away_pitcher_name` | `text` | Display name |
| `home_pitcher_hand` | `text` | `'L'` or `'R'` |
| `away_pitcher_hand` | `text` | `'L'` or `'R'` |
| `umpire_name` | `text` | Home plate umpire |
| `status` | `text` | `'scheduled'`, `'final'`, etc. |
| `home_score` | `integer` | Final home score (null if not final) |
| `away_score` | `integer` | Final away score (null if not final) |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

**Typical join pattern:**
```
// Enrich model_scores with game info
const gameIds = [...new Set(picks.map(p => p.game_id))];
const { data: games } = await supabase
  .from("games")
  .select("game_id, game_time, home_team, away_team, home_pitcher_name, away_pitcher_name")
  .in("game_id", gameIds);
```

---

### 3. `market_odds` — Sportsbook Odds

| Column | Type | Description |
|--------|------|-------------|
| `id` | `bigint` | Primary key (auto) |
| `game_date` | `date` | |
| `game_id` | `bigint` | FK → `games.game_id` |
| `event_id` | `text` | External event ID |
| `market` | `text` | Market type enum |
| `entity_type` | `text` | `'batter'`, `'pitcher'`, `'team'`, `'game'` |
| `player_id` | `bigint` | Nullable |
| `team_id` | `text` | Nullable |
| `opponent_team_id` | `text` | |
| `team_abbr` | `text` | |
| `opponent_team_abbr` | `text` | |
| `selection_key` | `text` | Unique key matching `model_scores.selection_key` |
| `side` | `text` | `'OVER'`, `'UNDER'`, `'YES'`, `'NO'`, `'HOME'`, `'AWAY'` |
| `bet_type` | `text` | |
| `line` | `double precision` | The line (e.g., `5.5`) |
| `price_american` | `integer` | American odds (e.g., `+350`, `-120`) |
| `price_decimal` | `double precision` | Decimal odds (e.g., `4.50`) |
| `implied_probability` | `double precision` | 0.0–1.0 |
| `odds_decimal` | `double precision` | Alternate decimal field |
| `sportsbook` | `text` | Book name (e.g., `"DraftKings"`, `"FanDuel"`) |
| `is_best_available` | `smallint` | `1` = best line across books |
| `fetched_at` | `timestamptz` | When odds were scraped |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

**Typical query — best available odds for a date:**
```
const { data: odds } = await supabase
  .from("market_odds")
  .select("game_id, market, player_id, team_abbr, sportsbook, price_american")
  .eq("game_date", date)
  .eq("is_best_available", 1);
```

---

### 4. `market_outcomes` — Actual Results

| Column | Type | Description |
|--------|------|-------------|
| `id` | `bigint` | Primary key (auto) |
| `game_date` | `date` | |
| `event_id` | `text` | |
| `market` | `text` | Market type enum |
| `game_id` | `bigint` | FK → `games.game_id` |
| `entity_type` | `text` | `'batter'`, `'pitcher'`, `'team'`, `'game'` |
| `player_id` | `bigint` | |
| `team_id` | `text` | |
| `opponent_team_id` | `text` | |
| `team_abbr` | `text` | |
| `selection_key` | `text` | Matches `model_scores.selection_key` |
| `side` | `text` | |
| `bet_type` | `text` | |
| `line` | `double precision` | |
| `outcome_value` | `double precision` | **The actual stat** (e.g., `1.0` = hit a HR, `7.0` = 7 Ks) |
| `outcome_text` | `text` | Human-readable result |
| `settled_at` | `timestamptz` | When graded |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

---

### 5. `bets` — Tracked Bets & Performance

| Column | Type | Description |
|--------|------|-------------|
| `id` | `bigint` | Primary key (auto) |
| `game_id` | `bigint` | FK → `games.game_id` |
| `game_date` | `date` | |
| `market` | `text` | Market type enum |
| `player_id` | `bigint` | |
| `player_name` | `text` | |
| `team_id` | `text` | |
| `opponent_team_id` | `text` | |
| `selection_key` | `text` | |
| `side` | `text` | |
| `bet_type` | `text` | |
| `line` | `double precision` | |
| `sportsbook` | `text` | |
| `odds` | `integer` | American odds at time of bet |
| `implied_prob_open` | `double precision` | Opening implied prob |
| `odds_close` | `integer` | Closing line odds |
| `implied_prob_close` | `double precision` | Closing implied prob |
| `clv_open_to_close` | `double precision` | Closing Line Value (positive = good) |
| `line_delta` | `double precision` | Line movement |
| `stake` | `double precision` | Dollar amount |
| `units` | `double precision` | Unit size |
| `model_score` | `double precision` | Score at time of bet |
| `model_edge` | `double precision` | Edge at time of bet |
| `signal` | `text` | Signal at time of bet |
| `result` | `text` | **`'win'`**, **`'loss'`**, `'push'`, `'void'`, `'pending'` |
| `payout` | `double precision` | Total payout if won |
| `profit` | `double precision` | Net profit/loss |
| `notes` | `text` | Optional notes |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |
| `settled_at` | `timestamptz` | When settled |

---

### 6. `score_runs` — Pipeline Health

| Column | Type | Description |
|--------|------|-------------|
| `id` | `bigint` | Primary key (auto) |
| `run_type` | `text` | See Run Type enum below |
| `game_date` | `date` | |
| `market` | `text` | |
| `triggered_by` | `text` | `'system'`, `'manual'`, etc. |
| `status` | `text` | `'started'`, `'completed'`, `'failed'` |
| `started_at` | `timestamptz` | |
| `finished_at` | `timestamptz` | |
| `rows_scored` | `integer` | Number of rows produced |
| `metadata_json` | `text` | JSON string with run details |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

---

## Enums & Constants

### Market Types

| Value | Entity Type | Description | Example Bet |
|-------|-------------|-------------|-------------|
| `HR` | `batter` | Home Run (Yes/No) | "Judge to hit a HR — YES +350" |
| `K` | `pitcher` | Strikeouts (Over/Under) | "Cole over 7.5 Ks -120" |
| `HITS_1P` | `batter` | Hits (Yes/No first-pitch) | |
| `HITS_LINE` | `batter` | Hits (Over/Under line) | "Soto over 1.5 hits +140" |
| `TB_LINE` | `batter` | Total Bases (Over/Under) | "Judge over 1.5 TB -110" |
| `OUTS_RECORDED` | `pitcher` | Outs Recorded (Over/Under) | "Cole over 17.5 outs -115" |
| `ML` | `game` | Full-game Moneyline | "Yankees ML -150" |
| `TOTAL` | `game` | Full-game Run Total (O/U) | "Over 8.5 runs -110" |
| `F5_ML` | `game` | First 5 Innings Moneyline | "Yankees F5 ML -130" |
| `F5_TOTAL` | `game` | First 5 Innings Total (O/U) | "F5 Over 4.5 -105" |
| `TEAM_TOTAL` | `game` | Team Run Total | "Yankees over 4.5 runs" |

**Group them in the UI as:**
- **Player Props:** `HR`, `K`, `HITS_1P`, `HITS_LINE`, `TB_LINE`, `OUTS_RECORDED`
- **Game Markets:** `ML`, `TOTAL`, `F5_ML`, `F5_TOTAL`, `TEAM_TOTAL`

### Signal

| Value | Color Suggestion | Meaning |
|-------|-----------------|---------|
| `BET` | Green | Strong edge — actionable bet |
| `LEAN` | Yellow/Amber | Moderate edge — worth watching |
| `SKIP` | Gray | No edge — pass |
| `FADE` | Red | Negative edge — bet the other side |

### Confidence Band

| Value | Meaning |
|-------|---------|
| `HIGH` | Strong data support, lineup confirmed |
| `MEDIUM` | Decent data, some uncertainty |
| `LOW` | Thin data or missing context |

### Visibility Tier (for Paywall)

| Value | Rule |
|-------|------|
| `FREE` | Signal = `BET` AND confidence = `HIGH` |
| `PRO` | Everything else |

### Bet Result

| Value | Meaning |
|-------|---------|
| `win` | Bet won |
| `loss` | Bet lost |
| `push` | Line matched exactly — stake returned |
| `void` | Cancelled / no action |
| `pending` | Not yet settled |

### Side

| Value | Used With |
|-------|-----------|
| `YES` / `NO` | HR, HITS_1P |
| `OVER` / `UNDER` | K, HITS_LINE, TB_LINE, OUTS_RECORDED, TOTAL, F5_TOTAL, TEAM_TOTAL |
| `HOME` / `AWAY` | ML, F5_ML |

### Entity Type

| Value | Markets |
|-------|---------|
| `batter` | HR, HITS_1P, HITS_LINE, TB_LINE |
| `pitcher` | K, OUTS_RECORDED |
| `team` | TEAM_TOTAL |
| `game` | ML, TOTAL, F5_ML, F5_TOTAL |

### Score Run Types

| Value | Description |
|-------|-------------|
| `overnight_features` | Nightly feature build |
| `morning_context` | Pre-game context refresh |
| `odds_refresh` | Odds scrape + rescore |
| `lineup_rescore` | Triggered by lineup change |
| `manual_score` | Manual trigger |
| `backtest` | Historical backfill |
| `manual_features` | Manual feature rebuild |

---

## Dashboard Views & Query Patterns

### View 1: Market Explorer (Main Page)

**What it shows:** Today's top predictions, filterable by market and signal.

```js
// 1. Fetch predictions
const { data: picks } = await supabase
  .from("model_scores")
  .select(`
    id, market, game_id, game_date, player_id, player_name,
    team_abbr, opponent_team_abbr, side, bet_type, line,
    model_score, model_prob, book_implied_prob, edge, signal,
    confidence_band, visibility_tier, factors_json, reasons_json,
    risk_flags_json, lineup_confirmed, entity_type
  `)
  .eq("game_date", selectedDate)
  .eq("is_active", 1)
  .in("signal", ["BET", "LEAN", "FADE"])
  .order("model_score", { ascending: false });

// 2. Enrich with game info
const gameIds = [...new Set(picks.map(p => p.game_id))];
const { data: games } = await supabase
  .from("games")
  .select("game_id, game_time, home_team, away_team, home_pitcher_name, away_pitcher_name")
  .in("game_id", gameIds);

// 3. Enrich with best odds
const { data: odds } = await supabase
  .from("market_odds")
  .select("game_id, market, player_id, team_abbr, sportsbook, price_american, line")
  .eq("game_date", selectedDate)
  .eq("is_best_available", 1);
```

**Card/row fields to display:**

| Field | Source | Format |
|-------|--------|--------|
| Player/Team | `player_name` or `team_abbr` | Text |
| Matchup | `away_team @ home_team` (from `games`) | Text |
| Game Time | `game_time` (from `games`) | e.g., "7:05 PM" |
| Market | `market` | Chip/badge |
| Side + Line | `side` + `line` | e.g., "OVER 7.5" or "YES" |
| Odds | `price_american` (from `market_odds`) | e.g., "+350" or "-120" |
| Model Score | `model_score` | 0–100 gauge/bar |
| Edge | `edge` | e.g., "+8.5%" |
| Signal | `signal` | Color-coded badge |
| Confidence | `confidence_band` | HIGH/MED/LOW badge |
| Lineup | `lineup_confirmed` | Green check / yellow warning |
| Reasons | `reasons_json` (parse) | Bullet list in expandable row |
| Risk Flags | `risk_flags_json` (parse) | Warning pills |
| Tier | `visibility_tier` | FREE badge or PRO lock icon |

**Filters:**
- Date picker (default: today)
- Market dropdown: `All`, `HR`, `K`, `HITS_LINE`, `TB_LINE`, `ML`, `TOTAL`, etc.
- Signal filter: `BET`, `LEAN`, `FADE`
- Min score slider: 60–100

---

### View 2: Performance Dashboard

**What it shows:** Historical win rates, ROI, signal breakdown.

```js
const { data: bets } = await supabase
  .from("bets")
  .select("signal, result, profit, units, market, model_score")
  .not("result", "eq", "pending");
```

**Computed metrics:**
- **Overall ROI:** `sum(profit) / sum(units) × 100`
- **Win Rate:** `count(result='win') / count(*) × 100`
- **Total Units P/L:** `sum(profit)`
- **By Signal:** Group by `signal` → `{name, bets, winRate, roi}`
- **By Score Bucket:** Bucket `model_score` into `80-100`, `70-79`, `60-69`, `<60` → `{label, bets, roi}`
- **By Market:** Group by `market` → `{market, bets, winRate, roi}`

---

### View 3: CLV (Closing Line Value) Tracker

```js
const { data } = await supabase
  .from("bets")
  .select("market, sportsbook, clv_open_to_close")
  .not("clv_open_to_close", "is", null);
```

**Metrics:**
- **Average CLV:** `avg(clv_open_to_close)` — positive = beating the market
- **By Market + Book:** Group by `market`, `sportsbook` → `{market, book, avgClv, count}`

---

### View 4: Bankroll / Bet Log

```js
const { data: bets } = await supabase
  .from("bets")
  .select("game_date, market, player_name, team_id, odds, units, result, profit, signal")
  .order("game_date", { ascending: false })
  .limit(200);
```

**Display:**
- Running bankroll chart (cumulative `profit` over time)
- Peak bankroll, current bankroll, max drawdown
- Current streak (W5, L2, etc.)
- Recent bets table: Date, Market, Player, Odds, Units, Result, Profit

---

### View 5: Model Health / System Status

```js
const { data: runs } = await supabase
  .from("score_runs")
  .select("run_type, status, started_at, finished_at, rows_scored")
  .order("started_at", { ascending: false })
  .limit(20);
```

**Display:**
- Last run time per `run_type`
- Status badges (completed / failed / stale)
- Staleness check: flag any `run_type` where `started_at` > 24h ago
- Row counts from recent runs

---

### View 6: Game Detail (Drill-down)

When a user clicks a game in Market Explorer, show full context.

```js
// Game info
const { data: game } = await supabase
  .from("games")
  .select("*")
  .eq("game_id", gameId)
  .single();

// All predictions for this game
const { data: picks } = await supabase
  .from("model_scores")
  .select("*")
  .eq("game_id", gameId)
  .eq("is_active", 1)
  .order("model_score", { ascending: false });

// All odds for this game
const { data: odds } = await supabase
  .from("market_odds")
  .select("*")
  .eq("game_id", gameId)
  .eq("is_best_available", 1);

// Outcomes (if game is final)
const { data: outcomes } = await supabase
  .from("market_outcomes")
  .select("*")
  .eq("game_id", gameId);
```

---

## Formatting Helpers

```js
// American odds display
function formatOdds(american) {
  if (american == null) return "—";
  return american > 0 ? `+${american}` : `${american}`;
}

// Parse JSON text fields safely
function parseJson(val, fallback = []) {
  if (!val) return fallback;
  if (typeof val === "object") return val;
  try { return JSON.parse(val); } catch { return fallback; }
}

// Signal → color mapping
const SIGNAL_COLORS = {
  BET:  { bg: "#22c55e", text: "#fff" }, // green
  LEAN: { bg: "#f59e0b", text: "#000" }, // amber
  SKIP: { bg: "#6b7280", text: "#fff" }, // gray
  FADE: { bg: "#ef4444", text: "#fff" }, // red
};

// Confidence → style
const CONFIDENCE_STYLES = {
  HIGH:   { bg: "#16a34a", text: "#fff" },
  MEDIUM: { bg: "#ca8a04", text: "#fff" },
  LOW:    { bg: "#9ca3af", text: "#000" },
};
```

---

## Team Abbreviations (all 30 MLB teams)

```
ARI, ATL, BAL, BOS, CHC, CHW, CIN, CLE, COL, DET,
HOU, KC,  LAA, LAD, MIA, MIL, MIN, NYM, NYY, OAK,
PHI, PIT, SD,  SF,  SEA, STL, TB,  TEX, TOR, WSH
```

---

## Data Refresh Cadence

| Pipeline Stage | Frequency | Tables Updated |
|----------------|-----------|----------------|
| Features build | Nightly (2 AM ET) | `batter_daily_features`, `pitcher_daily_features`, `team_daily_features`, `game_context_features` |
| Scoring | Morning + rescore on lineup change | `model_scores`, `score_runs` |
| Odds refresh | Every 30 min on game days | `market_odds` |
| Grading | Nightly after games finish | `market_outcomes`, `bets` |

**For the dashboard:** Poll `model_scores` by `game_date = today` — data changes throughout the day as lineups confirm and odds refresh. The `updated_at` column can be used for cache invalidation.

---

## Notes for Lovable

1. **All JSON fields** (`factors_json`, `reasons_json`, `risk_flags_json`, `metadata_json`) are stored as `text` — parse them client-side with `JSON.parse()`.

2. **Boolean fields** use `smallint` (0/1), not native boolean — filter with `.eq("is_active", 1)`, not `.eq("is_active", true)`.

3. **The `selection_key` column** is the join key between `model_scores`, `market_odds`, `market_outcomes`, and `bets`. It uniquely identifies a specific bet selection.

4. **Odds format:** `price_american` is the primary display format (e.g., `+350`, `-120`). Always show the `+` sign for positive values.

5. **Edge is a percentage** already (e.g., `8.5` means 8.5% edge). Display as `+8.5%`.

6. **model_score is 0–100.** Good threshold for display: 75+ is strong, 60-74 is moderate, below 60 is weak.

7. **Visibility tier** should gate content: show full details for `FREE` rows, show blurred/locked state for `PRO` rows (future paywall).

8. **No authentication required** for the initial build — the Supabase anon key with RLS handles access. User accounts and auth can be layered on later.
