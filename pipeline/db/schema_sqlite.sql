-- SQLite fallback schema for local-only development.
-- Production canonical schema is pipeline/db/schema.sql (PostgreSQL/Supabase).

CREATE TABLE IF NOT EXISTS stadiums (
    stadium_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team_abbr TEXT NOT NULL UNIQUE,
    city TEXT,
    state TEXT,
    latitude REAL,
    longitude REAL,
    elevation_ft INTEGER,
    roof_type TEXT CHECK(roof_type IN ('open', 'retractable', 'dome')),
    lf_distance INTEGER,
    cf_distance INTEGER,
    rf_distance INTEGER,
    hr_park_factor REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS park_factors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stadium_id INTEGER REFERENCES stadiums(stadium_id),
    season INTEGER NOT NULL,
    hr_factor REAL NOT NULL,
    hr_factor_lhb REAL,
    hr_factor_rhb REAL,
    UNIQUE(stadium_id, season)
);

CREATE TABLE IF NOT EXISTS umpires (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umpire_name TEXT NOT NULL,
    season INTEGER NOT NULL,
    games_umped INTEGER,
    avg_runs_per_game REAL,
    k_pct_above_avg REAL,
    zone_size TEXT,
    hr_per_game_avg REAL,
    UNIQUE(umpire_name, season)
);

CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,
    game_date DATE NOT NULL,
    game_time TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    stadium_id INTEGER REFERENCES stadiums(stadium_id),
    home_pitcher_id INTEGER,
    away_pitcher_id INTEGER,
    home_pitcher_name TEXT,
    away_pitcher_name TEXT,
    home_pitcher_hand TEXT CHECK(home_pitcher_hand IN ('L', 'R')),
    away_pitcher_hand TEXT CHECK(away_pitcher_hand IN ('L', 'R')),
    umpire_name TEXT,
    status TEXT DEFAULT 'scheduled',
    home_score INTEGER,
    away_score INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_games_teams ON games(home_team, away_team);

CREATE TABLE IF NOT EXISTS weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    fetch_time DATETIME NOT NULL,
    temperature_f REAL,
    humidity_pct REAL,
    wind_speed_mph REAL,
    wind_direction_deg INTEGER,
    wind_description TEXT,
    wind_hr_impact REAL,
    precipitation_pct REAL,
    conditions TEXT,
    UNIQUE(game_id, fetch_time)
);

CREATE INDEX IF NOT EXISTS idx_weather_game ON weather(game_id);

CREATE TABLE IF NOT EXISTS lineups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    batting_order INTEGER,
    position TEXT,
    is_starter INTEGER NOT NULL DEFAULT 0 CHECK(is_starter IN (0, 1)),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'mlb_stats_api',
    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active_version INTEGER NOT NULL DEFAULT 1 CHECK(active_version IN (0, 1)),
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

CREATE TABLE IF NOT EXISTS umpire_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    game_id INTEGER NOT NULL,
    umpire_name TEXT NOT NULL,
    fetched_at DATETIME NOT NULL,
    source TEXT NOT NULL DEFAULT 'mlb_stats_api',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, game_id, umpire_name, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_umpire_assignments_date ON umpire_assignments(game_date);
CREATE INDEX IF NOT EXISTS idx_umpire_assignments_game ON umpire_assignments(game_id);
CREATE INDEX IF NOT EXISTS idx_umpire_assignments_fetched_at ON umpire_assignments(fetched_at);

