"""snap_counts: weekly offense/defense/special-teams snap counts, 2013-2025."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition
from ffl_bigquery.schema_gen import specs_from_frame

_SAMPLE = Path(__file__).resolve().parents[3] / "tests/fixtures/nflverse/snap_counts.parquet"

# `season` already arrives as an integer upstream -- no override needed.
_TYPE_OVERRIDES: dict[str, str] = {}
_ENRICHMENT = {
    "pfr_player_id": {"semantic_tags": ["identifier", "join_key"],
                       "business_definition": "Pro-Football-Reference player id; "
                                              "joins ff_player_xref.pfr_id.",
                       "gotchas": ["Joins ff_player_xref.pfr_id, not gsis_id directly."]},
}

SNAP_COUNTS_SCHEMA = specs_from_frame(
    pd.read_parquet(_SAMPLE), table="snap_counts",
    enrichment=_ENRICHMENT, type_overrides=_TYPE_OVERRIDES, required=("season",),
)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_snap_counts(seasons=[season]).to_pandas()


def _transform(df: pd.DataFrame, season: int) -> pd.DataFrame:
    return align_to_schema(df.assign(season=season), SNAP_COUNTS_SCHEMA)


SNAP_COUNTS_SPEC = NflverseTableSpec(
    name="snap_counts",
    loader=_load,
    schema=SNAP_COUNTS_SCHEMA,
    partition=SeasonRangePartition(clustering=["week", "team", "pfr_player_id"]),
    transform=_transform,
    min_season=2013,
)
