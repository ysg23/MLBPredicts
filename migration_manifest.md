# MLB Railway → Supabase Pro Migration Manifest

> **Snapshot date:** 2026-02-27
> **Source:** Railway self-hosted Supabase (`hopper.proxy.rlwy.net:27639`)
> **Destination:** Supabase Pro "SportsBetting" project (shared with NHL)
> **Status at snapshot:** Scoring actively running — `HITS_LINE` market in progress for 2025-09-21
> **DO NOT MODIFY** this manifest until scoring is confirmed complete.

---

## 1. Scoring Process State

The backfill scoring job is actively running as of snapshot time. Status:

| Market | Status | Rows Scored | Date Range |
|--------|--------|-------------|------------|
| HR | ✅ completed (through 2025-09-21) | 361,874 total | 2023-03-31 → 2025-09-21 |
| K | ✅ completed (through 2025-09-21) | 15,594 total | 2023-04-04 → 2025-09-21 |
| HITS_1P | ✅ completed (through 2025-09-21) | 364,846 total | 2023-03-31 → 2025-09-21 |
| TB_LINE | ✅ completed (through 2025-09-20) | 363,679 total | 2023-03-31 → 2025-09-20 |
| HITS_LINE | 🔄 in progress (2025-09-21 started) | 364,024 total so far | 2023-03-31 → 2025-09-20 complete |
| OUTS_RECORDED | ✅ completed (through 2025-09-20) | 15,565 total | 2023-04-04 → 2025-09-20 |

**Total model_scores rows:** 1,450,890 (and growing)

**Most recent score_run (in progress):**
- `manual_score` | `HITS_LINE` | `2025-09-21` | status=`started` | started=`2026-02-28T00:30:02Z`

**Feature data coverage:**
- `batter_daily_features`: 469,491 rows | 918 players | 2023-03-30 → 2025-09-28
- `pitcher_daily_features`: 15,873 rows | 572 pitchers | 2023-04-04 → 2025-09-28
- `team_daily_features`: 16,266 rows | 45 teams | 2023-03-30 → 2025-09-28
- `game_context_features`: 8,313 rows | 2023-03-30 → 2025-09-28

**Outcomes data:**
- `market_outcomes`: 456,779 rows — coverage through 2025-09-20 for main markets

---

## 2. Complete Table Inventory

### 2a. Public Schema Tables (MLB data)

| Railway Table Name | Row Count | Type | Notes |
|--------------------|-----------|------|-------|
| `stadiums` | 30 | Reference | 30 MLB stadiums |
| `park_factors` | 0 | Reference | Empty — not yet loaded |
| `umpires` | 0 | Reference | Empty — not yet loaded |
| `games` | 8,313 | Core | All games 2023-2025, all status=final/cancelled |
| `weather` | 0 | Contextual | Empty — not used in backfill |
| `lineups` | 0 | Contextual | Empty — not used in backfill |
| `umpire_assignments` | 0 | Contextual | Empty — not used in backfill |
| `batter_stats` | 714,654 | Raw stats | 7/14/30-day windows, 2023-2025 |
| `pitcher_stats` | 33,362 | Raw stats | 14/30-day windows, 2023-2025 |
| `batter_daily_features` | 469,491 | Feature store | Computed, 918 players |
| `pitcher_daily_features` | 15,873 | Feature store | Computed, 572 pitchers |
| `team_daily_features` | 16,266 | Feature store | Computed, 45 teams |
| `game_context_features` | 8,313 | Feature store | One row per game |
| `hr_odds` | 0 | Odds (legacy) | Superseded by market_odds |
| `hr_model_scores` | 0 | Scores (legacy) | Superseded by model_scores |
| `market_odds` | 0 | Odds | Empty — live odds not fetched in backfill |
| `model_scores` | 1,450,890 | **CRITICAL** | Primary scoring output |
| `market_outcomes` | 456,779 | **CRITICAL** | Actual outcomes for grading |
| `score_runs` | 6,362 | Audit | One row per scoring run |
| `bets` | 0 | Tracking | Empty — not used |
| `closing_lines` | 0 | Tracking | Empty — not used |
| `schema_migrations` | 4 | Meta | Migration tracking |

---

## 3. Detailed Schema — Every Table

### `games` (8,313 rows)
Primary game schedule and results.

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| game_id | bigint | NOT NULL | — | PK |
| game_date | date | NOT NULL | — | |
| game_time | text | NULL | — | |
| home_team | text | NOT NULL | — | Team abbreviation |
| away_team | text | NOT NULL | — | Team abbreviation |
| stadium_id | bigint | NULL | — | FK → stadiums |
| home_pitcher_id | bigint | NULL | — | MLB player ID |
| away_pitcher_id | bigint | NULL | — | MLB player ID |
| home_pitcher_name | text | NULL | — | |
| away_pitcher_name | text | NULL | — | |
| home_pitcher_hand | text | NULL | — | CHECK: L/R |
| away_pitcher_hand | text | NULL | — | CHECK: L/R |
| umpire_name | text | NULL | — | |
| status | text | NULL | 'scheduled' | final/cancelled/completed early/etc |
| home_score | integer | NULL | — | |
| away_score | integer | NULL | — | |
| created_at | timestamptz | NULL | now() | |
| updated_at | timestamptz | NULL | now() | |

