from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
from google.cloud import bigquery

from ffl_bigquery.schema import spec_names
from ffl_bigquery.xref.schema import FF_XREF_KEY, FF_XREF_SCHEMA, XREF_ID_COLUMNS
from ffl_bigquery.xref.transform import transform_xref

FIXTURES = Path(__file__).parent / "fixtures"


def test_key_is_mfl_id_and_is_required():
    assert FF_XREF_KEY == "mfl_id"
    by_name = {s.name: s for s in FF_XREF_SCHEMA}
    assert by_name["mfl_id"].mode == "REQUIRED"
    assert by_name["mfl_id"].type == "INT64"


def test_gsis_id_is_nullable_because_38_percent_are_null_upstream():
    by_name = {s.name: s for s in FF_XREF_SCHEMA}
    assert by_name["gsis_id"].mode == "NULLABLE"


def test_covers_the_twenty_id_systems():
    assert len(XREF_ID_COLUMNS) == 20
    for col in ("mfl_id", "gsis_id", "fantasypros_id", "sleeper_id", "espn_id", "pfr_id"):
        assert col in XREF_ID_COLUMNS
    names = spec_names(FF_XREF_SCHEMA)
    for col in XREF_ID_COLUMNS:
        assert col in names


def test_merge_name_and_position_are_present_for_ffc_resolution():
    names = spec_names(FF_XREF_SCHEMA)
    assert "merge_name" in names
    assert "position" in names


def test_transform_aligns_to_schema_and_stamps_ingested_at():
    raw = pd.read_csv(FIXTURES / "ff_playerids_sample.csv")
    out = transform_xref(raw)
    assert list(out.columns) == spec_names(FF_XREF_SCHEMA)
    assert out["ingested_at"].notna().all()


def test_transform_drops_rows_without_the_merge_key():
    raw = pd.DataFrame({"mfl_id": [1, None], "merge_name": ["a b", "c d"],
                        "position": ["RB", "WR"]})
    out = transform_xref(raw)
    assert len(out) == 1
    assert out.iloc[0]["mfl_id"] == 1


def test_sync_creates_table_then_merges_on_mfl_id():
    from ffl_bigquery.xref.sync import run_sync_xref

    bq = MagicMock(spec=bigquery.Client)
    ns = MagicMock(xref_table="p.d.ff_player_xref", dry_run=False)
    loader = MagicMock(return_value=pd.read_csv(FIXTURES / "ff_playerids_sample.csv"))
    run_sync_xref(ns, bq_client=bq, load_playerids=loader)
    assert bq.create_table.called or bq.get_table.called
    merge_sql = [c[0][0] for c in bq.query.call_args_list if "MERGE" in str(c[0][0])]
    assert merge_sql and "ON t.mfl_id = s.mfl_id" in merge_sql[0]


def test_sync_dry_run_writes_nothing():
    from ffl_bigquery.xref.sync import run_sync_xref

    bq = MagicMock(spec=bigquery.Client)
    ns = MagicMock(xref_table="p.d.ff_player_xref", dry_run=True)
    rc = run_sync_xref(ns, bq_client=bq, load_playerids=MagicMock())
    assert rc == 0
    assert not bq.query.called
    assert not bq.load_table_from_dataframe.called
