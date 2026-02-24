-- Phase 1A: Foundation hardening (schema additions and indexes)
-- Safe append-only migration for multi-market feature-store architecture.

-- ============================================================
-- SCORE RUN AUDIT
-- ============================================================
CREATE TABLE IF NOT EXISTS score_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    game_date DATE,
    market TEXT,
    triggered_by TEXT NOT NULL DEFAULT 'system',
    status TEXT NOT NULL DEFAULT 'started',
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    rows_scored INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_score_runs_date_market ON score_runs(game_date, market);
CREATE INDEX IF NOT EXISTS idx_score_runs_type_status ON score_runs(run_type, status);
CREATE INDEX IF NOT EXISTS idx_score_runs_started_at ON score_runs(started_at);

-- ============================================================
-- LINEUPS SNAPSHOTS
-- ============================================================
CREATE TABLE IF NOT EXISTS lineups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    game_id INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    batting_order INTEGER,
    position TEXT,
    is_starter INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'mlb_stats_api',
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active_version INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(
        game_date,
        game_id,
        team_id,
        player_id,
        batting_order,
        position,
        is_starter,
        confirmed,
        fetched_at
    )
);

CREATE INDEX IF NOT EXISTS idx_lineups_game_date ON lineups(game_date);
CREATE INDEX IF NOT EXISTS idx_lineups_game_id ON lineups(game_id);
CREATE INDEX IF NOT EXISTS idx_lineups_team_id ON lineups(team_id);
CREATE INDEX IF NOT EXISTS idx_lineups_player_id ON lineups(player_id);
CREATE INDEX IF NOT EXISTS idx_lineups_fetched_at ON lineups(fetched_at);
CREATE INDEX IF NOT EXISTS idx_lineups_active_version ON lineups(active_version);

-- ============================================================
-- FEATURE STORE TABLES
-- ============================================================
CREATE TABLE IF NOT EXISTS batter_daily_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    team_id TEXT,
    bats TEXT,
    pa_7 INTEGER,
    pa_14 INTEGER,
    pa_30 INTEGER,
    k_pct_7 REAL,
    k_pct_14 REAL,
    k_pct_30 REAL,
    bb_pct_7 REAL,
    bb_pct_14 REAL,
    bb_pct_30 REAL,
    barrel_pct_7 REAL,
    barrel_pct_14 REAL,
    barrel_pct_30 REAL,
    hard_hit_pct_7 REAL,
    hard_hit_pct_14 REAL,
    hard_hit_pct_30 REAL,
    avg_exit_velo_7 REAL,
    avg_exit_velo_14 REAL,
    avg_exit_velo_30 REAL,
    fly_ball_pct_7 REAL,
    fly_ball_pct_14 REAL,
    fly_ball_pct_30 REAL,
    line_drive_pct_7 REAL,
    line_drive_pct_14 REAL,
    line_drive_pct_30 REAL,
    gb_pct_7 REAL,
    gb_pct_14 REAL,
    gb_pct_30 REAL,
    pull_pct_7 REAL,
    pull_pct_14 REAL,
    pull_pct_30 REAL,
    sweet_spot_pct_7 REAL,
    sweet_spot_pct_14 REAL,
    sweet_spot_pct_30 REAL,
    avg_launch_angle_7 REAL,
    avg_launch_angle_14 REAL,
    avg_launch_angle_30 REAL,
    iso_7 REAL,
    iso_14 REAL,
    iso_30 REAL,
    slg_7 REAL,
    slg_14 REAL,
    slg_30 REAL,
    ba_7 REAL,
    ba_14 REAL,
    ba_30 REAL,
    hit_rate_7 REAL,
    hit_rate_14 REAL,
    hit_rate_30 REAL,
    tb_per_pa_7 REAL,
    tb_per_pa_14 REAL,
    tb_per_pa_30 REAL,
    hr_rate_7 REAL,
    hr_rate_14 REAL,
    hr_rate_30 REAL,
    singles_rate_14 REAL,
    singles_rate_30 REAL,
    doubles_rate_14 REAL,
    doubles_rate_30 REAL,
    triples_rate_14 REAL,
    triples_rate_30 REAL,
    rbi_rate_14 REAL,
    rbi_rate_30 REAL,
    runs_rate_14 REAL,
    runs_rate_30 REAL,
    walk_rate_14 REAL,
    walk_rate_30 REAL,
    iso_vs_lhp REAL,
    iso_vs_rhp REAL,
    hit_rate_vs_lhp REAL,
    hit_rate_vs_rhp REAL,
    k_pct_vs_lhp REAL,
    k_pct_vs_rhp REAL,
    hot_cold_delta_iso REAL,
    hot_cold_delta_hit_rate REAL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, player_id)
);

