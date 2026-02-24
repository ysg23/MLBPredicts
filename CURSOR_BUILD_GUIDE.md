# MLB Multi-Market Bet Assist Engine — Cursor Build Guide (V2)

Master build guide for evolving this repo from an HR-first model into a fast, modular, sellable MLB bet-assist platform.

## Vision
Build a **decision-support app** (not just a pick generator) that supports:
- HR, Ks, Hits, Total Bases, Outs Recorded
- ML, Totals, F5 ML, F5 Totals, Team Totals
- Future props: RBI, Runs, Walks, Singles/Doubles, NRFI/YRFI

## Core Principles
- **Fast iteration > huge history** → use **2023–2025** only
- **One engine / many markets**
- **Feature store first** (reusable daily features)
- **No lookahead bias** (`as_of_date` discipline)
- **Lineup-aware rescoring**
- **Track CLV + model health**
- **Useful > flashy UI**

---

## Current Status (already in repo)
This repo already includes a multi-market foundation:
- Generic multi-market tables added to `pipeline/db/schema.sql` (`model_scores`, `market_odds`, `market_outcomes`, `bets`)
- `pipeline/scoring/base_engine.py`
- `pipeline/scoring/hr_model.py` (usable foundation)
- `pipeline/scoring/k_model.py` (foundation, odds integration pending)
- Scaffolds for `ml_model.py`, `totals_model.py`, `f5_ml_model.py`, `f5_total_model.py`
- `pipeline/fetchers/pitchers.py`
- `pipeline/run_pipeline.py` updated with `--score --market ...`

Use this guide to build the next phases **without architecture drift**.

---

## Data Sources and What They Unlock
### Sources in use
- **Statcast / pybaseball** (barrel%, EV, LA, batted-ball, outcomes)
- **MLB Stats API** (schedule, probable pitchers, lineups, game status, umpires)
- **OpenWeather API** (weather, wind, temp)
- **The Odds API** (books, prices, player props, ML/totals/F5 if covered)
- **FanGraphs park factors** (seasonal static park factors)

### Markets this stack supports (realistically)
**High-confidence now**
- HR, Ks, 1+ Hit, Hits O/U, TB O/U, 2+ TB ladders
- ML, Totals, F5 ML, F5 Totals, Team Totals
- Outs Recorded (with odds support)

**Medium-confidence after lineup/team features**
- RBI, Runs, ER Allowed, Walks (batter/pitcher)

**Later / more volatile**
- Singles, Doubles, Triples, NRFI/YRFI

---

## Stack
- **Cursor** — implementation assistant
- **Supabase** — PostgreSQL + auth (future)
- **Railway** — Python jobs + FastAPI status service
- **Vercel** — React dashboard
- **GitHub** — source of truth / deployment triggers

---

## How to Use This Guide in Cursor
1. Open this file in Cursor (`CURSOR_BUILD_GUIDE.md`)
2. Run prompts **one at a time**
3. Verify outputs after each prompt
4. Tell Cursor:
   > "Read the full repo. Then read CURSOR_BUILD_GUIDE.md and use it as the source of truth. Start with the next pending phase and show outputs before moving on."

If Cursor drifts:
> "Stop. Re-read the exact prompt in CURSOR_BUILD_GUIDE.md for this phase and follow it precisely."

---

## Target Architecture (end-state)

```text
pipeline/
├── run_pipeline.py                 # orchestrator / CLI
├── api.py                          # FastAPI health + manual trigger
├── backfill.py                     # historical statcast load (2023-2025)
├── build_features.py               # daily feature store job
├── refresh_odds.py                 # odds ingest/normalize job
├── fetch_lineups.py                # lineups + umpires pull
├── score_markets.py                # score one/all markets
├── rescore_on_lineup.py            # lineup-triggered rescore
├── grade_results.py                # outcomes + settle + CLV
├── alerts.py                       # Discord / push alerts
├── db/
│   ├── schema.sql
│   └── database.py
├── fetchers/
│   ├── statcast.py
│   ├── pitchers.py
│   ├── schedule.py
│   ├── lineups.py
│   ├── umpires.py
│   ├── weather.py
│   ├── odds.py
│   ├── park_factors.py
│   └── teams.py
├── features/
│   ├── batter_features.py
│   ├── pitcher_features.py
│   ├── team_features.py
│   └── game_context_features.py
├── scoring/
│   ├── base_engine.py
│   ├── market_specs.py
│   ├── hr_model.py
│   ├── k_model.py
│   ├── hits_model.py
│   ├── tb_model.py
│   ├── outs_recorded_model.py
│   ├── ml_model.py
│   ├── totals_model.py
│   ├── f5_ml_model.py
│   ├── f5_total_model.py
│   └── team_totals_model.py
├── grading/
│   ├── base_grader.py
│   ├── player_props.py
│   ├── game_markets.py
│   └── clv.py
└── utils/
    ├── odds_normalizer.py
    ├── dates.py
    └── caching.py
```

