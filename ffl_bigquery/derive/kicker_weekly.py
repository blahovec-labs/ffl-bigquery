"""ff_points_k_weekly: weekly kicker fantasy points.

Exists because ff_points_weekly does not score kickers. It derives from
load_player_stats(), whose weekly frame carries no kicking columns at all, so
every K row it publishes is arithmetically complete and numerically empty:
measured 2026-08-07, 2024 alone has 569 kicker rows across 43 players summing
to EXACTLY 0.0 fantasy points. Nothing is broken upstream -- the join was never
wrong, the scoring was simply never computed -- which is why the gap survived:
a zero looks like a bad week, not like a missing feature.

Derived from load_pbp() kick events, scored by ffl_bigquery.derive.kicker_scoring.
Every scored component ships beside its own raw count, so a consumer scoring
under different league rules re-derives totals from this table rather than
re-deriving the table -- and so the independent `verify` check can recompute
the components straight from the play-by-play by a different code path. A table
carrying only the total cannot be audited without trusting this transform, and
trusting the transform is exactly how the DST build's two attribution Criticals
survived review.

Two attribution rules, both learned the expensive way on ff_points_dst_weekly:

1. The kicker is `kicker_player_id`, read off the play. It is NEVER inferred
   from possession. nflverse's posteam/defteam orientation is not stable across
   play types -- on a kickoff posteam is the RECEIVING team -- and
   kicker_player_id is populated on kickoffs and punts too, so any rule that
   reaches for a possession column to identify "the kicker's team" is reading a
   column that means something different depending on the play. Measured on
   2024: kicker_player_id is non-null on every field-goal and extra-point
   attempt (45 distinct kickers), so there is nothing to infer.

2. A blocked kick is still the kicker's attempt. Blocked field goals take the
   miss penalty and blocked extra points score zero; neither is filtered out.
   Dropping them would quietly inflate every kicker who had one -- 19 blocked
   FGs and 13 blocked XPs in 2024.
"""
from __future__ import annotations

from typing import cast

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.derive.kicker_scoring import extra_point_points, field_goal_points
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition
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


