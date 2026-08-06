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
       "is offense-oriented and had to be re-attributed. That re-attribution is "
       "per EVENT, not per play: nflverse's posteam/defteam orientation is "
       "inverted between punts and kickoffs, so no single upstream column means "
       "'this row's team' (see _event_counts).",
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
        "fumble_recovery_1_team names who actually ended up with the ball.",
        "Counted only when fumble_recovery_1_team differs from fumbled_1_team "
        "-- i.e. a genuine change of possession. Testing recovery against "
        "defteam instead is wrong in BOTH directions on punts, where posteam "
        "is the PUNTING team: measured on 2024 REG, it dropped 23 muffed punts "
        "recovered by the punting team, and wrongly credited 22 muffs the "
        "receiving team recovered itself (no change of possession at all)."],
       "fumble_recovery_1_team"),
    _c("safeties", "INT64", "NULLABLE", "Safeties recorded.",
       "Count of safeties credited to this defense. Worth 2 points each. Rare: "
       "measured 15-24 league-wide per season.", ["metric"], [], "safety"),
    _c("defensive_tds", "INT64", "NULLABLE",
       "Defensive and return touchdowns scored.",
       "Count of touchdowns scored by this team while it was on defense or "
       "special teams. Worth 6 points each.",
       ["metric"],
       ["Credited by td_team, NOT by defteam. td_team is the only column that "
        "answers 'who scored'. Requiring td_team == defteam silently drops "
        "every KICKOFF-return touchdown, because on a kickoff the returning "
        "team is posteam, not defteam: measured on 2024 REG, 8 of 64 "
        "defensive/special-teams touchdowns were dropped, including all 7 "
        "kickoff-return TDs.",
        "Equally, td_team != posteam is NOT the right filter either -- it "
        "drops the same kickoff returns. The rule is 'everything except an "
        "offensive scrimmage touchdown': credit td_team unless the scoring "
        "team was posteam on a play that is not a kickoff or punt."],
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
       "columns, so a UNION across the two needs no cast. Note the two tables "
       "do NOT cover the same weeks: ff_points_weekly includes postseason, "
       "this table is regular season only, so a UNION must filter week ranges "
       "rather than assume they align.", ["metric"],
       ["NULL -- never 0 -- whenever points_allowed_bonus is NULL. A resolved "
        "0.0 would read as 'this defense scored nothing', which is exactly "
        "wrong for a week that has not been played yet: a mid-season "
        "`sync-nflverse --seasons latest` sees all 18 scheduled weeks from "
        "load_schedules() but only the played ones from load_pbp()."]),
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


# nflverse's posteam/defteam orientation is NOT consistent across play types:
#
#   play type      posteam            defteam
#   ------------   ----------------   ----------------
#   run / pass     offense            defense
#   punt           punting team       receiving team
#   kickoff        RECEIVING team     KICKING team
#
# Punt and kickoff are INVERTED relative to each other, so there is no single
# column that means "the team whose defense/special teams made this play".
# Grouping every event on defteam therefore gets special teams wrong in both
# directions -- it silently dropped all 7 kickoff-return TDs and 23 muffed
# punts recovered by the punting team in 2024 REG alone. Each event below is
# aggregated by the team it should actually be CREDITED to, and the per-event
# frames are combined at the end.
_SPECIAL_TEAMS_PLAY_TYPES = ("kickoff", "punt")


def _bool(s: pd.Series) -> pd.Series:
    """A possibly-nullable boolean Series as a plain numpy bool Series.

    Comparisons against pandas "string"-dtype columns return nullable booleans
    (three-valued logic), and an <NA> anywhere in a boolean mask makes
    DataFrame.loc raise. <NA> here can only mean "the upstream column did not
    say", which for every predicate in this module means "no, don't credit it".
    """
    return cast(pd.Series, s.fillna(False).astype(bool))


