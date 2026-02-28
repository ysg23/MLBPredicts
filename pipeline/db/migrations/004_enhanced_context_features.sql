ALTER TABLE mlb_game_context_features ADD COLUMN IF NOT EXISTS is_day_game SMALLINT;
ALTER TABLE mlb_game_context_features ADD COLUMN IF NOT EXISTS game_time_et TEXT;
ALTER TABLE mlb_pitcher_daily_features ADD COLUMN IF NOT EXISTS tto_k_decay_pct DOUBLE PRECISION;
ALTER TABLE mlb_pitcher_daily_features ADD COLUMN IF NOT EXISTS tto_hr_increase_pct DOUBLE PRECISION;
ALTER TABLE mlb_pitcher_daily_features ADD COLUMN IF NOT EXISTS tto_endurance_score DOUBLE PRECISION;
ALTER TABLE mlb_batter_daily_features ADD COLUMN IF NOT EXISTS recent_lineup_slot INTEGER;