FF_POINTS_K_WEEKLY_SCHEMA: list[ColumnSpec] = [
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season this kicker-week belongs to. Stamped from the sync loop's "
       "season argument, never read from the upstream frame, so a chunk's rows "
       "are always self-consistent.", ["identifier", "partition_key"]),
    _c("week", "INT64", "NULLABLE", "NFL week.",
       "Week the kicks were taken in, as published by load_pbp(). Unlike "
       "ff_points_dst_weekly -- which is regular season only, because it "
       "derives its row set from load_schedules() filtered to game_type='REG' "
       "-- this table follows load_pbp()'s own coverage and therefore includes "
       "postseason weeks (19+). That matches ff_points_weekly, the table this "
       "one exists to complete, so a join between the two does not silently "
       "drop January.",
       ["identifier", "cluster_key"]),
    _c("gsis_id", "STRING", "NULLABLE", "GSIS id of the KICKER.",
       "load_pbp()'s kicker_player_id, which is a GSIS id (^00-\\d+$) and "
       "therefore joins ff_points_weekly.gsis_id and ff_player_xref directly. "
       "This is the row's identity: every count below is grouped by this "
       "column, never by a possession column.",
       ["identifier", "join_key", "cluster_key"],
       ["Attribution is BY kicker_player_id, never by posteam. "
        "kicker_player_id is also populated on kickoffs and punts, where "
        "nflverse's posteam means something different than it does on a "
        "field goal (on a kickoff posteam is the RECEIVING team) -- the same "
        "orientation flip that cost ff_points_dst_weekly all 7 of its 2024 "
        "kickoff-return touchdowns. Only field-goal and extra-point attempts "
        "are read here, and the kicker is read off the play rather than "
        "inferred from it.",
        "Measured on 2024: kicker_player_id is non-null on all 1,166 field- "
        "goal and 1,302 extra-point attempts, across 45 distinct kickers. An "
        "attempt with a null kicker id would be dropped rather than grouped "
        "under a null key; the verify check recounts attempts straight from "
        "the play-by-play, so such a drop surfaces as a disagreement instead "
        "of as a quietly smaller table."],
       "kicker_player_id"),
    _c("player_name", "STRING", "NULLABLE", "Kicker display name.",
       "load_pbp()'s kicker_player_name for this kicker, taken from the "
       "week's first attempt. Display only -- gsis_id is the identity.",
       ["dimension"], [], "kicker_player_name"),
    _c("team", "STRING", "NULLABLE", "Team abbreviation of the KICKING team.",
       "The team the kicker kicked for this week, read from posteam on his own "
       "field-goal and extra-point attempts.",
       ["dimension"],
       ["A LABEL, not the grain: rows are grouped by gsis_id, and this column "
        "is carried along from the attempts rather than being any part of the "
        "key. posteam is only safe to read here because it is unambiguous on "
        "the two play types this table reads -- a field goal and an extra "
        "point are both snapped by the kicking team's offense, so posteam IS "
        "the kicking team. That is NOT true of a kickoff (posteam is the "
        "receiving team) or of a punt (posteam is the punting team), which is "
        "why those plays are excluded entirely rather than filtered late."],
       "posteam"),

    _c("fg_made", "INT64", "NULLABLE", "Field goals made.",
       "Count of attempts with field_goal_result='made'. Each is worth 3, 4 or "
       "5 points depending on distance -- see fg_made_yards_max's note on why "
       "the count alone does not determine the points.", ["metric"], [],
       "field_goal_result"),
    _c("fg_missed", "INT64", "NULLABLE", "Field goals missed, INCLUDING blocks.",
       "Count of attempts with field_goal_result in ('missed', 'blocked'). "
       "Worth -1 point each.",
       ["metric"],
       ["Blocked field goals are counted here, not dropped: a blocked kick is "
        "still the kicker's attempt and still takes the miss penalty. 2024 has "
        "19 of them; excluding them would silently inflate every kicker who "
        "had one, and would do it invisibly because the total would still look "
        "like a plausible number.",
        "fg_made + fg_missed is the attempt count -- there is no third "
        "outcome. An unrecognised field_goal_result raises rather than being "
        "counted as neither (see derive/kicker_scoring.py), so the two "
        "columns cannot silently fail to reconcile."],
       "field_goal_result"),
    _c("fg_made_yards_max", "INT64", "NULLABLE",
       "Longest MADE field goal, in yards.",
       "Maximum kick_distance over this week's MADE field goals. Distance is "
       "what selects the scoring tier (<=39 -> 3, 40-49 -> 4, 50+ -> 5), so a "
       "consumer re-scoring under different tiers needs at least this much of "
       "the distance distribution; the per-kick distances themselves live in "
       "the play-by-play.",
       ["metric"],
       ["Computed over MADE kicks only. The longest ATTEMPT is a different "
        "number and would overstate the kicker -- a 60-yard miss is not a "
        "60-yard make.",
        "NULL, never 0, when the kicker made no field goal that week. A 0 "
        "would read as a made 0-yard field goal rather than as 'no makes'."],
       "kick_distance"),
    _c("xp_made", "INT64", "NULLABLE", "Extra points made.",
       "Count of attempts with extra_point_result='good'. Worth 1 point each.",
       ["metric"], [], "extra_point_result"),
    _c("xp_missed", "INT64", "NULLABLE",
       "Extra points missed, INCLUDING blocks.",
       "Count of attempts with extra_point_result in ('failed', 'blocked'). "
       "Worth 0 points -- there is no penalty for a missed extra point under "
       "these rules -- but the attempt is still counted so xp_made + "
       "xp_missed reconciles against the play-by-play's attempt count.",
       ["metric"],
       ["Scores 0.0, which is NOT the same as being dropped: a dropped attempt "
        "breaks the reconciliation the verify check depends on. 2024 has 44 "
        "failed and 13 blocked extra points."],
       "extra_point_result"),
    _c("fantasy_points_kicker", "FLOAT64", "NULLABLE",
       "Total kicker fantasy points for the week.",
       "Sum of every scored attempt: made field goals at 3/4/5 by distance, "
       "missed and blocked field goals at -1, made extra points at 1, missed "
       "and blocked extra points at 0. Scored per ATTEMPT and then summed -- "
       "never re-derived from the count columns -- because the distance tier "
       "is a property of the individual kick that the counts do not carry. "
       "FLOAT64 to match ff_points_weekly's fantasy-point columns, so a UNION "
       "across the two needs no cast.",
       ["metric"],
       ["Not reconstructible from the count columns alone: three made field "
        "goals are worth anywhere from 9 to 15 points depending on distance. "
        "That is the point of shipping the components AND the total -- the "
        "verify check recomputes both from the play-by-play independently, "
        "rather than checking this total against columns this same transform "
        "wrote (a self-confirming check, and the exact shape that let two DST "
        "attribution bugs through)."]),
    INGESTED_AT_SPEC,
]

