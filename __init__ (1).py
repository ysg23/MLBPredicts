-- MLB HR Prop Pipeline - Database Schema
-- SQLite database for storing all pipeline data

-- ============================================================
-- REFERENCE / STATIC TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS stadiums (
    stadium_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    team_abbr TEXT NOT NULL,
    city TEXT,
    state TEXT,
    latitude REAL,
    longitude REAL,
    elevation_ft INTEGER,
    roof_type TEXT CHECK(roof_type IN ('open', 'retractable', 'dome')),
    lf_distance INTEGER,
    cf_distance INTEGER,
    rf_distance INTEGER,
    hr_park_factor REAL DEFAULT 1.0,  -- season-level HR park factor
    UNIQUE(team_abbr)
);

CREATE TABLE IF NOT EXISTS park_factors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stadium_id INTEGER REFERENCES stadiums(stadium_id),
    season INTEGER NOT NULL,
    hr_factor REAL NOT NULL,          -- HR park factor for the season
    hr_factor_lhb REAL,               -- HR factor for left-handed batters
    hr_factor_rhb REAL,               -- HR factor for right-handed batters
    UNIQUE(stadium_id, season)
);

-- ============================================================
-- DAILY GAME DATA
-- ============================================================

CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,       -- MLB game PK
    game_date DATE NOT NULL,
    game_time TEXT,                     -- ET start time
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
    status TEXT DEFAULT 'scheduled',   -- scheduled, live, final
    home_score INTEGER,
    away_score INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_games_teams ON games(home_team, away_team);

-- ============================================================
-- BATTER STATS (rolling windows for HR model)
-- ============================================================

CREATE TABLE IF NOT EXISTS batter_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    bat_hand TEXT CHECK(bat_hand IN ('L', 'R', 'S')),
    stat_date DATE NOT NULL,           -- date these stats were calculated
    window_days INTEGER NOT NULL,      -- rolling window (7, 14, 30, season)

    -- Core HR indicators
    barrel_pct REAL,                   -- barrel % (Statcast)
    hard_hit_pct REAL,                 -- hard hit % (95+ mph)
    avg_exit_velo REAL,                -- average exit velocity
    max_exit_velo REAL,                -- max exit velocity in window
    fly_ball_pct REAL,                 -- fly ball rate
    hr_per_fb REAL,                    -- HR / fly ball ratio
    pull_pct REAL,                     -- pull rate on fly balls
    avg_launch_angle REAL,             -- average launch angle
    sweet_spot_pct REAL,               -- 8-32 degree LA %

    -- Power metrics
    iso_power REAL,                    -- isolated power (SLG - AVG)
    slg REAL,                          -- slugging percentage
    woba REAL,                         -- weighted on-base average
    xwoba REAL,                        -- expected wOBA (Statcast)
    xslg REAL,                         -- expected SLG

    -- Plate appearances & results
    pa INTEGER,                        -- plate appearances in window
    ab INTEGER,                        -- at bats
    hrs INTEGER,                       -- actual HRs in window
    k_pct REAL,                        -- strikeout rate
    bb_pct REAL,                       -- walk rate

    -- vs handedness splits (for matchup context)
    iso_vs_lhp REAL,
    iso_vs_rhp REAL,
    barrel_pct_vs_lhp REAL,
    barrel_pct_vs_rhp REAL,
    hr_count_vs_lhp INTEGER,
    hr_count_vs_rhp INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, stat_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_batter_stats_player ON batter_stats(player_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_batter_stats_date ON batter_stats(stat_date);

-- ============================================================
-- PITCHER STATS (opponent context for HR model)
-- ============================================================

CREATE TABLE IF NOT EXISTS pitcher_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    pitch_hand TEXT CHECK(pitch_hand IN ('L', 'R')),
    stat_date DATE NOT NULL,
    window_days INTEGER NOT NULL,

    -- HR vulnerability indicators
    hr_per_9 REAL,                     -- HR/9 innings
    hr_per_fb REAL,                    -- HR / fly ball ratio
    fly_ball_pct REAL,                 -- fly ball rate allowed
    hard_hit_pct_against REAL,         -- hard hit % allowed
    barrel_pct_against REAL,           -- barrel % allowed
    avg_exit_velo_against REAL,        -- avg EV allowed

    -- Pitch quality indicators
    avg_fastball_velo REAL,            -- avg 4-seam velocity
    fastball_velo_trend REAL,          -- velo change vs season avg
    whiff_pct REAL,                    -- swinging strike %
    chase_pct REAL,                    -- chase rate induced
    zone_pct REAL,                     -- % pitches in zone

    -- Workload
    innings_pitched REAL,
    pitches_per_start REAL,
    days_rest INTEGER,                 -- days since last start

    -- Overall quality
    era REAL,
    fip REAL,
    xfip REAL,
    xera REAL,                         -- expected ERA (Statcast)

    -- vs handedness splits
    hr_per_9_vs_lhb REAL,
    hr_per_9_vs_rhb REAL,
    iso_allowed_vs_lhb REAL,
    iso_allowed_vs_rhb REAL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, stat_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_pitcher_stats_player ON pitcher_stats(player_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_pitcher_stats_date ON pitcher_stats(stat_date);

-- ============================================================
-- WEATHER (game-day conditions)
-- ============================================================

CREATE TABLE IF NOT EXISTS weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    fetch_time TIMESTAMP NOT NULL,     -- when this was pulled
    temperature_f REAL,
    humidity_pct REAL,
    wind_speed_mph REAL,
    wind_direction_deg INTEGER,        -- 0=N, 90=E, 180=S, 270=W
    wind_description TEXT,             -- "out to CF", "in from LF", "cross L-R"
    wind_hr_impact REAL,              -- calculated multiplier (>1 = helps HR, <1 = hurts)
    precipitation_pct REAL,
    conditions TEXT,                    -- clear, cloudy, dome, etc.
    UNIQUE(game_id, fetch_time)
);

CREATE INDEX IF NOT EXISTS idx_weather_game ON weather(game_id);

-- ============================================================
-- ODDS (HR prop lines from sportsbooks)
-- ============================================================

CREATE TABLE IF NOT EXISTS hr_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    sportsbook TEXT NOT NULL,          -- draftkings, fanduel, betmgm, etc.
    market TEXT DEFAULT 'hr',          -- hr, 2plus_hr
    over_price INTEGER,                -- American odds (+320)
    under_price INTEGER,               -- American odds (-450)
    implied_prob_over REAL,            -- calculated from odds
    implied_prob_under REAL,
    fetch_time TIMESTAMP NOT NULL,
    UNIQUE(game_id, player_id, sportsbook, fetch_time)
);

