"""
Game context daily feature snapshot builder.

Phase 2D goals:
- Build one game_context_features row per game/date
- Include park, weather, umpire, lineup-confirmation context
- Use deterministic weather multipliers with clean null fallbacks
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from db.database import query, upsert_many


MAX_BATCH_SIZE = 500


def _to_date(game_date: date | str) -> date:
    if isinstance(game_date, date):
        return game_date
    return datetime.strptime(game_date, "%Y-%m-%d").date()


def _chunked(rows: list[dict[str, Any]], size: int = MAX_BATCH_SIZE):
    for idx in range(0, len(rows), size):
        yield rows[idx : idx + size]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _games_for_date(game_dt: date) -> list[dict[str, Any]]:
    return query(
        """
        SELECT game_id, home_team, away_team, stadium_id,
               home_pitcher_id, away_pitcher_id, umpire_name
        FROM games
        WHERE game_date = ?
        ORDER BY game_id
        """,
        (game_dt.strftime("%Y-%m-%d"),),
    )


def _latest_weather(game_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT *
        FROM weather
        WHERE game_id = ?
        ORDER BY fetch_time DESC
        LIMIT 1
        """,
        (game_id,),
    )
    return rows[0] if rows else None


def _park_factors(stadium_id: int | None, season: int) -> tuple[float | None, float | None, float | None]:
    if not stadium_id:
        return None, None, None

    season_rows = query(
        """
        SELECT hr_factor
        FROM park_factors
        WHERE stadium_id = ? AND season = ?
        LIMIT 1
        """,
        (stadium_id, season),
    )
    if season_rows and season_rows[0].get("hr_factor") is not None:
        hr = float(season_rows[0]["hr_factor"])
        return hr, None, None

    fallback_rows = query(
        """
        SELECT hr_park_factor
        FROM stadiums
        WHERE stadium_id = ?
        LIMIT 1
        """,
        (stadium_id,),
    )
    if fallback_rows and fallback_rows[0].get("hr_park_factor") is not None:
        hr = float(fallback_rows[0]["hr_park_factor"])
        return hr, None, None

    return None, None, None


def _lineup_confirmed(game_dt: date, game_id: int, team_id: str) -> bool:
    rows = query(
        """
        SELECT 1
        FROM lineups
        WHERE game_date = ?
          AND game_id = ?
          AND team_id = ?
          AND confirmed = 1
          AND COALESCE(active_version, 1) = 1
        LIMIT 1
        """,
        (game_dt.strftime("%Y-%m-%d"), game_id, team_id),
    )
    return bool(rows)


def _umpire_context(umpire_name: str | None, season: int) -> tuple[float | None, float | None]:
    if not umpire_name:
        return None, None
    rows = query(
        """
        SELECT k_pct_above_avg, avg_runs_per_game
        FROM umpires
        WHERE umpire_name = ? AND season = ?
        LIMIT 1
        """,
        (umpire_name, season),
    )
    if not rows:
        return None, None
    return _to_float(rows[0].get("k_pct_above_avg")), _to_float(rows[0].get("avg_runs_per_game"))


def _weather_multipliers(weather_row: dict[str, Any] | None) -> tuple[float | None, float | None, str | None]:
    if not weather_row:
        return None, None, None

    temp = _to_float(weather_row.get("temperature_f"))
    wind_speed = _to_float(weather_row.get("wind_speed_mph")) or 0.0
    wind_desc = weather_row.get("wind_description")
    wind_hr_impact = _to_float(weather_row.get("wind_hr_impact")) or 1.0

    # HR multiplier prefers upstream weather fetcher's wind-aware impact.
    hr_multiplier = wind_hr_impact
    if temp is not None:
        if temp >= 80:
            hr_multiplier *= 1.03
        elif temp <= 55:
            hr_multiplier *= 0.97

    # Run environment multiplier uses a simple deterministic temp/wind blend.
    run_multiplier = 1.0
    if temp is not None:
        run_multiplier += (temp - 65.0) * 0.0025
    run_multiplier += min(wind_speed, 25.0) * 0.003
    run_multiplier = max(0.8, min(1.25, run_multiplier))

    return round(hr_multiplier, 4), round(run_multiplier, 4), wind_desc