_COUNT_COLUMNS = ["fg_made", "fg_missed", "xp_made", "xp_missed"]

# The only two play shapes this table reads. Both are snapped by the kicking
# team, so posteam is unambiguous on them -- see `team`'s gotcha. Kickoffs and
# punts also carry a kicker_player_id and are deliberately NOT read.
_FG_RESULT = "field_goal_result"
_XP_RESULT = "extra_point_result"
_FG_MADE = ("made",)
_FG_MISS = ("missed", "blocked")
_XP_MADE = ("good",)
_XP_MISS = ("failed", "blocked")

_ATTEMPT_COLUMNS = ["week", "gsis_id", "player_name", "team", "kind",
                    "result", "distance"]


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """`df[name]` if present, else an all-NA Series aligned to df's index.

    Also the single place a bare `df["col"]` bracket access is cast to
    `pd.Series` -- pandas-stubs' `__getitem__` overloads are ambiguous enough
    that leaving this uncast cascades into downstream type errors (same helper,
    same reason, as derive/scheme_week.py's `_col`).
    """
    if name in df.columns:
        return cast(pd.Series, df[name])
    return pd.Series([pd.NA] * len(df), index=df.index, dtype="object")


def _attempts(pbp: pd.DataFrame, result_col: str, kind: str) -> pd.DataFrame:
    """One row per kick attempt of `kind`, keyed by the KICKER.

    An attempt is any play carrying a non-null `result_col`. That is
    deliberately the whole vocabulary -- 'blocked' included -- rather than an
    allow-list of the outcomes we know how to score: an unfamiliar result
    string reaches kicker_scoring and raises there, instead of being filtered
    out here and disappearing from both the counts and the total at once.
    """
    if pbp.empty or result_col not in pbp.columns:
        return pd.DataFrame(columns=_ATTEMPT_COLUMNS)  # type: ignore[arg-type]
    # notna() on an object column of None returns plain bools; on a nullable
    # dtype it also returns plain bools (notna is never <NA>), so this mask is
    # always safe for .loc.
    mask = _col(pbp, result_col).notna() & _col(pbp, "kicker_player_id").notna()
    rows = cast(pd.DataFrame, pbp[mask])
    if rows.empty:
        return pd.DataFrame(columns=_ATTEMPT_COLUMNS)  # type: ignore[arg-type]
    out = pd.DataFrame({
        "week": cast(pd.Series, pd.to_numeric(_col(rows, "week"), errors="coerce")),
        "gsis_id": _col(rows, "kicker_player_id").astype("string"),
        "player_name": _col(rows, "kicker_player_name").astype("string"),
        # posteam, read ONLY on these two play types -- see `team`'s gotcha.
        "team": _col(rows, "posteam").astype("string"),
        "result": _col(rows, result_col).astype("string"),
        "distance": cast(
            pd.Series, pd.to_numeric(_col(rows, "kick_distance"), errors="coerce"),
        ),
    })
    out["kind"] = kind
    return out.reindex(columns=_ATTEMPT_COLUMNS)


def _score(kind: str, result: object, distance: object) -> float:
    """Points for a single attempt, via the pure scoring rules.

    NaN is normalised to None before it reaches field_goal_points. Passing NaN
    through would NOT raise the "made field goal has unknown distance" error it
    is supposed to: `nan <= 39` is False for every tier bound, so an unknown
    distance would fall through to the open-ended final tier and score a made
    kick at the maximum 5 points. The one place a missing value silently
    becomes the most generous answer, so it is converted, not trusted.
    """
    res = str(result)
    if kind == "xp":
        return extra_point_points(res)
    # cast: pd.isna is typed as bool | ndarray | NDFrame because it accepts
    # array-likes; `distance` here is always a scalar from a Series iteration.
    dist = None if cast(bool, pd.isna(distance)) else float(cast(float, distance))
    return field_goal_points(dist, res)