def _dst_touchdown_mask(df: pd.DataFrame) -> pd.Series:
    """Plays whose touchdown should be credited to a DST, by td_team.

    The rule is "every touchdown except an offensive scrimmage touchdown":
    credit td_team unless the scoring team was posteam on a play that is
    neither a kickoff nor a punt. Measured against real nflverse data:

    * 2024 REG -- 64 credited (36 pass, 10 punt, 8 run, 7 kickoff, 3 field
      goal). Requiring td_team == defteam credits only 56, dropping all 7
      kickoff-return TDs plus one punt TD scored by the punting team.
      td_team != posteam also credits only 56, for the same kickoff reason.
    * 2010 REG -- 118 credited, of which 23 are kickoff returns by posteam.

    The kickoff/punt carve-out is deliberately narrower than "not a run or
    pass". `play_type` is NULL on plays carrying a between-downs penalty, and
    in 1999 REG that is 8 touchdowns -- 6 of them ordinary offensive run/pass
    scores. Excluding posteam TDs by default and re-admitting only the two
    play types where posteam is the special-teams side gets those 6 right,
    while returning an identical answer to the run/pass phrasing on every
    season where play_type is populated.
    """
    scored = _flag(df, "touchdown").astype(bool)
    if "td_team" not in df.columns:
        return pd.Series(False, index=df.index)
    scored = scored & df["td_team"].notna()

    posteam = (
        df["posteam"] if "posteam" in df.columns
        else pd.Series(pd.NA, index=df.index, dtype="object")
    )
    scored_by_posteam = _bool(df["td_team"] == posteam)

    if "play_type" in df.columns:
        # cast: DataFrame.__getitem__ is typed as DataFrame | Series, so
        # .isin() widens to DataFrame in the stubs (same gap as _flag()).
        play_type = cast(pd.Series, df["play_type"])
        special = _bool(cast(pd.Series, play_type.isin(_SPECIAL_TEAMS_PLAY_TYPES)))
    else:
        special = pd.Series(False, index=df.index)

    return _bool(scored & (~scored_by_posteam | special))


def _fumble_recovery_mask(df: pd.DataFrame) -> pd.Series:
    """Plays where a fumble changed possession, creditable to the recoverer.

    Credit belongs to fumble_recovery_1_team whenever it differs from
    fumbled_1_team -- that difference IS the change of possession. Comparing
    the recoverer to defteam instead is wrong in both directions on punts,
    where posteam is the punting team: on 2024 REG it dropped 23 muffed punts
    recovered by the punting team and wrongly credited 22 muffs the receiving
    team recovered itself (no change of possession, so no fantasy credit).

    fumbled_1_team is published by nflverse for every season this table covers
    -- verified present and non-null on all 739 (1999) and 576 (2024) plays
    with a recorded fumble_recovery_1_team. If a future vintage drops it, the
    mask degrades to the pre-fix "recovered by defteam" behaviour, which is
    wrong on punts but never credits an offense its own recovery.
    """
    if "fumble_recovery_1_team" not in df.columns:
        return pd.Series(False, index=df.index)
    rec = df["fumble_recovery_1_team"]
    if "fumbled_1_team" in df.columns:
        fumbled = df["fumbled_1_team"]
        return _bool(rec.notna() & fumbled.notna() & (rec != fumbled))
    if "defteam" not in df.columns:
        return pd.Series(False, index=df.index)
    return _bool(rec.notna() & (rec == df["defteam"]))


def _blocked_kick_flag(df: pd.DataFrame) -> pd.Series:
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
    return cast(pd.Series, blocked.astype("int64"))


def _counts_by(
    df: pd.DataFrame, team_col: str, rename: dict[str, str],
) -> pd.DataFrame:
    """Sum `rename`'s flag columns per (game_id, <team_col>), publishing the
    result under the schema's count-column names with the crediting team in a
    uniform `team` column."""
    published = ["game_id", "team", *rename.values()]
    if df.empty or team_col not in df.columns or "game_id" not in df.columns:
        return pd.DataFrame(columns=published)  # type: ignore[arg-type]
    grouped = (
        df.groupby(["game_id", team_col], dropna=True)[list(rename)]
        .sum()
        .reset_index()
    )
    return grouped.rename(columns={team_col: "team", **rename})


