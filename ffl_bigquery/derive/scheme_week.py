"""team_scheme_week: per-(season, week, team) scheme fingerprint joined to head_coach.

The marquee derived table -- what makes "what changed when the coach changed"
answerable. Two things make it hard, both driven by upstream data quality, not
by the aggregation logic itself:

1. Personnel parsing is era-split and plausibility-gated -- see
   ffl_bigquery/derive/personnel.py. `personnel_source` records which era
   parser ran so a consumer never has to guess.

2. Charted metrics (coverage, pressure, FTN's play-action/motion/RPO/blitz) are
   a SAMPLE, never a census -- fill rates measured 2026-07-30 top out at 49.6%
   for coverage type and never reach 100% for anything except was_pressure in
   2023+. So every derived rate here ships beside its own denominator (a
   `plays_charted_*` column counting only the rows that were actually charted),
   and the rate is computed against THAT denominator, never against total
   `plays`. When the denominator is 0 -- no charting available for that
   season/week/team, or the source frame wasn't supplied at all -- the rate is
   pd.NA, never 0.0. A 0.0 blitz_rate would read as "this team never blitzed";
   the honest statement is "nobody charted it."

Grain: one row per (season, week, team). `plays`/`shotgun_rate`/`no_huddle_rate`/
`pass_rate`/`proe`/`epa_per_play` need only `load_pbp()` and are available
1999+ (pass_oe ships from load_pbp() directly -- no PROE modeling here).
Everything era-gated needs participation (2016+) and/or ftn_charting (2022+),
each of which this function accepts as an already-transformed frame -- NOT a
raw nflreadpy frame -- so callers are expected to have already run
PARTICIPATION_SPEC.transform / FTN_CHARTING_SPEC.transform (which is exactly
what SCHEME_WEEK_SPEC's own transform does, see bottom of this module).

Join detail that is load-bearing: participation.play_id and
ftn_charting.nflverse_play_id are Int64 in their own transforms specifically so
they're joinable; load_pbp()'s play_id is float (dtype vintage-dependent). This
module casts every play_id side to Int64 again before joining -- belt-and-
suspenders, since a float<->int join silently UNDER-matches (no error, just
missing rows) rather than raising.
"""
from __future__ import annotations

from typing import cast

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.derive.personnel import personnel_counts
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition
from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec

_PLAY_TYPES = ("pass", "run")


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


