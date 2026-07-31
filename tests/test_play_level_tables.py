import pandas as pd

from ffl_bigquery._schema_samples import read_sample
from ffl_bigquery.nflverse.tables.ftn_charting import FTN_CHARTING_SPEC
from ffl_bigquery.nflverse.tables.participation import (
    PARTICIPATION_SPEC,
    season_week_from_game_id,
)
from ffl_bigquery.schema import spec_names


def test_play_id_is_int64_on_both_so_bigquery_accepts_it_and_the_join_matches():
    p = {s.name: s for s in PARTICIPATION_SPEC.schema}
    f = {s.name: s for s in FTN_CHARTING_SPEC.schema}
    assert p["play_id"].type == "INT64"          # upstream Float64
    assert f["nflverse_play_id"].type == "INT64"  # upstream Int32
    # BigQuery rejects FLOAT64 as a partition or clustering key.
    for spec in (PARTICIPATION_SPEC, FTN_CHARTING_SPEC):
        by = {s.name: s for s in spec.schema}
        for c in spec.partition.clustering:
            assert by[c].type != "FLOAT64"


def test_coverage_windows_match_measurement():
    assert PARTICIPATION_SPEC.min_season == 2016
    assert FTN_CHARTING_SPEC.min_season == 2022


def test_season_week_parsed_from_game_id():
    assert season_week_from_game_id(pd.Series(["2023_07_DET_KC"])).iloc[0] == (2023, 7)


def test_participation_derives_season_and_week_since_upstream_has_neither():
    raw = read_sample("participation")
    out = PARTICIPATION_SPEC.transform(raw, 2023)
    assert list(out.columns) == spec_names(PARTICIPATION_SPEC.schema)
    assert (out["season"] == 2023).all()
    assert out["week"].notna().any()
    assert str(out["play_id"].dtype) == "Int64"


def test_ftn_transform_aligns_and_casts():
    raw = read_sample("ftn_charting")
    out = FTN_CHARTING_SPEC.transform(raw, 2023)
    assert list(out.columns) == spec_names(FTN_CHARTING_SPEC.schema)
    assert (out["season"] == 2023).all()
    assert str(out["nflverse_play_id"].dtype) == "Int64"


def test_the_two_tables_join_on_a_common_int_key():
    p = PARTICIPATION_SPEC.transform(read_sample("participation"), 2023)
    f = FTN_CHARTING_SPEC.transform(read_sample("ftn_charting"), 2023)
    # Same dtype on both sides is the precondition for the join matching at all.
    assert str(p["play_id"].dtype) == str(f["nflverse_play_id"].dtype)
