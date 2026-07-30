import argparse
import re
from unittest.mock import MagicMock

from google.cloud import bigquery

from ffl_bigquery.adp.schema import FF_ADP_KEYS
from ffl_bigquery.verify.adp import (
    check_no_duplicates_at_grain,
    check_resolution_rate,
    check_snapshot_idempotent,
    run_verify,
)


def _query_returning(rows):
    c = MagicMock(spec=bigquery.Client)
    job = MagicMock()
    job.result.return_value = rows
    c.query.return_value = job
    return c


def _query_returning_sequence(rows_sequence):
    """A client whose query() returns a new result set per call, in order.

    Lets a single mocked bq_client stand in for run_verify's three distinct
    queries (resolution rate, duplicate grain, snapshot idempotency), each of
    which expects a different row shape.
    """
    c = MagicMock(spec=bigquery.Client)
    jobs = []
    for rows in rows_sequence:
        job = MagicMock()
        job.result.return_value = rows
        jobs.append(job)
    c.query.side_effect = jobs
    return c


def _verify_ns(**overrides):
    defaults = dict(adp_table="p.d.ff_adp", season=2026, min_resolution_rate=0.60)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _group_by_columns(sql: str) -> list[str]:
    """Extract the column list from a single-line `GROUP BY ...` clause."""
    m = re.search(r"GROUP BY ([^\n]+)", sql)
    assert m, f"no GROUP BY clause found in query: {sql}"
    return [c.strip() for c in m.group(1).split(",")]


def test_resolution_rate_passes_when_above_floor():
    c = _query_returning([MagicMock(source="mfl", n=100, resolved=95, rate=0.95)])
    results = check_resolution_rate(c, adp_table="p.d.ff_adp", season=2026,
                                   min_rate=0.60)
    assert all(r.passed for r in results)
    assert "mfl" in results[0].detail


def test_resolution_rate_fails_per_source_when_below_floor():
    c = _query_returning([
        MagicMock(source="ffc", n=200, resolved=60, rate=0.30),
        MagicMock(source="mfl", n=100, resolved=95, rate=0.95),
    ])
    results = check_resolution_rate(c, adp_table="p.d.ff_adp", season=2026,
                                   min_rate=0.60)
    by_source = {r.name: r for r in results}
    assert by_source["resolution_rate[ffc]"].passed is False
    assert by_source["resolution_rate[mfl]"].passed is True


def test_resolution_rate_fails_loudly_when_no_rows_exist():
    results = check_resolution_rate(_query_returning([]), adp_table="p.d.ff_adp",
                                    season=2026, min_rate=0.60)
    assert len(results) == 1
    assert results[0].passed is False
    assert "no rows" in results[0].detail.lower()


def test_duplicate_check_passes_on_zero_and_groups_by_exact_grain():
    c = _query_returning([MagicMock(dupes=0)])
    r = check_no_duplicates_at_grain(c, adp_table="p.d.ff_adp", season=2026)
    assert r.passed is True
    sql = c.query.call_args[0][0]
    # Exact-match, not substring: "source" is a substring of "source_player_id",
    # so a dropped "source" key would still pass a membership/substring check.
    assert _group_by_columns(sql) == FF_ADP_KEYS


def test_duplicate_check_fails_when_dupes_present():
    r = check_no_duplicates_at_grain(_query_returning([MagicMock(dupes=3)]),
                                     adp_table="p.d.ff_adp", season=2026)
    assert r.passed is False
    assert "3" in r.detail


def test_snapshot_idempotency_passes_with_one_row_per_grain_per_day():
    c = _query_returning([MagicMock(max_rows_per_grain=1)])
    assert check_snapshot_idempotent(c, adp_table="p.d.ff_adp",
                                     season=2026).passed is True


def test_snapshot_idempotency_fails_when_a_grain_repeats_within_a_day():
    c = _query_returning([MagicMock(max_rows_per_grain=2)])
    assert check_snapshot_idempotent(c, adp_table="p.d.ff_adp",
                                     season=2026).passed is False


def test_snapshot_idempotency_groups_by_exact_grain():
    c = _query_returning([MagicMock(max_rows_per_grain=1)])
    check_snapshot_idempotent(c, adp_table="p.d.ff_adp", season=2026)
    sql = c.query.call_args[0][0]
    assert _group_by_columns(sql) == FF_ADP_KEYS


def test_run_verify_returns_zero_when_all_checks_pass():
    c = _query_returning_sequence([
        [MagicMock(source="mfl", n=100, resolved=95, rate=0.95)],
        [MagicMock(dupes=0)],
        [MagicMock(max_rows_per_grain=1)],
    ])
    assert run_verify(_verify_ns(), bq_client=c) == 0


def test_run_verify_returns_one_when_resolution_rate_fails():
    c = _query_returning_sequence([
        [MagicMock(source="ffc", n=200, resolved=10, rate=0.05)],
        [MagicMock(dupes=0)],
        [MagicMock(max_rows_per_grain=1)],
    ])
    assert run_verify(_verify_ns(), bq_client=c) == 1


def test_run_verify_returns_one_when_duplicate_check_fails():
    c = _query_returning_sequence([
        [MagicMock(source="mfl", n=100, resolved=95, rate=0.95)],
        [MagicMock(dupes=5)],
        [MagicMock(max_rows_per_grain=1)],
    ])
    assert run_verify(_verify_ns(), bq_client=c) == 1


def test_run_verify_returns_one_when_snapshot_idempotency_fails():
    c = _query_returning_sequence([
        [MagicMock(source="mfl", n=100, resolved=95, rate=0.95)],
        [MagicMock(dupes=0)],
        [MagicMock(max_rows_per_grain=2)],
    ])
    assert run_verify(_verify_ns(), bq_client=c) == 1


def test_run_verify_runs_and_reports_all_three_checks(capsys):
    c = _query_returning_sequence([
        [MagicMock(source="mfl", n=100, resolved=95, rate=0.95)],
        [MagicMock(dupes=0)],
        [MagicMock(max_rows_per_grain=1)],
    ])
    run_verify(_verify_ns(), bq_client=c)
    assert c.query.call_count == 3
    out = capsys.readouterr().out
    assert "resolution_rate[mfl]" in out
    assert "no_duplicates_at_grain" in out
    assert "snapshot_idempotent" in out
    assert "3/3 checks passed" in out