CREATE INDEX IF NOT EXISTS idx_batter_daily_features_game_date ON batter_daily_features(game_date);
CREATE INDEX IF NOT EXISTS idx_batter_daily_features_player_id ON batter_daily_features(player_id);
CREATE INDEX IF NOT EXISTS idx_batter_daily_features_team_id ON batter_daily_features(team_id);

CREATE TABLE IF NOT EXISTS pitcher_daily_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    pitcher_id INTEGER NOT NULL,
    team_id TEXT,
    throws TEXT,
    batters_faced_14 INTEGER,
    batters_faced_30 INTEGER,
    k_pct_14 REAL,
    k_pct_30 REAL,
    bb_pct_14 REAL,
    bb_pct_30 REAL,
    hr_per_9_14 REAL,
    hr_per_9_30 REAL,
    hr_per_fb_14 REAL,
    hr_per_fb_30 REAL,
    hard_hit_pct_allowed_14 REAL,
    hard_hit_pct_allowed_30 REAL,
    barrel_pct_allowed_14 REAL,
    barrel_pct_allowed_30 REAL,
    avg_exit_velo_allowed_14 REAL,
    avg_exit_velo_allowed_30 REAL,
    fly_ball_pct_allowed_14 REAL,
    fly_ball_pct_allowed_30 REAL,
    whiff_pct_14 REAL,
    whiff_pct_30 REAL,
    chase_pct_14 REAL,
    chase_pct_30 REAL,
    avg_fastball_velo_14 REAL,
    avg_fastball_velo_30 REAL,
    fastball_velo_trend_14 REAL,
    outs_recorded_avg_last_5 REAL,
    pitches_avg_last_5 REAL,
    starter_role_confidence REAL,
    split_k_pct_vs_lhh REAL,
    split_k_pct_vs_rhh REAL,
    split_hr_allowed_rate_vs_lhh REAL,
    split_hr_allowed_rate_vs_rhh REAL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, pitcher_id)
);

CREATE INDEX IF NOT EXISTS idx_pitcher_daily_features_game_date ON pitcher_daily_features(game_date);
CREATE INDEX IF NOT EXISTS idx_pitcher_daily_features_pitcher_id ON pitcher_daily_features(pitcher_id);
CREATE INDEX IF NOT EXISTS idx_pitcher_daily_features_team_id ON pitcher_daily_features(team_id);

CREATE TABLE IF NOT EXISTS team_daily_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    team_id TEXT NOT NULL,
    opponent_team_id TEXT,
    offense_k_pct_14 REAL,
    offense_k_pct_30 REAL,
    offense_bb_pct_14 REAL,
    offense_bb_pct_30 REAL,
    offense_iso_14 REAL,
    offense_iso_30 REAL,
    offense_ba_14 REAL,
    offense_ba_30 REAL,
    offense_obp_14 REAL,
    offense_obp_30 REAL,
    offense_slg_14 REAL,
    offense_slg_30 REAL,
    offense_hit_rate_14 REAL,
    offense_hit_rate_30 REAL,
    offense_tb_per_pa_14 REAL,
    offense_tb_per_pa_30 REAL,
    runs_per_game_14 REAL,
    runs_per_game_30 REAL,
    hr_rate_14 REAL,
    hr_rate_30 REAL,
    bullpen_era_proxy_14 REAL,
    bullpen_whip_proxy_14 REAL,
    bullpen_k_pct_14 REAL,
    bullpen_hr9_14 REAL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, team_id)
);

