"""ff_points_dst_weekly: weekly team-defense fantasy points.

Exists because ff_points_weekly scores PLAYERS only -- it derives from
load_player_stats(), which has no team-defense rows at all -- while fantasy
drafts have always rostered a DST. Measured 2026-07-31: the ADP board carries
12 DEF entries per season with zero scoreable outcomes, so a DST slot either
scored NULL or had to be excluded from the league entirely.

Derived from load_pbp() event flags plus load_schedules() final scores. Every
scored component ships beside its own raw count column, so a consumer scoring
under different league rules re-derives totals from this table rather than
re-deriving the table.
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


FF_POINTS_DST_WEEKLY_SCHEMA: list[ColumnSpec] = [
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season this defensive week belongs to. Stamped from the sync loop's "
       "season argument, never read from the upstream frame, so a chunk's rows "
       "are always self-consistent.", ["identifier", "partition_key"]),
    _c("week", "INT64", "NULLABLE", "NFL week.",
       "Regular-season week. Postseason is excluded entirely (game_type='REG'), "
       "so this never exceeds 17 for seasons through 2020 or 18 from 2021 on -- "
       "the league added a regular-season week in 2021.",
       ["identifier", "cluster_key"]),
    _c("team", "STRING", "NULLABLE", "Team abbreviation of the DEFENSE.",
       "The team whose defense/special teams accumulated this row. Note this is "
       "the defending team, not the possessing offense -- the raw play-by-play "
       "is offense-oriented and had to be re-attributed.",
       ["identifier", "cluster_key", "dimension"]),
    _c("opponent", "STRING", "NULLABLE", "Opponent team abbreviation.",
       "The offense this defense faced. Its final score is points_allowed_total.",
       ["dimension"]),
    _c("game_id", "STRING", "NULLABLE", "nflverse game identifier.",
       "The game this defensive week came from. One row per (team, game); a "
       "team playing zero games in a week (bye) has no row at all rather than a "
       "zero row, so a missing row means 'did not play', not 'played badly'.",
       ["identifier", "join_key"]),

    _c("sacks", "INT64", "NULLABLE", "Sacks recorded.",
       "Count of sacks credited to this defense, summed from load_pbp()'s sack "
       "flag over plays where this team was defteam. Worth 1 point each.",
       ["metric"], [], "sack"),
    _c("interceptions", "INT64", "NULLABLE", "Interceptions recorded.",
       "Count of interceptions credited to this defense. Worth 2 points each.",
       ["metric"], [], "interception"),
    _c("fumble_recoveries", "INT64", "NULLABLE", "Fumbles recovered.",
       "Count of plays where fumble_recovery_1_team equals this team. Worth 2 "
       "points each.",
       ["metric"],
       ["Derived from fumble_recovery_1_team -- NOT from fumble_lost credited "
        "to defteam. fumble_lost is an offensive stat and only implies a "
        "defensive recovery; it gets the awkward cases wrong (muffed punts, an "
        "offense recovering its own fumble, fumbles on a change of possession). "
        "fumble_recovery_1_team names who actually ended up with the ball."],
       "fumble_recovery_1_team"),
    _c("safeties", "INT64", "NULLABLE", "Safeties recorded.",
       "Count of safeties credited to this defense. Worth 2 points each. Rare: "
       "measured 15-24 league-wide per season.", ["metric"], [], "safety"),
    _c("defensive_tds", "INT64", "NULLABLE",
       "Defensive and return touchdowns scored.",
       "Count of touchdowns scored by this team while it was on defense or "
       "special teams. Worth 6 points each.",
       ["metric"],
       ["Credited by td_team, NOT by defteam. On a pick-six the scoring team is "
        "the defense, but on a punt-return touchdown the returning team was the "
        "receiving team on that play -- defteam gets exactly one of those two "
        "cases wrong. td_team is the only column that answers 'who scored'."],
       "td_team"),
    _c("blocked_kicks", "INT64", "NULLABLE", "Kicks blocked.",
       "Count of blocked field goals, extra points, and punts credited to this "
       "defense. Worth 2 points each.", ["metric"]),
    _c("points_allowed_total", "INT64", "NULLABLE", "Points the opponent scored.",
       "The opponent's final score, read from load_schedules() rather than "
       "summed from terminal play-by-play rows.",
       ["metric"],
       ["This is the opponent's TOTAL final score. It is deliberately NOT net "
        "of points the opponent's own defense or special teams scored against "
        "our offense. Some league rules exclude those; most public ones do not. "
        "The simplification is named in this column rather than hidden, so a "
        "consumer applying stricter rules knows to re-derive."]),
    _c("points_allowed_bonus", "INT64", "NULLABLE",
       "Tiered bonus for points allowed.",
       "Tiered on points_allowed_total: 0 -> +10, 1-6 -> +7, 7-13 -> +4, "
       "14-20 -> +1, 21-27 -> 0, 28-34 -> -1, 35+ -> -4. NULL when the final "
       "score could not be resolved -- never 0, which would silently award a "
       "shutout bonus.", ["metric"]),
    _c("fantasy_points_dst", "FLOAT64", "NULLABLE",
       "Total DST fantasy points for the week.",
       "Sum of every scored component: sacks*1 + interceptions*2 + "
       "fumble_recoveries*2 + safeties*2 + defensive_tds*6 + blocked_kicks*2 + "
       "points_allowed_bonus. FLOAT64 to match ff_points_weekly's fantasy-point "
       "columns, so a UNION across the two needs no cast.", ["metric"]),
    INGESTED_AT_SPEC,
]
