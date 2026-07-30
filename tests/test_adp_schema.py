from ffl_bigquery.adp.schema import (
    FF_ADP_KEYS,
    FF_ADP_PARTITION,
    FF_ADP_SCHEMA,
    FFC_FORMATS,
)
from ffl_bigquery.schema import spec_names, to_bq_schema


def test_grain_key_is_the_six_snapshot_columns():
    assert FF_ADP_KEYS == [
        "source", "season", "scoring_format", "teams",
        "snapshot_date", "source_player_id",
    ]


def test_every_key_is_required_and_present_in_schema():
    by_name = {s.name: s for s in FF_ADP_SCHEMA}
    for k in FF_ADP_KEYS:
        assert k in by_name, f"{k} missing from FF_ADP_SCHEMA"
        assert by_name[k].mode == "REQUIRED", f"{k} must be REQUIRED"


def test_partition_is_day_on_snapshot_date_clustered_by_slicers():
    assert FF_ADP_PARTITION.field == "snapshot_date"
    assert FF_ADP_PARTITION.clustering == ["season", "source", "scoring_format", "teams"]
    # BigQuery permits at most four clustering columns.
    assert len(FF_ADP_PARTITION.clustering) <= 4


def test_pick_bounds_are_named_by_semantics_not_ffc_high_low():
    names = spec_names(FF_ADP_SCHEMA)
    assert "adp_earliest_pick" in names
    assert "adp_latest_pick" in names
    # FFC's ambiguous names must not leak into the table.
    assert "high" not in names
    assert "low" not in names
    assert "adp_high" not in names


def test_season_and_teams_are_int64_for_clustering():
    by_name = {s.name: s for s in FF_ADP_SCHEMA}
    assert by_name["season"].type == "INT64"
    assert by_name["teams"].type == "INT64"
    assert by_name["snapshot_date"].type == "DATE"
    assert by_name["adp"].type == "FLOAT64"


def test_gsis_id_is_nullable_because_resolution_can_fail():
    by_name = {s.name: s for s in FF_ADP_SCHEMA}
    assert by_name["gsis_id"].mode == "NULLABLE"


def test_schema_converts_to_bq_and_ends_with_ingested_at():
    fields = to_bq_schema(FF_ADP_SCHEMA)
    assert len(fields) == len(FF_ADP_SCHEMA) == 25
    assert fields[-1].name == "ingested_at"


def test_ffc_formats_cover_the_measured_set():
    assert FFC_FORMATS == ("standard", "ppr", "half-ppr", "2qb", "dynasty", "rookie")
