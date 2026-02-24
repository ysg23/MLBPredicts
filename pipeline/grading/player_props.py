"""
Grade player prop markets from MLB boxscore data.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import requests

from config import MLB_STATS_BASE
from db.database import query
from grading.base_grader import SUPPORTED_PLAYER_PROP_MARKETS, build_outcome_row


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


def _fetch_boxscore(game_id: int, timeout: int = 20) -> dict[str, Any] | None:
    url = f"{MLB_STATS_BASE}/game/{game_id}/boxscore"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _is_game_final(game_id: int) -> bool:
    rows = query(
        """
        SELECT status
        FROM games
        WHERE game_id = ?
        LIMIT 1
        """,
        (game_id,),
    )
    if not rows:
        return False
    status = str(rows[0].get("status") or "").lower()
    return status in {"final", "game over", "completed"}


def _extract_player_stats(boxscore: dict[str, Any]) -> dict[int, dict[str, int]]:
    stats_by_player: dict[int, dict[str, int]] = defaultdict(lambda: {"hr": 0, "hits": 0, "tb": 0, "k": 0, "outs": 0})
    teams = (boxscore.get("teams") or {})
    for side in ("home", "away"):
        team = teams.get(side) or {}
        players = team.get("players") or {}
        for player_key, player_payload in players.items():
            player_id = _safe_int(str(player_key).replace("ID", ""))
            if player_id is None:
                continue
            stats = player_payload.get("stats") or {}
            batting = stats.get("batting") or {}
            pitching = stats.get("pitching") or {}
            hr = _safe_int(batting.get("homeRuns")) or 0
            hits = _safe_int(batting.get("hits")) or 0
            tb = _safe_int(batting.get("totalBases")) or 0
            k = _safe_int(pitching.get("strikeOuts")) or 0
            outs = _safe_int(pitching.get("outs")) or 0

            stats_by_player[player_id]["hr"] = hr
            stats_by_player[player_id]["hits"] = hits
            stats_by_player[player_id]["tb"] = tb
            stats_by_player[player_id]["k"] = k
            stats_by_player[player_id]["outs"] = outs
    return stats_by_player


def _selection_outcome_value(selection: dict[str, Any], player_stats: dict[int, dict[str, int]]) -> tuple[float | None, str | None]:
    market = str(selection.get("market") or "").upper()
    player_id = selection.get("player_id")
    if player_id is None:
        return None, None
    player = player_stats.get(int(player_id))
    if player is None:
        return None, None

    if market == "HR":
        value = float(player["hr"])
        return value, f"hr={int(value)}"
    if market in {"HITS_1P", "HITS_LINE"}:
        value = float(player["hits"])
        return value, f"hits={int(value)}"
    if market == "TB_LINE":
        value = float(player["tb"])
        return value, f"tb={int(value)}"
    if market == "K":
        value = float(player["k"])
        return value, f"k={int(value)}"
    if market == "OUTS_RECORDED":
        value = float(player["outs"])
        return value, f"outs={int(value)}"
    return None, None


def grade_player_prop_outcomes(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [s for s in selections if str(s.get("market") or "").upper() in SUPPORTED_PLAYER_PROP_MARKETS]
    if not filtered:
        return []

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for selection in filtered:
        game_id = selection.get("game_id")
        if game_id is None:
            continue
        grouped[int(game_id)].append(selection)

    outcomes: list[dict[str, Any]] = []
    for game_id, game_rows in grouped.items():
        if not _is_game_final(game_id):
            continue
        boxscore = _fetch_boxscore(game_id)
        if not boxscore:
            continue
        player_stats = _extract_player_stats(boxscore)
        for selection in game_rows:
            value, text = _selection_outcome_value(selection, player_stats)
            if value is None:
                continue
            outcomes.append(build_outcome_row(selection, value, text))
    return outcomes