CREATE TABLE IF NOT EXISTS batter_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    bat_hand TEXT CHECK(bat_hand IN ('L', 'R', 'S')),
    stat_date DATE NOT NULL,
    window_days INTEGER NOT NULL,
    barrel_pct REAL,
    hard_hit_pct REAL,
    avg_exit_velo REAL,
    max_exit_velo REAL,
    fly_ball_pct REAL,
    hr_per_fb REAL,
    pull_pct REAL,
    avg_launch_angle REAL,
    sweet_spot_pct REAL,
    iso_power REAL,
    slg REAL,
    woba REAL,
    xwoba REAL,
    xslg REAL,
    pa INTEGER,
    ab INTEGER,
    hrs INTEGER,
    k_pct REAL,
    bb_pct REAL,
    iso_vs_lhp REAL,
    iso_vs_rhp REAL,
    barrel_pct_vs_lhp REAL,
    barrel_pct_vs_rhp REAL,
    hr_count_vs_lhp INTEGER,
    hr_count_vs_rhp INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, stat_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_batter_stats_player ON batter_stats(player_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_batter_stats_date ON batter_stats(stat_date);

CREATE TABLE IF NOT EXISTS pitcher_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    pitch_hand TEXT CHECK(pitch_hand IN ('L', 'R')),
    stat_date DATE NOT NULL,
    window_days INTEGER NOT NULL,
    batters_faced INTEGER,
    k_pct REAL,
    bb_pct REAL,
    so_per_9 REAL,
    hr_per_9 REAL,
    hr_per_fb REAL,
    fly_ball_pct REAL,
    hard_hit_pct_against REAL,
    barrel_pct_against REAL,
    avg_exit_velo_against REAL,
    avg_fastball_velo REAL,
    fastball_velo_trend REAL,
    whiff_pct REAL,
    chase_pct REAL,
    zone_pct REAL,
    innings_pitched REAL,
    pitches_per_start REAL,
    days_rest INTEGER,
    era REAL,
    fip REAL,
    xfip REAL,
    xera REAL,
    hr_per_9_vs_lhb REAL,
    hr_per_9_vs_rhb REAL,
    iso_allowed_vs_lhb REAL,
    iso_allowed_vs_rhb REAL,
    k_pct_vs_lhb REAL,
    k_pct_vs_rhb REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, stat_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_pitcher_stats_player ON pitcher_stats(player_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_pitcher_stats_date ON pitcher_stats(stat_date);

CREATE TABLE IF NOT EXISTS batter_daily_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    team_id TEXT,
    bats TEXT,
    pa_7 INTEGER, pa_14 INTEGER, pa_30 INTEGER,
    k_pct_7 REAL, k_pct_14 REAL, k_pct_30 REAL,
    bb_pct_7 REAL, bb_pct_14 REAL, bb_pct_30 REAL,
    barrel_pct_7 REAL, barrel_pct_14 REAL, barrel_pct_30 REAL,
    hard_hit_pct_7 REAL, hard_hit_pct_14 REAL, hard_hit_pct_30 REAL,
    avg_exit_velo_7 REAL, avg_exit_velo_14 REAL, avg_exit_velo_30 REAL,
    fly_ball_pct_7 REAL, fly_ball_pct_14 REAL, fly_ball_pct_30 REAL,
    line_drive_pct_7 REAL, line_drive_pct_14 REAL, line_drive_pct_30 REAL,
    gb_pct_7 REAL, gb_pct_14 REAL, gb_pct_30 REAL,
    pull_pct_7 REAL, pull_pct_14 REAL, pull_pct_30 REAL,
    sweet_spot_pct_7 REAL, sweet_spot_pct_14 REAL, sweet_spot_pct_30 REAL,
    avg_launch_angle_7 REAL, avg_launch_angle_14 REAL, avg_launch_angle_30 REAL,
    iso_7 REAL, iso_14 REAL, iso_30 REAL,
    slg_7 REAL, slg_14 REAL, slg_30 REAL,
    ba_7 REAL, ba_14 REAL, ba_30 REAL,
    hit_rate_7 REAL, hit_rate_14 REAL, hit_rate_30 REAL,
    tb_per_pa_7 REAL, tb_per_pa_14 REAL, tb_per_pa_30 REAL,
    hr_rate_7 REAL, hr_rate_14 REAL, hr_rate_30 REAL,
    singles_rate_14 REAL, singles_rate_30 REAL,
    doubles_rate_14 REAL, doubles_rate_30 REAL,
    triples_rate_14 REAL, triples_rate_30 REAL,
    rbi_rate_14 REAL, rbi_rate_30 REAL,
    runs_rate_14 REAL, runs_rate_30 REAL,
    walk_rate_14 REAL, walk_rate_30 REAL,
    iso_vs_lhp REAL, iso_vs_rhp REAL,
    hit_rate_vs_lhp REAL, hit_rate_vs_rhp REAL,
    k_pct_vs_lhp REAL, k_pct_vs_rhp REAL,
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
    k_pct_14 REAL, k_pct_30 REAL,
    bb_pct_14 REAL, bb_pct_30 REAL,
    hr_per_9_14 REAL, hr_per_9_30 REAL,
    hr_per_fb_14 REAL, hr_per_fb_30 REAL,
    hard_hit_pct_allowed_14 REAL, hard_hit_pct_allowed_30 REAL,
    barrel_pct_allowed_14 REAL, barrel_pct_allowed_30 REAL,
    avg_exit_velo_allowed_14 REAL, avg_exit_velo_allowed_30 REAL,
    fly_ball_pct_allowed_14 REAL, fly_ball_pct_allowed_30 REAL,
    whiff_pct_14 REAL, whiff_pct_30 REAL,
    chase_pct_14 REAL, chase_pct_30 REAL,
    avg_fastball_velo_14 REAL, avg_fastball_velo_30 REAL,
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
    offense_k_pct_14 REAL, offense_k_pct_30 REAL,
    offense_bb_pct_14 REAL, offense_bb_pct_30 REAL,
    offense_iso_14 REAL, offense_iso_30 REAL,
    offense_ba_14 REAL, offense_ba_30 REAL,
    offense_obp_14 REAL, offense_obp_30 REAL,
    offense_slg_14 REAL, offense_slg_30 REAL,
    offense_hit_rate_14 REAL, offense_hit_rate_30 REAL,
    offense_tb_per_pa_14 REAL, offense_tb_per_pa_30 REAL,
    runs_per_game_14 REAL, runs_per_game_30 REAL,
    hr_rate_14 REAL, hr_rate_30 REAL,
    bullpen_era_proxy_14 REAL, bullpen_whip_proxy_14 REAL,
    bullpen_k_pct_14 REAL, bullpen_hr9_14 REAL,
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
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    home_pitcher_id INTEGER,
    away_pitcher_id INTEGER,
    park_factor_hr REAL, park_factor_runs REAL, park_factor_hits REAL,
    weather_temp_f REAL, weather_wind_speed_mph REAL, weather_wind_dir TEXT,
    weather_hr_multiplier REAL, weather_run_multiplier REAL,
    umpire_name TEXT, umpire_k_boost REAL, umpire_run_env REAL,
    lineups_confirmed_home INTEGER NOT NULL DEFAULT 0 CHECK(lineups_confirmed_home IN (0, 1)),
    lineups_confirmed_away INTEGER NOT NULL DEFAULT 0 CHECK(lineups_confirmed_away IN (0, 1)),
    is_final_context INTEGER NOT NULL DEFAULT 0 CHECK(is_final_context IN (0, 1)),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_date, game_id)
);

