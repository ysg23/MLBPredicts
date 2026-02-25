-- Migration 004: Enhanced context features
-- Adds day/night game classification, times-through-the-order (TTO) metrics,
-- and refined lineup-order-aware scoring support.

-- ============================================================
-- game_context_features: day/night classification
-- ============================================================
ALTER TABLE game_context_features ADD COLUMN IF NOT EXISTS is_day_game SMALLINT;
ALTER TABLE game_context_features ADD COLUMN IF NOT EXISTS game_time_et TEXT;

-- ============================================================
-- pitcher_daily_features: times-through-the-order (TTO) metrics
-- ============================================================
-- TTO K decay: estimated % decline in K rate from 1st to 3rd time through lineup
-- League avg is ~15-20% decline; elite starters may only decline 8-10%
ALTER TABLE pitcher_daily_features ADD COLUMN IF NOT EXISTS tto_k_decay_pct DOUBLE PRECISION;

-- TTO HR increase: estimated % increase in HR allowed rate from 1st to 3rd TTO
ALTER TABLE pitcher_daily_features ADD COLUMN IF NOT EXISTS tto_hr_increase_pct DOUBLE PRECISION;

-- TTO performance score: composite 0-100 score of how well pitcher holds up through order
-- Higher = better at maintaining stuff deeper into games
ALTER TABLE pitcher_daily_features ADD COLUMN IF NOT EXISTS tto_endurance_score DOUBLE PRECISION;

-- ============================================================
-- batter_daily_features: lineup-position-aware PA expectation
-- ============================================================
-- Stores the player's most common recent batting order position
ALTER TABLE batter_daily_features ADD COLUMN IF NOT EXISTS recent_lineup_slot INTEGER;
