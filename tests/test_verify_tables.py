import argparse
from unittest.mock import MagicMock

import pytest
from google.cloud import bigquery

from ffl_bigquery.verify import run_verify_cli
from ffl_bigquery.verify.tables import (
    SCHEME_RATE_DENOMINATOR_PAIRS,
    check_participation_coverage_matrix,
    check_points_weekly_ppr_reconciles,
    check_scheme_rate_denominators,
    run_verify_participation_coverage,
    run_verify_points_weekly,
    run_verify_scheme_denominators,
)


def _query_returning(rows):
    c = MagicMock(spec=bigquery.Client)
    job = MagicMock()
    job.result.return_value = rows
    c.query.return_value = job
    return c


def _query_returning_sequence(rows_sequence):
    c = MagicMock(spec=bigquery.Client)
    jobs = []
    for rows in rows_sequence:
        job = MagicMock()
        job.result.return_value = rows
        jobs.append(job)
    c.query.side_effect = jobs
    return c


# ---------------------------------------------------------------------------
# Check 4: ff_points_weekly PPR reconciliation
# ---------------------------------------------------------------------------


def test_points_weekly_reconciles_when_recomputed_matches_upstream():
    c = _query_returning([MagicMock(n=1000, mismatches=0)])
    r = check_points_weekly_ppr_reconciles(
        c, points_weekly_table="p.d.ff_points_weekly", season=2023, tolerance=0.01,
    )
    assert r.passed is True


def test_points_weekly_fails_when_recomputed_disagrees():
    c = _query_returning([MagicMock(n=1000, mismatches=7)])
    r = check_points_weekly_ppr_reconciles(
        c, points_weekly_table="p.d.ff_points_weekly", season=2023, tolerance=0.01,
    )
    assert r.passed is False
    assert "7" in r.detail


def test_points_weekly_fails_loudly_when_no_rows_match():
    c = _query_returning([MagicMock(n=0, mismatches=0)])
    r = check_points_weekly_ppr_reconciles(
        c, points_weekly_table="p.d.ff_points_weekly", season=2023, tolerance=0.01,
    )
    assert r.passed is False
    assert "no rows" in r.detail.lower()


def test_points_weekly_query_filters_by_season_and_embeds_tolerance():
    c = _query_returning([MagicMock(n=1, mismatches=0)])
    check_points_weekly_ppr_reconciles(
        c, points_weekly_table="p.d.ff_points_weekly", season=2024, tolerance=0.25,
    )
    sql = c.query.call_args[0][0]
    assert "season = 2024" in sql
    assert "0.25" in sql


# ---------------------------------------------------------------------------
# Check 5: team_scheme_week rate/denominator invariants
# ---------------------------------------------------------------------------


def test_scheme_denominators_all_pairs_pass():
    rows = [[MagicMock(n=100, null_denom=0, over_one=0)] for _ in SCHEME_RATE_DENOMINATOR_PAIRS]
    c = _query_returning_sequence(rows)
    results = check_scheme_rate_denominators(
        c, scheme_week_table="p.d.team_scheme_week", season=2023,
    )
    assert len(results) == len(SCHEME_RATE_DENOMINATOR_PAIRS)
    assert all(r.passed for r in results)


def test_scheme_denominators_flags_a_rate_with_a_null_denominator():
    rows = [[MagicMock(n=100, null_denom=0, over_one=0)] for _ in SCHEME_RATE_DENOMINATOR_PAIRS]
    rows[0] = [MagicMock(n=100, null_denom=3, over_one=0)]
    c = _query_returning_sequence(rows)
    results = check_scheme_rate_denominators(
        c, scheme_week_table="p.d.team_scheme_week", season=2023,
    )
    first_rate = SCHEME_RATE_DENOMINATOR_PAIRS[0][0]
    by_name = {r.name: r for r in results}
    assert by_name[f"scheme_denominators[{first_rate}]"].passed is False
    assert sum(1 for r in results if not r.passed) == 1