**Indexes:** `idx_games_date` (game_date), `idx_games_teams` (home_team, away_team)
**FKs:** `stadium_id → stadiums.stadium_id`
**Game status breakdown:** 8,262 final | 32 cancelled | 14 completed early: rain | 4 completed early | 1 completed early: wet grounds

---

### `stadiums` (30 rows)
MLB stadium reference data.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| stadium_id | bigint | NOT NULL | — | PK |
| name | text | NOT NULL | — |
| team_abbr | text | NOT NULL | — | UNIQUE |
| city | text | NULL | — |
| state | text | NULL | — |
| latitude | double | NULL | — |
| longitude | double | NULL | — |
| elevation_ft | integer | NULL | — |
| roof_type | text | NULL | — | CHECK: open/retractable/dome |
| lf_distance | integer | NULL | — |
| cf_distance | integer | NULL | — |
| rf_distance | integer | NULL | — |
| hr_park_factor | double | NULL | 1.0 |

**Indexes:** PK on `stadium_id`, UNIQUE on `team_abbr`

---

### `park_factors` (0 rows)
Per-stadium, per-season park factors (LHB/RHB splits).

| Column | Type | Nullable |
|--------|------|----------|
| id | bigint | NOT NULL | PK |
| stadium_id | bigint | NULL | FK → stadiums |
| season | integer | NOT NULL |
| hr_factor | double | NOT NULL |
| hr_factor_lhb | double | NULL |
| hr_factor_rhb | double | NULL |

**Unique:** `(stadium_id, season)`

---

### `umpires` (0 rows)
Umpire performance statistics by season.

| Column | Type | Nullable |
|--------|------|----------|
| id | bigint | NOT NULL | PK |
| umpire_name | text | NOT NULL |
| season | integer | NOT NULL |
| games_umped | integer | NULL |
| avg_runs_per_game | double | NULL |
| k_pct_above_avg | double | NULL |
| zone_size | text | NULL |
| hr_per_game_avg | double | NULL |

**Unique:** `(umpire_name, season)`

---

### `umpire_assignments` (0 rows)
Per-game umpire assignments.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| id | bigint | NOT NULL | PK |
| game_date | date | NOT NULL |
| game_id | bigint | NOT NULL | FK → games |
| umpire_name | text | NOT NULL |
| fetched_at | timestamptz | NOT NULL |
| source | text | NOT NULL | 'mlb_stats_api' |
| created_at | timestamptz | NOT NULL | now() |
| updated_at | timestamptz | NOT NULL | now() |

**Unique:** `(game_date, game_id, umpire_name, fetched_at)`

---

### `weather` (0 rows)
Per-game weather conditions.

| Column | Type | Nullable |
|--------|------|----------|
| id | bigint | NOT NULL | PK |
| game_id | bigint | NULL | FK → games |
| fetch_time | timestamptz | NOT NULL |
| temperature_f | double | NULL |
| humidity_pct | double | NULL |
| wind_speed_mph | double | NULL |
| wind_direction_deg | integer | NULL |
| wind_description | text | NULL |
| wind_hr_impact | double | NULL |
| precipitation_pct | double | NULL |
| conditions | text | NULL |

**Unique:** `(game_id, fetch_time)`

---

### `lineups` (0 rows)
Per-game batting lineup assignments.

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| id | bigint | NOT NULL | PK |
| game_date | date | NOT NULL |
| game_id | bigint | NOT NULL | FK → games |
| team_id | text | NOT NULL |
| player_id | bigint | NOT NULL |
| batting_order | integer | NULL |
| position | text | NULL |
| is_starter | smallint | NOT NULL | 0 | CHECK: 0/1 |
| confirmed | smallint | NOT NULL | 0 | CHECK: 0/1 |
| source | text | NOT NULL | 'mlb_stats_api' |
| fetched_at | timestamptz | NOT NULL | now() |
| active_version | smallint | NOT NULL | 1 | CHECK: 0/1 |
| created_at | timestamptz | NOT NULL | now() |
| updated_at | timestamptz | NOT NULL | now() |

---

