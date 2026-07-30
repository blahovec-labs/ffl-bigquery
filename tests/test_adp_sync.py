from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest
from google.cloud import bigquery

from ffl_bigquery.adp.sync import _fetch_and_transform, build_chunks, run_sync_adp
from ffl_bigquery.http import SourceUnavailable
from ffl_bigquery.runs import AdpChunk, RunsTable

XREF = pd.DataFrame({
    "mfl_id": [8658], "gsis_id": ["00-0025394"],
    "merge_name": ["adrian peterson"], "position": ["RB"],
})


def _ns(**kw):
    base = dict(
        seasons="2015", sources="ffc", formats="ppr", teams="12",
        adp_table="p.d.ff_adp", xref_table="p.d.ff_player_xref",
        runs_table="p.d._ffl_ingest_runs", resume=False, dry_run=False,
        min_interval=0.0,
    )
    base.update(kw)
    return MagicMock(**base)


def _ffc_payload(players):
    return {"status": "Success",
            "meta": {"total_drafts": 844, "start_date": "2015-09-06",
                     "end_date": "2015-09-09"},
            "players": players}


_PLAYER = {"player_id": 925, "name": "Adrian Peterson", "position": "RB",
           "team": "MIN", "adp": 1.8, "adp_formatted": "1.02",
           "times_drafted": 329, "high": 1, "low": 5, "stdev": 1.0, "bye": 6}


def test_build_chunks_is_the_cross_product_for_ffc():
    chunks = build_chunks(seasons=[2025, 2026], sources=["ffc"],
                          formats=["ppr", "standard"], teams=[10, 12])
    assert len(chunks) == 8
    assert all(c.source == "ffc" for c in chunks)


def test_unknown_source_in_sources_raises_before_any_request():
    """--sources fcc (a typo for ffc) must fail fast, not silently fall through
    to MyFantasyLeague and issue a live request it was never asked for.
    """
    sess = MagicMock()
    with pytest.raises(ValueError, match="fcc"):
        run_sync_adp(_ns(sources="fcc"), bq_client=MagicMock(spec=bigquery.Client),
                     session=sess, load_xref=lambda _: XREF,
                     today=date(2026, 7, 29), runs=MagicMock(), writer=MagicMock())
    assert not sess.get_json.called


def test_unknown_source_mixed_with_a_valid_one_still_raises():
    sess = MagicMock()
    with pytest.raises(ValueError, match="fcc"):
        run_sync_adp(_ns(sources="ffc,fcc"), bq_client=MagicMock(spec=bigquery.Client),
                     session=sess, load_xref=lambda _: XREF,
                     today=date(2026, 7, 29), runs=MagicMock(), writer=MagicMock())
    assert not sess.get_json.called


def test_fetch_and_transform_raises_on_unknown_chunk_source():
    chunk = AdpChunk(source="bogus", season=2026, scoring_format="ppr", teams=12)
    with pytest.raises(ValueError, match="bogus"):
        _fetch_and_transform(chunk, MagicMock(), date(2026, 7, 29))


def test_build_chunks_restricts_mfl_to_ppr_and_standard():
    chunks = build_chunks(seasons=[2026], sources=["mfl"],
                          formats=["ppr", "standard", "dynasty", "rookie"],
                          teams=[12])
    assert {c.scoring_format for c in chunks} == {"ppr", "standard"}


def test_successful_chunk_merges_rows_and_records_success():
    bq = MagicMock(spec=bigquery.Client)
    sess = MagicMock()
    sess.get_json.return_value = _ffc_payload([_PLAYER])
    runs = MagicMock()
    rc = run_sync_adp(_ns(), bq_client=bq, session=sess,
                      load_xref=lambda _: XREF, today=date(2026, 7, 29), runs=runs)
    assert rc == 0
    assert runs.record_success.called
    assert runs.record_success.call_args.kwargs["rows_written"] == 1


def test_resolution_happens_before_the_write():
    bq = MagicMock(spec=bigquery.Client)
    sess = MagicMock()
    sess.get_json.return_value = _ffc_payload([_PLAYER])
    captured = {}
    writer = MagicMock()
    writer.merge_rows.side_effect = lambda **kw: (
        captured.update(df=kw["df"]) or len(kw["df"])
    )
    run_sync_adp(_ns(), bq_client=bq, session=sess, load_xref=lambda _: XREF,
                 today=date(2026, 7, 29), runs=MagicMock(), writer=writer)
    assert captured["df"].iloc[0]["gsis_id"] == "00-0025394"


def test_empty_response_records_empty_and_writes_nothing():
    sess = MagicMock()
    sess.get_json.return_value = {"status": "Error", "errors": "No ADP data found."}
    runs, writer = MagicMock(), MagicMock()
    rc = run_sync_adp(_ns(seasons="2007"), bq_client=MagicMock(spec=bigquery.Client),
                      session=sess, load_xref=lambda _: XREF,
                      today=date(2026, 7, 29), runs=runs, writer=writer)
    assert rc == 0
    assert runs.record_empty.called
    assert not writer.merge_rows.called


def test_one_failing_chunk_does_not_stop_the_others():
    sess = MagicMock()
    sess.get_json.side_effect = [
        SourceUnavailable("down"), _ffc_payload([_PLAYER]),
    ]
    runs, writer = MagicMock(), MagicMock()
    writer.merge_rows.return_value = 1
    rc = run_sync_adp(_ns(seasons="2015,2016"),
                      bq_client=MagicMock(spec=bigquery.Client), session=sess,
                      load_xref=lambda _: XREF, today=date(2026, 7, 29),
                      runs=runs, writer=writer)
    assert rc == 0
    assert runs.record_failed.call_count == 1
    assert runs.record_success.call_count == 1