def derive_kicker_weekly(pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    """Weekly kicker fantasy points for one season.

    Grain: one row per (season, week, gsis_id). A kicker who attempted nothing
    in a week has no row -- as opposed to a 0.0 row -- because the play-by-play
    cannot distinguish "inactive" from "never got in range", and only the
    latter is honestly a zero.
    """
    # Empty frames are dropped BEFORE the concat rather than after: pandas
    # warns (and will eventually change dtypes) when an all-NA frame takes
    # part, and a season with no extra points is a real shape for a one-game
    # slice, not an error.
    frames = [
        f for f in (_attempts(pbp, _FG_RESULT, "fg"), _attempts(pbp, _XP_RESULT, "xp"))
        if not f.empty
    ]
    if not frames:
        return align_to_schema(pd.DataFrame(), FF_POINTS_K_WEEKLY_SCHEMA)
    attempts = pd.concat(frames, ignore_index=True)

    is_fg = attempts["kind"] == "fg"
    is_xp = attempts["kind"] == "xp"
    result = attempts["result"]
    attempts["fg_made"] = (is_fg & result.isin(_FG_MADE)).astype("int64")
    attempts["fg_missed"] = (is_fg & result.isin(_FG_MISS)).astype("int64")
    attempts["xp_made"] = (is_xp & result.isin(_XP_MADE)).astype("int64")
    attempts["xp_missed"] = (is_xp & result.isin(_XP_MISS)).astype("int64")
    # Longest MADE field goal only -- a 60-yard miss is not a 60-yard make.
    attempts["made_distance"] = attempts["distance"].where(attempts["fg_made"] == 1)
    # Scored per attempt, then summed. The distance tier is a property of the
    # individual kick, so no aggregation of the counts can reproduce it.
    attempts["points"] = [
        _score(kind, result, distance)
        for kind, result, distance in zip(
            attempts["kind"], attempts["result"], attempts["distance"], strict=True,
        )
    ]

    # dropna=False: a null week would otherwise vanish from the output with no
    # trace. There are none in any measured season, so this is a guard, not a
    # code path -- but a silently smaller table is the failure mode this
    # library keeps paying for.
    grouped = attempts.groupby(["week", "gsis_id"], dropna=False, sort=True).agg(
        player_name=("player_name", "first"),
        team=("team", "first"),
        fg_made=("fg_made", "sum"),
        fg_missed=("fg_missed", "sum"),
        fg_made_yards_max=("made_distance", "max"),
        xp_made=("xp_made", "sum"),
        xp_missed=("xp_missed", "sum"),
        fantasy_points_kicker=("points", "sum"),
    ).reset_index()

    for col in _COUNT_COLUMNS:
        grouped[col] = grouped[col].astype("Int64")
    # Float64 -> Int64 rather than a bare astype: kick_distance is float
    # upstream (and NaN for a kicker with no makes), and the nullable
    # intermediate is what lets the NA survive the cast as pd.NA instead of
    # raising or becoming a sentinel integer.
    grouped["fg_made_yards_max"] = (
        grouped["fg_made_yards_max"].astype("Float64").astype("Int64")
    )
    grouped["fantasy_points_kicker"] = grouped["fantasy_points_kicker"].astype("Float64")
    grouped["week"] = grouped["week"].astype("Int64")
    grouped["season"] = season
    return align_to_schema(grouped, FF_POINTS_K_WEEKLY_SCHEMA)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_pbp(seasons=[season]).to_pandas()


KICKER_WEEKLY_SPEC = NflverseTableSpec(
    name="ff_points_k_weekly",
    loader=_load,
    schema=FF_POINTS_K_WEEKLY_SCHEMA,
    partition=SeasonRangePartition(clustering=["week", "gsis_id"]),
    transform=derive_kicker_weekly,
    min_season=1999,
)
