import argparse
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ffl_bigquery.nflverse.driver import run_sync_nflverse, run_sync_nflverse_cli
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec
from ffl_bigquery.writer import TableRef, WriteSeasonResult


def _spec_cols() -> list[ColumnSpec]:
    return [
        ColumnSpec(name="season", type="INT64", mode="REQUIRED",
                   short_description="s", business_definition="s", semantic_tags=[],
                   valid_range=None, valid_values=None, example_value=None,
                   gotchas=[], source_field="season", deprecated_in_year=None),
        INGESTED_AT_SPEC,
    ]


def _table(name="t", rows=1, min_season=2010, loader=None):
    def _default(season: int) -> pd.DataFrame:
        return pd.DataFrame({"season": [season] * rows})

    return NflverseTableSpec(
        name=name, loader=loader or _default, schema=_spec_cols(),
        partition=None, transform=lambda df, season: df, min_season=min_season,
    )


def _refs(*names):
    return {n: TableRef.parse(f"p.d.{n}") for n in names}


def test_writes_each_season_and_records_success():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    rc = run_sync_nflverse([_table()], seasons=[2020, 2021], writer=w, runs=r,
                           runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    assert rc == 0
    assert w.write_season.call_count == 2
    assert {c.kwargs["season"] for c in w.write_season.call_args_list} == {2020, 2021}
    assert r.record_success.call_count == 2


def test_season_below_min_season_is_recorded_empty_and_not_fetched():
    calls = []

    def _loader(season: int) -> pd.DataFrame:
        calls.append(season)
        return pd.DataFrame({"season": [season]})

    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    r.record_empty.return_value = True
    run_sync_nflverse([_table(min_season=2013, loader=_loader)],
                      seasons=[2012, 2013], writer=w, runs=r,
                      runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    assert calls == [2013]           # 2012 never fetched
    assert r.record_empty.call_count == 1


def test_one_failing_table_does_not_stop_the_others():
    def _boom(season: int) -> pd.DataFrame:
        raise RuntimeError("upstream down")

    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    r.record_failed.return_value = True
    rc = run_sync_nflverse([_table(name="bad", loader=_boom), _table(name="good")],
                           seasons=[2020], writer=w, runs=r,
                           runs_ref=TableRef.parse("p.d.runs"),
                           table_refs=_refs("bad", "good"))
    assert rc == 0
    assert r.record_failed.call_count == 1
    assert r.record_success.call_count == 1


def test_all_failing_returns_nonzero():
    def _boom(season: int) -> pd.DataFrame:
        raise RuntimeError("down")

    w, r = MagicMock(), MagicMock()
    r.completed_chunks.return_value = set()
    r.record_failed.return_value = True
    rc = run_sync_nflverse([_table(loader=_boom)], seasons=[2020], writer=w, runs=r,
                           runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    assert rc == 1


def test_resume_skips_completed_chunks():
    calls = []

    def _loader(season: int) -> pd.DataFrame:
        calls.append(season)
        return pd.DataFrame({"season": [season]})

    w, r = MagicMock(), MagicMock()
    r.completed_chunks.return_value = {("t", 2020)}
    r.record_success.return_value = True
    w.write_season.return_value = 1
    run_sync_nflverse([_table(loader=_loader)], seasons=[2020, 2021], writer=w, runs=r,
                      runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"),
                      resume=True)
    assert calls == [2021]


def test_runlog_write_failures_are_counted_and_the_run_still_completes(caplog):
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = False        # every run-log write is lost
    with caplog.at_level("ERROR"):
        rc = run_sync_nflverse([_table()], seasons=[2020, 2021], writer=w, runs=r,
                               runs_ref=TableRef.parse("p.d.runs"),
                               table_refs=_refs("t"))
    assert rc == 0
    assert w.write_season.call_count == 2
    assert "2 run-log write failures" in caplog.text


def test_dropped_rows_are_accumulated_and_reported_in_the_summary(caplog):
    # write_season's dropped count must not vanish into a per-chunk log line
    # only -- the run-level summary needs it too, escalated to error like
    # runlog_failures already is (dropped rows above a warning is exactly
    # the invisibility Task 2 exists to fix).
    w, r = MagicMock(), MagicMock()
    w.write_season.side_effect = [
        WriteSeasonResult(1, 3), WriteSeasonResult(1, 0),
    ]
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    with caplog.at_level("ERROR"):
        rc = run_sync_nflverse([_table()], seasons=[2020, 2021], writer=w, runs=r,
                               runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    assert rc == 0
    assert "3 row(s) dropped by the season guard" in caplog.text


def test_summary_line_stays_at_info_when_nothing_is_dropped(caplog):
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = WriteSeasonResult(1, 0)
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    with caplog.at_level("INFO"):
        run_sync_nflverse([_table()], seasons=[2020], writer=w, runs=r,
                          runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    error_records = [rec for rec in caplog.records if rec.levelname == "ERROR"]
    assert error_records == []
    assert "0 row(s) dropped by the season guard" in caplog.text


def test_dropped_total_defaults_to_zero_for_a_writer_returning_a_bare_int():
    # A test double / older writer returning a plain int (no .dropped
    # attribute) must not crash the driver -- getattr(..., 0) covers it.
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    rc = run_sync_nflverse([_table()], seasons=[2020], writer=w, runs=r,
                           runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    assert rc == 0


def test_transform_receives_the_season_and_its_output_is_written():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    spec = NflverseTableSpec(
        name="t", loader=lambda s: pd.DataFrame({"season": [s]}),
        schema=_spec_cols(), partition=None,
        transform=lambda df, season: df.assign(season=season * 10), min_season=2010,
    )
    run_sync_nflverse([spec], seasons=[2020], writer=w, runs=r,
                      runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"))
    written = w.write_season.call_args[0][1]
    assert written["season"].iloc[0] == 20200


def test_dry_run_makes_no_calls():
    calls = []
    w, r = MagicMock(), MagicMock()
    run_sync_nflverse([_table(loader=lambda s: calls.append(s) or pd.DataFrame())],
                      seasons=[2020], writer=w, runs=r,
                      runs_ref=TableRef.parse("p.d.runs"), table_refs=_refs("t"),
                      dry_run=True)
    assert calls == []
    assert not w.write_season.called


def test_missing_table_ref_raises_before_any_fetch():
    calls = []
    w, r = MagicMock(), MagicMock()
    r.completed_chunks.return_value = set()
    with pytest.raises(KeyError):
        run_sync_nflverse([_table(name="t", loader=lambda s: calls.append(s) or pd.DataFrame())],
                          seasons=[2020], writer=w, runs=r,
                          runs_ref=TableRef.parse("p.d.runs"), table_refs={})
    assert calls == []


# ---------------------------------------------------------------------------
# run_sync_nflverse_cli: the sync-nflverse CLI orchestration layer -- resolves
# --dataset/--tables/--seasons into run_sync_nflverse's explicit arguments.
# ---------------------------------------------------------------------------


def _cli_ns(**kw):
    base = dict(
        seasons="2020", dataset="p.d", tables=None, runs_table=None,
        resume=False, dry_run=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_rejects_a_malformed_dataset():
    with pytest.raises(ValueError, match="p"):
        run_sync_nflverse_cli(_cli_ns(dataset="p"), bq_client=MagicMock(),
                              load_specs=lambda: [_table(name="ff_opportunity")])


def test_cli_rejects_an_unknown_table_name():
    with pytest.raises(ValueError, match="bogus"):
        run_sync_nflverse_cli(_cli_ns(tables="bogus"), bq_client=MagicMock(),
                              load_specs=lambda: [_table(name="ff_opportunity")])


def test_cli_defaults_to_all_nine_loaded_specs():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    names = ["ff_opportunity", "snap_counts", "injuries", "depth_charts",
             "participation", "ftn_charting", "nfl_coaches",
             "ff_points_weekly", "team_scheme_week"]
    run_sync_nflverse_cli(
        _cli_ns(), bq_client=MagicMock(), writer=w, runs=r,
        load_specs=lambda: [_table(name=n) for n in names],
    )
    # One season requested, nine tables -- nine write_season calls.
    assert w.write_season.call_count == 9


def test_cli_tables_flag_restricts_to_the_requested_subset():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    names = ["ff_opportunity", "snap_counts", "injuries"]
    run_sync_nflverse_cli(
        _cli_ns(tables="snap_counts"), bq_client=MagicMock(), writer=w, runs=r,
        load_specs=lambda: [_table(name=n) for n in names],
    )
    assert w.write_season.call_count == 1


def test_cli_derives_table_refs_from_the_dataset_argument():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    run_sync_nflverse_cli(
        _cli_ns(dataset="myproj.mydata", tables="snap_counts"),
        bq_client=MagicMock(), writer=w, runs=r,
        load_specs=lambda: [_table(name="snap_counts")],
    )
    ref = w.write_season.call_args[0][0]
    assert str(ref) == "myproj.mydata.snap_counts"


def test_cli_default_runs_table_is_derived_from_the_dataset():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    run_sync_nflverse_cli(
        _cli_ns(dataset="p.d", tables="snap_counts"), bq_client=MagicMock(),
        writer=w, runs=r, load_specs=lambda: [_table(name="snap_counts")],
    )
    runs_ref = r.create_table_if_missing.call_args[0][0]
    assert str(runs_ref) == "p.d._ffl_nflverse_runs"


def test_cli_explicit_runs_table_overrides_the_default():
    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    run_sync_nflverse_cli(
        _cli_ns(dataset="p.d", tables="snap_counts", runs_table="p.d.custom"),
        bq_client=MagicMock(), writer=w, runs=r,
        load_specs=lambda: [_table(name="snap_counts")],
    )
    runs_ref = r.create_table_if_missing.call_args[0][0]
    assert str(runs_ref) == "p.d.custom"


def test_cli_seasons_latest_resolves_via_current_season():
    calls = []

    def loader(season: int) -> pd.DataFrame:
        calls.append(season)
        return pd.DataFrame({"season": [season]})

    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = set()
    r.record_success.return_value = True
    run_sync_nflverse_cli(
        _cli_ns(seasons="latest", tables="snap_counts"), bq_client=MagicMock(),
        writer=w, runs=r, current_season=2024,
        load_specs=lambda: [_table(name="snap_counts", loader=loader)],
    )
    assert calls == [2024]


def test_cli_dry_run_never_touches_the_writer():
    w, r = MagicMock(), MagicMock()
    run_sync_nflverse_cli(
        _cli_ns(dry_run=True, tables="snap_counts"), bq_client=MagicMock(),
        writer=w, runs=r, load_specs=lambda: [_table(name="snap_counts")],
    )
    assert not w.write_season.called


def test_cli_resume_is_forwarded_to_the_driver():
    calls = []

    def loader(season: int) -> pd.DataFrame:
        calls.append(season)
        return pd.DataFrame({"season": [season]})

    w, r = MagicMock(), MagicMock()
    w.write_season.return_value = 1
    r.completed_chunks.return_value = {("snap_counts", 2020)}
    r.record_success.return_value = True
    run_sync_nflverse_cli(
        _cli_ns(seasons="2020", tables="snap_counts", resume=True),
        bq_client=MagicMock(), writer=w, runs=r,
        load_specs=lambda: [_table(name="snap_counts", loader=loader)],
    )
    assert calls == []  # already-completed chunk skipped
