"""
Grade game/team markets from final game state.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import requests

from config import MLB_STATS_BASE
from db.database import query
from grading.base_grader import SUPPORTED_GAME_MARKETS, build_outcome_row


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _game_row(game_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT game_id, home_team, away_team, status, home_score, away_score
        FROM games
        WHERE game_id = ?
        LIMIT 1
        """,
        (game_id,),
    )
    return rows[0] if rows else None


def _is_game_final(game: dict[str, Any] | None) -> bool:
    if not game:
        return False
    status = str(game.get("status") or "").lower()
    return status in {"final", "game over", "completed"}


def _fetch_first5_scores(game_id: int, timeout: int = 20) -> tuple[int | None, int | None]:
    url = f"{MLB_STATS_BASE}/game/{game_id}/linescore"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return None, None

    innings = payload.get("innings") or []
    if not innings:
        return None, None

    home_total = 0
    away_total = 0
    counted = 0
    for inning in innings:
        if counted >= 5:
            break
        away = _safe_int((inning.get("away") or {}).get("runs")) or 0
        home = _safe_int((inning.get("home") or {}).get("runs")) or 0
        away_total += away
        home_total += home
        counted += 1
    if counted < 5:
        return None, None
    return home_total, away_total


def _infer_team_for_team_total(selection: dict[str, Any], game: dict[str, Any]) -> str | None:
    team_id = selection.get("team_id")
    if team_id in {game.get("home_team"), game.get("away_team")}:
        return team_id
    key = str(selection.get("selection_key") or "").upper()
    if "|HOME" in key:
        return game.get("home_team")
    if "|AWAY" in key:
        return game.get("away_team")
    return None


def _selection_outcome_value(selection: dict[str, Any], game: dict[str, Any], first5_cache: dict[int, tuple[int | None, int | None]]) -> tuple[float | None, str | None]:
    market = str(selection.get("market") or "").upper()
    home_score = _safe_int(game.get("home_score"))
    away_score = _safe_int(game.get("away_score"))
    if home_score is None or away_score is None:
        return None, None

    if market == "ML":
        if home_score == away_score:
            return 0.5, f"ml_tie:{home_score}-{away_score}"
        return (1.0 if home_score > away_score else 0.0), f"final:{home_score}-{away_score}"
    if market == "TOTAL":
        total = float(home_score + away_score)
        return total, f"final_total={int(total)}"
    if market == "TEAM_TOTAL":
        target_team = _infer_team_for_team_total(selection, game)
        if target_team is None:
            return None, None
        if target_team == game.get("home_team"):
            value = float(home_score)
        else:
            value = float(away_score)
        return value, f"team_runs={int(value)}"

    # F5 markets rely on linescore
    game_id = int(game["game_id"])
    if game_id not in first5_cache:
        first5_cache[game_id] = _fetch_first5_scores(game_id)
    home_f5, away_f5 = first5_cache[game_id]
    if home_f5 is None or away_f5 is None:
        return None, None

    if market == "F5_ML":
        if home_f5 == away_f5:
            return 0.5, f"f5_tie:{home_f5}-{away_f5}"
        return (1.0 if home_f5 > away_f5 else 0.0), f"f5:{home_f5}-{away_f5}"
    if market == "F5_TOTAL":
        total = float(home_f5 + away_f5)
        return total, f"f5_total={int(total)}"
    return None, None


def grade_game_market_outcomes(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [s for s in selections if str(s.get("market") or "").upper() in SUPPORTED_GAME_MARKETS]
    if not filtered:
        return []

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for selection in filtered:
        game_id = selection.get("game_id")
        if game_id is None:
            continue
        grouped[int(game_id)].append(selection)

    outcomes: list[dict[str, Any]] = []
    first5_cache: dict[int, tuple[int | None, int | None]] = {}
    for game_id, game_rows in grouped.items():
        game = _game_row(game_id)
        if not _is_game_final(game):
            continue
        for selection in game_rows:
            value, text = _selection_outcome_value(selection, game, first5_cache)
            if value is None:
                continue
            outcomes.append(build_outcome_row(selection, value, text))
    return outcomes