CREATE INDEX IF NOT EXISTS idx_hr_odds_game ON hr_odds(game_date, player_id);

-- ============================================================
-- UMPIRE TENDENCIES
-- ============================================================

CREATE TABLE IF NOT EXISTS umpires (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umpire_name TEXT NOT NULL,
    season INTEGER NOT NULL,
    games_umped INTEGER,
    avg_runs_per_game REAL,
    k_pct_above_avg REAL,             -- how much more/less Ks vs league avg
    zone_size TEXT,                     -- tight, average, wide
    hr_per_game_avg REAL,
    UNIQUE(umpire_name, season)
);

-- ============================================================
-- MODEL SCORES (daily output)
-- ============================================================

CREATE TABLE IF NOT EXISTS hr_model_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    opponent TEXT,
    opposing_pitcher TEXT,

    -- Individual factor scores (0-100)
    barrel_score REAL,                 -- barrel % rolling rank
    matchup_score REAL,                -- batter vs pitcher hand
    park_weather_score REAL,           -- park factor × wind × temp
    pitcher_vuln_score REAL,           -- pitcher HR vulnerability
    hot_cold_score REAL,               -- recent performance trend

    -- Composite
    model_score REAL,                  -- weighted composite (0-100)
    model_prob REAL,                   -- model's estimated HR probability
    book_implied_prob REAL,            -- best available book implied prob
    edge REAL,                         -- model_prob - book_implied_prob
    signal TEXT CHECK(signal IN ('BET', 'LEAN', 'SKIP', 'FADE')),
    confidence TEXT CHECK(confidence IN ('HIGH', 'MEDIUM', 'LOW')),

    -- Cross-reference with paid tools
    propfinder_signal TEXT,            -- what PropFinder says (manual input)
    ballparkpal_signal TEXT,           -- what BallparkPal says
    hrpredict_signal TEXT,             -- what HomeRunPredict says
    consensus_agreement INTEGER,       -- how many tools agree (0-3)

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(game_id, player_id, game_date)
);

CREATE INDEX IF NOT EXISTS idx_model_scores_date ON hr_model_scores(game_date);
CREATE INDEX IF NOT EXISTS idx_model_scores_signal ON hr_model_scores(signal);

