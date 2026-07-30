from pathlib import Path

import pandas as pd
import pytest

from ffl_bigquery.nflverse.tables.injuries import INJURIES_SPEC
from ffl_bigquery.nflverse.tables.opportunity import OPPORTUNITY_SPEC
from ffl_bigquery.nflverse.tables.snap_counts import SNAP_COUNTS_SPEC
from ffl_bigquery.schema import spec_names

FIX = Path(__file__).parent / "fixtures" / "nflverse"


@pytest.mark.parametrize(
    "spec,min_season",
    [(OPPORTUNITY_SPEC, 2006), (SNAP_COUNTS_SPEC, 2013), (INJURIES_SPEC, 2009)],
)
def test_min_season_matches_measured_coverage(spec, min_season):
    assert spec.min_season == min_season


@pytest.mark.parametrize(
    "spec", [OPPORTUNITY_SPEC, SNAP_COUNTS_SPEC, INJURIES_SPEC]
)
def test_season_is_int64_and_required(spec):
    # INTEGER RANGE partitioning rejects a STRING or FLOAT64 partition column.
    season = next(s for s in spec.schema if s.name == "season")
    assert season.type == "INT64"
    assert season.mode == "REQUIRED"


@pytest.mark.parametrize(
    "spec", [OPPORTUNITY_SPEC, SNAP_COUNTS_SPEC, INJURIES_SPEC]
)
def test_partitioned_on_season_with_at_most_four_cluster_columns(spec):
    assert spec.partition is not None
    assert spec.partition.field == "season"
    assert len(spec.partition.clustering) <= 4


@pytest.mark.parametrize(
    "spec,fixture",
    [(OPPORTUNITY_SPEC, "ff_opportunity"), (SNAP_COUNTS_SPEC, "snap_counts"),
     (INJURIES_SPEC, "injuries")],
)
def test_transform_stamps_the_loop_season_and_aligns_to_schema(spec, fixture):
    raw = pd.read_parquet(FIX / f"{fixture}.parquet")
    out = spec.transform(raw, 1999)
    assert list(out.columns) == spec_names(spec.schema)
    # The loop's season wins over whatever upstream carried.
    assert (out["season"] == 1999).all()
    assert out["ingested_at"].notna().all()


@pytest.mark.parametrize(
    "spec", [OPPORTUNITY_SPEC, SNAP_COUNTS_SPEC, INJURIES_SPEC]
)
def test_schema_ends_with_ingested_at(spec):
    assert spec.schema[-1].name == "ingested_at"


def test_rankings_is_snapshot_grain_not_season_chunked():
    from ffl_bigquery.nflverse.tables.rankings import (
        FF_RANKINGS_KEYS,
        FF_RANKINGS_PARTITION,
        FF_RANKINGS_SCHEMA,
    )

    assert FF_RANKINGS_KEYS == ["scrape_date", "ecr_type", "id"]
    assert FF_RANKINGS_PARTITION.field == "scrape_date"
    assert "season" not in spec_names(FF_RANKINGS_SCHEMA)
    for k in FF_RANKINGS_KEYS:
        assert next(s for s in FF_RANKINGS_SCHEMA if s.name == k).mode == "REQUIRED"
