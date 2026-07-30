"""ff_opportunity: weekly fantasy opportunity/usage metrics, 2006-2025 (159 cols)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition
from ffl_bigquery.schema_gen import specs_from_frame

_SAMPLE = Path(__file__).resolve().parents[3] / "tests/fixtures/nflverse/ff_opportunity.parquet"

# `season` arrives as String upstream; INTEGER RANGE partitioning requires INT64.
_TYPE_OVERRIDES = {"season": "INT64"}
_ENRICHMENT = {
    "season": {"semantic_tags": ["identifier", "partition_key"],
               "business_definition": "NFL season. Cast from the String the "
                                      "feed publishes so RANGE partitioning accepts it.",
               "gotchas": ["Upstream dtype is String, not an integer."]},
    "player_id": {"semantic_tags": ["identifier", "join_key"],
                  "business_definition": "nflverse player id; joins ff_player_xref."},
}

OPPORTUNITY_SCHEMA = specs_from_frame(
    pd.read_parquet(_SAMPLE), table="ff_opportunity",
    enrichment=_ENRICHMENT, type_overrides=_TYPE_OVERRIDES, required=("season",),
)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_ff_opportunity(seasons=[season]).to_pandas()


def _transform(df: pd.DataFrame, season: int) -> pd.DataFrame:
    return align_to_schema(df.assign(season=season), OPPORTUNITY_SCHEMA)


OPPORTUNITY_SPEC = NflverseTableSpec(
    name="ff_opportunity",
    loader=_load,
    schema=OPPORTUNITY_SCHEMA,
    partition=SeasonRangePartition(clustering=["week", "player_id"]),
    transform=_transform,
    min_season=2006,
)
