from unittest.mock import MagicMock

from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from ffl_bigquery.nflverse.runs import NflverseChunk, NflverseRunsTable
from ffl_bigquery.writer import TableRef

REF = TableRef.parse("p.d._ffl_nflverse_runs")


def test_chunk_key_is_table_and_season():
    assert NflverseChunk("injuries", 2020).key == ("injuries", 2020)


def test_create_table_marks_key_columns_required():
    c = MagicMock(spec=bigquery.Client)
    c.get_table.side_effect = NotFound("nope")
    NflverseRunsTable(client=c).create_table_if_missing(REF)
    schema = {f.name: f for f in c.create_table.call_args[0][0].schema}
    for col in ("table_name", "season", "status", "run_at"):
        assert schema[col].mode == "REQUIRED"


def test_completed_chunks_includes_success_and_empty_only():
    c = MagicMock(spec=bigquery.Client)
    seen = {}

    def _q(sql):
        seen["sql"] = sql
        job = MagicMock()
        job.result.return_value = [MagicMock(table_name="injuries", season=2020)]
        return job

    c.query.side_effect = _q
    assert NflverseRunsTable(client=c).completed_chunks(ref=REF) == {("injuries", 2020)}
    assert "status IN ('success', 'empty')" in seen["sql"]


def test_record_success_writes_the_chunk_key_and_returns_true():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = []
    ok = NflverseRunsTable(client=c).record_success(
        ref=REF, chunk=NflverseChunk("injuries", 2020), rows_written=5,
    )
    assert ok is True
    row = c.insert_rows_json.call_args[0][1][0]
    assert (row["table_name"], row["season"], row["status"], row["rows_written"]) == (
        "injuries", 2020, "success", 5,
    )
    assert row["library_version"]


def test_record_returns_false_on_reported_errors():
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.return_value = [{"index": 0, "errors": ["boom"]}]
    assert NflverseRunsTable(client=c).record_empty(
        ref=REF, chunk=NflverseChunk("injuries", 2020),
    ) is False


def test_record_returns_false_when_insert_raises_rather_than_propagating():
    # A raising run-log write must never abort the caller's loop.
    c = MagicMock(spec=bigquery.Client)
    c.insert_rows_json.side_effect = RuntimeError("transport blew up")
    assert NflverseRunsTable(client=c).record_failed(
        ref=REF, chunk=NflverseChunk("injuries", 2020), error="x",
    ) is False