### `batter_stats` (714,654 rows)
Raw Statcast batter statistics by rolling window.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| id | bigint | NOT NULL | PK |
| player_id | bigint | NOT NULL | MLB player ID |
| player_name | text | NOT NULL |
| team | text | NULL |
| bat_hand | text | NULL | CHECK: L/R/S |
| stat_date | date | NOT NULL |
| window_days | integer | NOT NULL | 7, 14, or 30 |
| barrel_pct | double | NULL |
| hard_hit_pct | double | NULL |
| avg_exit_velo | double | NULL |
| max_exit_velo | double | NULL |
| fly_ball_pct | double | NULL |
| hr_per_fb | double | NULL |
| pull_pct | double | NULL |
| avg_launch_angle | double | NULL |
| sweet_spot_pct | double | NULL |
| iso_power | double | NULL |
| slg | double | NULL |
| woba | double | NULL |
| xwoba | double | NULL |
| xslg | double | NULL |
| pa | integer | NULL |
| ab | integer | NULL |
| hrs | integer | NULL |
| k_pct | double | NULL |
| bb_pct | double | NULL |
| iso_vs_lhp | double | NULL |
| iso_vs_rhp | double | NULL |
| barrel_pct_vs_lhp | double | NULL |
| barrel_pct_vs_rhp | double | NULL |
| hr_count_vs_lhp | integer | NULL |
| hr_count_vs_rhp | integer | NULL |
| created_at | timestamptz | NULL | now() |

**Unique:** `(player_id, stat_date, window_days)`
**Windows:** 7 rows=200,873 | 14 rows=235,429 | 30 rows=278,352
**Date range:** 2023-03-30 → 2025-09-28

---

### `pitcher_stats` (33,362 rows)
Raw Statcast pitcher statistics by rolling window.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| id | bigint | NOT NULL | PK |
| player_id | bigint | NOT NULL |
| player_name | text | NOT NULL |
| team | text | NULL |
| pitch_hand | text | NULL | CHECK: L/R |
| stat_date | date | NOT NULL |
| window_days | integer | NOT NULL | 14 or 30 |
| batters_faced | integer | NULL |
| k_pct | double | NULL |
| bb_pct | double | NULL |
| so_per_9 | double | NULL |
| hr_per_9 | double | NULL |
| hr_per_fb | double | NULL |
| fly_ball_pct | double | NULL |
| hard_hit_pct_against | double | NULL |
| barrel_pct_against | double | NULL |
| avg_exit_velo_against | double | NULL |
| avg_fastball_velo | double | NULL |
| fastball_velo_trend | double | NULL |
| whiff_pct | double | NULL |
| chase_pct | double | NULL |
| zone_pct | double | NULL |
| innings_pitched | double | NULL |
| pitches_per_start | double | NULL |
| days_rest | integer | NULL |
| era | double | NULL |
| fip | double | NULL |
| xfip | double | NULL |
| xera | double | NULL |
| hr_per_9_vs_lhb | double | NULL |
| hr_per_9_vs_rhb | double | NULL |
| iso_allowed_vs_lhb | double | NULL |
| iso_allowed_vs_rhb | double | NULL |
| k_pct_vs_lhb | double | NULL |
| k_pct_vs_rhb | double | NULL |
| created_at | timestamptz | NULL | now() |

**Unique:** `(player_id, stat_date, window_days)`
**Windows:** 14 rows=16,681 | 30 rows=16,681
**Date range:** 2023-03-30 → 2025-09-28

---

### `batter_daily_features` (469,491 rows)
Computed daily feature store for batters — model input layer.

**Key:** `(game_date, player_id)` UNIQUE
**Coverage:** 918 players | 2023-03-30 → 2025-09-28

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| game_date | date | NOT NULL |
| player_id | bigint | NOT NULL |
| team_id | text | NULL |
| bats | text | NULL |
| pa_7/14/30 | integer | Plate appearances by window |
| k_pct_7/14/30 | double | Strikeout rate |
| bb_pct_7/14/30 | double | Walk rate |
| barrel_pct_7/14/30 | double | Barrel rate |
| hard_hit_pct_7/14/30 | double | Hard hit rate |
| avg_exit_velo_7/14/30 | double | Exit velocity |
| fly_ball_pct_7/14/30 | double | |
| line_drive_pct_7/14/30 | double | |
| gb_pct_7/14/30 | double | Ground ball rate |
| pull_pct_7/14/30 | double | |
| sweet_spot_pct_7/14/30 | double | |
| avg_launch_angle_7/14/30 | double | |
| iso_7/14/30 | double | Isolated power |
| slg_7/14/30 | double | |
| ba_7/14/30 | double | Batting average |
| hit_rate_7/14/30 | double | |
| tb_per_pa_7/14/30 | double | Total bases per PA |
| hr_rate_7/14/30 | double | HR rate |
| singles_rate_14/30 | double | |
| doubles_rate_14/30 | double | |
| triples_rate_14/30 | double | |
| rbi_rate_14/30 | double | |
| runs_rate_14/30 | double | |
| walk_rate_14/30 | double | |
| iso_vs_lhp/rhp | double | Handedness splits |
| hit_rate_vs_lhp/rhp | double | |
| k_pct_vs_lhp/rhp | double | |
| hot_cold_delta_iso | double | Trend indicator |
| hot_cold_delta_hit_rate | double | |
| recent_lineup_slot | integer | NULL |
| created_at | timestamptz | NOT NULL |
| updated_at | timestamptz | NOT NULL |