CREATE INDEX IF NOT EXISTS idx_team_daily_features_game_date ON team_daily_features(game_date);
CREATE INDEX IF NOT EXISTS idx_team_daily_features_team_id ON team_daily_features(team_id);
CREATE INDEX IF NOT EXISTS idx_team_daily_features_opp_team_id ON team_daily_features(opponent_team_id);

CREATE TABLE IF NOT EXISTS game_context_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    game_id INTEGER NOT NULL,
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    home_pitcher_id INTEGER,
    away_pitcher_id INTEGER,
    park_factor_hr REAL,
    park_factor_runs REAL,
    park_factor_hits REAL,
    weather_temp_f REAL,
    weather_wind_speed_mph REAL,
    weather_wind_dir TEXT,
    weather_hr_multiplier REAL,
    weather_run_multiplier REAL,
    umpire_name TEXT,
    umpire_k_boost REAL,
    umpire_run_env REAL,
    lineups_confirmed_home INTEGER NOT NULL DEFAULT 0,
    lineups_confirmed_away INTEGER NOT NULL DEFAULT 0,
    is_final_context INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, game_id)
);

CREATE INDEX IF NOT EXISTS idx_game_context_features_game_date ON game_context_features(game_date);
CREATE INDEX IF NOT EXISTS idx_game_context_features_game_id ON game_context_features(game_id);
CREATE INDEX IF NOT EXISTS idx_game_context_features_home_team ON game_context_features(home_team_id);
CREATE INDEX IF NOT EXISTS idx_game_context_features_away_team ON game_context_features(away_team_id);

-- ============================================================
-- EXISTING GENERIC TABLE EXTENSIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS market_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    game_id INTEGER,
    player_id INTEGER,
    team_abbr TEXT,
    opponent_team_abbr TEXT,
    bet_type TEXT NOT NULL,
    line REAL,
    odds_decimal REAL,
    sportsbook TEXT NOT NULL,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    game_id INTEGER,
    player_id INTEGER,
    team_abbr TEXT,
    bet_type TEXT NOT NULL,
    line REAL,
    outcome_value REAL,
    outcome_text TEXT,
    settled_at DATETIME
);

CREATE TABLE IF NOT EXISTS model_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    game_id INTEGER NOT NULL,
    game_date DATE NOT NULL,
    player_id INTEGER,
    player_name TEXT,
    team_abbr TEXT,
    opponent_team_abbr TEXT,
    bet_type TEXT NOT NULL,
    line REAL,
    model_score REAL NOT NULL,
    model_prob REAL,
    model_projection REAL,
    book_implied_prob REAL,
    edge REAL,
    signal TEXT,
    factors_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER,
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    bet_type TEXT DEFAULT 'hr_yes',
    sportsbook TEXT,
    odds INTEGER,
    stake REAL,
    units REAL,
    model_score REAL,
    model_edge REAL,
    result TEXT,
    payout REAL,
    profit REAL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    settled_at DATETIME
);

ALTER TABLE market_odds ADD COLUMN game_date DATE;
ALTER TABLE market_odds ADD COLUMN event_id TEXT;
ALTER TABLE market_odds ADD COLUMN entity_type TEXT;
ALTER TABLE market_odds ADD COLUMN team_id TEXT;
ALTER TABLE market_odds ADD COLUMN opponent_team_id TEXT;
ALTER TABLE market_odds ADD COLUMN selection_key TEXT;
ALTER TABLE market_odds ADD COLUMN side TEXT;
ALTER TABLE market_odds ADD COLUMN price_american INTEGER;
ALTER TABLE market_odds ADD COLUMN price_decimal REAL;
ALTER TABLE market_odds ADD COLUMN implied_probability REAL;
ALTER TABLE market_odds ADD COLUMN source_market_key TEXT;
ALTER TABLE market_odds ADD COLUMN is_best_available INTEGER DEFAULT 0;
ALTER TABLE market_odds ADD COLUMN created_at DATETIME;
ALTER TABLE market_odds ADD COLUMN updated_at DATETIME;