def test_all_chunks_failing_returns_nonzero():
    sess = MagicMock()
    sess.get_json.side_effect = SourceUnavailable("down")
    rc = run_sync_adp(_ns(), bq_client=MagicMock(spec=bigquery.Client), session=sess,
                      load_xref=lambda _: XREF, today=date(2026, 7, 29),
                      runs=MagicMock(), writer=MagicMock())
    assert rc == 1


def test_resume_skips_completed_chunks():
    runs = MagicMock()
    runs.completed_chunks.return_value = {("ffc", 2015, "ppr", 12)}
    sess = MagicMock()
    rc = run_sync_adp(_ns(resume=True), bq_client=MagicMock(spec=bigquery.Client),
                      session=sess, load_xref=lambda _: XREF,
                      today=date(2026, 7, 29), runs=runs, writer=MagicMock())
    assert rc == 0
    assert not sess.get_json.called


def test_xref_is_loaded_once_across_many_chunks():
    calls = []
    sess = MagicMock()
    sess.get_json.return_value = _ffc_payload([_PLAYER])
    writer = MagicMock()
    writer.merge_rows.return_value = 1

    def _load(_):
        calls.append(1)
        return XREF

    run_sync_adp(_ns(seasons="2014,2015,2016"),
                 bq_client=MagicMock(spec=bigquery.Client), session=sess,
                 load_xref=_load, today=date(2026, 7, 29), runs=MagicMock(),
                 writer=writer)
    assert len(calls) == 1


def test_dry_run_makes_no_requests_and_no_writes():
    sess, writer = MagicMock(), MagicMock()
    rc = run_sync_adp(_ns(dry_run=True), bq_client=MagicMock(spec=bigquery.Client),
                      session=sess, load_xref=lambda _: XREF,
                      today=date(2026, 7, 29), runs=MagicMock(), writer=writer)
    assert rc == 0
    assert not sess.get_json.called
    assert not writer.merge_rows.called


def test_runlog_write_failure_is_counted_and_the_run_still_completes(caplog):
    """Late-added requirement (2026-07-29): record_* returning False must not be
    invisible. A lost run-log row means --resume would re-issue a request it
    already knows is void, so the run must count the loss and escalate the
    final summary to log.error -- without raising, since the chunk's ADP rows
    are already MERGE-written and the MERGE is idempotent.
    """
    sess = MagicMock()
    sess.get_json.return_value = _ffc_payload([_PLAYER])
    runs = MagicMock()
    runs.record_success.return_value = False  # simulates a lost run-log row
    writer = MagicMock()
    writer.merge_rows.return_value = 1

    caplog.set_level("INFO", logger="ffl_bigquery.adp.sync")
    rc = run_sync_adp(_ns(), bq_client=MagicMock(spec=bigquery.Client), session=sess,
                      load_xref=lambda _: XREF, today=date(2026, 7, 29),
                      runs=runs, writer=writer)

    # The chunk's data is already durably written; a run-log write failure
    # must not fail the run or block subsequent chunks.
    assert rc == 0
    assert writer.merge_rows.called

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "a run-log write failure must escalate to log.error"
    assert "1 run-log write failures" in error_records[-1].getMessage()


def test_runlog_write_failure_is_not_counted_when_record_succeeds(caplog):
    """Negative control for the counter: when every record_* call reports True
    (the default MagicMock behavior), the summary must report zero run-log
    write failures and log at INFO, not ERROR.
    """
    sess = MagicMock()
    sess.get_json.return_value = _ffc_payload([_PLAYER])
    runs = MagicMock()
    runs.record_success.return_value = True
    writer = MagicMock()
    writer.merge_rows.return_value = 1

    caplog.set_level("INFO", logger="ffl_bigquery.adp.sync")
    rc = run_sync_adp(_ns(), bq_client=MagicMock(spec=bigquery.Client), session=sess,
                      load_xref=lambda _: XREF, today=date(2026, 7, 29),
                      runs=runs, writer=writer)

    assert rc == 0
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    info_records = [r for r in caplog.records if r.levelname == "INFO"]
    assert any("0 run-log write failures" in r.getMessage() for r in info_records)


def test_run_completes_all_chunks_when_every_runlog_insert_raises():
    """A *raising* run-log insert (transport error, permission error, the
    streaming-404 window right after the runs table is first created) must
    degrade to a counted run-log failure, not abort the run. Uses a real
    RunsTable (not a mock) so record_success/record_failed genuinely go
    through _record and its insert_rows_json call.
    """
    bq = MagicMock(spec=bigquery.Client)
    bq.insert_rows_json.side_effect = RuntimeError("boom")
    sess = MagicMock()
    sess.get_json.return_value = _ffc_payload([_PLAYER])
    writer = MagicMock()
    writer.merge_rows.return_value = 1
    runs = RunsTable(client=bq)

    rc = run_sync_adp(_ns(seasons="2014,2015,2016"), bq_client=bq, session=sess,
                      load_xref=lambda _: XREF, today=date(2026, 7, 29),
                      runs=runs, writer=writer)

    assert rc == 0
    assert writer.merge_rows.call_count == 3