Total: **84 columns**

---

### `pitcher_daily_features` (15,873 rows)
Computed daily feature store for pitchers — model input layer.

**Key:** `(game_date, pitcher_id)` UNIQUE
**Coverage:** 572 pitchers | 2023-04-04 → 2025-09-28

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| game_date | date | NOT NULL |
| pitcher_id | bigint | NOT NULL |
| team_id | text | NULL |
| throws | text | NULL |
| batters_faced_14/30 | integer | |
| k_pct_14/30 | double | |
| bb_pct_14/30 | double | |
| hr_per_9_14/30 | double | |
| hr_per_fb_14/30 | double | |
| hard_hit_pct_allowed_14/30 | double | |
| barrel_pct_allowed_14/30 | double | |
| avg_exit_velo_allowed_14/30 | double | |
| fly_ball_pct_allowed_14/30 | double | |
| whiff_pct_14/30 | double | |
| chase_pct_14/30 | double | |
| avg_fastball_velo_14/30 | double | |
| fastball_velo_trend_14 | double | |
| outs_recorded_avg_last_5 | double | |
| pitches_avg_last_5 | double | |
| starter_role_confidence | double | |
| split_k_pct_vs_lhh/rhh | double | Handedness splits |
| split_hr_allowed_rate_vs_lhh/rhh | double | |
| tto_k_decay_pct | double | Times-through-order |
| tto_hr_increase_pct | double | |
| tto_endurance_score | double | |
| created_at | timestamptz | NOT NULL |
| updated_at | timestamptz | NOT NULL |

Total: **41 columns**

---

### `team_daily_features` (16,266 rows)
Computed daily team offense/bullpen features.

**Key:** `(game_date, team_id)` UNIQUE
**Coverage:** 45 teams | 2023-03-30 → 2025-09-28

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| game_date | date | NOT NULL |
| team_id | text | NOT NULL |
| opponent_team_id | text | NULL |
| offense_k_pct_14/30 | double | |
| offense_bb_pct_14/30 | double | |
| offense_iso_14/30 | double | |
| offense_ba_14/30 | double | |
| offense_obp_14/30 | double | |
| offense_slg_14/30 | double | |
| offense_hit_rate_14/30 | double | |
| offense_tb_per_pa_14/30 | double | |
| runs_per_game_14/30 | double | |
| hr_rate_14/30 | double | |
| bullpen_era_proxy_14 | double | |
| bullpen_whip_proxy_14 | double | |
| bullpen_k_pct_14 | double | |
| bullpen_hr9_14 | double | |
| created_at | timestamptz | NOT NULL |
| updated_at | timestamptz | NOT NULL |

Total: **30 columns**

---

### `game_context_features` (8,313 rows)
Combined game context (park, weather, umpire, lineups).

**Key:** `(game_date, game_id)` UNIQUE
**Coverage:** 2023-03-30 → 2025-09-28

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| id | bigint | NOT NULL | PK |
| game_date | date | NOT NULL |
| game_id | bigint | NOT NULL | FK → games |
| home_team_id | text | NOT NULL |
| away_team_id | text | NOT NULL |
| home_pitcher_id | bigint | NULL |
| away_pitcher_id | bigint | NULL |
| park_factor_hr | double | NULL |
| park_factor_runs | double | NULL |
| park_factor_hits | double | NULL |
| weather_temp_f | double | NULL |
| weather_wind_speed_mph | double | NULL |
| weather_wind_dir | text | NULL |
| weather_hr_multiplier | double | NULL |
| weather_run_multiplier | double | NULL |
| umpire_name | text | NULL |
| umpire_k_boost | double | NULL |
| umpire_run_env | double | NULL |
| lineups_confirmed_home | smallint | NOT NULL | 0 | CHECK: 0/1 |
| lineups_confirmed_away | smallint | NOT NULL | 0 | CHECK: 0/1 |
| is_final_context | smallint | NOT NULL | 0 | CHECK: 0/1 |
| is_day_game | smallint | NULL |
| game_time_et | text | NULL |
| created_at | timestamptz | NOT NULL |
| updated_at | timestamptz | NOT NULL |

---