ALTER TABLE market_outcomes ADD COLUMN game_date DATE;
ALTER TABLE market_outcomes ADD COLUMN event_id TEXT;
ALTER TABLE market_outcomes ADD COLUMN entity_type TEXT;
ALTER TABLE market_outcomes ADD COLUMN team_id TEXT;
ALTER TABLE market_outcomes ADD COLUMN opponent_team_id TEXT;
ALTER TABLE market_outcomes ADD COLUMN selection_key TEXT;
ALTER TABLE market_outcomes ADD COLUMN side TEXT;
ALTER TABLE market_outcomes ADD COLUMN created_at DATETIME;
ALTER TABLE market_outcomes ADD COLUMN updated_at DATETIME;

ALTER TABLE model_scores ADD COLUMN score_run_id INTEGER;
ALTER TABLE model_scores ADD COLUMN event_id TEXT;
ALTER TABLE model_scores ADD COLUMN entity_type TEXT;
ALTER TABLE model_scores ADD COLUMN team_id TEXT;
ALTER TABLE model_scores ADD COLUMN opponent_team_id TEXT;
ALTER TABLE model_scores ADD COLUMN selection_key TEXT;
ALTER TABLE model_scores ADD COLUMN side TEXT;
ALTER TABLE model_scores ADD COLUMN confidence_band TEXT;
ALTER TABLE model_scores ADD COLUMN reasons_json TEXT;
ALTER TABLE model_scores ADD COLUMN risk_flags_json TEXT;
ALTER TABLE model_scores ADD COLUMN lineup_confirmed INTEGER;
ALTER TABLE model_scores ADD COLUMN weather_final INTEGER;
ALTER TABLE model_scores ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE model_scores ADD COLUMN updated_at DATETIME;

ALTER TABLE bets ADD COLUMN market TEXT DEFAULT 'HR';
ALTER TABLE bets ADD COLUMN team_id TEXT;
ALTER TABLE bets ADD COLUMN opponent_team_id TEXT;
ALTER TABLE bets ADD COLUMN selection_key TEXT;
ALTER TABLE bets ADD COLUMN side TEXT;
ALTER TABLE bets ADD COLUMN line REAL;
ALTER TABLE bets ADD COLUMN odds_close INTEGER;
ALTER TABLE bets ADD COLUMN implied_prob_open REAL;
ALTER TABLE bets ADD COLUMN implied_prob_close REAL;
ALTER TABLE bets ADD COLUMN clv_open_to_close REAL;
ALTER TABLE bets ADD COLUMN line_delta REAL;
ALTER TABLE bets ADD COLUMN updated_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_market_odds_date_market ON market_odds(game_date, market);
CREATE INDEX IF NOT EXISTS idx_market_odds_game_market ON market_odds(game_id, market);
CREATE INDEX IF NOT EXISTS idx_market_odds_fetched_at ON market_odds(fetched_at);
CREATE INDEX IF NOT EXISTS idx_market_odds_player_id ON market_odds(player_id);
CREATE INDEX IF NOT EXISTS idx_market_odds_team_id ON market_odds(team_id);

CREATE INDEX IF NOT EXISTS idx_market_outcomes_date_market ON market_outcomes(game_date, market);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_game_market ON market_outcomes(game_id, market);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_player_id ON market_outcomes(player_id);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_team_id ON market_outcomes(team_id);

CREATE INDEX IF NOT EXISTS idx_model_scores_game_market ON model_scores(game_id, market);
CREATE INDEX IF NOT EXISTS idx_model_scores_player_id ON model_scores(player_id);
CREATE INDEX IF NOT EXISTS idx_model_scores_team_id ON model_scores(team_id);

CREATE INDEX IF NOT EXISTS idx_bets_date_market ON bets(game_date, market);
CREATE INDEX IF NOT EXISTS idx_bets_game_market ON bets(game_id, market);
CREATE INDEX IF NOT EXISTS idx_bets_player_id ON bets(player_id);
CREATE INDEX IF NOT EXISTS idx_bets_team_id ON bets(team_id);
