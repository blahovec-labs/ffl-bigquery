"""depth_charts: two disjoint upstream schemas reconciled into one table.

load_depth_charts(seasons=True) returns a UNION of two eras that share exactly
one column, gsis_id (measured 2026-07-29):

  legacy 2001-2024  869,185 rows  season/club_code/week/depth_team/formation...
                                  `dt` is 100% NULL
  modern 2025+      935,857 rows  dt/team/pos_rank/pos_slot/pos_abb...
                                  `season` is NULL

So partitioning on DATE(dt) strands 48% of rows in a NULL partition and
partitioning on season strands the other 52%. Neither naive choice works, hence
this normalization: a common core, a source_era discriminator, and season
derived from dt for modern rows.
"""
from __future__ import annotations

from typing import cast

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
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


DEPTH_CHARTS_SCHEMA: list[ColumnSpec] = [
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season the depth chart belongs to. Present upstream for legacy rows; "
       "derived from `dt` for modern rows, where a January or February date "
       "belongs to the PREVIOUS season because an NFL season spans calendar years.",
       ["identifier", "partition_key"],
       ["Modern upstream rows carry no season at all."], "season|dt"),
    _c("week", "INT64", "NULLABLE", "NFL week, legacy rows only.",
       "Week of the depth chart. The modern feed publishes a timestamp instead "
       "of a week, so this is NULL for source_era='modern'.", ["identifier"]),
    _c("team", "STRING", "NULLABLE", "Team abbreviation.",
       "Team the depth chart belongs to. From `club_code` on legacy rows and "
       "`team` on modern rows.", ["dimension"], [], "club_code|team"),
    _c("gsis_id", "STRING", "NULLABLE", "nflverse player id.",
       "The only column both upstream eras share. Joins ff_player_xref.",
       ["identifier", "join_key"]),
    _c("player_name", "STRING", "NULLABLE", "Player display name.",
       "Assembled from the legacy feed's name parts, or taken from the modern "
       "feed's player_name.", ["dimension"], [], "first_name+last_name|player_name"),
    _c("position", "STRING", "NULLABLE", "Player position.",
       "From `position` on legacy rows and `pos_abb` on modern rows.",
       ["dimension"], [], "position|pos_abb"),
    _c("depth_rank", "INT64", "NULLABLE", "Depth-chart rank at the position.",
       "1 = starter. From `depth_team` on legacy rows and `pos_rank` on modern "
       "rows -- two upstream names for the same concept.",
       ["metric"], [], "depth_team|pos_rank"),
    _c("depth_position", "STRING", "NULLABLE", "Depth-chart position label.",
       "From `depth_position` on legacy rows and `pos_name` on modern rows.",
       ["dimension"], [], "depth_position|pos_name"),
    _c("formation", "STRING", "NULLABLE", "Formation grouping, legacy only.",
       "Offense/Defense/Special Teams grouping. Legacy feed only; NULL for "
       "modern rows.", ["dimension"]),
    _c("pos_slot", "STRING", "NULLABLE", "Positional slot, modern only.",
       "Fine-grained slot label (e.g. RB1). Modern feed only; NULL for legacy "
       "rows.", ["dimension"]),
    _c("source_era", "STRING", "REQUIRED", "Which upstream schema this row came from.",
       "'legacy' (2001-2024, season-keyed) or 'modern' (2025+, dt-keyed). The "
       "two upstream shapes share only gsis_id, so this discriminator is how a "
       "consumer knows which columns to expect to be populated.",
       ["dimension", "quality"],
       ["Always filter or group on this before reasoning about NULL columns."]),
    INGESTED_AT_SPEC,
]

DEPTH_CHARTS_PARTITION = SeasonRangePartition(
    clustering=["team", "gsis_id", "source_era"], start=2001,
)


def _num(series: pd.Series) -> pd.Series:
    """pd.to_numeric(...).astype('Int64') is a genuine pandas-stub gap (same
    one documented at writer.py:45 and adp/resolve.py:61) -- cast the result
    so the gap doesn't cascade into every downstream use."""
    return cast(pd.Series, pd.to_numeric(series, errors="coerce").astype("Int64"))  # type: ignore[union-attr]


def _season_from_dt(dt: pd.Series) -> pd.Series:
    """An NFL season spans calendar years: Jan/Feb belong to the prior season."""
    d = pd.to_datetime(dt, errors="coerce")
    return _num(d.dt.year - (d.dt.month <= 2).astype("int64"))


def _legacy_name(df: pd.DataFrame) -> pd.Series:
    first = df.get("first_name")
    last = df.get("last_name")
    if first is None or last is None:
        return pd.Series([pd.NA] * len(df), index=df.index, dtype="object")
    return (first.fillna("").astype(str) + " " + last.fillna("").astype(str)).str.strip()


def normalize_depth_charts(df: pd.DataFrame, season: int) -> pd.DataFrame:
    """Map either upstream era onto the normalized core.

    Era is detected PER ROW (by whether `season` is present), not assumed from
    the caller's argument, because a frame can legitimately contain both.
    """
    if df.empty:
        return pd.DataFrame(columns=[s.name for s in DEPTH_CHARTS_SCHEMA])  # type: ignore[arg-type]

    has_season = df["season"].notna() if "season" in df.columns else pd.Series(
        [False] * len(df), index=df.index
    )
    era = pd.Series(["legacy"] * len(df), index=df.index).where(has_season, "modern")

    def col(*names: str) -> pd.Series:
        """First present column among names, else all-NA."""
        for n in names:
            if n in df.columns:
                return cast(pd.Series, df[n])
        return pd.Series([pd.NA] * len(df), index=df.index, dtype="object")

    derived_season = _season_from_dt(col("dt")) if "dt" in df.columns else pd.Series(
        [pd.NA] * len(df), index=df.index, dtype="Int64"
    )
    season_out = _num(col("season"))
    season_out = season_out.fillna(derived_season).fillna(season)

    name_out = col("player_name")
    if "player_name" not in df.columns or name_out.isna().all():
        name_out = _legacy_name(df)
    else:
        name_out = name_out.fillna(_legacy_name(df))

    out = pd.DataFrame({
        "season": season_out,
        "week": _num(col("week")),
        "team": col("club_code").fillna(col("team"))
        if "club_code" in df.columns else col("team"),
        "gsis_id": col("gsis_id"),
        "player_name": name_out,
        "position": col("position").fillna(col("pos_abb"))
        if "position" in df.columns else col("pos_abb"),
        "depth_rank": (
            _num(col("depth_team")).fillna(_num(col("pos_rank")))
            if "depth_team" in df.columns
            else _num(col("pos_rank"))
        ),
        "depth_position": col("depth_position").fillna(col("pos_name"))
        if "depth_position" in df.columns else col("pos_name"),
        "formation": col("formation"),
        "pos_slot": col("pos_slot"),
        "source_era": era,
    })
    return align_to_schema(out, DEPTH_CHARTS_SCHEMA)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_depth_charts(seasons=[season]).to_pandas()


DEPTH_CHARTS_SPEC = NflverseTableSpec(
    name="depth_charts",
    loader=_load,
    schema=DEPTH_CHARTS_SCHEMA,
    partition=DEPTH_CHARTS_PARTITION,
    transform=normalize_depth_charts,
    min_season=2001,
)
