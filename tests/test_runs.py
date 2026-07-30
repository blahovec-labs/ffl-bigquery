from unittest.mock import MagicMock

from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from ffl_bigquery.runs import AdpChunk, RunsTable, parse_seasons
from ffl_bigquery.writer import TableRef

REF = TableRef.parse("p.d._ffl_ingest_runs")


def test_parse_seasons_range():
    assert parse_seasons("2010-2013", 2026) == [2010, 2011, 2012, 2013]


def test_parse_seasons_list_and_single_and_latest():
    assert parse_seasons("2015,2020", 2026) == [2015, 2020]
    assert parse_seasons("2024", 2026) == [2024]
    assert parse_seasons("latest", 2026) == [2026]


def test_chunk_key_is_the_four_work_columns():
    c = AdpChunk(source="ffc", season=2026, scoring_format="ppr", teams=12)
    assert c.key == ("ffc", 2026, "ppr", 12)


def test_create_table_is_a_noop_when_it_exists():
    c = MagicMock(spec=bigquery.Client)
    RunsTable(client=c).create_table_if_missing(REF)
    assert not c.create_table.called


def test_create_table_defines_the_four_key_columns_as_required():
    c = MagicMock(spec=bigquery.Client)
    c.get_table.side_effect = NotFound("nope")
    RunsTable(client=c).create_table_if_missing(REF)
    schema = {f.name: f for f in c.create_table.call_args[0][0].schema}
    for col in ("source", "season", "scoring_format", "teams", "status", "run_at"):
        assert schema[col].mode == "REQUIRED", f"{col} should be REQUIRED"


def test_completed_chunks_includes_success_and_empty_only():
    c = MagicMock(spec=bigquery.Client)
    sql_seen = {}

    def _query(sql):
        sql_seen["sql"] = sql
        job = MagicMock()
        job.result.return_value = [
            MagicMock(source="ffc", season=2010, scoring_format="ppr", teams=12),
        ]
        return job

    c.query.side_effect = _query
    out = RunsTable(client=c).completed_chunks(ref=REF)
    assert out == {("ffc", 2010, "ppr", 12)}
    assert "status IN ('success', 'empty')" in sql_seen["sql"]


def test_record_empty_writes_status_empty_with_the_chunk_key():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    chunk = AdpChunk(source="ffc", season=2007, scoring_format="ppr", teams=12)
    RunsTable(client=c).record_empty(ref=REF, chunk=chunk)
    row = c.insert_rows_json.call_args[0][1][0]
    assert row["status"] == "empty"
    assert (row["source"], row["season"], row["scoring_format"], row["teams"]) == (
        "ffc", 2007, "ppr", 12,
    )
    assert row["error"] is None
    assert row["library_version"]


def test_record_failed_carries_the_error_text():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    RunsTable(client=c).record_failed(
        ref=REF, chunk=AdpChunk("mfl", 2020, "ppr", 12), error="boom",
    )
    row = c.insert_rows_json.call_args[0][1][0]
    assert row["status"] == "failed"
    assert row["error"] == "boom"


def test_record_success_carries_rows_written():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    RunsTable(client=c).record_success(
        ref=REF, chunk=AdpChunk("ffc", 2026, "ppr", 12), rows_written=242,
    )
    row = c.insert_rows_json.call_args[0][1][0]
    assert row["status"] == "success"
    assert row["rows_written"] == 242


def test_record_success_returns_true_on_clean_insert():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    ok = RunsTable(client=c).record_success(
        ref=REF, chunk=AdpChunk("ffc", 2026, "ppr", 12), rows_written=242,
    )
    assert ok is True


def test_record_success_returns_false_when_insert_reports_errors():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = [{"index": 0, "errors": ["boom"]}]
    ok = RunsTable(client=c).record_success(
        ref=REF, chunk=AdpChunk("ffc", 2026, "ppr", 12), rows_written=242,
    )
    assert ok is False


def test_record_empty_returns_true_on_clean_insert():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    ok = RunsTable(client=c).record_empty(
        ref=REF, chunk=AdpChunk("ffc", 2007, "ppr", 12),
    )
    assert ok is True


def test_record_empty_returns_false_when_insert_reports_errors():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = [{"index": 0, "errors": ["boom"]}]
    ok = RunsTable(client=c).record_empty(
        ref=REF, chunk=AdpChunk("ffc", 2007, "ppr", 12),
    )
    assert ok is False


def test_record_failed_returns_true_on_clean_insert():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    ok = RunsTable(client=c).record_failed(
        ref=REF, chunk=AdpChunk("mfl", 2020, "ppr", 12), error="boom",
    )
    assert ok is True


def test_record_failed_returns_false_when_insert_reports_errors():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = [{"index": 0, "errors": ["boom"]}]
    ok = RunsTable(client=c).record_failed(
        ref=REF, chunk=AdpChunk("mfl", 2020, "ppr", 12), error="boom",
    )
    assert ok is False


def test_record_returns_false_rather_than_raising_when_insert_rows_json_raises():
    # A transport error, a permission error, or the well-known BigQuery
    # streaming-404 window right after the runs table is first created all
    # surface as insert_rows_json *raising*, not returning an errors list.
    # _record's bool contract must stay total: callers (run_sync_adp's
    # per-chunk try/except) rely on record_* never raising.
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.side_effect = RuntimeError("boom")
    ok = RunsTable(client=c).record_success(
        ref=REF, chunk=AdpChunk("ffc", 2026, "ppr", 12), rows_written=242,
    )
    assert ok is False