### `market_odds` (0 rows)
Normalized sportsbook odds for all markets.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| id | bigint | NOT NULL | PK |
| game_date | date | NOT NULL |
| game_id | bigint | NULL | FK → games |
| event_id | text | NULL |
| market | text | NOT NULL | HR/K/HITS_1P/etc |
| entity_type | text | NULL | CHECK: batter/pitcher/team/game |
| player_id | bigint | NULL |
| team_id | text | NULL |
| opponent_team_id | text | NULL |
| team_abbr | text | NULL |
| opponent_team_abbr | text | NULL |
| selection_key | text | NULL |
| side | text | NULL | CHECK: OVER/UNDER/YES/NO/HOME/AWAY |
| bet_type | text | NOT NULL |
| line | double | NULL |
| price_american | integer | NULL |
| price_decimal | double | NULL |
| implied_probability | double | NULL |
| odds_decimal | double | NULL |
| sportsbook | text | NOT NULL |
| source_market_key | text | NULL |
| is_best_available | smallint | NOT NULL | 0 | CHECK: 0/1 |
| fetched_at | timestamptz | NULL | now() |
| created_at | timestamptz | NULL | now() |
| updated_at | timestamptz | NULL | now() |

---

### `model_scores` (1,450,890 rows) ⚠️ CRITICAL — STILL BEING WRITTEN
Primary scoring output table. One row per player/market/game/score_run.

**Key:** `(market, game_id, player_id, team_abbr, bet_type, line, score_run_id)` UNIQUE

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| id | bigint | NOT NULL | PK |
| score_run_id | bigint | NULL | FK → score_runs |
| market | text | NOT NULL | HR/K/HITS_1P/HITS_LINE/TB_LINE/OUTS_RECORDED |
| game_id | bigint | NOT NULL | FK → games |
| game_date | date | NOT NULL |
| event_id | text | NULL |
| entity_type | text | NULL | CHECK: batter/pitcher/team/game |
| player_id | bigint | NULL |
| player_name | text | NULL |
| team_id | text | NULL |
| opponent_team_id | text | NULL |
| team_abbr | text | NULL |
| opponent_team_abbr | text | NULL |
| selection_key | text | NULL |
| side | text | NULL | CHECK: OVER/UNDER/YES/NO/HOME/AWAY |
| bet_type | text | NOT NULL |
| line | double | NULL |
| model_score | double | NOT NULL | 0-100 scale |
| model_prob | double | NULL |
| model_projection | double | NULL |
| book_implied_prob | double | NULL |
| edge | double | NULL | % edge vs implied |
| signal | text | NULL | CHECK: BET/LEAN/SKIP/FADE |
| confidence_band | text | NULL | CHECK: HIGH/MEDIUM/LOW |
| visibility_tier | text | NULL | DEFAULT 'FREE' |
| factors_json | text | NULL | Component scores as JSON |
| reasons_json | text | NULL | Human-readable reasons |
| risk_flags_json | text | NULL | Risk flags list |
| lineup_confirmed | smallint | NULL | CHECK: 0/1 |
| weather_final | smallint | NULL | CHECK: 0/1 |
| is_active | smallint | NOT NULL | 1 | CHECK: 0/1 |
| created_at | timestamptz | NULL | now() |
| updated_at | timestamptz | NULL | now() |

**Market breakdown:**
| Market | Total | Active | Date Range |
|--------|-------|--------|------------|
| HR | 361,874 | 361,874 | 2023-03-31 → 2025-09-21 |
| HITS_LINE | 364,024 | 364,024 | 2023-03-31 → 2025-09-20 |
| HITS_1P | 364,846 | 0 | 2023-03-31 → 2025-09-21 |
| TB_LINE | 363,679 | 363,679 | 2023-03-31 → 2025-09-20 |
| K | 15,594 | 15,594 | 2023-04-04 → 2025-09-21 |
| OUTS_RECORDED | 15,565 | 15,565 | 2023-04-04 → 2025-09-20 |

*(Note: HITS_1P has is_active=0 — these rows were superseded by re-runs)*

---

### `market_outcomes` (456,779 rows) ⚠️ CRITICAL
Actual outcome values — used for grading.

**Key:** `(market, game_id, player_id, team_abbr, bet_type, line, selection_key)` UNIQUE

| Column | Type | Nullable |
|--------|------|----------|
| id | bigint | NOT NULL | PK |
| game_date | date | NOT NULL |
| event_id | text | NULL |
| market | text | NOT NULL |
| game_id | bigint | NULL | FK → games |
| entity_type | text | NULL | CHECK: batter/pitcher/team/game |
| player_id | bigint | NULL |
| team_id | text | NULL |
| opponent_team_id | text | NULL |
| team_abbr | text | NULL |
| selection_key | text | NULL |
| side | text | NULL | CHECK: OVER/UNDER/YES/NO/HOME/AWAY |
| bet_type | text | NOT NULL |
| line | double | NULL |
| outcome_value | double | NULL | Actual stat (e.g., 1.0 for HR hit) |
| outcome_text | text | NULL |
| settled_at | timestamptz | NULL |
| created_at | timestamptz | NULL |
| updated_at | timestamptz | NULL |

**Outcome coverage:**
| Market | Rows | Date Range |
|--------|------|------------|
| HITS_LINE | 142,065 | 2023-03-31 → 2025-09-20 |
| HR | 141,915 | 2023-03-31 → 2025-09-20 |
| TB_LINE | 141,839 | 2023-03-31 → 2025-09-20 |
| K | 15,480 | 2023-04-04 → 2025-09-20 |
| OUTS_RECORDED | 15,480 | 2023-04-04 → 2025-09-20 |

