"""ftn_charting: FTN's manually-charted play-level features, 2022-2025.

`nflverse_play_id` arrives as **Int32** upstream. Left uncast it still LOOKS
fine on its own, but it must join `participation.play_id`, which is a
vintage-dependent Float64/Int32 upstream that this library casts to INT64
(see participation.py). A float<->int join silently under-matches rather than
erroring, so both sides are cast to INT64 here too -- that shared type is the
precondition for the join matching at all, which team_scheme_week depends on.

`season` already arrives as an integer most seasons, but the override is kept
defensively: `injuries.season` proved vintage-dependent (Float64 for older
seasons, Int32 for 2024) despite looking like a plain int on any one sampled
season, and this table's schema is built from a single sampled season's
fixture.
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


# `nflverse_play_id` arrives as Int32; cast to INT64 so it joins
# participation.play_id (Float64/Int32-vintage-dependent, also cast to
# INT64) without silently under-matching. `season` is defensively overridden
# for the same reason injuries.season needed it -- see module docstring.
_TYPE_OVERRIDES = {"nflverse_play_id": "INT64", "season": "INT64"}
_ENRICHMENT = {
    "nflverse_play_id": {"semantic_tags": ["identifier", "join_key", "cluster_key"],
                         "business_definition": "Play identifier within "
                                                "nflverse_game_id. Cast to INT64 "
                                                "so it joins participation.play_id "
                                                "-- an uncast float<->int join "
                                                "would silently under-match.",
                         "gotchas": ["Must match participation.play_id's dtype "
                                     "exactly (both INT64) or the join under-"
                                     "matches with no error."]},
    "nflverse_game_id": {"semantic_tags": ["identifier", "join_key", "cluster_key"],
                         "business_definition": "nflverse game id, format "
                                                "YYYY_WW_AWAY_HOME."},
    "season": {"semantic_tags": ["identifier", "partition_key"],
               "business_definition": "NFL season. Cast to INT64 defensively -- "
                                      "see module docstring for why a "
                                      "single-season sample isn't proof this "
                                      "dtype is stable across the coverage "
                                      "window.",
               "gotchas": ["Dtype may vary by season even though the sampled "
                           "season looks like a plain int; injuries.season set "
                           "this precedent."]},
}

FTN_CHARTING_SCHEMA = specs_from_frame(
    read_sample("ftn_charting"), table="ftn_charting",
    enrichment=_ENRICHMENT, type_overrides=_TYPE_OVERRIDES, required=("season",),
)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_ftn_charting(seasons=[season]).to_pandas()


def _transform(df: pd.DataFrame, season: int) -> pd.DataFrame:
    out = df.assign(season=season)
    out["nflverse_play_id"] = _num(cast(pd.Series, out["nflverse_play_id"]))
    return align_to_schema(out, FTN_CHARTING_SCHEMA)


FTN_CHARTING_SPEC = NflverseTableSpec(
    name="ftn_charting",
    loader=_load,
    schema=FTN_CHARTING_SCHEMA,
    partition=SeasonRangePartition(
        clustering=["week", "nflverse_game_id"], start=2022,
    ),
    transform=_transform,
    min_season=2022,
)