---

# Phase 0 — Pre-Flight

## 0A. Git + Branching
```bash
git checkout -b multimarket-foundation
```

## 0B. Supabase Setup
1. Create project
2. Copy keys (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`)
3. Run `pipeline/db/schema.sql`
4. Verify all tables exist

## 0C. Env Vars
### Pipeline
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `ODDS_API_KEY`
- `WEATHER_API_KEY`
- `DISCORD_WEBHOOK_URL` (optional)
- `PIPELINE_TRIGGER_API_KEY` (FastAPI trigger)

### Dashboard
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`

## 0D. Smoke Test
```bash
cd pipeline
python run_pipeline.py --init
python run_pipeline.py --test
python run_pipeline.py --daily --date 2026-03-27
python run_pipeline.py --score --market HR --date 2026-03-27
python run_pipeline.py --score --market K --date 2026-03-27
```

---

# Phase 1 — Foundation Hardening (DB + Feature Store + Odds Normalization)

## Prompt 1A — Extend Schema for Feature Store, Lineups, Score Runs, CLV

```text
Update /pipeline/db/schema.sql (appenditive migration-safe changes) to support a fast multi-market engine.

Keep existing HR-specific tables for backward compatibility.

Add/ensure these tables exist with indexes and updated_at timestamps:

1) score_runs
- id (pk)
- run_type (text): overnight_features, morning_context, odds_refresh, lineup_rescore, manual_score, backtest
- game_date (date)
- market (nullable text)
- triggered_by (text)
- status (text)
- started_at (timestamptz)
- finished_at (timestamptz)
- rows_scored (int default 0)
- metadata_json (jsonb default '{}')

2) lineups
- id (pk)
- game_date (date indexed)
- game_id (text/int indexed, match existing game_id type)
- team_id (int/text indexed)
- player_id (int indexed)
- batting_order (nullable int)
- position (nullable text)
- is_starter (bool)
- confirmed (bool)
- source (text)
- fetched_at (timestamptz indexed)
- active_version (bool default true)
- created_at/updated_at
- unique constraint to prevent duplicates per snapshot

3) batter_daily_features
- game_date (date indexed)
- player_id (indexed)
- team_id (nullable)
- bats (nullable text)
- pa_7, pa_14, pa_30
- k_pct_7, k_pct_14, k_pct_30
- bb_pct_7, bb_pct_14, bb_pct_30
- barrel_pct_7, barrel_pct_14, barrel_pct_30
- hard_hit_pct_7, hard_hit_pct_14, hard_hit_pct_30
- avg_exit_velo_7, avg_exit_velo_14, avg_exit_velo_30
- fly_ball_pct_7, fly_ball_pct_14, fly_ball_pct_30
- line_drive_pct_7, line_drive_pct_14, line_drive_pct_30
- gb_pct_7, gb_pct_14, gb_pct_30
- pull_pct_7, pull_pct_14, pull_pct_30
- sweet_spot_pct_7, sweet_spot_pct_14, sweet_spot_pct_30
- avg_launch_angle_7, avg_launch_angle_14, avg_launch_angle_30
- iso_7, iso_14, iso_30
- slg_7, slg_14, slg_30
- ba_7, ba_14, ba_30
- hit_rate_7, hit_rate_14, hit_rate_30
- tb_per_pa_7, tb_per_pa_14, tb_per_pa_30
- hr_rate_7, hr_rate_14, hr_rate_30
- singles_rate_14, singles_rate_30
- doubles_rate_14, doubles_rate_30
- triples_rate_14, triples_rate_30
- rbi_rate_14, rbi_rate_30
- runs_rate_14, runs_rate_30
- walk_rate_14, walk_rate_30
- iso_vs_lhp, iso_vs_rhp
- hit_rate_vs_lhp, hit_rate_vs_rhp
- k_pct_vs_lhp, k_pct_vs_rhp
- hot_cold_delta_iso (7 minus 30)
- hot_cold_delta_hit_rate
- created_at/updated_at
- unique(game_date, player_id)

4) pitcher_daily_features
- game_date (date indexed)
- pitcher_id (indexed)
- team_id (nullable)
- throws (nullable text)
- batters_faced_14, batters_faced_30
- k_pct_14, k_pct_30
- bb_pct_14, bb_pct_30
- hr_per_9_14, hr_per_9_30
- hr_per_fb_14, hr_per_fb_30
- hard_hit_pct_allowed_14, hard_hit_pct_allowed_30
- barrel_pct_allowed_14, barrel_pct_allowed_30
- avg_exit_velo_allowed_14, avg_exit_velo_allowed_30
- fly_ball_pct_allowed_14, fly_ball_pct_allowed_30
- whiff_pct_14, whiff_pct_30
- chase_pct_14, chase_pct_30
- avg_fastball_velo_14, avg_fastball_velo_30
- fastball_velo_trend_14
- outs_recorded_avg_last_5
- pitches_avg_last_5
- starter_role_confidence
- split_k_pct_vs_lhh, split_k_pct_vs_rhh
- split_hr_allowed_rate_vs_lhh, split_hr_allowed_rate_vs_rhh
- created_at/updated_at
- unique(game_date, pitcher_id)

5) team_daily_features
- game_date (date indexed)
- team_id (indexed)
- opponent_team_id (nullable)
- offense_k_pct_14, offense_k_pct_30
- offense_bb_pct_14, offense_bb_pct_30
- offense_iso_14, offense_iso_30
- offense_ba_14, offense_ba_30
- offense_obp_14, offense_obp_30
- offense_slg_14, offense_slg_30
- offense_hit_rate_14, offense_hit_rate_30
- offense_tb_per_pa_14, offense_tb_per_pa_30
- runs_per_game_14, runs_per_game_30
- hr_rate_14, hr_rate_30
- bullpen_era_proxy_14
- bullpen_whip_proxy_14
- bullpen_k_pct_14
- bullpen_hr9_14
- created_at/updated_at
- unique(game_date, team_id)

6) game_context_features
- game_date, game_id (unique)
- home_team_id, away_team_id
- home_pitcher_id, away_pitcher_id
- park_factor_hr, park_factor_runs, park_factor_hits (nullable)
- weather_temp_f
- weather_wind_speed_mph
- weather_wind_dir
- weather_hr_multiplier
- weather_run_multiplier
- umpire_name (nullable)
- umpire_k_boost (nullable)
- umpire_run_env (nullable)
- lineups_confirmed_home bool
- lineups_confirmed_away bool
- is_final_context bool
- created_at/updated_at

Also ensure existing generic tables `model_scores`, `market_odds`, `market_outcomes`, `bets` include useful indexes on:
- (game_date, market)
- (game_id, market)
- fetched_at for market_odds
- player_id/team_id where applicable

Do not drop or rename existing columns used by current code.
```

## Prompt 1B — Add Migration Helper (Optional but recommended)

```text
Create /pipeline/db/migrations/ and add a lightweight migration runner script (no Alembic needed yet).

Create /pipeline/db/migrate.py that:
- Connects to Supabase/Postgres using existing config
- Reads SQL files in /pipeline/db/migrations sorted by filename
- Applies unapplied migrations tracked in a schema_migrations table
- Prints success/failure clearly

Add a migration file that contains the Phase 1A schema additions if schema.sql is no longer the only source.
Keep backward compatibility with the current workflow where schema.sql can still be run manually in Supabase SQL Editor.
```

## Prompt 1C — Build Odds Normalization Utility (critical)

```text
Create /pipeline/utils/odds_normalizer.py and refactor /pipeline/fetchers/odds.py to use it.

Goal: normalize all The Odds API responses into a single internal shape for market_odds inserts.

Normalized fields:
- game_date
- game_id
- event_id
- market
- entity_type (batter/pitcher/team/game)
- player_id (nullable)
- team_id (nullable)
- opponent_team_id (nullable)
- selection_key (stable normalized key)
- side (OVER/UNDER/YES/NO/HOME/AWAY or nullable)
- line (nullable float)
- price_american
- price_decimal
- implied_probability
- sportsbook
- source_market_key
- fetched_at

Requirements:
1. Implement converters/helpers:
   - american_to_decimal
   - american_to_implied_prob
   - decimal_to_implied_prob
2. Market mapping layer:
   - Map Odds API market keys/names to internal market enum values (HR, K, HITS_LINE, HITS_1P, TB_LINE, ML, TOTAL, F5_ML, F5_TOTAL, TEAM_TOTAL)
3. Selection key strategy examples:
   - HR yes/no player prop: "HR|player:{player_id}|YES"
   - K over/under: "K|player:{player_id}|line:6.5|OVER"
   - ML home: "ML|game:{game_id}|HOME"
4. Preserve raw source market names in `source_market_key` for debugging
5. Skip unsupported markets safely and log counts
6. Add unit-testable pure functions where possible

Update fetchers/odds.py so inserts go to `market_odds` for supported markets while leaving existing HR-specific logic intact (backward compatibility).
```

## Prompt 1D — Add Score Run Audit Helpers

```text
Update /pipeline/db/database.py to add helper functions:
- create_score_run(run_type, game_date=None, market=None, triggered_by='system', metadata=None) -> id
- complete_score_run(score_run_id, status, rows_scored=0, metadata=None)
- fail_score_run(score_run_id, error_message, metadata=None)

Use these in scoring jobs later to track every scoring pass.
```

---

# Phase 2 — Feature Store (Speed + Reuse)

## Prompt 2A — Build Batter Daily Features Generator

```text
Create /pipeline/features/batter_features.py and /pipeline/build_features.py support for batter feature snapshots.

Goal: generate `batter_daily_features` for a given game_date using only data available before that date (no lookahead).

Inputs:
- historical statcast/event data already loaded in DB (and/or existing batter_stats table if useful)
- schedule/games table for active teams/players that day

Requirements:
1. Main function:
   build_batter_daily_features(game_date: date, seasons_back=3)
2. For each batter expected to be relevant that day (from lineups if available, otherwise probable player pool from odds/rosters), compute rolling windows:
   - 7d, 14d, 30d
3. Compute and store metrics needed for:
   HR, Hits, TB, RBI, Runs, Walks
4. Include split metrics vs LHP / vs RHP where sample size is sufficient; otherwise fallback to overall and set a risk flag later
5. Upsert to `batter_daily_features` in batches (500 rows max per batch)
6. Add verbose progress logging and row counts

Performance:
- process only the requested date (incremental)
- avoid full-table scans if possible
- cache intermediate aggregates in-memory per date run
```

## Prompt 2B — Build Pitcher Daily Features Generator

```text
Create /pipeline/features/pitcher_features.py and wire into build_features.py.

Goal: generate `pitcher_daily_features` snapshots for probable starters on a given game_date.

Inputs:
- `pitcher_stats` (existing daily fetcher output)
- historical pitch-level/statcast data if needed for gaps
- games/probable starters for the date

Requirements:
1. Main function:
   build_pitcher_daily_features(game_date: date)
2. Compute 14d / 30d windows and last-5-start role metrics:
   - K%, BB%, HR/9, HR/FB
   - hard-hit/barrel/EV allowed
   - whiff/chase
   - velo + velo trend
   - outs_recorded_avg_last_5 / pitches_avg_last_5
   - starter_role_confidence (0-1 or 0-100)
3. Upsert rows in batches
4. If data is missing, store partial row + allow risk flags later rather than failing entire job
```

## Prompt 2C — Build Team Daily Features Generator

```text
Create /pipeline/features/team_features.py and wire into build_features.py.

Goal: generate `team_daily_features` for all teams on a given game_date.

Requirements:
1. Main function:
   build_team_daily_features(game_date: date)
2. Offense windows (14d/30d): K%, BB%, BA, OBP, SLG, ISO, hit rate, TB/PA, runs/game, HR rate
3. Bullpen proxy metrics (14d): ERA/WHIP/K%/HR9 proxies using available pitcher appearance data
4. Upsert in batches
5. Return summary counts + missing-data warnings
```

## Prompt 2D — Build Game Context Features Generator

```text
Create /pipeline/features/game_context_features.py and wire into build_features.py.

Goal: generate one `game_context_features` row per game for a given date.

Inputs:
- games/schedule + probable pitchers
- weather table
- park factors table (FanGraphs static load)
- lineups table (if available)
- umpire assignments (if available)

Requirements:
1. Main function:
   build_game_context_features(game_date: date)
2. Compute and store:
   - park_factor_hr / runs / hits
   - temp, wind, wind dir
   - weather_hr_multiplier / weather_run_multiplier (simple deterministic formulas; configurable)
   - umpire placeholders/boosts if available
   - lineup confirmation flags for both teams
   - is_final_context (true only if lineups + weather + probable pitchers present)
3. Upsert per game
```

## Prompt 2E — Build Features Orchestrator CLI

```text
Create /pipeline/build_features.py and add CLI hooks in /pipeline/run_pipeline.py.

`build_features.py` should:
- accept `--date` and optional `--all-dates`
- call in order:
  1) batter features
  2) pitcher features
  3) team features
  4) game context features
- create a `score_runs` audit row with run_type='overnight_features' or 'manual_features'
- print detailed summary

Update run_pipeline.py to support:
- `--build-features`
- `--date YYYY-MM-DD`

Do not break existing `--daily` or `--score` flows.
```

---

# Phase 3 — Lineups + Umpires + Re-scoring Triggers

## Prompt 3A — Build Lineups Fetcher (MLB Stats API)

```text
Create /pipeline/fetchers/lineups.py and /pipeline/fetch_lineups.py.

Goal: pull official lineups from MLB Stats API and store snapshots in `lineups`.

Requirements:
1. Fetch for a target date (default today)
2. Store both tentative and confirmed lineups if available
3. Fields per player: player_id, team_id, batting_order, position, is_starter, confirmed, source, fetched_at
4. If a new snapshot is fetched, mark previous active_version rows inactive for that game/team (or use versioning strategy)
5. Return list of games/teams that changed since previous snapshot

Add CLI support:
- `python fetch_lineups.py --date YYYY-MM-DD`
```

## Prompt 3B — Build Umpire Fetcher (MLB Stats API)

```text
Create /pipeline/fetchers/umpires.py.

Goal: fetch probable/assigned umpires for games and store in an umpire-related table or enrich `game_context_features` source data.

Requirements:
1. Pull crew/plate ump when available
2. Store normalized umpire name + game_id + date + fetched_at
3. If historical umpire stats are not yet available, still store assignment so future factor integration is easy
4. Do not fail the pipeline if umpires are missing
```

## Prompt 3C — Lineup-triggered Rescore Job

```text
Create /pipeline/rescore_on_lineup.py.

Goal: when a lineup changes or becomes confirmed, re-run scoring for affected batter/team/game markets only.

Requirements:
1. Input: game_date and optional game_id/team_id
2. Detect affected markets:
   - batter props: HR, HITS_1P, HITS_LINE, TB_LINE, RBI, RUNS
   - game/team markets if lineup materially changes: TOTAL, TEAM_TOTAL, F5_TOTAL, ML/F5_ML optional
3. Create `score_runs` audit rows with run_type='lineup_rescore'
4. Mark prior model_scores rows as superseded if needed (or store latest by created_at/score_run_id semantics)
5. Print changed rows count
```

---

# Phase 4 — Market Engine (High-ROI Markets First)

## Prompt 4A — Formalize Market Specs Registry

```text
Create /pipeline/scoring/market_specs.py.

Define a registry/config for each market with:
- market code
- entity_type
- required feature tables
- output type (probability or projection)
- edge method (probability vs implied, projection vs line)
- default thresholds for BET/LEAN/SKIP/FADE
- whether lineup confirmation is required/recommended
- risk flag behavior for missing data

Include specs for:
HR, K, HITS_1P, HITS_LINE, TB_LINE, OUTS_RECORDED, ML, TOTAL, F5_ML, F5_TOTAL, TEAM_TOTAL
```

## Prompt 4B — Upgrade Base Engine for Multi-Market Standardization

```text
Enhance /pipeline/scoring/base_engine.py so every market module can plug in consistently.

Add/standardize support for:
- score_run_id tracking
- factors_json, reasons_json, risk_flags_json
- confidence_band assignment
- lineup/weather finalization flags
- latest-odds selection logic (best available or preferred book)
- signal assignment using market-specific thresholds from market_specs
- supersede strategy (optional: mark previous rows inactive or rely on latest score_run)

Provide shared helper functions:
- percentile_score
- zscore_to_0_100 (optional)
- clamp
- implied_prob_from_american
- choose_best_odds_row
- projection_edge_pct (projection vs line)
- probability_edge_pct (model_prob vs implied_prob)
- build_reasons(top factors)
- build_risk_flags(missing inputs / stale inputs / lineup pending)
```

## Prompt 4C — Complete K Model (odds-integrated)

```text
Finish /pipeline/scoring/k_model.py using the new feature store and normalized market_odds.

Goal: score pitcher strikeout props (K line over/under).

Inputs:
- pitcher_daily_features
- opponent team_daily_features (team K profile)
- game_context_features (weather, umpire if available)
- market_odds rows where market='K'

Model output:
- projected Ks
- optional model probability (over/under line hit probability approximation)
- model_score 0-100
- edge vs line and/or implied prob
- signal + confidence + reasons + risk flags

Requirements:
1. Handle both line-based scoring and probability edge if odds include pricing
2. Store one row per side (OVER/UNDER) or a single row with preferred side (choose one pattern and document it)
3. Use market_specs thresholds
4. Write to `model_scores`
```

## Prompt 4D — Build Hits Model (1+ Hit + Hits Line)

```text
Create /pipeline/scoring/hits_model.py.

Support:
- HITS_1P (yes/no 1+ hit market)
- HITS_LINE (over/under hit line, usually 0.5/1.5)

Inputs:
- batter_daily_features
- opposing pitcher_daily_features
- game_context_features
- lineups (batting order + confirmed)
- market_odds for relevant hit markets

Factors (example, tuneable):
- contact form / K% (batter)
- hit rate / BA / OBP recent windows
- pitcher contact suppression allowed
- batting order PA expectation
- park + weather hit/run environment
- platoon split fit
- hot/cold delta

Store:
- projection (expected hits)
- probability if possible (for 1+ hit especially)
- model_score / edge / signal / reasons / risk flags

Ensure lineup pending vs confirmed is reflected in confidence/risk flags.
```

## Prompt 4E — Build Total Bases Model

```text
Create /pipeline/scoring/tb_model.py.

Support TB line props and 2+ / 3+ TB style ladders when odds available.

Inputs:
- batter_daily_features (SLG, ISO, TB/PA, extra-base hit rates)
- pitcher_daily_features (hard-hit/barrel/fly-ball allowed)
- game_context_features (park/weather)
- lineups (batting order)
- market_odds where market='TB_LINE' or ladder variants

Output:
- projected TB
- line edge and/or implied-prob edge
- signal, confidence, reasons, risk flags

Design to support alternate ladder lines in the same model.
```

## Prompt 4F — Build Outs Recorded Model

```text
Create /pipeline/scoring/outs_recorded_model.py.

Goal: score pitcher outs recorded props.

Inputs:
- pitcher_daily_features (outs avg last 5, pitches avg, role confidence)
- opponent team_daily_features (contact / patience profile)
- game_context_features
- market_odds for OUTS_RECORDED

Factors:
- starter leash / role confidence
- recent pitch counts
- efficiency indicators (BB%, contact, K%, WHIP proxy)
- opponent patience / lineup strength
- weather delays risk (optional flag)

Output projected outs + signal.
```

## Prompt 4G — Wire High-ROI Markets into `--score`

```text
Update /pipeline/run_pipeline.py and/or create /pipeline/score_markets.py.

Support:
- `--score --market HR`
- `--score --market K`
- `--score --market HITS_1P`
- `--score --market HITS_LINE`
- `--score --market TB_LINE`
- `--score --market OUTS_RECORDED`
- `--score --all-markets` (run a configurable subset first)

For `--all-markets`, start with:
HR, K, HITS_1P, HITS_LINE, TB_LINE, OUTS_RECORDED

Create score_runs audit rows per market and summarize rows written.
```

---

# Phase 5 — Sides & Totals (ML / Totals / F5 / Team Totals)

## Prompt 5A — Extend Odds Fetcher Coverage for Sides/Totals/F5/Team Totals

```text
Refactor /pipeline/fetchers/odds.py to ingest and normalize:
- ML
- TOTAL
- F5_ML
- F5_TOTAL
- TEAM_TOTAL
in addition to existing props.

Requirements:
1. Use `utils/odds_normalizer.py` market mapping
2. Store rows in `market_odds`
3. Preserve book + fetched_at + line + side + implied probs
4. Mark best available rows (is_best_available) per selection using latest fetch and best price logic
5. Keep backward compatibility with any existing hr_odds writes until migration is complete
```

## Prompt 5B — Build ML Model

```text
Implement /pipeline/scoring/ml_model.py.

Inputs:
- pitcher_daily_features (SP edge)
- team_daily_features (offense + bullpen proxies)
- game_context_features (park/weather)
- market_odds where market='ML'

Output:
- win probability for each side (home/away)
- model_score per side
- edge vs implied probability
- signal, confidence, reasons, risk flags

Requirements:
- score both sides or score preferred side only (pick one convention and document it)
- include lineup confirmation impact in confidence/risk flags
```

## Prompt 5C — Build Totals + F5 Total Models

```text
Implement /pipeline/scoring/totals_model.py and /pipeline/scoring/f5_total_model.py.

TOTAL inputs:
- SP features (both teams)
- bullpen proxies (team_daily_features)
- offense features (both teams)
- park/weather/umpire context
- market_odds TOTAL lines

F5_TOTAL differences:
- downweight bullpen; upweight SPs
- keep same normalized output schema

Output:
- projected total runs / projected F5 runs
- side preference (OVER/UNDER)
- edge and signal
```

## Prompt 5D — Build F5 ML + Team Totals Models

```text
Implement /pipeline/scoring/f5_ml_model.py and /pipeline/scoring/team_totals_model.py.

F5_ML:
- emphasize SP edge + lineup quality + park/weather
- de-emphasize bullpen

TEAM_TOTAL:
- offense team features + opposing SP + opposing bullpen (for full game) + context
- support both over/under rows from market_odds

Store all outputs in `model_scores`.
```

---

# Phase 6 — Grading, Settlement, CLV, Backtesting

## Prompt 6A — Market-Agnostic Grader + Result Settlement

```text
Create /pipeline/grading/base_grader.py, /pipeline/grading/player_props.py, /pipeline/grading/game_markets.py and /pipeline/grade_results.py.

Goal: grade outcomes and settle logged bets across markets.

Requirements:
1. Ingest final stats / game outcomes from available data sources (Statcast + MLB Stats API)
2. Write normalized results to `market_outcomes`
3. Settle `bets` rows:
   - WIN/LOSS/PUSH/VOID
   - profit_units
4. Support at minimum:
   HR, K, HITS_1P, HITS_LINE, TB_LINE, OUTS_RECORDED, ML, TOTAL, F5_ML, F5_TOTAL, TEAM_TOTAL
5. CLI:
   python grade_results.py --date YYYY-MM-DD
```

## Prompt 6B — CLV Tracking

```text
Create /pipeline/grading/clv.py and extend odds refresh / grading flow.

Goal: track closing line value for bets and model recommendations.

Requirements:
1. Define closing line selection rules (latest pregame snapshot, best available among tracked books)
2. Save closing snapshots in `closing_lines` table or mark `market_odds.is_closing_line=true`
3. Compute CLV for `bets` rows:
   - price delta and/or implied prob delta
   - line movement delta for line-based markets
4. Save `clv_open_to_close` (and optional line_delta fields)
5. Expose helper functions for dashboard reporting
```

## Prompt 6C — Backtesting Refactor (Feature-store based, no lookahead)

```text
Create or refactor /pipeline/backtest.py to use feature snapshots and normalized outcomes.

Requirements:
1. CLI:
   python backtest.py --market K --start 2025-03-27 --end 2025-09-28
   python backtest.py --market HITS_1P ...
2. Use `batter_daily_features`, `pitcher_daily_features`, `team_daily_features`, `game_context_features` as of each date
3. Pull corresponding `market_outcomes`
4. Simulate ROI using estimated or stored historical odds
5. Output:
   - hit rate by signal
   - ROI by signal and score bucket
   - calibration (where applicable)
   - factor contribution diagnostics
   - CSV export to /pipeline/data/backtest_results_<market>.csv
6. Strict no-lookahead enforcement
```

---

# Phase 7 — Pipeline Scheduling & Operational Speed

## Recommended Jobs / Cadence
Use separate Railway jobs/services instead of one giant monolith run.

### Overnight (daily)
- Statcast refresh / pitcher refresh
- Build features (`--build-features`)
- Park factor static load (seasonal only; skip daily unless manual)

### Morning (10 AM ET)
- Schedule + probable pitchers
- Weather refresh
- Umpire fetch (if available)
- Odds refresh
- Initial scoring (all major markets)

### Midday / Pregame
- Odds refresh every ~30 min on game days
- Lineup fetch polling
- `rescore_on_lineup` for affected games/markets
- Weather refresh (~2 PM ET and/or pregame window)

### Postgame / Night
- Grade results
- Settle bets
- Capture CLV closers if not already done

## Prompt 7A — Split Jobs + CLI Entrypoints

```text
Create lightweight CLI entry scripts (or subcommands) for:
- refresh_odds.py
- fetch_lineups.py
- build_features.py
- score_markets.py
- grade_results.py
- rescore_on_lineup.py

All scripts should:
- accept `--date`
- log clearly
- fail gracefully with non-zero exit on hard failures
- use score_runs audit rows where relevant

Keep `run_pipeline.py` as a convenience orchestrator but do not require it for every scheduled job.
```

---

# Phase 8 — Dashboard (Usefulness + Sellability)

## Dashboard Product Goals
The UI should answer:
- What should I bet?
- Why?
- How strong is the edge?
- What’s the risk / uncertainty?
- How has this market/model performed?
- What happened to the line after I bet (CLV)?

## Prompt 8A — Market Explorer / Today’s Board (multi-market)

```text
Refactor the dashboard Today/Picks page into a multi-market "Market Explorer".

Requirements:
1. Market dropdown/tabs:
   HR, K, HITS_1P, HITS_LINE, TB_LINE, OUTS_RECORDED, ML, TOTAL, F5_ML, F5_TOTAL, TEAM_TOTAL
2. Query `model_scores` by selected date + market
3. Filters:
   - signal
   - sportsbook
   - lineup confirmed only
   - min edge
   - confidence band
4. Sort by model_score, edge_pct, game time, odds
5. Card/table row fields:
   - player/team/game
   - side + line + odds
   - model projection/probability
   - implied probability
   - edge_pct
   - signal
   - confidence band
   - lineup/weather flags
6. Expandable details:
   - factor bars
   - reasons_json
   - risk_flags_json
   - context (weather, umpire, batting order)
7. Log Bet button writes to `bets`
```

## Prompt 8B — Performance / Model Health Pages

```text
Build dashboard pages for:
1) Performance
2) Model Health
3) CLV / Line Movement
4) Bankroll / Bet Log

Performance page:
- ROI, win %, units by market/date range
- signal breakdown
- score bucket results

Model Health:
- calibration charts (where applicable)
- factor diagnostics (from backtest outputs if stored)
- stale data / missing source flags

CLV:
- average CLV by market/book
- line movement chart for logged bets
- CLV vs actual outcomes scatter/summary

Bankroll:
- bet log table
- profit curve
- streaks, max drawdown
- filters by market/book/date
```

## Prompt 8C — Product-Grade UX Additions (high value)

```text
Add UI support for:
- exposure warnings (same game/team overexposure)
- correlation flags (simple first pass)
- saved filters / favorite markets
- alerts panel (lineup posted, edge crossed threshold, line moved)
- data freshness indicators for odds/weather/lineups

Keep design dense, data-forward, dark theme. Prioritize usefulness over decoration.
```

---

# Phase 9 — Alerts & Monetization Readiness

## Prompt 9A — Alerts (Discord first, extensible later)

```text
Enhance /pipeline/alerts.py to send market-aware alerts after scoring and on lineup rescores.

Alert payload should include:
- date + market
- top picks (score + edge + side + line + odds)
- key reasons (short)
- risk flags summary
- lineup confirmation status
- dashboard link

Support configurable thresholds per market (e.g., alert only BET and high-confidence LEANs).
Skip gracefully if webhook not set.
```

## Prompt 9B — Basic Tiering/Usage Prep (code-ready, not full auth product yet)

```text
Prepare the codebase for future paid tiers without implementing full billing yet.

Requirements:
1. Add a `visibility_tier` field or equivalent concept to model_scores outputs (e.g., FREE/PRO)
2. Add dashboard feature flags for hiding/showing advanced metrics (CLV, reasons, factor detail)
3. Document where Supabase Auth + Stripe would be integrated later
4. Do not implement billing yet; just leave clean extension points
```

---

# Phase 10 — Deployment / Ops (Railway + Vercel)

## Prompt 10A — Railway Config (API + Jobs)

```text
Finalize Railway deployment configs.

Requirements:
1. Ensure /pipeline/requirements.txt includes all current modules
2. Add/verify /pipeline/Dockerfile for Python 3.11 slim
3. Add/verify /pipeline/railway.toml for FastAPI health service
4. Document separate Railway jobs/services and commands:
   - api service: uvicorn api:app --host 0.0.0.0 --port ${PORT}
   - odds refresh job: python refresh_odds.py --date today
   - features job: python build_features.py --date today
   - score job: python score_markets.py --all-markets --date today
   - grade job: python grade_results.py --date today
   - lineup job (polling/cron): python fetch_lineups.py --date today && python rescore_on_lineup.py --date today
5. Document required env vars
```

## Prompt 10B — Vercel Config + README Update

```text
Finalize dashboard deployment for Vercel and update README.md.

Requirements:
1. Verify /dashboard/vercel.json for Vite SPA rewrites
2. Add /dashboard/.env.example with Supabase anon vars
3. Update README with:
   - local setup
   - schema/migration steps
   - common CLI commands
   - job scheduling overview
   - current supported markets
   - known limitations / TODOs
```

---

# Operating Standards (must-follow)

## Data & Modeling
- No lookahead bias, ever
- Use `as_of_date` logic for feature snapshots and backtests
- Store risk flags instead of silently dropping rows when partial data exists
- Lineup-confirmed status must influence confidence and/or risk flags for batter props

## Performance
- Batch inserts (500 rows max to Supabase)
- Process by date incrementally
- Avoid full rescoring when only one lineup changed
- Reuse feature store across markets
- Cache expensive fetches where appropriate

## Product / UX
- Show projection + line + edge, not just "bet this"
- Show uncertainty (confidence band + risk flags)
- Track performance and CLV transparently

---

# Suggested Build Order (practical, fast, useful)

## Sprint 1 (foundation hardening)
1. Phase 1A/1C/1D (schema + odds normalization + score runs)
2. Phase 2 (feature store generators + build_features)
3. Phase 3A/3C (lineups + lineup rescore)

## Sprint 2 (high-ROI prop expansion)
1. Phase 4A/4B (market specs + base engine upgrades)
2. Phase 4C (K model completion)
3. Phase 4D (Hits)
4. Phase 4E (TB)
5. Phase 4F (Outs Recorded)

## Sprint 3 (sides/totals)
1. Phase 5A (odds coverage)
2. Phase 5B (ML)
3. Phase 5C (Totals/F5 Totals)
4. Phase 5D (F5 ML / Team Totals)

## Sprint 4 (grading + CLV + dashboard)
1. Phase 6A/6B/6C
2. Phase 8A/8B/8C
3. Phase 9A alerts

## Sprint 5 (ops + polish)
1. Phase 10A/10B
2. performance tuning
3. bug fixes + calibration pass

---

# Final Notes for Cursor
- Preserve backward compatibility with current HR tables while the generalized engine is adopted.
- Prefer incremental, testable changes.
- Add docstrings + concise comments for market-specific formulas.
- If a source is missing data (lineups/umpires/weather), score with risk flags instead of crashing.
- Always print row counts and summaries after fetch/feature/score/grade jobs.

