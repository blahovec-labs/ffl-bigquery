"""The DST verify check is a self-consistency guard: fantasy_points_dst must
equal the components it claims to be a sum of. It catches the failure mode
where a scoring weight is changed in one place and not the other, which no
schema test can see."""
import argparse
from unittest.mock import MagicMock

from google.cloud import bigquery

from ffl_bigquery.verify.tables import (
    check_dst_components_reconcile,
    check_dst_covers_adp_defenses,
    run_verify_dst,
)


class _Row(dict):
    __getattr__ = dict.get


def _row(**kw):
    base = dict(
        season=2024, week=1, team="BAL", sacks=2, interceptions=1,
        fumble_recoveries=0, safeties=0, defensive_tds=0, blocked_kicks=0,
        points_allowed_bonus=0, fantasy_points_dst=4.0,
    )
    base.update(kw)
    return _Row(base)


def test_consistent_row_produces_no_findings():
    assert check_dst_components_reconcile([_row()]) == []


def test_total_disagreeing_with_components_is_reported():
    findings = check_dst_components_reconcile([_row(fantasy_points_dst=99.0)])
    assert len(findings) == 1
    assert "2024" in findings[0] and "BAL" in findings[0]


def test_null_bonus_row_is_skipped_not_failed():
    # points_allowed_bonus is NULL when the final score was unresolvable. Such
    # a row cannot be reconciled, and reporting it as a mismatch would bury real
    # findings in noise -- it is a coverage gap, not an arithmetic error.
    assert check_dst_components_reconcile([
        _row(points_allowed_bonus=None, fantasy_points_dst=4.0),
    ]) == []


def test_multiple_bad_rows_are_all_reported():
    findings = check_dst_components_reconcile([
        _row(team="BAL", fantasy_points_dst=99.0),
        _row(team="KC", fantasy_points_dst=-5.0),
    ])
    assert len(findings) == 2


# --- coverage: every drafted DEF must have somewhere to score ---


def test_every_drafted_defense_resolves():
    rows = [_Row(season=2024, team="BAL", dst_weeks=17)]
    assert check_dst_covers_adp_defenses(rows) == []


def test_drafted_defense_with_no_dst_rows_is_reported():
    """The exact failure this table exists to prevent: a DEF on the draft board
    with nothing to score against. Team abbreviations drift across relocations
    (SD->LAC, STL->LAR, OAK->LV), so an ADP board naming a franchise by its
    old code would silently produce zero-scoring defenses rather than an error."""
    rows = [_Row(season=2016, team="SD", dst_weeks=0)]
    findings = check_dst_covers_adp_defenses(rows)
    assert len(findings) == 1
    assert "SD" in findings[0] and "2016" in findings[0]


# ---------------------------------------------------------------------------
# run_verify_dst: the BigQuery-facing wrapper. The coverage half must
# announce itself as SKIPPED when --adp-table is absent, not silently print
# "0 findings" -- that failure mode has already cost this project real
# debugging time (see check_dst_covers_adp_defenses's docstring).
# ---------------------------------------------------------------------------


def _query_returning_sequence(rows_sequence):
    c = MagicMock(spec=bigquery.Client)
    jobs = []
    for rows in rows_sequence:
        job = MagicMock()
        job.result.return_value = rows
        jobs.append(job)
    c.query.side_effect = jobs
    return c


def test_run_verify_dst_passes_and_announces_coverage_skipped_without_adp_table(capsys):
    c = _query_returning_sequence([[_row()]])
    ns = argparse.Namespace(dst_table="p.d.ff_points_dst_weekly", adp_table=None)
    assert run_verify_dst(ns, bq_client=c) == 0
    assert "SKIPPED" in capsys.readouterr().out


def test_run_verify_dst_fails_on_arithmetic_mismatch():
    c = _query_returning_sequence([[_row(fantasy_points_dst=99.0)]])
    ns = argparse.Namespace(dst_table="p.d.ff_points_dst_weekly", adp_table=None)
    assert run_verify_dst(ns, bq_client=c) == 1


def test_run_verify_dst_coverage_half_fails_on_a_gap():
    reconcile_rows = [_row()]
    coverage_rows = [_Row(season=2016, team="SD", dst_weeks=0)]
    c = _query_returning_sequence([reconcile_rows, coverage_rows])
    ns = argparse.Namespace(
        dst_table="p.d.ff_points_dst_weekly", adp_table="p.d.ff_adp",
    )
    assert run_verify_dst(ns, bq_client=c) == 1


def test_run_verify_dst_coverage_half_passes_when_fully_covered():
    reconcile_rows = [_row()]
    coverage_rows = [_Row(season=2024, team="BAL", dst_weeks=17)]
    c = _query_returning_sequence([reconcile_rows, coverage_rows])
    ns = argparse.Namespace(
        dst_table="p.d.ff_points_dst_weekly", adp_table="p.d.ff_adp",
    )
    assert run_verify_dst(ns, bq_client=c) == 0