---

### `score_runs` (6,362 rows)
Audit log of every scoring run.

| Column | Type | Notes |
|--------|------|-------|
| id | bigint | PK |
| run_type | text | CHECK: overnight_features/morning_context/odds_refresh/lineup_rescore/manual_score/backtest/manual_features |
| game_date | date | NULL |
| market | text | NULL |
| triggered_by | text | DEFAULT 'system' |
| status | text | DEFAULT 'started' |
| started_at | timestamptz | NOT NULL |
| finished_at | timestamptz | NULL |
| rows_scored | integer | DEFAULT 0 |
| metadata_json | text | DEFAULT '{}' |
| created_at | timestamptz | NOT NULL |
| updated_at | timestamptz | NOT NULL |

---

### `hr_odds` (0 rows — legacy)
Legacy HR-specific odds table. Superseded by `market_odds`.

| Column | Type |
|--------|------|
| id, game_id, game_date, player_id, player_name | identifiers |
| sportsbook, market | text |
| over_price, under_price | integer (American odds) |
| implied_prob_over, implied_prob_under | double |
| fetch_time | timestamptz |

---

### `hr_model_scores` (0 rows — legacy)
Legacy HR-only scoring table. Superseded by `model_scores`.

| Column | Type | Notes |
|--------|------|-------|
| id, game_id, game_date, player_id, player_name, team, opponent, opposing_pitcher | identifiers |
| barrel_score, matchup_score, park_weather_score, pitcher_vuln_score, hot_cold_score | double | Component scores |
| model_score, model_prob, book_implied_prob, edge | double | Outputs |
| signal | text | CHECK: BET/LEAN/SKIP/FADE |
| confidence | text | CHECK: HIGH/MEDIUM/LOW |
| propfinder_signal, ballparkpal_signal, hrpredict_signal | text | External consensus |
| consensus_agreement | integer | |
| created_at | timestamptz | |

---

### `bets` (0 rows)
Manual bet tracking. Not used in backfill.

### `closing_lines` (0 rows)
CLV capture. Not used in backfill.

### `schema_migrations` (4 rows)
Migration tracking — filenames only (no version column).

---

## 4. Indexes Summary

| Table | Index | Type | Columns |
|-------|-------|------|---------|
| batter_daily_features | batter_daily_features_pkey | PK | id |
| batter_daily_features | batter_daily_features_game_date_player_id_key | UQ | game_date, player_id |
| batter_daily_features | idx_batter_daily_features_game_date | IX | game_date |
| batter_daily_features | idx_batter_daily_features_player_id | IX | player_id |
| batter_daily_features | idx_batter_daily_features_team_id | IX | team_id |
| batter_stats | batter_stats_pkey | PK | id |
| batter_stats | batter_stats_player_id_stat_date_window_days_key | UQ | player_id, stat_date, window_days |
| batter_stats | idx_batter_stats_date | IX | stat_date |
| batter_stats | idx_batter_stats_player | IX | player_id, stat_date |
| bets | bets_pkey | PK | id |
| bets | idx_bets_date/date_market/game_market/player_id/result/team_id | IX | various |
| closing_lines | closing_lines_pkey | PK | id |
| closing_lines | closing_lines_..._key | UQ | market, game_id, player_id, team_id, selection_key, side, bet_type, line |
| game_context_features | game_context_features_pkey | PK | id |
| game_context_features | game_context_features_game_date_game_id_key | UQ | game_date, game_id |
| game_context_features | idx_game_context_features_* | IX | game_date, game_id, home_team_id, away_team_id |
| games | games_pkey | PK | game_id |
| games | idx_games_date | IX | game_date |
| games | idx_games_teams | IX | home_team, away_team |
| hr_model_scores | hr_model_scores_pkey | PK | id |
| hr_model_scores | hr_model_scores_game_id_player_id_game_date_key | UQ | game_id, player_id, game_date |
| hr_odds | hr_odds_pkey | PK | id |
| hr_odds | hr_odds_..._key | UQ | game_id, player_id, sportsbook, fetch_time |
| lineups | lineups_pkey | PK | id |
| lineups | lineups_..._key | UQ | game_date, game_id, team_id, player_id, batting_order, position, is_starter, confirmed, fetched_at |
| market_odds | market_odds_pkey | PK | id |
| market_odds | idx_market_odds_* | IX | date_market, fetched_at, game_market, lookup, player_id, selection_key, team_id |
| market_outcomes | market_outcomes_pkey | PK | id |
| market_outcomes | market_outcomes_..._key | UQ | market, game_id, player_id, team_abbr, bet_type, line, selection_key |
| market_outcomes | idx_market_outcomes_* | IX | date_market, game_market, player_id, team_id |
| model_scores | model_scores_pkey | PK | id |
| model_scores | model_scores_..._key | UQ | market, game_id, player_id, team_abbr, bet_type, line, score_run_id |
| model_scores | idx_model_scores_* | IX | date_market+signal+score, game_market, player_id, team_id, visibility_tier |
| park_factors | park_factors_pkey | PK | id |
| park_factors | park_factors_stadium_id_season_key | UQ | stadium_id, season |
| pitcher_daily_features | pitcher_daily_features_pkey | PK | id |
| pitcher_daily_features | pitcher_daily_features_game_date_pitcher_id_key | UQ | game_date, pitcher_id |
| pitcher_daily_features | idx_pitcher_daily_features_* | IX | game_date, pitcher_id, team_id |
| pitcher_stats | pitcher_stats_pkey | PK | id |
| pitcher_stats | pitcher_stats_player_id_stat_date_window_days_key | UQ | player_id, stat_date, window_days |
| score_runs | score_runs_pkey | PK | id |
| score_runs | idx_score_runs_* | IX | date_market, started_at, type_status |
| stadiums | stadiums_pkey | PK | stadium_id |
| stadiums | stadiums_team_abbr_key | UQ | team_abbr |
| team_daily_features | team_daily_features_pkey | PK | id |
| team_daily_features | team_daily_features_game_date_team_id_key | UQ | game_date, team_id |
| umpire_assignments | umpire_assignments_pkey | PK | id |
| umpire_assignments | umpire_assignments_..._key | UQ | game_date, game_id, umpire_name, fetched_at |
| umpires | umpires_pkey | PK | id |
| umpires | umpires_umpire_name_season_key | UQ | umpire_name, season |
| weather | weather_pkey | PK | id |
| weather | weather_game_id_fetch_time_key | UQ | game_id, fetch_time |

