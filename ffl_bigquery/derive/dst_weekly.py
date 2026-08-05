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

from typing import cast

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.derive.dst_scoring import (
    BLOCKED_KICK_POINTS,
    FUMBLE_RECOVERY_POINTS,
    INTERCEPTION_POINTS,
    SACK_POINTS,
    SAFETY_POINTS,
    TOUCHDOWN_POINTS,
    points_allowed_bonus,
)
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

_COUNT_COLUMNS = [
    "sacks", "interceptions", "fumble_recoveries", "safeties",
    "defensive_tds", "blocked_kicks",
]


def _flag(df: pd.DataFrame, name: str) -> pd.Series:
    """A play-by-play event flag as a 0/1 integer series.

    Upstream dtypes are vintage-dependent -- the same flag arrives as Float64 in
    some seasons and Int32 in others, and is absent entirely from the oldest
    frames. Coercing here means the aggregation never silently sums a column of
    strings or trips over a missing one.
    """
    if name not in df.columns:
        return pd.Series(0, index=df.index, dtype="int64")
    # pd.to_numeric(...).fillna(...).astype(...) is a genuine pandas-stub gap
    # (same one documented at writer.py:45 / scheme_week.py:180) -- cast the
    # result so the gap doesn't cascade into every downstream use.
    return cast(
        pd.Series,
        pd.to_numeric(df[name], errors="coerce").fillna(0).astype("int64"),  # type: ignore[union-attr]
    )


def _team_game_rows(schedules: pd.DataFrame, season: int) -> pd.DataFrame:
    """One row per (game, team) for REG games only, carrying the opponent and
    the points that team's defense allowed.

    The schedule is the source of the ROW SET, not the play-by-play: a defense
    that records no countable event still played and still earns its
    points-allowed bonus, so deriving rows from events would silently drop it.
    """
    if schedules.empty:
        return pd.DataFrame()
    reg = schedules[schedules["game_type"] == "REG"]
    if reg.empty:
        return pd.DataFrame()
    home = pd.DataFrame({
        "game_id": reg["game_id"], "week": reg["week"],
        "team": reg["home_team"], "opponent": reg["away_team"],
        "points_allowed_total": reg["away_score"],
    })
    away = pd.DataFrame({
        "game_id": reg["game_id"], "week": reg["week"],
        "team": reg["away_team"], "opponent": reg["home_team"],
        "points_allowed_total": reg["home_score"],
    })
    out = pd.concat([home, away], ignore_index=True)
    out["season"] = season
    return out


def _event_counts(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (game_id, defending team) counts of each scoring event."""
    if pbp.empty or "defteam" not in pbp.columns:
        return pd.DataFrame(columns=["game_id", "team", *_COUNT_COLUMNS])  # type: ignore[arg-type]

    df = pbp.copy()
    df["_sack"] = _flag(df, "sack")
    df["_interception"] = _flag(df, "interception")
    df["_safety"] = _flag(df, "safety")

    # Fumble recovery is credited by who ACTUALLY recovered, not inferred from
    # the offense's fumble_lost flag -- see the fumble_recoveries column gotcha.
    rec_team = df["fumble_recovery_1_team"] if "fumble_recovery_1_team" in df else None
    df["_fumble"] = (
        (rec_team.notna() & (rec_team == df["defteam"])).astype("int64")
        if rec_team is not None else 0
    )

    # Touchdowns are credited by td_team, never by defteam -- defteam is wrong
    # for return TDs. See the defensive_tds column gotcha.
    td_team = df["td_team"] if "td_team" in df else None
    df["_td"] = (
        (_flag(df, "touchdown").astype(bool) & td_team.notna()
         & (td_team == df["defteam"])).astype("int64")
        if td_team is not None else 0
    )

    blocked = pd.Series(False, index=df.index)
    for col in ("field_goal_result", "extra_point_result"):
        if col in df.columns:
            # astype("string") + == "blocked" on an all-None column produces a
            # nullable boolean Series of <NA> (pandas' three-valued logic).
            # OR-ing that into `blocked` (plain numpy bool) upcasts the whole
            # accumulator to nullable-and-unknown, so a game with no blocked
            # kicks and an all-null field_goal_result/extra_point_result column
            # (a real upstream shape) crashes on the int64 cast below instead
            # of just meaning "not blocked". fillna(False) resolves the unknown
            # the only way it can mean here: no recorded block.
            blocked = blocked | (df[col].astype("string") == "blocked").fillna(False)
    if "punt_blocked" in df.columns:
        blocked = blocked | _flag(df, "punt_blocked").astype(bool)
    df["_blocked"] = blocked.astype("int64")

    grouped = (
        df.groupby(["game_id", "defteam"], dropna=True)[
            ["_sack", "_interception", "_fumble", "_safety", "_td", "_blocked"]
        ]
        .sum()
        .reset_index()
    )
    return grouped.rename(columns={
        "defteam": "team", "_sack": "sacks", "_interception": "interceptions",
        "_fumble": "fumble_recoveries", "_safety": "safeties",
        "_td": "defensive_tds", "_blocked": "blocked_kicks",
    })


def derive_dst_weekly(
    pbp: pd.DataFrame, schedules: pd.DataFrame, season: int,
) -> pd.DataFrame:
    """Weekly team-defense fantasy points for one season."""
    rows = _team_game_rows(schedules, season)
    if rows.empty:
        return align_to_schema(pd.DataFrame(), FF_POINTS_DST_WEEKLY_SCHEMA)

    counts = _event_counts(pbp)
    merged = rows.merge(counts, on=["game_id", "team"], how="left")
    for col in _COUNT_COLUMNS:
        if col not in merged.columns:
            merged[col] = 0
        # Same pandas-stub gap as _flag() above -- cast to silence pyright.
        merged[col] = cast(
            pd.Series,
            pd.to_numeric(merged[col], errors="coerce").fillna(0).astype("int64"),  # type: ignore[union-attr]
        )

    merged["points_allowed_total"] = cast(
        pd.Series,
        pd.to_numeric(
            merged["points_allowed_total"], errors="coerce",
        ).astype("Int64"),  # type: ignore[union-attr]
    )
    merged["points_allowed_bonus"] = merged["points_allowed_total"].map(
        lambda p: points_allowed_bonus(None if pd.isna(p) else int(p)),
    ).astype("Int64")

    event_points = (
        merged["sacks"] * SACK_POINTS
        + merged["interceptions"] * INTERCEPTION_POINTS
        + merged["fumble_recoveries"] * FUMBLE_RECOVERY_POINTS
        + merged["safeties"] * SAFETY_POINTS
        + merged["defensive_tds"] * TOUCHDOWN_POINTS
        + merged["blocked_kicks"] * BLOCKED_KICK_POINTS
    )
    merged["fantasy_points_dst"] = (
        event_points + merged["points_allowed_bonus"].fillna(0)
    ).astype("float64")

    merged["season"] = season
    merged["week"] = cast(
        pd.Series,
        pd.to_numeric(merged["week"], errors="coerce").astype("Int64"),  # type: ignore[union-attr]
    )
    return align_to_schema(merged, FF_POINTS_DST_WEEKLY_SCHEMA)