def build_game_context_features(game_date: date | str) -> dict[str, Any]:
    """
    Build game_context_features rows for all games on one date.
    """
    game_dt = _to_date(game_date)
    season = game_dt.year
    print(f"\n🔧 Building game_context_features for {game_dt}")

    games = _games_for_date(game_dt)
    if not games:
        print("  ⚠️ No games found for date")
        return {
            "game_date": game_dt.strftime("%Y-%m-%d"),
            "rows_upserted": 0,
            "warnings": ["No games found for date"],
        }

    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for game in games:
        game_id = int(game["game_id"])
        home_team_id = str(game["home_team"])
        away_team_id = str(game["away_team"])
        home_pitcher_id = game.get("home_pitcher_id")
        away_pitcher_id = game.get("away_pitcher_id")
        umpire_name = game.get("umpire_name")

        park_hr, park_runs, park_hits = _park_factors(game.get("stadium_id"), season=season)
        weather_row = _latest_weather(game_id)
        weather_hr_mult, weather_run_mult, wind_dir = _weather_multipliers(weather_row)

        temp_f = _to_float(weather_row.get("temperature_f")) if weather_row else None
        wind_speed_mph = _to_float(weather_row.get("wind_speed_mph")) if weather_row else None

        umpire_k_boost, umpire_run_env = _umpire_context(umpire_name, season=season)

        lineups_confirmed_home = _lineup_confirmed(game_dt, game_id, home_team_id)
        lineups_confirmed_away = _lineup_confirmed(game_dt, game_id, away_team_id)

        has_probable_pitchers = home_pitcher_id is not None and away_pitcher_id is not None
        has_weather = weather_row is not None
        is_final_context = (
            lineups_confirmed_home
            and lineups_confirmed_away
            and has_weather
            and has_probable_pitchers
        )

        if not has_weather:
            warnings.append(f"game_id={game_id}: missing_weather")
        if not lineups_confirmed_home or not lineups_confirmed_away:
            warnings.append(f"game_id={game_id}: lineup_pending")
        if not has_probable_pitchers:
            warnings.append(f"game_id={game_id}: probable_pitcher_missing")

        rows.append(
            {
                "game_date": game_dt.strftime("%Y-%m-%d"),
                "game_id": game_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "home_pitcher_id": home_pitcher_id,
                "away_pitcher_id": away_pitcher_id,
                "park_factor_hr": park_hr,
                "park_factor_runs": park_runs,
                "park_factor_hits": park_hits,
                "weather_temp_f": temp_f,
                "weather_wind_speed_mph": wind_speed_mph,
                "weather_wind_dir": wind_dir,
                "weather_hr_multiplier": weather_hr_mult,
                "weather_run_multiplier": weather_run_mult,
                "umpire_name": umpire_name,
                "umpire_k_boost": umpire_k_boost,
                "umpire_run_env": umpire_run_env,
                "lineups_confirmed_home": 1 if lineups_confirmed_home else 0,
                "lineups_confirmed_away": 1 if lineups_confirmed_away else 0,
                "is_final_context": 1 if is_final_context else 0,
            }
        )

    upserted = 0
    for batch in _chunked(rows, size=MAX_BATCH_SIZE):
        upserted += upsert_many(
            "game_context_features",
            batch,
            conflict_cols=["game_date", "game_id"],
        )

    print(
        "  ✅ Game context features built: "
        f"generated={len(rows)}, upserted={upserted}, warnings={len(warnings)}"
    )
    return {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "rows_generated": len(rows),
        "rows_upserted": upserted,
        "warnings": warnings,
    }
