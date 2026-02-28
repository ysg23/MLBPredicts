-- Enable Row Level Security on tables the dashboard reads.
-- The Supabase anon key gets read-only access via these policies.
-- Write operations still require the service_role key (used by the pipeline).

-- mlb_model_scores: primary dashboard data source
ALTER TABLE mlb_model_scores ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_model_scores"
  ON mlb_model_scores FOR SELECT
  TO anon
  USING (true);

-- mlb_games: matchup / game time info
ALTER TABLE mlb_games ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_games"
  ON mlb_games FOR SELECT
  TO anon
  USING (true);

-- mlb_market_odds: sportsbook odds display
ALTER TABLE mlb_market_odds ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_market_odds"
  ON mlb_market_odds FOR SELECT
  TO anon
  USING (true);

-- mlb_bets: performance, bankroll, CLV tabs
ALTER TABLE mlb_bets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_bets"
  ON mlb_bets FOR SELECT
  TO anon
  USING (true);

-- mlb_score_runs: model health / freshness checks
ALTER TABLE mlb_score_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_score_runs"
  ON mlb_score_runs FOR SELECT
  TO anon
  USING (true);

-- mlb_market_outcomes: grading/settlement display
ALTER TABLE mlb_market_outcomes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_market_outcomes"
  ON mlb_market_outcomes FOR SELECT
  TO anon
  USING (true);

-- Allow the service_role (pipeline) full access on all RLS-enabled tables.
-- Supabase service_role bypasses RLS by default, but these are explicit
-- in case the project changes that setting.
CREATE POLICY "service_full_model_scores"
  ON mlb_model_scores FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_games"
  ON mlb_games FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_market_odds"
  ON mlb_market_odds FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_bets"
  ON mlb_bets FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_score_runs"
  ON mlb_score_runs FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_market_outcomes"
  ON mlb_market_outcomes FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);