TEAM_SCHEME_WEEK_SCHEMA: list[ColumnSpec] = [
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season this scheme-week belongs to. Stamped from the sync loop's "
       "season argument.", ["identifier", "partition_key"]),
    _c("week", "INT64", "REQUIRED", "NFL week.",
       "Week within the season, from load_pbp().", ["identifier", "cluster_key"]),
    _c("team", "STRING", "REQUIRED", "Team abbreviation on offense.",
       "The offense (posteam) this row's metrics describe. From load_pbp()'s "
       "posteam.", ["identifier", "dimension", "cluster_key"], [], "posteam"),
    _c("head_coach", "STRING", "NULLABLE", "Head coach of `team` this week.",
       "Joined from nfl_coaches on (season, week, team). NULL if no coaches "
       "row matches (e.g. a bye week, or the coaches frame wasn't supplied)."),
    _c("plays", "INT64", "NULLABLE", "Offensive plays run.",
       "Count of pass/run plays run by `team` in this week, from load_pbp() "
       "filtered to play_type in ('pass', 'run') -- special-teams/no-play "
       "rows excluded before any aggregation."),
    _c("shotgun_rate", "FLOAT64", "NULLABLE", "Share of plays run from shotgun.",
       "Mean of load_pbp()'s shotgun flag over `plays`.", ["metric"]),
    _c("no_huddle_rate", "FLOAT64", "NULLABLE", "Share of plays run no-huddle.",
       "Mean of load_pbp()'s no_huddle flag over `plays`.", ["metric"]),
    _c("pass_rate", "FLOAT64", "NULLABLE", "Share of plays that were passes.",
       "Count of play_type == 'pass' divided by `plays`.", ["metric"]),
    _c("proe", "FLOAT64", "NULLABLE", "Pass rate over expected.",
       "Mean of load_pbp()'s pass_oe -- shipped upstream directly, not "
       "modeled here.", ["metric"], [], "pass_oe"),
    _c("epa_per_play", "FLOAT64", "NULLABLE", "Mean EPA per offensive play.",
       "Mean of load_pbp()'s epa over `plays`.", ["metric"]),
    _c("plays_with_personnel", "INT64", "NULLABLE",
       "Denominator for personnel_*_rate: plausible-personnel plays.",
       "Count of participation plays for this team-week whose personnel row "
       "passed the era-appropriate plausibility gate (see "
       "ffl_bigquery/derive/personnel.py) -- NOT total `plays`. Available "
       "2016+ (participation's coverage window); 0 (not NULL) when "
       "participation was supplied but no row passed the gate.",
       ["metric", "denominator"]),
    _c("personnel_11_rate", "FLOAT64", "NULLABLE",
       "Share of plausible-personnel plays that were 11 personnel (1 RB, 1 TE).",
       "Numerator / plays_with_personnel. pd.NA when plays_with_personnel is "
       "0 -- never 0.0, which would misread as 'never ran 11 personnel' "
       "instead of 'no plausible personnel data.'", ["metric"]),
    _c("personnel_12_rate", "FLOAT64", "NULLABLE",
       "Share of plausible-personnel plays that were 12 personnel (1 RB, 2 TE).",
       "Numerator / plays_with_personnel. Same NULL-not-zero rule as "
       "personnel_11_rate.", ["metric"]),
    _c("personnel_21_rate", "FLOAT64", "NULLABLE",
       "Share of plausible-personnel plays that were 21 personnel (2 RB, 1 TE).",
       "Numerator / plays_with_personnel. Same NULL-not-zero rule as "
       "personnel_11_rate.", ["metric"]),
    _c("personnel_source", "STRING", "NULLABLE",
       "Which upstream column personnel_*_rate was parsed from.",
       "'offense_positions' for season >= 2023, 'offense_personnel' for "
       "earlier seasons -- see personnel_counts(). NULL if no participation "
       "data was supplied at all.", ["dimension", "quality"],
       ["Two eras, two different upstream columns, two different "
        "plausibility rules -- never assume one shape for both."]),
    _c("plays_charted_coverage", "INT64", "NULLABLE",
       "Denominator for man_rate/zone_rate/avg_defenders_in_box.",
       "Count of participation plays for this team-week with a non-blank "
       "defense_man_zone_type -- NOT total `plays`. Coverage charting fill "
       "never exceeds 49.6% even in its best-covered seasons (2023-2025); it "
       "is 0.000 in 2016-2017.", ["metric", "denominator"]),
    _c("man_rate", "FLOAT64", "NULLABLE", "Share of charted coverage plays run man.",
       "Numerator / plays_charted_coverage. pd.NA when plays_charted_coverage "
       "is 0.", ["metric"]),
    _c("zone_rate", "FLOAT64", "NULLABLE", "Share of charted coverage plays run zone.",
       "Numerator / plays_charted_coverage. pd.NA when plays_charted_coverage "
       "is 0.", ["metric"]),
    _c("avg_defenders_in_box", "FLOAT64", "NULLABLE",
       "Mean defenders in the box, over charted coverage plays.",
       "Mean of participation's defenders_in_box, restricted to rows with a "
       "charted defense_man_zone_type (same denominator as man_rate/"
       "zone_rate).", ["metric"]),
    _c("plays_charted_pressure", "INT64", "NULLABLE",
       "Denominator for pressure_rate.",
       "Count of participation plays for this team-week with a non-null "
       "was_pressure -- NOT total `plays`.", ["metric", "denominator"]),
    _c("pressure_rate", "FLOAT64", "NULLABLE", "Share of charted-pressure plays pressured.",
       "Numerator / plays_charted_pressure. pd.NA when plays_charted_pressure "
       "is 0.", ["metric"]),
    _c("plays_charted_ftn", "INT64", "NULLABLE",
       "Denominator for play_action_rate/motion_rate/rpo_rate/screen_rate/"
       "blitz_rate/avg_pass_rushers.",
       "Count of ftn_charting plays matched to this team-week -- NOT total "
       "`plays`. Available 2022+ (ftn_charting's coverage window).",
       ["metric", "denominator"]),
    _c("play_action_rate", "FLOAT64", "NULLABLE", "Share of FTN-charted plays with play action.",
       "Numerator / plays_charted_ftn. pd.NA when plays_charted_ftn is 0.",
       ["metric"]),
    _c("motion_rate", "FLOAT64", "NULLABLE", "Share of FTN-charted plays with pre-snap motion.",
       "Numerator / plays_charted_ftn. pd.NA when plays_charted_ftn is 0.",
       ["metric"]),
    _c("rpo_rate", "FLOAT64", "NULLABLE", "Share of FTN-charted plays that were RPOs.",
       "Numerator / plays_charted_ftn. pd.NA when plays_charted_ftn is 0.",
       ["metric"]),
    _c("screen_rate", "FLOAT64", "NULLABLE", "Share of FTN-charted plays that were screens.",
       "Numerator / plays_charted_ftn. pd.NA when plays_charted_ftn is 0.",
       ["metric"]),
    _c("blitz_rate", "FLOAT64", "NULLABLE",
       "Share of FTN-charted plays with at least one charted blitzer.",
       "Count of rows with n_blitzers >= 1, divided by plays_charted_ftn. "
       "pd.NA when plays_charted_ftn is 0.", ["metric"]),
    _c("avg_pass_rushers", "FLOAT64", "NULLABLE",
       "Mean charted pass rushers, over FTN-charted plays.",
       "Mean of ftn_charting's n_pass_rushers, over plays_charted_ftn.",
       ["metric"]),
    INGESTED_AT_SPEC,
]