---

## 5. Foreign Keys

| From Table.Column | → To Table.Column | Constraint Name |
|-------------------|-------------------|-----------------|
| bets.game_id | → games.game_id | bets_game_id_fkey |
| game_context_features.game_id | → games.game_id | game_context_features_game_id_fkey |
| games.stadium_id | → stadiums.stadium_id | games_stadium_id_fkey |
| hr_model_scores.game_id | → games.game_id | hr_model_scores_game_id_fkey |
| hr_odds.game_id | → games.game_id | hr_odds_game_id_fkey |
| lineups.game_id | → games.game_id | lineups_game_id_fkey |
| market_odds.game_id | → games.game_id | market_odds_game_id_fkey |
| market_outcomes.game_id | → games.game_id | market_outcomes_game_id_fkey |
| model_scores.score_run_id | → score_runs.id | model_scores_score_run_id_fkey |
| model_scores.game_id | → games.game_id | model_scores_game_id_fkey |
| park_factors.stadium_id | → stadiums.stadium_id | park_factors_stadium_id_fkey |
| weather.game_id | → games.game_id | weather_game_id_fkey |

---

## 6. RLS Policies

RLS is **enabled on 6 tables** (the dashboard-facing ones). Feature/raw stat tables have NO RLS.

| Table | Policy | Command |
|-------|--------|---------|
| bets | dashboard_read_bets | SELECT |
| bets | service_full_bets | ALL |
| games | dashboard_read_games | SELECT |
| games | service_full_games | ALL |
| market_odds | dashboard_read_market_odds | SELECT |
| market_odds | service_full_market_odds | ALL |
| market_outcomes | dashboard_read_market_outcomes | SELECT |
| market_outcomes | service_full_market_outcomes | ALL |
| model_scores | dashboard_read_model_scores | SELECT |
| model_scores | service_full_model_scores | ALL |
| score_runs | dashboard_read_score_runs | SELECT |
| score_runs | service_full_score_runs | ALL |

---

## 7. Views & Functions

- **Views:** None in public schema
- **Functions/RPC:** None in public schema
- **Triggers:** None in public schema
- **Enum types:** None (all enums are CHECK constraints on text columns)

---

## 8. Extensions

| Extension | Version |
|-----------|---------|
| pg_graphql | 1.5.11 |
| pg_net | 0.14.0 |
| pg_stat_statements | 1.10 |
| pgcrypto | 1.3 |
| pgjwt | 0.2.0 |
| plpgsql | 1.0 |
| supabase_vault | 0.3.1 |
| uuid-ossp | 1.1 |

---

## 9. Migration Name Mapping

When migrating to the Supabase Pro "SportsBetting" project, all MLB tables must be prefixed with `mlb_`. **No data is being dropped — all rows move 1:1.**

### 9a. Core Data Tables (MUST migrate — contains data)