def test_scheme_denominators_flags_a_rate_above_one():
    rows = [[MagicMock(n=100, null_denom=0, over_one=0)] for _ in SCHEME_RATE_DENOMINATOR_PAIRS]
    rows[1] = [MagicMock(n=100, null_denom=0, over_one=2)]
    c = _query_returning_sequence(rows)
    results = check_scheme_rate_denominators(
        c, scheme_week_table="p.d.team_scheme_week", season=2023,
    )
    second_rate = SCHEME_RATE_DENOMINATOR_PAIRS[1][0]
    by_name = {r.name: r for r in results}
    assert by_name[f"scheme_denominators[{second_rate}]"].passed is False
    assert sum(1 for r in results if not r.passed) == 1


def test_scheme_denominators_fails_loudly_when_no_rows_for_a_pair():
    rows = [[MagicMock(n=0, null_denom=0, over_one=0)] for _ in SCHEME_RATE_DENOMINATOR_PAIRS]
    c = _query_returning_sequence(rows)
    results = check_scheme_rate_denominators(
        c, scheme_week_table="p.d.team_scheme_week", season=2023,
    )
    assert all(not r.passed for r in results)
    assert all("no rows" in r.detail.lower() for r in results)


# ---------------------------------------------------------------------------
# Check 6: nfl_participation coverage-matrix regression
# ---------------------------------------------------------------------------


def test_coverage_matrix_passes_when_within_documented_bounds():
    c = _query_returning([
        MagicMock(season=2016, n=1000, coverage_fill_rate=0.0),
        MagicMock(season=2017, n=1000, coverage_fill_rate=0.0),
        MagicMock(season=2023, n=1000, coverage_fill_rate=0.496),
    ])
    r = check_participation_coverage_matrix(c, participation_table="p.d.participation")
    assert r.passed is True


def test_coverage_matrix_fails_when_2016_no_longer_reads_zero():
    c = _query_returning([
        MagicMock(season=2016, n=1000, coverage_fill_rate=0.10),
        MagicMock(season=2017, n=1000, coverage_fill_rate=0.0),
    ])
    r = check_participation_coverage_matrix(c, participation_table="p.d.participation")
    assert r.passed is False
    assert "2016" in r.detail


def test_coverage_matrix_fails_when_a_later_season_exceeds_the_ceiling():
    c = _query_returning([MagicMock(season=2023, n=1000, coverage_fill_rate=0.80)])
    r = check_participation_coverage_matrix(c, participation_table="p.d.participation")
    assert r.passed is False
    assert "2023" in r.detail


def test_coverage_matrix_fails_loudly_when_no_rows_at_all():
    c = _query_returning([])
    r = check_participation_coverage_matrix(c, participation_table="p.d.participation")
    assert r.passed is False
    assert "no rows" in r.detail.lower()


# ---------------------------------------------------------------------------
# Per-check-group CLI runners
# ---------------------------------------------------------------------------


def test_run_verify_points_weekly_returns_zero_when_passing():
    c = _query_returning([MagicMock(n=100, mismatches=0)])
    ns = argparse.Namespace(points_weekly_table="p.d.ff_points_weekly", season=2023,
                            ppr_tolerance=0.01)
    assert run_verify_points_weekly(ns, bq_client=c) == 0


def test_run_verify_points_weekly_returns_one_when_failing():
    c = _query_returning([MagicMock(n=100, mismatches=3)])
    ns = argparse.Namespace(points_weekly_table="p.d.ff_points_weekly", season=2023,
                            ppr_tolerance=0.01)
    assert run_verify_points_weekly(ns, bq_client=c) == 1


def test_run_verify_scheme_denominators_returns_zero_when_all_pass():
    rows = [[MagicMock(n=100, null_denom=0, over_one=0)] for _ in SCHEME_RATE_DENOMINATOR_PAIRS]
    c = _query_returning_sequence(rows)
    ns = argparse.Namespace(scheme_week_table="p.d.team_scheme_week", season=2023)
    assert run_verify_scheme_denominators(ns, bq_client=c) == 0