TEAM_SCHEME_WEEK_PARTITION = SeasonRangePartition(clustering=["week", "team"], start=1999)


def _num(series: pd.Series) -> pd.Series:
    """pd.to_numeric(...).astype('Int64') is a genuine pandas-stub gap (same
    one documented at writer.py:45 and depth_charts.py:93) -- cast the result
    so the gap doesn't cascade into every downstream use."""
    return cast(pd.Series, pd.to_numeric(series, errors="coerce").astype("Int64"))  # type: ignore[union-attr]


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """`df[name]` if present, else an all-NA Series aligned to df's index.

    Also the single place a bare `df["col"]` bracket access is cast to
    `pd.Series` -- pandas-stubs' `__getitem__` overloads are ambiguous enough
    (Series | DataFrame | Any, worse once chained) that leaving this uncast
    cascades into dozens of downstream reportArgumentType/reportOperatorIssue
    errors across every function below.
    """
    if name in df.columns:
        return cast(pd.Series, df[name])
    return pd.Series([pd.NA] * len(df), index=df.index, dtype="object")


def _rate(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """numerator / denominator, pd.NA (never 0.0) when denominator is 0."""
    denom = denominator.astype("Float64")
    denom = denom.mask(denom == 0)
    return numerator.astype("Float64") / denom


def _attach_team_week(
    source: pd.DataFrame,
    plays_ref: pd.DataFrame,
    *,
    game_id_col: str,
    play_id_col: str,
    keep_cols: list[str],
) -> pd.DataFrame:
    """Inner-join a play-level charting source onto this season's (week, posteam)
    plays, so a row with no team column of its own (participation, ftn_charting)
    can be attributed to a team via load_pbp()'s posteam.

    Only `keep_cols` (plus the join keys) is carried over from `source` -- the
    real participation/ftn_charting tables already carry their own season/week
    columns, and pulling those through unfiltered would collide with plays_ref's
    same-named columns from the pbp side.
    """
    if source.empty or plays_ref.empty:
        return pd.DataFrame()
    cols = [game_id_col, play_id_col] + [c for c in keep_cols if c in source.columns]
    s = cast(pd.DataFrame, source[cols]).copy()
    s[play_id_col] = _num(_col(s, play_id_col))
    merged = s.merge(
        plays_ref, left_on=[game_id_col, play_id_col], right_on=["game_id", "play_id"],
        how="inner",
    )
    return merged


def _base_metrics(filtered_pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    df = filtered_pbp.copy()
    df["is_pass"] = (_col(df, "play_type") == "pass").astype(float)
    g = df.groupby(["week", "posteam"])
    grouped = pd.DataFrame({
        "plays": g["play_id"].count(),
        "shotgun_rate": g["shotgun"].mean(),
        "no_huddle_rate": g["no_huddle"].mean(),
        "pass_rate": g["is_pass"].mean(),
        "proe": g["pass_oe"].mean(),
        "epa_per_play": g["epa"].mean(),
    }).reset_index()
    grouped = grouped.rename(columns={"posteam": "team"})  # type: ignore[call-overload]
    grouped["season"] = season
    return grouped


def _personnel_metrics(
    participation: pd.DataFrame, plays_ref: pd.DataFrame, season: int
) -> pd.DataFrame:
    merged = _attach_team_week(
        participation, plays_ref, game_id_col="nflverse_game_id", play_id_col="play_id",
        keep_cols=["offense_positions", "offense_personnel"],
    )
    if merged.empty:
        return pd.DataFrame()

    counts = personnel_counts(merged, season)
    merged = pd.concat(
        [merged.reset_index(drop=True), counts.reset_index(drop=True)], axis=1,
    )
    plausible = _col(merged, "plausible")
    n_rb = _col(merged, "n_rb")
    n_te = _col(merged, "n_te")
    merged["is_11"] = plausible & (n_rb == 1) & (n_te == 1)
    merged["is_12"] = plausible & (n_rb == 1) & (n_te == 2)
    merged["is_21"] = plausible & (n_rb == 2) & (n_te == 1)

    g = merged.groupby(["week", "posteam"])
    grouped = pd.DataFrame({
        "plays_with_personnel": g["plausible"].sum(),
        "n11": g["is_11"].sum(),
        "n12": g["is_12"].sum(),
        "n21": g["is_21"].sum(),
        "personnel_source": g["personnel_source"].first(),
    }).reset_index()
    grouped["plays_with_personnel"] = grouped["plays_with_personnel"].astype("Int64")
    denom = _col(grouped, "plays_with_personnel")
    grouped["personnel_11_rate"] = _rate(_col(grouped, "n11"), denom)
    grouped["personnel_12_rate"] = _rate(_col(grouped, "n12"), denom)
    grouped["personnel_21_rate"] = _rate(_col(grouped, "n21"), denom)
    grouped = grouped.drop(columns=["n11", "n12", "n21"])
    grouped = grouped.rename(columns={"posteam": "team"})  # type: ignore[call-overload]
    return grouped


def _coverage_pressure_metrics(
    participation: pd.DataFrame, plays_ref: pd.DataFrame
) -> pd.DataFrame:
    merged = _attach_team_week(
        participation, plays_ref, game_id_col="nflverse_game_id", play_id_col="play_id",
        keep_cols=["defense_man_zone_type", "defense_coverage_type",
                   "defenders_in_box", "was_pressure"],
    )
    if merged.empty:
        return pd.DataFrame()

    mz = _col(merged, "defense_man_zone_type")
    charted_coverage = mz.notna() & (mz.astype(str).str.strip() != "")
    merged["_charted_coverage"] = charted_coverage
    merged["_is_man"] = charted_coverage & (mz == "MAN_COVERAGE")
    merged["_is_zone"] = charted_coverage & (mz == "ZONE_COVERAGE")

    wp = _col(merged, "was_pressure")
    charted_pressure = wp.notna()
    merged["_charted_pressure"] = charted_pressure
    # .where(...) instead of .fillna(False).astype(bool): fillna on an object
    # column carrying True/False/None triggers a pandas downcasting
    # FutureWarning; charted_pressure already excludes the None rows from the
    # AND, so the replacement value here never actually surfaces.
    merged["_is_pressure"] = charted_pressure & wp.where(charted_pressure, False).astype(bool)

    dib = _col(merged, "defenders_in_box")
    dib_numeric = cast(pd.Series, pd.to_numeric(dib, errors="coerce"))
    merged["_defenders_in_box_charted"] = dib_numeric.where(charted_coverage)

    g = merged.groupby(["week", "posteam"])
    grouped = pd.DataFrame({
        "plays_charted_coverage": g["_charted_coverage"].sum(),
        "n_man": g["_is_man"].sum(),
        "n_zone": g["_is_zone"].sum(),
        "avg_defenders_in_box": g["_defenders_in_box_charted"].mean(),
        "plays_charted_pressure": g["_charted_pressure"].sum(),
        "n_pressure": g["_is_pressure"].sum(),
    }).reset_index()
    grouped["plays_charted_coverage"] = grouped["plays_charted_coverage"].astype("Int64")
    grouped["plays_charted_pressure"] = grouped["plays_charted_pressure"].astype("Int64")
    coverage_denom = _col(grouped, "plays_charted_coverage")
    pressure_denom = _col(grouped, "plays_charted_pressure")
    grouped["man_rate"] = _rate(_col(grouped, "n_man"), coverage_denom)
    grouped["zone_rate"] = _rate(_col(grouped, "n_zone"), coverage_denom)
    grouped["pressure_rate"] = _rate(_col(grouped, "n_pressure"), pressure_denom)
    grouped = grouped.drop(columns=["n_man", "n_zone", "n_pressure"])
    grouped = grouped.rename(columns={"posteam": "team"})  # type: ignore[call-overload]
    return grouped


def _ftn_metrics(ftn: pd.DataFrame, plays_ref: pd.DataFrame) -> pd.DataFrame:
    merged = _attach_team_week(
        ftn, plays_ref, game_id_col="nflverse_game_id", play_id_col="nflverse_play_id",
        keep_cols=["is_play_action", "is_motion", "is_rpo", "is_screen_pass",
                   "n_blitzers", "n_pass_rushers"],
    )
    if merged.empty:
        return pd.DataFrame()

    blitzers = _col(merged, "n_blitzers")
    blitzers_numeric = cast(pd.Series, pd.to_numeric(blitzers, errors="coerce"))
    merged["_is_blitz"] = blitzers_numeric.fillna(0) >= 1

    g = merged.groupby(["week", "posteam"])
    grouped = pd.DataFrame({
        "plays_charted_ftn": g["nflverse_play_id"].count(),
        "n_play_action": g["is_play_action"].sum(),
        "n_motion": g["is_motion"].sum(),
        "n_rpo": g["is_rpo"].sum(),
        "n_screen": g["is_screen_pass"].sum(),
        "n_blitz": g["_is_blitz"].sum(),
        "avg_pass_rushers": g["n_pass_rushers"].mean(),
    }).reset_index()
    grouped["plays_charted_ftn"] = grouped["plays_charted_ftn"].astype("Int64")
    denom = _col(grouped, "plays_charted_ftn")
    grouped["play_action_rate"] = _rate(_col(grouped, "n_play_action"), denom)
    grouped["motion_rate"] = _rate(_col(grouped, "n_motion"), denom)
    grouped["rpo_rate"] = _rate(_col(grouped, "n_rpo"), denom)
    grouped["screen_rate"] = _rate(_col(grouped, "n_screen"), denom)
    grouped["blitz_rate"] = _rate(_col(grouped, "n_blitz"), denom)
    grouped = grouped.drop(columns=["n_play_action", "n_motion", "n_rpo", "n_screen", "n_blitz"])
    grouped = grouped.rename(columns={"posteam": "team"})  # type: ignore[call-overload]
    return grouped


def derive_scheme_week(
    pbp: pd.DataFrame,
    participation: pd.DataFrame,
    ftn: pd.DataFrame,
    coaches: pd.DataFrame,
    season: int,
) -> pd.DataFrame:
    """Build the (season, week, team) scheme fingerprint.

    `participation` and `ftn` are expected already-transformed (Int64 play-id
    columns, aligned to their own schemas) -- pass PARTICIPATION_SPEC.transform
    / FTN_CHARTING_SPEC.transform output, or an empty frame if that source
    isn't available for `season`. This function re-casts play_id defensively
    regardless (see module docstring's join-detail note).
    """
    if pbp.empty:
        return align_to_schema(pd.DataFrame(), TEAM_SCHEME_WEEK_SCHEMA)

    play_type_mask = _col(pbp, "play_type").isin(_PLAY_TYPES)
    filtered = cast(pd.DataFrame, pbp[play_type_mask]).copy()
    if filtered.empty:
        return align_to_schema(pd.DataFrame(), TEAM_SCHEME_WEEK_SCHEMA)
    filtered["play_id"] = _num(_col(filtered, "play_id"))

    out = _base_metrics(filtered, season)
    plays_ref = cast(
        pd.DataFrame, filtered[["game_id", "play_id", "week", "posteam"]]
    ).drop_duplicates()

    if not coaches.empty:
        out = out.merge(
            cast(pd.DataFrame, coaches[["season", "week", "team", "head_coach"]]),
            on=["season", "week", "team"], how="left",
        )
    else:
        out["head_coach"] = pd.NA

    personnel = _personnel_metrics(participation, plays_ref, season)
    if not personnel.empty:
        out = out.merge(personnel, on=["week", "team"], how="left")

    coverage_pressure = _coverage_pressure_metrics(participation, plays_ref)
    if not coverage_pressure.empty:
        out = out.merge(coverage_pressure, on=["week", "team"], how="left")

    ftn_metrics = _ftn_metrics(ftn, plays_ref)
    if not ftn_metrics.empty:
        out = out.merge(ftn_metrics, on=["week", "team"], how="left")

    return align_to_schema(out, TEAM_SCHEME_WEEK_SCHEMA)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_pbp(seasons=[season]).to_pandas()


def _transform(pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    """NflverseTableSpec's contract is a single (df, season) -> df transform,
    but this table genuinely needs four sources at different grains. Rather
    than bend the driver, this transform fetches the other three itself --
    reusing PARTICIPATION_SPEC/FTN_CHARTING_SPEC's own loader+transform (so the
    Int64 play-id casts and schema alignment this table's join depends on are
    guaranteed identical to what those tables actually write) and building
    coaches from load_schedules() via transform_coaches, the same as
    nfl_coaches itself.
    """
    import nflreadpy as nfl

    from ffl_bigquery.coaches.transform import transform_coaches
    from ffl_bigquery.nflverse.tables.ftn_charting import FTN_CHARTING_SPEC
    from ffl_bigquery.nflverse.tables.participation import PARTICIPATION_SPEC

    participation = (
        PARTICIPATION_SPEC.transform(PARTICIPATION_SPEC.loader(season), season)
        if season >= PARTICIPATION_SPEC.min_season else pd.DataFrame()
    )
    ftn = (
        FTN_CHARTING_SPEC.transform(FTN_CHARTING_SPEC.loader(season), season)
        if season >= FTN_CHARTING_SPEC.min_season else pd.DataFrame()
    )
    schedules = nfl.load_schedules(seasons=[season]).to_pandas()
    coaches = transform_coaches(schedules, season)
    return derive_scheme_week(pbp, participation, ftn, coaches, season)


SCHEME_WEEK_SPEC = NflverseTableSpec(
    name="team_scheme_week",
    loader=_load,
    schema=TEAM_SCHEME_WEEK_SCHEMA,
    partition=TEAM_SCHEME_WEEK_PARTITION,
    transform=_transform,
    min_season=1999,
)
