-- Enable Row Level Security on tables the dashboard reads.
-- The Supabase anon key gets read-only access via these policies.
-- Write operations still require the service_role key (used by the pipeline).

-- model_scores: primary dashboard data source
ALTER TABLE model_scores ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_model_scores"
  ON model_scores FOR SELECT
  TO anon
  USING (true);

-- games: matchup / game time info
ALTER TABLE games ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_games"
  ON games FOR SELECT
  TO anon
  USING (true);

-- market_odds: sportsbook odds display
ALTER TABLE market_odds ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_market_odds"
  ON market_odds FOR SELECT
  TO anon
  USING (true);

-- bets: performance, bankroll, CLV tabs
ALTER TABLE bets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_bets"
  ON bets FOR SELECT
  TO anon
  USING (true);

-- score_runs: model health / freshness checks
ALTER TABLE score_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_score_runs"
  ON score_runs FOR SELECT
  TO anon
  USING (true);

-- market_outcomes: grading/settlement display
ALTER TABLE market_outcomes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "dashboard_read_market_outcomes"
  ON market_outcomes FOR SELECT
  TO anon
  USING (true);

-- Allow the service_role (pipeline) full access on all RLS-enabled tables.
-- Supabase service_role bypasses RLS by default, but these are explicit
-- in case the project changes that setting.
CREATE POLICY "service_full_model_scores"
  ON model_scores FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_games"
  ON games FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_market_odds"
  ON market_odds FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_bets"
  ON bets FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_score_runs"
  ON score_runs FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

CREATE POLICY "service_full_market_outcomes"
  ON market_outcomes FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);
