-- Phase 9B extension point: visibility tiering for future paid plans.
ALTER TABLE mlb_model_scores
ADD COLUMN IF NOT EXISTS visibility_tier TEXT DEFAULT 'FREE';

UPDATE mlb_model_scores
SET visibility_tier = COALESCE(visibility_tier, 'FREE');

CREATE INDEX IF NOT EXISTS idx_mlb_model_scores_visibility_tier
ON mlb_model_scores(visibility_tier);
