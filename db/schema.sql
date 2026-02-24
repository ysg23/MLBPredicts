-- MLB HR Prop Pipeline - Database Schema (Supabase / Postgres)
-- Run this in: Supabase Dashboard → SQL Editor → New Query → paste → Run

-- ============================================================
-- REFERENCE / STATIC TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS stadiums (
    stadium_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    team_abbr TEXT NOT NULL UNIQUE,
    city TEXT,
    state TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    elevation_ft INTEGER,
    roof_type TEXT CHECK(roof_type IN ('open', 'retractable', 'dome')),
    lf_distance INTEGER,
    cf_distance INTEGER,
    rf_distance INTEGER,
    hr_park_factor DOUBLE PRECISION DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS park_factors (
    id SERIAL PRIMARY KEY,
    stadium_id INTEGER REFERENCES stadiums(stadium_id),
    season INTEGER NOT NULL,
    hr_factor DOUBLE PRECISION NOT NULL,
    hr_factor_lhb DOUBLE PRECISION,
    hr_factor_rhb DOUBLE PRECISION,
    UNIQUE(stadium_id, season)
);

-- ============================================================
-- DAILY GAME DATA
-- ============================================================

CREATE TABLE IF NOT EXISTS games (
    game_id BIGINT PRIMARY KEY,
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
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_games_teams ON games(home_team, away_team);

-- ============================================================
-- BATTER STATS (rolling windows for HR model)
-- ============================================================

CREATE TABLE IF NOT EXISTS batter_stats (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    bat_hand TEXT CHECK(bat_hand IN ('L', 'R', 'S')),
    stat_date DATE NOT NULL,
    window_days INTEGER NOT NULL,

    barrel_pct DOUBLE PRECISION,
    hard_hit_pct DOUBLE PRECISION,
    avg_exit_velo DOUBLE PRECISION,
    max_exit_velo DOUBLE PRECISION,
    fly_ball_pct DOUBLE PRECISION,
    hr_per_fb DOUBLE PRECISION,
    pull_pct DOUBLE PRECISION,
    avg_launch_angle DOUBLE PRECISION,
    sweet_spot_pct DOUBLE PRECISION,

    iso_power DOUBLE PRECISION,
    slg DOUBLE PRECISION,
    woba DOUBLE PRECISION,
    xwoba DOUBLE PRECISION,
    xslg DOUBLE PRECISION,

    pa INTEGER,
    ab INTEGER,
    hrs INTEGER,
    k_pct DOUBLE PRECISION,
    bb_pct DOUBLE PRECISION,

    iso_vs_lhp DOUBLE PRECISION,
    iso_vs_rhp DOUBLE PRECISION,
    barrel_pct_vs_lhp DOUBLE PRECISION,
    barrel_pct_vs_rhp DOUBLE PRECISION,
    hr_count_vs_lhp INTEGER,
    hr_count_vs_rhp INTEGER,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, stat_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_batter_stats_player ON batter_stats(player_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_batter_stats_date ON batter_stats(stat_date);

-- ============================================================
-- PITCHER STATS (opponent context for HR model)
-- ============================================================

CREATE TABLE IF NOT EXISTS pitcher_stats (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    pitch_hand TEXT CHECK(pitch_hand IN ('L', 'R')),
    stat_date DATE NOT NULL,
    window_days INTEGER NOT NULL,

    hr_per_9 DOUBLE PRECISION,
    hr_per_fb DOUBLE PRECISION,
    fly_ball_pct DOUBLE PRECISION,
    hard_hit_pct_against DOUBLE PRECISION,
    barrel_pct_against DOUBLE PRECISION,
    avg_exit_velo_against DOUBLE PRECISION,

    avg_fastball_velo DOUBLE PRECISION,
    fastball_velo_trend DOUBLE PRECISION,
    whiff_pct DOUBLE PRECISION,
    chase_pct DOUBLE PRECISION,
    zone_pct DOUBLE PRECISION,

    innings_pitched DOUBLE PRECISION,
    pitches_per_start DOUBLE PRECISION,
    days_rest INTEGER,

    era DOUBLE PRECISION,
    fip DOUBLE PRECISION,
    xfip DOUBLE PRECISION,
    xera DOUBLE PRECISION,

    hr_per_9_vs_lhb DOUBLE PRECISION,
    hr_per_9_vs_rhb DOUBLE PRECISION,
    iso_allowed_vs_lhb DOUBLE PRECISION,
    iso_allowed_vs_rhb DOUBLE PRECISION,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, stat_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_pitcher_stats_player ON pitcher_stats(player_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_pitcher_stats_date ON pitcher_stats(stat_date);

-- ============================================================
-- WEATHER (game-day conditions)
-- ============================================================

CREATE TABLE IF NOT EXISTS weather (
    id SERIAL PRIMARY KEY,
    game_id BIGINT REFERENCES games(game_id),
    fetch_time TIMESTAMPTZ NOT NULL,
    temperature_f DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    wind_speed_mph DOUBLE PRECISION,
    wind_direction_deg INTEGER,
    wind_description TEXT,
    wind_hr_impact DOUBLE PRECISION,
    precipitation_pct DOUBLE PRECISION,
    conditions TEXT,
    UNIQUE(game_id, fetch_time)
);

CREATE INDEX IF NOT EXISTS idx_weather_game ON weather(game_id);

-- ============================================================
-- ODDS (HR prop lines from sportsbooks)
-- ============================================================

CREATE TABLE IF NOT EXISTS hr_odds (
    id SERIAL PRIMARY KEY,
    game_id BIGINT REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    sportsbook TEXT NOT NULL,
    market TEXT DEFAULT 'hr',
    over_price INTEGER,
    under_price INTEGER,
    implied_prob_over DOUBLE PRECISION,
    implied_prob_under DOUBLE PRECISION,
    fetch_time TIMESTAMPTZ NOT NULL,
    UNIQUE(game_id, player_id, sportsbook, fetch_time)
);

CREATE INDEX IF NOT EXISTS idx_hr_odds_game ON hr_odds(game_date, player_id);

-- ============================================================
-- UMPIRE TENDENCIES
-- ============================================================

CREATE TABLE IF NOT EXISTS umpires (
    id SERIAL PRIMARY KEY,
    umpire_name TEXT NOT NULL,
    season INTEGER NOT NULL,
    games_umped INTEGER,
    avg_runs_per_game DOUBLE PRECISION,
    k_pct_above_avg DOUBLE PRECISION,
    zone_size TEXT,
    hr_per_game_avg DOUBLE PRECISION,
    UNIQUE(umpire_name, season)
);

-- ============================================================
-- MODEL SCORES (daily output)
-- ============================================================

CREATE TABLE IF NOT EXISTS hr_model_scores (
    id SERIAL PRIMARY KEY,
    game_id BIGINT REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    opponent TEXT,
    opposing_pitcher TEXT,

    barrel_score DOUBLE PRECISION,
    matchup_score DOUBLE PRECISION,
    park_weather_score DOUBLE PRECISION,
    pitcher_vuln_score DOUBLE PRECISION,
    hot_cold_score DOUBLE PRECISION,

    model_score DOUBLE PRECISION,
    model_prob DOUBLE PRECISION,
    book_implied_prob DOUBLE PRECISION,
    edge DOUBLE PRECISION,
    signal TEXT CHECK(signal IN ('BET', 'LEAN', 'SKIP', 'FADE')),
    confidence TEXT CHECK(confidence IN ('HIGH', 'MEDIUM', 'LOW')),

    propfinder_signal TEXT,
    ballparkpal_signal TEXT,
    hrpredict_signal TEXT,
    consensus_agreement INTEGER,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(game_id, player_id, game_date)
);

CREATE INDEX IF NOT EXISTS idx_model_scores_date ON hr_model_scores(game_date);
CREATE INDEX IF NOT EXISTS idx_model_scores_signal ON hr_model_scores(signal);

-- ============================================================
-- BET TRACKING (bankroll management)
-- ============================================================

CREATE TABLE IF NOT EXISTS bets (
    id SERIAL PRIMARY KEY,
    game_id BIGINT REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    bet_type TEXT DEFAULT 'hr_yes',
    sportsbook TEXT,
    odds INTEGER,
    stake DOUBLE PRECISION,
    units DOUBLE PRECISION,
    model_score DOUBLE PRECISION,
    model_edge DOUBLE PRECISION,
    result TEXT CHECK(result IN ('win', 'loss', 'push', 'void', 'pending')),
    payout DOUBLE PRECISION,
    profit DOUBLE PRECISION,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bets_date ON bets(game_date);
CREATE INDEX IF NOT EXISTS idx_bets_result ON bets(result);

-- ============================================================
-- ACTUAL HR OUTCOMES (for backtesting)
-- ============================================================

CREATE TABLE IF NOT EXISTS hr_outcomes (
    id SERIAL PRIMARY KEY,
    game_id BIGINT,
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    opponent TEXT,
    opposing_pitcher_id INTEGER,
    opposing_pitcher_name TEXT,
    hit_hr BOOLEAN NOT NULL DEFAULT FALSE,
    hr_count INTEGER DEFAULT 0,
    UNIQUE(game_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_hr_outcomes_date ON hr_outcomes(game_date);
CREATE INDEX IF NOT EXISTS idx_hr_outcomes_player ON hr_outcomes(player_id, game_date);

-- ============================================================
-- VIEWS
-- ============================================================

CREATE OR REPLACE VIEW v_todays_picks AS
SELECT
    ms.game_date,
    ms.player_name,
    ms.team,
    ms.opponent,
    ms.opposing_pitcher,
    ms.model_score,
    ms.model_prob,
    ms.book_implied_prob,
    ms.edge,
    ms.signal,
    ms.confidence,
    ms.barrel_score,
    ms.matchup_score,
    ms.park_weather_score,
    ms.pitcher_vuln_score,
    ms.hot_cold_score,
    ms.propfinder_signal,
    ms.ballparkpal_signal,
    ms.hrpredict_signal,
    ms.consensus_agreement,
    w.temperature_f,
    w.wind_speed_mph,
    w.wind_description,
    w.wind_hr_impact
FROM hr_model_scores ms
LEFT JOIN weather w ON ms.game_id = w.game_id
WHERE ms.game_date = CURRENT_DATE
ORDER BY ms.model_score DESC;

CREATE OR REPLACE VIEW v_betting_performance AS
SELECT
    game_date,
    COUNT(*) as total_bets,
    SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
    ROUND(
        CAST(SUM(CASE WHEN result = 'win' THEN 1.0 ELSE 0.0 END) AS NUMERIC)
        / CAST(COUNT(*) AS NUMERIC) * 100
    , 1) as win_pct,
    SUM(profit) as daily_profit,
    SUM(stake) as daily_volume,
    ROUND(
        CAST(SUM(profit) AS NUMERIC)
        / NULLIF(CAST(SUM(stake) AS NUMERIC), 0) * 100
    , 1) as roi_pct
FROM bets
WHERE result IN ('win', 'loss')
GROUP BY game_date
ORDER BY game_date DESC;

CREATE OR REPLACE VIEW v_tool_accuracy AS
SELECT
    'My Model' as tool,
    COUNT(*) as picks_tracked,
    SUM(CASE WHEN ms.signal = 'BET' AND o.hit_hr THEN 1 ELSE 0 END) as bet_wins,
    SUM(CASE WHEN ms.signal = 'BET' THEN 1 ELSE 0 END) as bet_total,
    ROUND(
        CAST(SUM(CASE WHEN ms.signal = 'BET' AND o.hit_hr THEN 1 ELSE 0 END) AS NUMERIC)
        / NULLIF(CAST(SUM(CASE WHEN ms.signal = 'BET' THEN 1 ELSE 0 END) AS NUMERIC), 0) * 100
    , 1) as bet_win_pct
FROM hr_model_scores ms
LEFT JOIN hr_outcomes o ON ms.game_id = o.game_id AND ms.player_id = o.player_id
WHERE o.game_id IS NOT NULL;

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE hr_model_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE bets ENABLE ROW LEVEL SECURITY;
ALTER TABLE weather ENABLE ROW LEVEL SECURITY;
ALTER TABLE games ENABLE ROW LEVEL SECURITY;
ALTER TABLE stadiums ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE batter_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE pitcher_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr_odds ENABLE ROW LEVEL SECURITY;
ALTER TABLE umpires ENABLE ROW LEVEL SECURITY;
ALTER TABLE park_factors ENABLE ROW LEVEL SECURITY;

-- Public read access (dashboard reads with anon key)
CREATE POLICY "Public read" ON hr_model_scores FOR SELECT USING (true);
CREATE POLICY "Public read" ON bets FOR SELECT USING (true);
CREATE POLICY "Public read" ON weather FOR SELECT USING (true);
CREATE POLICY "Public read" ON games FOR SELECT USING (true);
CREATE POLICY "Public read" ON stadiums FOR SELECT USING (true);
CREATE POLICY "Public read" ON hr_outcomes FOR SELECT USING (true);
CREATE POLICY "Public read" ON batter_stats FOR SELECT USING (true);
CREATE POLICY "Public read" ON pitcher_stats FOR SELECT USING (true);
CREATE POLICY "Public read" ON hr_odds FOR SELECT USING (true);
CREATE POLICY "Public read" ON umpires FOR SELECT USING (true);
CREATE POLICY "Public read" ON park_factors FOR SELECT USING (true);

-- Public write on bets and model scores (dashboard inserts/updates)
CREATE POLICY "Public insert" ON bets FOR INSERT WITH CHECK (true);
CREATE POLICY "Public update" ON bets FOR UPDATE USING (true);
CREATE POLICY "Public update" ON hr_model_scores FOR UPDATE USING (true);
