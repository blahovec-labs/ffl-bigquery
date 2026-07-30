"""sync-coaches: the NflverseTableSpec descriptor for nfl_coaches."""
from __future__ import annotations

from ffl_bigquery.coaches.schema import NFL_COACHES_SCHEMA
from ffl_bigquery.coaches.transform import transform_coaches
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition


def _load(season: int):
    import nflreadpy as nfl

    return nfl.load_schedules(seasons=[season]).to_pandas()


COACHES_SPEC = NflverseTableSpec(
    name="nfl_coaches",
    loader=_load,
    schema=NFL_COACHES_SCHEMA,
    partition=SeasonRangePartition(clustering=["team", "game_id"], start=1999),
    transform=transform_coaches,
    min_season=1999,
)
