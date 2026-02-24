# MLB Multi-Market Model — Cursor Build Guide (Refactor)

This repo is now refactored into a **market-agnostic engine** with pluggable market modules.

## What's New
- Generic tables: `model_scores`, `market_odds`, `market_outcomes`
- Scoring modules:
  - ✅ HR: `/pipeline/scoring/hr_model.py` (scores players with HR props from `hr_odds`)
  - ✅ K: `/pipeline/scoring/k_model.py` (scores probable starters; odds/lines not wired yet)
  - 🧱 ML/TOTAL/F5: scaffolded modules (return empty until team/bullpen + odds markets are added)
- Pitcher fetcher added: `/pipeline/fetchers/pitchers.py` (writes to `pitcher_stats`)

## Quick Start (Local SQLite)
```bash
cd pipeline
pip install -r requirements.txt  # or pip install pybaseball requests pandas numpy python-dotenv fastapi uvicorn[standard]
python run_pipeline.py --init
python run_pipeline.py --daily --date 2026-03-27
python run_pipeline.py --score --market HR --date 2026-03-27
python run_pipeline.py --score --market K  --date 2026-03-27
```

## Cursor Refactor Prompts (Next)
1) Extend odds fetcher to write K/ML/TOTAL/F5 markets into `market_odds`
2) Add team offense + bullpen fetchers for ML/TOTAL/F5 scoring
3) Implement ML/TOTAL/F5 scoring projections + edge calcs
4) Add generalized backtester keyed by `market`

If you want, I can generate the exact Cursor prompts for each of the above phases.
