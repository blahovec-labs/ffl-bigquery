"""Schema shape for ff_points_dst_weekly."""
from ffl_bigquery.derive.dst_weekly import FF_POINTS_DST_WEEKLY_SCHEMA
from ffl_bigquery.schema import spec_names


def test_grain_and_clustering_columns_present():
    names = spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)
    for col in ("season", "week", "team"):
        assert col in names


def test_every_scored_component_ships_beside_its_raw_count():
    # A consumer re-scoring under different league rules needs the counts, not
    # just our total. If a count column is ever dropped, the table silently
    # becomes un-rederivable -- so the pairing is asserted, not assumed.
    names = spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)
    for col in (
        "sacks", "interceptions", "fumble_recoveries", "safeties",
        "defensive_tds", "blocked_kicks", "points_allowed_total",
    ):
        assert col in names, f"missing raw count column: {col}"


def test_scoring_columns_present():
    names = spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)
    assert "points_allowed_bonus" in names
    assert "fantasy_points_dst" in names


def test_clustering_keys_are_not_float():
    # SeasonRangePartition clusters on week+team. BigQuery rejects FLOAT64
    # clustering keys with a 400 at table-creation time -- caught live during
    # the 0.1.0 build, so it is asserted here at the schema level too.
    by_name = {s.name: s for s in FF_POINTS_DST_WEEKLY_SCHEMA}
    assert by_name["week"].type == "INT64"
    assert by_name["team"].type == "STRING"
    assert by_name["season"].type == "INT64"


def test_counts_are_int_and_points_are_float():
    by_name = {s.name: s for s in FF_POINTS_DST_WEEKLY_SCHEMA}
    for col in ("sacks", "interceptions", "fumble_recoveries", "safeties",
                "defensive_tds", "blocked_kicks", "points_allowed_total"):
        assert by_name[col].type == "INT64", f"{col} should be a count"
    assert by_name["fantasy_points_dst"].type == "FLOAT64"


def test_every_column_has_a_business_definition():
    for s in FF_POINTS_DST_WEEKLY_SCHEMA:
        assert s.business_definition.strip(), f"{s.name} has no business_definition"