CREATE INDEX IF NOT EXISTS idx_game_context_features_game_date ON game_context_features(game_date);
CREATE INDEX IF NOT EXISTS idx_game_context_features_game_id ON game_context_features(game_id);
CREATE INDEX IF NOT EXISTS idx_game_context_features_home_team ON game_context_features(home_team_id);
CREATE INDEX IF NOT EXISTS idx_game_context_features_away_team ON game_context_features(away_team_id);

CREATE TABLE IF NOT EXISTS hr_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    sportsbook TEXT NOT NULL,
    market TEXT DEFAULT 'hr',
    over_price INTEGER,
    under_price INTEGER,
    implied_prob_over REAL,
    implied_prob_under REAL,
    fetch_time DATETIME NOT NULL,
    UNIQUE(game_id, player_id, sportsbook, fetch_time)
);

CREATE INDEX IF NOT EXISTS idx_hr_odds_game ON hr_odds(game_date, player_id);

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

CREATE TABLE IF NOT EXISTS hr_model_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    opponent TEXT,
    opposing_pitcher TEXT,
    barrel_score REAL, matchup_score REAL, park_weather_score REAL, pitcher_vuln_score REAL, hot_cold_score REAL,
    model_score REAL, model_prob REAL, book_implied_prob REAL, edge REAL,
    signal TEXT, confidence TEXT,
    propfinder_signal TEXT, ballparkpal_signal TEXT, hrpredict_signal TEXT,
    consensus_agreement INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_id, player_id, game_date)
);

CREATE INDEX IF NOT EXISTS idx_model_scores_date ON hr_model_scores(game_date);
CREATE INDEX IF NOT EXISTS idx_model_scores_signal ON hr_model_scores(signal);

CREATE TABLE IF NOT EXISTS market_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    game_id INTEGER REFERENCES games(game_id),
    event_id TEXT,
    market TEXT NOT NULL,
    entity_type TEXT,
    player_id INTEGER,
    team_id TEXT,
    opponent_team_id TEXT,
    team_abbr TEXT,
    opponent_team_abbr TEXT,
    selection_key TEXT,
    side TEXT,
    bet_type TEXT NOT NULL,
    line REAL,
    price_american INTEGER,
    price_decimal REAL,
    implied_probability REAL,
    odds_decimal REAL,
    sportsbook TEXT NOT NULL,
    source_market_key TEXT,
    is_best_available INTEGER NOT NULL DEFAULT 0,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_market_odds_lookup ON market_odds(market, game_id, player_id, team_abbr, bet_type, fetched_at);
CREATE INDEX IF NOT EXISTS idx_market_odds_date_market ON market_odds(game_date, market);
CREATE INDEX IF NOT EXISTS idx_market_odds_game_market ON market_odds(game_id, market);
CREATE INDEX IF NOT EXISTS idx_market_odds_fetched_at ON market_odds(fetched_at);
CREATE INDEX IF NOT EXISTS idx_market_odds_player_id ON market_odds(player_id);
CREATE INDEX IF NOT EXISTS idx_market_odds_team_id ON market_odds(team_id);
CREATE INDEX IF NOT EXISTS idx_market_odds_selection_key ON market_odds(selection_key);

