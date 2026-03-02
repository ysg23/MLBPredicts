# Scoring Conventions

Reference document for the MLB (MLBPredicts) and NHL (NHLPredicts) scoring pipelines.
NHL should port these patterns after backtesting finishes; no shared code yet — just
consistent conventions in both repos.

---

## 1. Market Spec Registry

Every market is described by a `MarketSpec` frozen dataclass registered in
`scoring/market_specs.py` (MLB) or the equivalent in the NHL pipeline.

### Required fields

| Field | Type | Description |
|---|---|---|
| `market` | `str` | Unique market code, e.g. `"HR"`, `"HITS_1P"` |
| `entity_type` | `"batter" \| "pitcher" \| "team" \| "game"` | Primary scoring entity |
| `required_feature_tables` | `tuple[str, ...]` | Tables that must have rows for the date |
| `output_type` | `"probability" \| "projection" \| "hybrid"` | Model output semantics |
| `edge_method` | `"probability_vs_implied" \| "projection_vs_line" \| "hybrid"` | How edge% is calculated |
| `thresholds` | `dict[str, dict[str, float]]` | Per-signal threshold sets (see §2) |
| `lineup_requirement` | `"required" \| "recommended" \| "not_required"` | How missing lineup affects output |
| `missing_data_policy` | `"degrade_confidence" \| "skip_row" \| "store_with_risk_flags"` | Behaviour when inputs are missing |
| `weather_recommended` | `bool` | Whether weather context is used (default `True`) |

### Conventions
- Market codes are UPPERCASE strings.
- `entity_type = "game"` is used for both-sides markets (ML, totals) where the
  scoring unit is the game itself, not a player or team.
- Do not add per-market weights or constants to the `MarketSpec`; those live in the
  model module itself.

---

## 2. Signal Assignment API

```
assign_signal(market, model_score, edge_pct) → "BET" | "LEAN" | "FADE" | "SKIP"
```

Inputs:
- `model_score` — composite 0–100 score computed by the market model
- `edge_pct` — `(model_prob - implied_prob) * 100` for probability markets;
  `((projection - line) / |line|) * 100` for projection markets; `None` when
  no odds are available

### Score-only mode (no odds)
Uses only `model_score` against the `min_score` thresholds:

```
score >= BET.min_score   → BET
score >= LEAN.min_score  → LEAN
score <= FADE.max_score  → FADE
else                     → SKIP
```

### Full mode (odds available)
Requires both score **and** edge to fire:

```
score >= BET.min_score  AND edge >= BET.min_edge_pct  → BET
score >= LEAN.min_score AND edge >= LEAN.min_edge_pct → LEAN
score <= FADE.max_score AND edge <= FADE.max_edge_pct → FADE
else                                                  → SKIP
```

### Standard threshold presets

| Preset | BET score | BET edge | LEAN score | LEAN edge | FADE score | FADE edge |
|---|---|---|---|---|---|---|
| `DEFAULT_THRESHOLDS` | 75 | 5.0% | 60 | 2.5% | ≤35 | ≤-3.0% |
| `CONSERVATIVE_THRESHOLDS` | 78 | 6.0% | 64 | 3.5% | ≤32 | ≤-4.0% |
| `AGGRESSIVE_THRESHOLDS` | 72 | 4.0% | 58 | 2.0% | ≤38 | ≤-2.5% |

Use `optimize_thresholds.py` to derive per-market thresholds from backtest data
before manually updating `market_specs.py`.

---

## 3. Confidence Banding

```
confidence_band(model_score, risk_flags) → "HIGH" | "MEDIUM" | "LOW"
```

Base band from score:
- score ≥ 78 → HIGH
- score ≥ 60 → MEDIUM
- else → LOW

Risk flag degradation (applied after base):
- ≥ 2 risk flags AND base = HIGH → MEDIUM
- ≥ 3 risk flags AND base = MEDIUM → LOW

---

## 4. Risk Flag Schema

Risk flags are stored as a JSON array on each `model_scores` row.

| Flag | Meaning |
|---|---|
| `missing:<input>` | A required input was absent, e.g. `missing:opposing_pitcher_features` |
| `stale:<input>` | Input data is older than expected, e.g. `stale:weather` |
| `lineup_pending` | Lineup not confirmed at scoring time |
| `weather_pending` | Weather data not available at scoring time |

