import json
from datetime import date
from pathlib import Path

import pandas as pd

from ffl_bigquery.adp.ffc import FfcResponse
from ffl_bigquery.adp.mfl import MflResponse
from ffl_bigquery.adp.schema import FF_ADP_SCHEMA
from ffl_bigquery.adp.transform import transform_ffc, transform_mfl
from ffl_bigquery.schema import spec_names

FIXTURES = Path(__file__).parent / "fixtures"
SNAP = date(2026, 7, 29)


def _ffc() -> FfcResponse:
    p = json.loads((FIXTURES / "ffc_ppr_2015.json").read_text())
    return FfcResponse(players=p["players"], total_drafts=p["meta"]["total_drafts"],
                       window_start=p["meta"]["start_date"],
                       window_end=p["meta"]["end_date"])


def _mfl() -> MflResponse:
    p = json.loads((FIXTURES / "mfl_adp_2015.json").read_text())
    return MflResponse(players=p["adp"]["player"], total_drafts=15877,
                       total_picks=421904)


def test_ffc_output_columns_match_schema_exactly():
    df = transform_ffc(_ffc(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert list(df.columns) == spec_names(FF_ADP_SCHEMA)


def test_mfl_output_columns_match_schema_exactly():
    df = transform_mfl(_mfl(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert list(df.columns) == spec_names(FF_ADP_SCHEMA)


def test_ffc_maps_high_to_earliest_and_low_to_latest():
    df = transform_ffc(_ffc(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    row = df.iloc[0]
    assert row["player_name"] == "Adrian Peterson"
    assert row["adp"] == 1.8
    assert row["adp_earliest_pick"] == 1     # FFC "high"
    assert row["adp_latest_pick"] == 5       # FFC "low"
    assert row["adp_stdev"] == 1.0
    assert row["times_drafted"] == 329
    assert row["bye"] == 6
    assert row["adp_formatted"] == "1.02"


def test_ffc_stamps_grain_and_window():
    df = transform_ffc(_ffc(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    row = df.iloc[0]
    assert row["source"] == "ffc"
    assert row["season"] == 2015
    assert row["scoring_format"] == "ppr"
    assert row["teams"] == 12
    assert row["snapshot_date"] == SNAP
    assert row["source_player_id"] == "925"   # string, not int
    assert row["total_drafts"] == 844
    assert str(row["window_start_date"]) == "2015-09-06"
    assert str(row["window_end_date"]) == "2015-09-09"


def test_ffc_leaves_mfl_only_columns_null():
    df = transform_ffc(_ffc(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    for col in ("draft_selected_pct", "source_rank", "is_keeper", "is_mock"):
        assert pd.isna(df.iloc[0][col]), f"{col} should be NULL for FFC"


def test_ffc_transform_preserves_every_input_row():
    df = transform_ffc(_ffc(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert len(df) == 2
    row = df.iloc[1]
    assert row["player_name"] == "Le'Veon Bell"
    assert row["adp_earliest_pick"] == 1   # high
    assert row["adp_latest_pick"] == 8     # low


def test_mfl_casts_string_numerics_and_maps_min_max_picks():
    df = transform_mfl(_mfl(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    row = df.iloc[0]
    assert row["source"] == "mfl"
    assert row["source_player_id"] == "11192"
    assert row["adp"] == 9.11                 # from the string "9.11"
    assert row["adp_earliest_pick"] == 1      # minPick
    assert row["adp_latest_pick"] == 439      # maxPick
    assert row["times_drafted"] == 9068       # draftsSelectedIn
    assert row["draft_selected_pct"] == 66.0
    assert row["source_rank"] == 1
    assert row["total_drafts"] == 15877


def test_mfl_transform_preserves_every_input_row():
    df = transform_mfl(_mfl(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert len(df) == 2
    row = df.iloc[1]
    assert row["source_player_id"] == "9988"
    assert row["adp_earliest_pick"] == 1     # minPick
    assert row["adp_latest_pick"] == 439     # maxPick


def test_mfl_leaves_ffc_only_columns_null():
    df = transform_mfl(_mfl(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    row = df.iloc[0]
    for col in ("player_name", "position", "team", "adp_stdev", "bye",
                "adp_formatted", "window_start_date", "window_end_date"):
        assert pd.isna(row[col]), f"{col} should be NULL for MFL"


def test_mfl_records_keeper_and_mock_slicers_when_supplied():
    df = transform_mfl(_mfl(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP, is_keeper=False, is_mock=True)
    assert bool(df.iloc[0]["is_keeper"]) is False
    assert bool(df.iloc[0]["is_mock"]) is True


def test_gsis_id_is_unresolved_at_transform_time():
    df = transform_ffc(_ffc(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert df["gsis_id"].isna().all()


def test_mfl_gsis_id_is_unresolved_at_transform_time():
    df = transform_mfl(_mfl(), season=2015, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert df["gsis_id"].isna().all()


def test_empty_response_yields_empty_frame_with_schema_columns():
    empty = FfcResponse(players=[], total_drafts=None, window_start=None,
                        window_end=None)
    df = transform_ffc(empty, season=2007, scoring_format="ppr", teams=12,
                       snapshot_date=SNAP)
    assert df.empty
    assert list(df.columns) == spec_names(FF_ADP_SCHEMA)