def _event_counts(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (game_id, crediting team) counts of each scoring event.

    NOT a single groupby on defteam -- see _SPECIAL_TEAMS_PLAY_TYPES above for
    why that is unsound. Sacks, interceptions, safeties and blocked kicks are
    aggregated by defteam because they occur only on plays where the
    offense/defense orientation is unambiguous; touchdowns are aggregated by
    td_team and fumble recoveries by fumble_recovery_1_team.

    Known theoretical exception: a SAFETY scored on a punt play would be
    credited to defteam, i.e. to the receiving team rather than the punting
    team's coverage unit. There are zero such plays in 2024 REG -- all 15
    safeties that season are run/pass/no_play -- so the behaviour is left
    alone and documented rather than special-cased against no evidence.
    """
    empty = pd.DataFrame(columns=["game_id", "team", *_COUNT_COLUMNS])  # type: ignore[arg-type]
    if pbp.empty or "game_id" not in pbp.columns:
        return empty

    df = pbp.copy()
    frames: list[pd.DataFrame] = []

    if "defteam" in df.columns:
        df["_sack"] = _flag(df, "sack")
        df["_interception"] = _flag(df, "interception")
        df["_safety"] = _flag(df, "safety")
        df["_blocked"] = _blocked_kick_flag(df)
        frames.append(_counts_by(df, "defteam", {
            "_sack": "sacks", "_interception": "interceptions",
            "_safety": "safeties", "_blocked": "blocked_kicks",
        }))

    tds = df.loc[_dst_touchdown_mask(df)].copy()
    tds["_td"] = 1
    frames.append(_counts_by(tds, "td_team", {"_td": "defensive_tds"}))

    fumbles = df.loc[_fumble_recovery_mask(df)].copy()
    fumbles["_fumble"] = 1
    frames.append(_counts_by(
        fumbles, "fumble_recovery_1_team", {"_fumble": "fumble_recoveries"},
    ))

    combined = pd.concat(frames, ignore_index=True)
    if combined.empty:
        return empty
    for col in _COUNT_COLUMNS:
        if col not in combined.columns:
            combined[col] = 0
        # Same pandas-stub gap as _flag() above -- cast to silence pyright.
        combined[col] = cast(
            pd.Series,
            pd.to_numeric(combined[col], errors="coerce").fillna(0).astype("int64"),  # type: ignore[union-attr]
        )
    return (
        combined.groupby(["game_id", "team"], dropna=True)[_COUNT_COLUMNS]
        .sum()
        .reset_index()
    )


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
    # NULL bonus MUST propagate to a NULL total. fillna(0) here would publish a
    # concrete 0.0 for every team-week whose final score is unresolvable, which
    # reads as "this defense scored nothing" rather than "not known yet". That
    # is not a corner case: a mid-season `sync-nflverse --seasons latest` gets
    # all 18 scheduled weeks from load_schedules() but only the played ones
    # from load_pbp(), so every future week would ship a 0.0. Measured against
    # the live 2026 schedule before this fix: 544 rows, all 0.0, zero NULLs.
    # "Float64" (nullable) rather than "float64" because NaN in a FLOAT64
    # column loads to BigQuery as NaN, a value -- only pd.NA loads as NULL.
    merged["fantasy_points_dst"] = (
        event_points + merged["points_allowed_bonus"]
    ).astype("Float64")

    merged["season"] = season
    merged["week"] = cast(
        pd.Series,
        pd.to_numeric(merged["week"], errors="coerce").astype("Int64"),  # type: ignore[union-attr]
    )
    return align_to_schema(merged, FF_POINTS_DST_WEEKLY_SCHEMA)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_pbp(seasons=[season]).to_pandas()


def _transform(pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    """NflverseTableSpec's contract is a single (df, season) -> df transform,
    but points allowed needs authoritative final scores, which live in
    load_schedules() and not in the play-by-play. Rather than bend the driver,
    this transform fetches schedules itself -- the same posture
    SCHEME_WEEK_SPEC's transform takes for its three extra sources.
    """
    import nflreadpy as nfl

    schedules = nfl.load_schedules(seasons=[season]).to_pandas()
    return derive_dst_weekly(pbp, schedules, season)


DST_WEEKLY_SPEC = NflverseTableSpec(
    name="ff_points_dst_weekly",
    loader=_load,
    schema=FF_POINTS_DST_WEEKLY_SCHEMA,
    partition=SeasonRangePartition(clustering=["week", "team"]),
    transform=_transform,
    min_season=1999,
)
