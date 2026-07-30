"""schedules -> nfl_coaches, one row per (game_id, team).

load_schedules() publishes home_coach/away_coach side by side. Unpivoting into
one row per team collapses them into a single head_coach column, which makes
"who was coaching this team in this game" a direct lookup and turns tenure
stints into a GROUP BY. Mid-season firings need no special case -- they are
simply two different values across two weeks.
"""
from __future__ import annotations

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.coaches.schema import NFL_COACHES_SCHEMA


def transform_coaches(schedules: pd.DataFrame, season: int) -> pd.DataFrame:
    if schedules.empty:
        return pd.DataFrame(columns=[s.name for s in NFL_COACHES_SCHEMA])  # type: ignore[arg-type]

    date_col = "gameday" if "gameday" in schedules.columns else "game_date"
    base = schedules[["game_id", "week", date_col, "home_team", "away_team",
                      "home_coach", "away_coach"]].copy()

    home = base.rename(columns={"home_team": "team", "away_team": "opponent",
                                "home_coach": "head_coach"})  # type: ignore[call-overload]
    home = home.drop(columns=["away_coach"])
    home["is_home"] = True
    away = base.rename(columns={"away_team": "team", "home_team": "opponent",
                                "away_coach": "head_coach"})  # type: ignore[call-overload]
    away = away.drop(columns=["home_coach"])
    away["is_home"] = False

    out = pd.concat([home, away], ignore_index=True)
    out["season"] = season
    out["game_date"] = pd.to_datetime(out[date_col], errors="coerce").dt.date
    out = out.drop(columns=[date_col])
    return align_to_schema(out, NFL_COACHES_SCHEMA)
