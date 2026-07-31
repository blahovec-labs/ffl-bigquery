"""participation: play-level offense/defense personnel and coverage, 2016-2025.

Two landmines, both measured 2026-07-29:

1. `play_id` arrives as **Float64** upstream (it is Int32 in some seasons and
   Float64 in others -- the same vintage-dependent-dtype trap documented for
   `injuries.season`). Left as-is, BigQuery refuses FLOAT64 as a partition or
   clustering key, and a float<->int join against `ftn_charting`'s Int32
   `nflverse_play_id` silently under-matches (no error, just missing rows).
   Cast to INT64 unconditionally, regardless of what the upstream dtype
   happens to be this run.
2. Upstream publishes **no `season` or `week` column at all** -- both are
   derived from `nflverse_game_id`, formatted `YYYY_WW_AWAY_HOME`. `season`
   is still stamped from the loop argument (the writer's authority for which
   partition a row belongs to); only `week` is actually sourced from the
   parsed game id.
"""
from __future__ import annotations

from typing import cast

import pandas as pd

from ffl_bigquery._schema_samples import read_sample
from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition
from ffl_bigquery.schema_gen import specs_from_frame


def _num(series: pd.Series) -> pd.Series:
    """pd.to_numeric(...).astype('Int64') is a genuine pandas-stub gap (same
    one documented at writer.py:45 and depth_charts.py:93) -- cast the result
    so the gap doesn't cascade into every downstream use."""
    return cast(pd.Series, pd.to_numeric(series, errors="coerce").astype("Int64"))  # type: ignore[union-attr]


def season_week_from_game_id(game_id: pd.Series) -> pd.Series:
    """Parse (season, week) tuples from an nflverse_game_id 'YYYY_WW_AWAY_HOME'.

    Malformed or unparseable ids yield (pd.NA, pd.NA) rather than raising --
    a single bad game id must not crash a whole season's ingest.
    """
    parts = game_id.astype(str).str.split("_", n=3)

    def _parse(p: object) -> tuple[object, object]:
        if not isinstance(p, list) or len(p) < 2:
            return (pd.NA, pd.NA)
        try:
            return (int(p[0]), int(p[1]))
        except ValueError:
            return (pd.NA, pd.NA)

    return parts.map(_parse)


def _week_from_game_id(game_id: pd.Series) -> pd.Series:
    parsed = season_week_from_game_id(game_id)
    return _num(parsed.map(lambda t: t[1]))


# `play_id` arrives as Float64 (or Int32, depending on season) upstream;
# BigQuery RANGE partitioning/clustering requires a true INT64 column.
_TYPE_OVERRIDES = {"play_id": "INT64"}
_ENRICHMENT = {
    "play_id": {"semantic_tags": ["identifier", "join_key", "cluster_key"],
                "business_definition": "Play identifier within nflverse_game_id. "
                                       "Cast to INT64 -- upstream dtype varies by "
                                       "season (Float64 or Int32) and this table "
                                       "joins ftn_charting.nflverse_play_id, so an "
                                       "uncast float<->int join would silently "
                                       "under-match.",
                "gotchas": ["Upstream dtype is vintage-dependent (Float64 some "
                            "seasons, Int32 others); always cast, never trust "
                            "the raw dtype."]},
    "nflverse_game_id": {"semantic_tags": ["identifier", "join_key", "cluster_key"],
                         "business_definition": "nflverse game id, format "
                                                "YYYY_WW_AWAY_HOME. Source of the "
                                                "derived season/week columns."},
    "season": {"semantic_tags": ["identifier", "partition_key"],
               "business_definition": "NFL season. Not published upstream at all "
                                      "for this table -- stamped from the ingest "
                                      "loop's season argument, which is "
                                      "authoritative over anything parseable from "
                                      "nflverse_game_id.",
               "gotchas": ["Upstream has no season column whatsoever."]},
    "week": {"semantic_tags": ["identifier", "cluster_key"],
             "business_definition": "NFL week, parsed from the WW segment of "
                                    "nflverse_game_id. NULL if the game id is "
                                    "missing or malformed.",
             "gotchas": ["Upstream has no week column whatsoever; derived, not "
                         "sourced."]},
}


def _schema_sample() -> pd.DataFrame:
    df = read_sample("participation")
    game_id = cast(pd.Series, df["nflverse_game_id"])
    return df.assign(season=1999, week=_week_from_game_id(game_id))


PARTICIPATION_SCHEMA = specs_from_frame(
    _schema_sample(), table="participation",
    enrichment=_ENRICHMENT, type_overrides=_TYPE_OVERRIDES, required=("season",),
)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_participation(seasons=[season]).to_pandas()


def _transform(df: pd.DataFrame, season: int) -> pd.DataFrame:
    game_id = cast(pd.Series, df["nflverse_game_id"])
    week = _week_from_game_id(game_id)
    out = df.assign(season=season, week=week)
    out["play_id"] = _num(cast(pd.Series, out["play_id"]))
    return align_to_schema(out, PARTICIPATION_SCHEMA)


PARTICIPATION_SPEC = NflverseTableSpec(
    name="participation",
    loader=_load,
    schema=PARTICIPATION_SCHEMA,
    partition=SeasonRangePartition(
        clustering=["week", "nflverse_game_id", "play_id"], start=2016,
    ),
    transform=_transform,
    min_season=2016,
)