-- ============================================================
-- BET TRACKING (bankroll management)
-- ============================================================

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER REFERENCES games(game_id),
    game_date DATE NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    bet_type TEXT DEFAULT 'hr_yes',    -- hr_yes, hr_no, 2plus_hr
    sportsbook TEXT,
    odds INTEGER,                      -- American odds at time of bet
    stake REAL,                        -- dollars wagered
    units REAL,                        -- unit size
    model_score REAL,                  -- model score at time of bet
    model_edge REAL,                   -- edge at time of bet
    result TEXT CHECK(result IN ('win', 'loss', 'push', 'void', 'pending')),
    payout REAL,                       -- actual payout (0 if loss)
    profit REAL,                       -- payout - stake
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settled_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bets_date ON bets(game_date);
CREATE INDEX IF NOT EXISTS idx_bets_result ON bets(result);

-- ============================================================
-- VIEWS (convenience queries)
-- ============================================================

-- Today's actionable picks
CREATE VIEW IF NOT EXISTS v_todays_picks AS
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
WHERE ms.game_date = DATE('now')
ORDER BY ms.model_score DESC;

-- Betting performance tracker
CREATE VIEW IF NOT EXISTS v_betting_performance AS
SELECT
    game_date,
    COUNT(*) as total_bets,
    SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
    ROUND(SUM(CASE WHEN result = 'win' THEN 1.0 ELSE 0.0 END) / COUNT(*) * 100, 1) as win_pct,
    SUM(profit) as daily_profit,
    SUM(stake) as daily_volume,
    ROUND(SUM(profit) / SUM(stake) * 100, 1) as roi_pct
FROM bets
WHERE result IN ('win', 'loss')
GROUP BY game_date
ORDER BY game_date DESC;

-- Running bankroll
CREATE VIEW IF NOT EXISTS v_bankroll_running AS
SELECT
    game_date,
    SUM(profit) OVER (ORDER BY game_date) as cumulative_profit,
    SUM(stake) OVER (ORDER BY game_date) as cumulative_wagered,
    ROUND(SUM(profit) OVER (ORDER BY game_date) / SUM(stake) OVER (ORDER BY game_date) * 100, 2) as running_roi
FROM bets
WHERE result IN ('win', 'loss')
ORDER BY game_date;

-- Tool accuracy comparison
CREATE VIEW IF NOT EXISTS v_tool_accuracy AS
SELECT
    'PropFinder' as tool,
    COUNT(*) as picks_tracked,
    SUM(CASE WHEN propfinder_signal = 'BET' AND b.result = 'win' THEN 1 ELSE 0 END) as tool_bet_wins,
    SUM(CASE WHEN propfinder_signal = 'BET' THEN 1 ELSE 0 END) as tool_bet_total,
    ROUND(
        CAST(SUM(CASE WHEN propfinder_signal = 'BET' AND b.result = 'win' THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(SUM(CASE WHEN propfinder_signal = 'BET' THEN 1 ELSE 0 END), 0) * 100
    , 1) as tool_win_pct
FROM hr_model_scores ms
LEFT JOIN bets b ON ms.game_id = b.game_id AND ms.player_id = b.player_id
WHERE ms.propfinder_signal IS NOT NULL

UNION ALL

SELECT
    'BallparkPal',
    COUNT(*),
    SUM(CASE WHEN ballparkpal_signal = 'BET' AND b.result = 'win' THEN 1 ELSE 0 END),
    SUM(CASE WHEN ballparkpal_signal = 'BET' THEN 1 ELSE 0 END),
    ROUND(
        CAST(SUM(CASE WHEN ballparkpal_signal = 'BET' AND b.result = 'win' THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(SUM(CASE WHEN ballparkpal_signal = 'BET' THEN 1 ELSE 0 END), 0) * 100
    , 1)
FROM hr_model_scores ms
LEFT JOIN bets b ON ms.game_id = b.game_id AND ms.player_id = b.player_id
WHERE ms.ballparkpal_signal IS NOT NULL

UNION ALL

SELECT
    'HomeRunPredict',
    COUNT(*),
    SUM(CASE WHEN hrpredict_signal = 'BET' AND b.result = 'win' THEN 1 ELSE 0 END),
    SUM(CASE WHEN hrpredict_signal = 'BET' THEN 1 ELSE 0 END),
    ROUND(
        CAST(SUM(CASE WHEN hrpredict_signal = 'BET' AND b.result = 'win' THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(SUM(CASE WHEN hrpredict_signal = 'BET' THEN 1 ELSE 0 END), 0) * 100
    , 1)
FROM hr_model_scores ms
LEFT JOIN bets b ON ms.game_id = b.game_id AND ms.player_id = b.player_id
WHERE ms.hrpredict_signal IS NOT NULL;