Flags use snake_case input names matching the feature table column or logical input
name (not the DB column name).

---

## 5. Factor Score Conventions

All factor subscores are on a **0–100 scale** where:
- 50 = neutral / league average
- > 50 = favorable for the modelled outcome
- < 50 = unfavorable

### Normalization patterns

**Linear scale** (`_scale_between(x, lo, hi)`):
```python
clamp((x - lo) / (hi - lo) * 100)
```
Use when the factor has a known reasonable range (e.g. barrel%, HR/9).

**Relative slope** (for hot/cold deltas):
```python
relative_slope = delta / max(baseline, floor)
score = clamp(50 + relative_slope * scale, lo_cap, hi_cap)
```
Use instead of raw absolute deltas to avoid penalizing high-baseline players.
- For ISO hot/cold: `baseline = iso_30`, `floor = 0.05`, `scale = 100`, `caps = [10, 90]`
- For hit-rate hot/cold: `baseline = hit_rate_30`, `floor = 0.05`, `scale = 100`, `caps = [10, 90]`

**Platoon advantage** (relative, not absolute):
```python
avg_rate = (split_rate + other_rate) / 2
advantage = (split_rate - avg_rate) / avg_rate
score = clamp(50 + advantage * 150, 20, 80)
```

---

## 6. Backtest Output Format

### CSV columns (one row per simulated bet)

| Column | Type | Description |
|---|---|---|
| `game_date` | date | Game date |
| `market` | str | Market code |
| `game_id` | int | |
| `selection_key` | str | Unique selection identifier |
| `signal` | str | BET/LEAN/FADE/SKIP |
| `model_score` | float | 0–100 |
| `model_prob` | float | 0–1 |
| `edge` | float | Edge % (signed) |
| `side` | str | e.g. OVER, YES, HOME |
| `line` | float | |
| `open_odds` | float | American odds at score time |
| `open_implied_prob` | float | |
| `close_implied_prob` | float | |
| `clv` | float | Closing line value (`open_implied - close_implied`) |
| `outcome_value` | float | Realized stat value |
| `settlement` | str | win/loss/push |
| `profit_units` | float | P&L in units (stake = 1) |
| `score_bucket` | str | <50 / 50-59 / 60-69 / 70-79 / 80+ |
| `prob_bucket` | str | e.g. 60-69% |

### Aggregation metrics

Reported in backtest summary JSON:

| Metric | Definition |
|---|---|
| `win_rate` | wins / (wins + losses), pushes excluded |
| `roi_units` | total_profit / rows_graded |
| `calibration_by_prob_bucket` | avg model_prob vs realized win rate per prob bucket |
| `factor_diagnostics` | Pearson correlation of each factor score with profit_units |

### Sharpe-style threshold metric (optimize_thresholds.py)

```
sharpe = ROI / std(profit_per_bet)
```
- Minimum 30 bets required for a combo to be reported.
- Grid: `min_score ∈ [55, 60, 65, 70, 75, 78, 80]`, `min_edge ∈ [2, 3, 4, 5, 6, 7]%`.
- Best combo = highest Sharpe, then highest ROI as tiebreaker.

---

## 7. Visibility Tier

```
visibility_tier(signal, confidence_band) → "FREE" | "PRO"
```

| Condition | Tier |
|---|---|
| signal = BET **and** confidence_band = HIGH | FREE |
| everything else | PRO |

This is a simple rule — no ML or user attributes involved.  The billing/auth layer
that enforces the tier lives outside the scoring pipeline.

---

## 8. Porting to NHL

When NHLPredicts backtesting is complete, replicate these conventions:

1. Create `scoring/market_specs.py` with `MarketSpec` dataclass and `MARKET_SPECS` dict.
2. Ensure `assign_signal()` uses per-market `thresholds` from the spec (not global constants).
3. Use `build_risk_flags()` and `_confidence_band()` helpers from a shared base engine
   (or copy the pattern verbatim).
4. Store `factors_json`, `reasons_json`, `risk_flags_json` on every score row.
5. Adopt the relative-slope normalization for hot/cold deltas (§5).
6. Adopt platoon advantage formula (§5) when split data is available.
7. Run `optimize_thresholds.py` (or an NHL equivalent) before going live.

No shared Python package is needed yet — duplicate the pattern and reconcile later.