| Current Railway Name | → New Supabase Pro Name | Rows | Priority |
|---------------------|------------------------|------|----------|
| `games` | `mlb_games` | 8,313 | P1 — FK anchor for everything |
| `stadiums` | `mlb_stadiums` | 30 | P1 — FK anchor for games |
| `model_scores` | `mlb_model_scores` | ~1.5M+ | P1 — CRITICAL, primary output |
| `market_outcomes` | `mlb_market_outcomes` | 456,779 | P1 — CRITICAL, grading source |
| `score_runs` | `mlb_score_runs` | 6,362 | P1 — audit trail |
| `batter_daily_features` | `mlb_batter_daily_features` | 469,491 | P1 — feature store |
| `pitcher_daily_features` | `mlb_pitcher_daily_features` | 15,873 | P1 — feature store |
| `team_daily_features` | `mlb_team_daily_features` | 16,266 | P1 — feature store |
| `game_context_features` | `mlb_game_context_features` | 8,313 | P1 — feature store |
| `batter_stats` | `mlb_batter_stats` | 714,654 | P2 — raw source stats |
| `pitcher_stats` | `mlb_pitcher_stats` | 33,362 | P2 — raw source stats |

### 9b. Reference Tables (small, migrate with data)

| Current Railway Name | → New Supabase Pro Name | Rows |
|---------------------|------------------------|------|
| `stadiums` | `mlb_stadiums` | 30 |
| `park_factors` | `mlb_park_factors` | 0 |
| `umpires` | `mlb_umpires` | 0 |

### 9c. Empty Tables (migrate schema only — no data yet)

| Current Railway Name | → New Supabase Pro Name | Notes |
|---------------------|------------------------|-------|
| `weather` | `mlb_weather` | Will be populated in-season |
| `lineups` | `mlb_lineups` | Will be populated in-season |
| `umpire_assignments` | `mlb_umpire_assignments` | Will be populated in-season |
| `market_odds` | `mlb_market_odds` | Will be populated in-season |
| `bets` | `mlb_bets` | Manual tracking, empty |
| `closing_lines` | `mlb_closing_lines` | CLV tracking, empty |

### 9d. Legacy Tables (migrate schema, archive data)

| Current Railway Name | → New Supabase Pro Name | Status |
|---------------------|------------------------|--------|
| `hr_odds` | `mlb_hr_odds` | 0 rows, superseded by mlb_market_odds |
| `hr_model_scores` | `mlb_hr_model_scores` | 0 rows, superseded by mlb_model_scores |

### 9e. Shared Tables (DO NOT rename — already exist in SportsBetting)

These tables exist on the Supabase Pro project and are shared with NHL:
- `pipeline_runs` — shared monitoring (NHL equivalent of score_runs)
- `pipeline_failures` — shared dead letter queue
- `data_source_health` — shared source health tracking
- `user_saved_picks` — shared user picks (sport-discriminated via `sport` column)

**MLB equivalent mapping:**
- `score_runs` → `mlb_score_runs` (MLB-specific audit; also extend `pipeline_runs` for shared monitoring)
- No MLB equivalent for `pipeline_failures` or `data_source_health` yet — adopt NHL's shared tables

---

## 10. Post-Migration Checklist

- [ ] Confirm HITS_LINE scoring for 2025-09-21 complete (check score_runs)
- [ ] Verify final model_scores count before dump
- [ ] pg_dump from Railway Postgres (public schema only)
- [ ] Restore to Supabase Pro with mlb_ prefix rename
- [ ] Recreate all indexes with mlb_ table names
- [ ] Recreate all foreign keys with mlb_ table names
- [ ] Apply RLS policies for mlb_ tables (match NHL pattern: service role full, anon read)
- [ ] Update all MLBPredicts Python scripts: replace bare table names with mlb_ prefixes
- [ ] Swap SUPABASE_DB_URL from Railway to Supabase Pro connection string
- [ ] Verify row counts match post-migration
- [ ] Run one test score_markets.py run against new DB to confirm connectivity

---

## 11. Migration Order (Respecting FKs)

```
1. mlb_stadiums           (no deps)
2. mlb_park_factors       (deps: mlb_stadiums)
3. mlb_umpires            (no deps)
4. mlb_games              (deps: mlb_stadiums)
5. mlb_score_runs         (no deps)
6. mlb_weather            (deps: mlb_games)
7. mlb_lineups            (deps: mlb_games)
8. mlb_umpire_assignments (deps: mlb_games)
9. mlb_hr_odds            (deps: mlb_games)
10. mlb_hr_model_scores   (deps: mlb_games)
11. mlb_batter_stats      (no deps)
12. mlb_pitcher_stats     (no deps)
13. mlb_batter_daily_features  (no deps)
14. mlb_pitcher_daily_features (no deps)
15. mlb_team_daily_features    (no deps)
16. mlb_game_context_features  (deps: mlb_games)
17. mlb_market_odds       (deps: mlb_games)
18. mlb_market_outcomes   (deps: mlb_games)
19. mlb_model_scores      (deps: mlb_games, mlb_score_runs)
20. mlb_bets              (deps: mlb_games)
21. mlb_closing_lines     (deps: mlb_games)
```
