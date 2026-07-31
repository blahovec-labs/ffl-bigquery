from ffl_bigquery.partition import ClusterOnly, SeasonRangePartition, TimePartition


def test_time_partition_holds_field_and_clustering():
    p = TimePartition(field="snapshot_date", clustering=["season", "source"])
    assert p.field == "snapshot_date"
    assert p.clustering == ["season", "source"]


def test_season_range_partition_defaults():
    p = SeasonRangePartition(clustering=["week"])
    assert p.field == "season"
    assert (p.start, p.end, p.interval) == (1999, 2100, 1)


def test_cluster_only_holds_clustering_with_no_partition_field():
    p = ClusterOnly(clustering=["season", "team"])
    assert p.clustering == ["season", "team"]
