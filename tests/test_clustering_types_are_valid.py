"""Guard: every clustering column must be a type BigQuery can actually cluster on.

This exists because of a live failure on 2026-07-30: creating ff_opportunity
returned "Field week has type FLOAT, which is not supported for clustering."
The plan had documented the FLOAT64-is-not-clusterable landmine for `play_id`
and overridden it there, but `week` -- also float upstream, also a clustering
key on several tables -- slipped through.

No unit test caught it because the constraint is BigQuery's, not pandas'. This
guard checks every spec at once so the whole class cannot recur silently.
"""
from __future__ import annotations

import pytest

from ffl_bigquery.coaches.sync import COACHES_SPEC
from ffl_bigquery.derive.points_weekly import POINTS_WEEKLY_SPEC
from ffl_bigquery.derive.scheme_week import SCHEME_WEEK_SPEC
from ffl_bigquery.nflverse.tables.depth_charts import DEPTH_CHARTS_SPEC
from ffl_bigquery.nflverse.tables.ftn_charting import FTN_CHARTING_SPEC
from ffl_bigquery.nflverse.tables.injuries import INJURIES_SPEC
from ffl_bigquery.nflverse.tables.opportunity import OPPORTUNITY_SPEC
from ffl_bigquery.nflverse.tables.participation import PARTICIPATION_SPEC
from ffl_bigquery.nflverse.tables.snap_counts import SNAP_COUNTS_SPEC

# https://cloud.google.com/bigquery/docs/clustered-tables#limitations
CLUSTERABLE = {"INT64", "STRING", "DATE", "TIMESTAMP", "DATETIME", "BOOL",
               "NUMERIC", "BIGNUMERIC"}

ALL_SPECS = [
    OPPORTUNITY_SPEC, SNAP_COUNTS_SPEC, INJURIES_SPEC, DEPTH_CHARTS_SPEC,
    PARTICIPATION_SPEC, FTN_CHARTING_SPEC, COACHES_SPEC, POINTS_WEEKLY_SPEC,
    SCHEME_WEEK_SPEC,
]


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_every_clustering_column_is_a_clusterable_type(spec):
    by_name = {c.name: c.type for c in spec.schema}
    clustering = spec.partition.clustering if spec.partition else []
    for col in clustering:
        assert col in by_name, f"{spec.name}: clusters on undeclared column {col!r}"
        assert by_name[col] in CLUSTERABLE, (
            f"{spec.name}: clustering column {col!r} is {by_name[col]}, which "
            f"BigQuery cannot cluster on"
        )


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_at_most_four_clustering_columns(spec):
    clustering = spec.partition.clustering if spec.partition else []
    assert len(clustering) <= 4, f"{spec.name}: BigQuery allows at most 4"


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.name)
def test_partition_key_is_int64_when_range_partitioned(spec):
    part = spec.partition
    if part is None or not hasattr(part, "start"):
        return
    by_name = {c.name: c.type for c in spec.schema}
    assert by_name.get(part.field) == "INT64", (
        f"{spec.name}: RANGE partition key {part.field!r} must be INT64, got "
        f"{by_name.get(part.field)}"
    )