def test_run_verify_scheme_denominators_returns_one_when_any_fail():
    rows = [[MagicMock(n=100, null_denom=0, over_one=0)] for _ in SCHEME_RATE_DENOMINATOR_PAIRS]
    rows[0] = [MagicMock(n=100, null_denom=5, over_one=0)]
    c = _query_returning_sequence(rows)
    ns = argparse.Namespace(scheme_week_table="p.d.team_scheme_week", season=2023)
    assert run_verify_scheme_denominators(ns, bq_client=c) == 1


def test_run_verify_participation_coverage_returns_zero_when_passing():
    c = _query_returning([MagicMock(season=2023, n=1000, coverage_fill_rate=0.3)])
    ns = argparse.Namespace(participation_table="p.d.participation")
    assert run_verify_participation_coverage(ns, bq_client=c) == 0


def test_run_verify_participation_coverage_returns_one_when_failing():
    c = _query_returning([MagicMock(season=2023, n=1000, coverage_fill_rate=0.9)])
    ns = argparse.Namespace(participation_table="p.d.participation")
    assert run_verify_participation_coverage(ns, bq_client=c) == 1


# ---------------------------------------------------------------------------
# run_verify_cli: the --checks dispatcher
# ---------------------------------------------------------------------------


def test_run_verify_cli_unknown_check_raises():
    ns = argparse.Namespace(checks="bogus")
    with pytest.raises(ValueError, match="bogus"):
        run_verify_cli(ns, bq_client=MagicMock())


def test_run_verify_cli_points_weekly_without_its_table_raises():
    ns = argparse.Namespace(checks="points-weekly", points_weekly_table=None, season=2023)
    with pytest.raises(ValueError, match="points-weekly-table"):
        run_verify_cli(ns, bq_client=MagicMock())


def test_run_verify_cli_scheme_denominators_without_its_table_raises():
    ns = argparse.Namespace(checks="scheme-denominators", scheme_week_table=None, season=2023)
    with pytest.raises(ValueError, match="scheme-week-table"):
        run_verify_cli(ns, bq_client=MagicMock())


def test_run_verify_cli_participation_coverage_without_its_table_raises():
    ns = argparse.Namespace(checks="participation-coverage", participation_table=None)
    with pytest.raises(ValueError, match="participation-table"):
        run_verify_cli(ns, bq_client=MagicMock())


def test_run_verify_cli_dispatches_to_adp_by_default(monkeypatch):
    calls = []

    def fake_run_adp(ns, *, bq_client):
        calls.append("adp")
        return 0

    monkeypatch.setattr("ffl_bigquery.verify.adp.run_verify", fake_run_adp)
    ns = argparse.Namespace(checks="adp", adp_table="p.d.ff_adp", season=2026,
                            min_resolution_rate=0.6)
    assert run_verify_cli(ns, bq_client=MagicMock()) == 0
    assert calls == ["adp"]


def test_run_verify_cli_runs_multiple_groups_and_fails_if_any_group_fails(monkeypatch):
    monkeypatch.setattr("ffl_bigquery.verify.adp.run_verify", lambda ns, *, bq_client: 0)
    monkeypatch.setattr(
        "ffl_bigquery.verify.tables.run_verify_points_weekly",
        lambda ns, *, bq_client: 1,
    )
    ns = argparse.Namespace(
        checks="adp,points-weekly", adp_table="p.d.ff_adp", season=2026,
        min_resolution_rate=0.6, points_weekly_table="p.d.ff_points_weekly",
        ppr_tolerance=0.01,
    )
    assert run_verify_cli(ns, bq_client=MagicMock()) == 1


def test_run_verify_cli_runs_multiple_groups_and_passes_when_all_pass(monkeypatch):
    monkeypatch.setattr("ffl_bigquery.verify.adp.run_verify", lambda ns, *, bq_client: 0)
    monkeypatch.setattr(
        "ffl_bigquery.verify.tables.run_verify_points_weekly",
        lambda ns, *, bq_client: 0,
    )
    ns = argparse.Namespace(
        checks="adp,points-weekly", adp_table="p.d.ff_adp", season=2026,
        min_resolution_rate=0.6, points_weekly_table="p.d.ff_points_weekly",
        ppr_tolerance=0.01,
    )
    assert run_verify_cli(ns, bq_client=MagicMock()) == 0
