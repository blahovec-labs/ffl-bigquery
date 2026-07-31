"""nfl_coaches: head coach at per-game grain, one row per (game_id, team).

load_schedules() carries home_coach/away_coach side by side with 100% fill
(measured 24/24 across four eras -- 2005/2012/2019/2024). Storing one row per
(game_id, team) instead of one row per season means tenure stints fall out of
a GROUP BY and mid-season firings need no special-case rule: the same team
just carries two different head_coach values across two weeks. See
ffl_bigquery/coaches/transform.py for the unpivot that produces this shape.
"""
from __future__ import annotations

from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec


def _c(name: str, type: str, mode: str, short: str, definition: str,
       tags: list[str] | None = None, gotchas: list[str] | None = None,
       source_field: str = "") -> ColumnSpec:
    return ColumnSpec(
        name=name, type=type, mode=mode, short_description=short,  # type: ignore[arg-type]
        business_definition=definition, semantic_tags=tags or [],
        valid_range=None, valid_values=None, example_value=None,
        gotchas=gotchas or [], source_field=source_field or name,
        deprecated_in_year=None,
    )


NFL_COACHES_SCHEMA: list[ColumnSpec] = [
    _c("game_id", "STRING", "REQUIRED", "nflverse game id.",
       "The nflverse game identifier (e.g. '2019_01_GB_CHI'). Joins nfl_plays "
       "and nfl_schedules-derived tables.", ["identifier", "join_key"]),
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season the game belongs to. Stamped from the sync loop's season "
       "argument, not read from the upstream schedule frame, so a chunk's "
       "rows are always self-consistent even if an upstream frame carries "
       "rows for more than one season.", ["identifier", "partition_key"]),
    _c("week", "INT64", "NULLABLE", "NFL week.",
       "Week of the game within the season, as published by load_schedules()."),
    _c("game_date", "DATE", "NULLABLE", "Calendar date of the game.",
       "Kickoff date, from the schedule's gameday field.", [],
       [], "gameday"),
    _c("team", "STRING", "REQUIRED", "Team abbreviation.",
       "The team this row's head_coach coached in this game. Each game "
       "produces two rows -- one for home_team, one for away_team -- so this "
       "column is half of the (game_id, team) grain key.",
       ["identifier", "dimension"]),
    _c("opponent", "STRING", "NULLABLE", "The other team in the game.",
       "The opposing team abbreviation: away_team when team=home_team and "
       "vice versa.", ["dimension"]),
    _c("is_home", "BOOL", "NULLABLE", "Whether `team` was the home team.",
       "True when this row was derived from the game's home_team/home_coach, "
       "False when derived from away_team/away_coach.", ["dimension"]),
    _c("head_coach", "STRING", "NULLABLE", "Head coach of `team` in this game.",
       "The head coach who led `team` in this specific game, from "
       "home_coach/away_coach. Measured at 100% fill in four sampled eras "
       "(2005/2012/2019/2024) -- not a claim about every season 1999-2026, "
       "just those four -- which is what makes per-game grain viable here: "
       "because every game already carries a real coach name, "
       "unpivoting into (game_id, team) rows -- rather than inventing a "
       "season-level 'coach of record' rule -- lets a mid-season firing show "
       "up as two different values across two weeks, with no special case.",
       ["dimension"], [], "home_coach|away_coach"),
    INGESTED_AT_SPEC,
]