CREATE TABLE IF NOT EXISTS market_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    event_id TEXT,
    market TEXT NOT NULL,
    game_id INTEGER REFERENCES games(game_id),
    entity_type TEXT,
    player_id INTEGER,
    team_id TEXT,
    opponent_team_id TEXT,
    team_abbr TEXT,
    selection_key TEXT,
    side TEXT,
    bet_type TEXT NOT NULL,
    line REAL,
    outcome_value REAL,
    outcome_text TEXT,
    settled_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market, game_id, player_id, team_abbr, bet_type, line, selection_key)
);

CREATE INDEX IF NOT EXISTS idx_market_outcomes_date_market ON market_outcomes(game_date, market);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_game_market ON market_outcomes(game_id, market);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_player_id ON market_outcomes(player_id);
CREATE INDEX IF NOT EXISTS idx_market_outcomes_team_id ON market_outcomes(team_id);

CREATE TABLE IF NOT EXISTS model_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    score_run_id INTEGER,
    market TEXT NOT NULL,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    game_date DATE NOT NULL,
    event_id TEXT,
    entity_type TEXT,
    player_id INTEGER,
    player_name TEXT,
    team_id TEXT,
    opponent_team_id TEXT,
    team_abbr TEXT,
    opponent_team_abbr TEXT,
    selection_key TEXT,
    side TEXT,
    bet_type TEXT NOT NULL,
    line REAL,
    model_score REAL NOT NULL,
    model_prob REAL,
    model_projection REAL,
    book_implied_prob REAL,
    edge REAL,
    signal TEXT,
    confidence_band TEXT,
    factors_json TEXT,
    reasons_json TEXT,
    risk_flags_json TEXT,
    lineup_confirmed INTEGER,
    weather_final INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market, game_id, player_id, team_abbr, bet_type, line, score_run_id)
);

CREATE INDEX IF NOT EXISTS idx_model_scores_date_market ON model_scores(game_date, market, signal, model_score);
CREATE INDEX IF NOT EXISTS idx_model_scores_game_market ON model_scores(game_id, market);
CREATE INDEX IF NOT EXISTS idx_model_scores_player_id ON model_scores(player_id);
CREATE INDEX IF NOT EXISTS idx_model_scores_team_id ON model_scores(team_id);

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    game_date DATE NOT NULL,
    market TEXT DEFAULT 'HR',
    player_id INTEGER,
    player_name TEXT,
    team_id TEXT,
    opponent_team_id TEXT,
    selection_key TEXT,
    side TEXT,
    bet_type TEXT DEFAULT 'hr_yes',
    line REAL,
    sportsbook TEXT,
    odds INTEGER,
    odds_close INTEGER,
    implied_prob_open REAL,
    implied_prob_close REAL,
    clv_open_to_close REAL,
    line_delta REAL,
    stake REAL,
    units REAL,
    model_score REAL,
    model_edge REAL,
    result TEXT,
    payout REAL,
    profit REAL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    settled_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_bets_date ON bets(game_date);
CREATE INDEX IF NOT EXISTS idx_bets_result ON bets(result);
CREATE INDEX IF NOT EXISTS idx_bets_date_market ON bets(game_date, market);
CREATE INDEX IF NOT EXISTS idx_bets_game_market ON bets(game_id, market);
CREATE INDEX IF NOT EXISTS idx_bets_player_id ON bets(player_id);
CREATE INDEX IF NOT EXISTS idx_bets_team_id ON bets(team_id);

CREATE TABLE IF NOT EXISTS closing_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_date DATE NOT NULL,
    market TEXT NOT NULL,
    game_id INTEGER NOT NULL,
    event_id TEXT,
    entity_type TEXT,
    player_id INTEGER,
    team_id TEXT,
    opponent_team_id TEXT,
    team_abbr TEXT,
    opponent_team_abbr TEXT,
    selection_key TEXT,
    side TEXT,
    bet_type TEXT,
    line REAL,
    sportsbook TEXT,
    price_american INTEGER,
    price_decimal REAL,
    implied_probability REAL,
    fetched_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market, game_id, player_id, team_id, selection_key, side, bet_type, line)
);

CREATE INDEX IF NOT EXISTS idx_closing_lines_date_market ON closing_lines(game_date, market);
CREATE INDEX IF NOT EXISTS idx_closing_lines_selection_key ON closing_lines(selection_key);
