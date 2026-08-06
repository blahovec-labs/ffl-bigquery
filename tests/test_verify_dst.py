"""The DST verify check is a self-consistency guard: fantasy_points_dst must
equal the components it claims to be a sum of. It catches the failure mode
where a scoring weight is changed in one place and not the other, which no
schema test can see."""
import argparse
from unittest.mock import MagicMock

from google.cloud import bigquery

from ffl_bigquery.verify.tables import (
    DST_TEAM_ABBREV_ALIASES,
    check_dst_bonus_rederives,
    check_dst_components_reconcile,
    check_dst_covers_adp_defenses,
    check_dst_season_signal_floor,
    run_verify_dst,
)


class _Row(dict):
    __getattr__ = dict.get


def _row(**kw):
    base = dict(
        season=2024, week=1, team="BAL", sacks=2, interceptions=1,
        fumble_recoveries=0, safeties=0, defensive_tds=0, blocked_kicks=0,
        points_allowed_total=27, points_allowed_bonus=0, fantasy_points_dst=4.0,
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


def test_reconciliation_is_blind_to_both_special_teams_attribution_bugs():
    """Why the arithmetic check is not enough on its own. The real ARI 2024
    week 1 row shipped a perfectly self-consistent 3.0 (2 sacks + 1 fumble
    recovery + a -1 bonus) while the correct answer was 9.0 -- the dropped
    kickoff-return TD is invisible to a check that recomputes the total from
    the same count columns the broken transform wrote."""
    broken_ari = _row(
        season=2024, week=1, team="ARI", sacks=2, interceptions=0,
        fumble_recoveries=1, safeties=0, defensive_tds=0, blocked_kicks=0,
        points_allowed_total=34, points_allowed_bonus=-1, fantasy_points_dst=3.0,
    )
    assert check_dst_components_reconcile([broken_ari]) == []
    assert check_dst_bonus_rederives([broken_ari]) == []


# --- bonus re-derivation: independent of the components ---


def test_bonus_matching_its_points_allowed_total_produces_no_findings():
    assert check_dst_bonus_rederives([_row(points_allowed_total=27,
                                           points_allowed_bonus=0)]) == []


def test_bonus_disagreeing_with_points_allowed_total_is_reported():
    # 27 points allowed is the 21-27 tier (+0); +10 is the shutout bonus.
    findings = check_dst_bonus_rederives([_row(points_allowed_total=27,
                                               points_allowed_bonus=10)])
    assert len(findings) == 1
    assert "27" in findings[0] and "BAL" in findings[0]


def test_a_null_score_resolved_to_a_concrete_bonus_is_reported():
    """The failure the arithmetic check cannot see: an unresolvable final score
    quietly given a real bonus rather than left NULL."""
    findings = check_dst_bonus_rederives([_row(points_allowed_total=None,
                                               points_allowed_bonus=0)])
    assert len(findings) == 1


def test_null_score_with_null_bonus_is_consistent():
    assert check_dst_bonus_rederives([_row(points_allowed_total=None,
                                           points_allowed_bonus=None)]) == []


# --- per-season signal floor: the only guard that survives a column rename ---


def _season_rows(n, **kw):
    base = dict(sacks=1, interceptions=1, fumble_recoveries=1, safeties=1,
                defensive_tds=1, blocked_kicks=1)
    base.update(kw)
    return [_row(week=i, **base) for i in range(n)]


def test_a_season_with_every_component_present_produces_no_findings():
    findings, skipped = check_dst_season_signal_floor(_season_rows(4), min_rows=1)
    assert findings == [] and skipped == []


def test_a_component_zero_across_a_whole_season_is_reported():
    """derive.dst_weekly._flag returns all zeros for an ABSENT column by
    design, so an upstream rename of `sack` would score every defense
    bonus-only and pass reconciliation, bonus re-derivation and coverage. A
    season-wide zero is not a football outcome; it is a missing column."""
    findings, _ = check_dst_season_signal_floor(
        _season_rows(4, sacks=0), min_rows=1,
    )
    assert len(findings) == 1
    assert "sacks" in findings[0] and "2024" in findings[0]


def test_defensive_tds_zero_across_a_season_is_reported():
    findings, _ = check_dst_season_signal_floor(
        _season_rows(4, defensive_tds=0), min_rows=1,
    )
    assert any("defensive_tds" in f for f in findings)


def test_a_partial_season_is_skipped_rather_than_falsely_flagged():
    """Safeties are rare (15 league-wide in 2024), so a mid-season sync can
    legitimately have zero of them. Flagging that would make the guard fail on
    healthy data -- the exact reason guards get switched off."""
    findings, skipped = check_dst_season_signal_floor(
        _season_rows(4, safeties=0), min_rows=400,
    )
    assert findings == []
    assert skipped == [2024]


def test_each_season_is_judged_separately():
    rows = _season_rows(3) + [
        _row(season=2023, week=i, sacks=0, interceptions=1, fumble_recoveries=1,
             safeties=1, defensive_tds=1, blocked_kicks=1) for i in range(3)
    ]
    findings, _ = check_dst_season_signal_floor(rows, min_rows=1)
    assert len(findings) == 1
    assert findings[0].startswith("2023")


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


def test_run_verify_dst_fails_on_a_bonus_that_does_not_re_derive():
    c = _query_returning_sequence([[_row(points_allowed_total=0,
                                         points_allowed_bonus=0)]])
    ns = argparse.Namespace(dst_table="p.d.ff_points_dst_weekly", adp_table=None)
    assert run_verify_dst(ns, bq_client=c) == 1


def test_run_verify_dst_reports_an_empty_table_as_a_failure():
    """A table with no rows is a failure, not a pass -- it means the sync never
    ran, and every other guard here trivially finds nothing in it."""
    c = _query_returning_sequence([[]])
    ns = argparse.Namespace(dst_table="p.d.ff_points_dst_weekly", adp_table=None)
    assert run_verify_dst(ns, bq_client=c) == 1


def test_run_verify_dst_labels_every_guard_on_its_own_line(capsys):
    c = _query_returning_sequence([[_row()]])
    ns = argparse.Namespace(dst_table="p.d.ff_points_dst_weekly", adp_table=None)
    run_verify_dst(ns, bq_client=c)
    out = capsys.readouterr().out
    for label in ("[dst][arithmetic]", "[dst][bonus]", "[dst][signal-floor]",
                  "[dst][coverage]"):
        assert label in out, f"missing summary line for {label}"


# ---------------------------------------------------------------------------
# The coverage query's two bounds. Without either, this guard reports findings
# on a completely healthy dataset every single run -- and a guard that always
# exits 1 gets switched off, which is worse than no guard at all.
# ---------------------------------------------------------------------------


def _coverage_sql(adp_table="p.d.ff_adp"):
    c = _query_returning_sequence([[_row()], []])
    ns = argparse.Namespace(
        dst_table="p.d.ff_points_dst_weekly", adp_table=adp_table,
    )
    run_verify_dst(ns, bq_client=c)
    return c.query.call_args_list[1].args[0]


def test_coverage_query_is_bounded_to_seasons_the_dst_table_covers():
    """ff_adp is forward-looking -- FFC published 24 DEF entries for 2026 while
    the DST table can only cover completed seasons. Unbounded, the documented
    command returned 24 findings and exit 1 against a correct dataset."""
    sql = _coverage_sql()
    assert "MAX(season)" in sql
    assert "a.season <= c.max_season" in sql


def test_coverage_query_normalizes_the_rams_abbreviation():
    """FFC publishes LAR; nflverse schedules and play-by-play use LA. Any
    season in which the Rams DST is drafted produced a permanent finding."""
    assert DST_TEAM_ABBREV_ALIASES["LAR"] == "LA"
    sql = _coverage_sql()
    assert "WHEN 'LAR' THEN 'LA'" in sql


def test_coverage_finding_names_the_abbreviation_the_adp_board_actually_used():
    rows = [_Row(season=2016, adp_team="STL", team="LA", dst_weeks=0)]
    findings = check_dst_covers_adp_defenses(rows)
    assert len(findings) == 1
    assert "STL" in findings[0] and "LA" in findings[0]
