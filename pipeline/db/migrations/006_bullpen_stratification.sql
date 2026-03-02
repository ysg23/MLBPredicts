-- Migration 006: Bullpen stratification columns
-- Adds high-leverage bullpen metrics to mlb_team_daily_features.
-- High-leverage tier = setup/closer proxy (K% > 25, or K% > 20 with high workload).
--
-- Run via: python db/migrate.py  (idempotent — safe to re-run)

ALTER TABLE mlb_team_daily_features
    ADD COLUMN IF NOT EXISTS bullpen_high_lev_era_14   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS bullpen_high_lev_k_pct_14 DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS bullpen_high_lev_hr9_14   DOUBLE PRECISION;
