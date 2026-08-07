"""The kicker verify checks.

The DST verifier's original defect is the thing these tests exist to prevent:
it recomputed fantasy_points_dst from the component columns the SAME transform
had written, so two Critical attribution bugs reconciled to zero findings -- a
wrong total that was perfectly consistent with its own wrong components. So the
recomputation guard here takes its expected counts from nfl_plays, by a code
path that shares nothing with derive/kicker_weekly.py, and the other three
guards each fail on a corruption the recomputation cannot see.
"""
import argparse
import inspect
from unittest.mock import MagicMock

import pytest
from google.cloud import bigquery

import ffl_bigquery.verify.tables as verify_tables
from ffl_bigquery.verify.tables import (
    KICKER_SEASON_FLOORS,
    check_kicker_counts_match_plays,
    check_kicker_coverage,
    check_kicker_season_floors,
    check_kicker_tier_sanity,
    run_verify_kicker,
)


class _Row(dict):
    __getattr__ = dict.get


def _krow(**kw):
    """One ff_points_k_weekly row. Defaults are internally consistent: two
    made field goals with a 45-yard long (so 3+4=7 is achievable), one miss
    (-1) and three extra points (+3) = 9.0."""
    base = dict(
        season=2024, week=1, gsis_id="00-0039750", player_name="K", team="BAL",
        fg_made=2, fg_missed=1, fg_made_yards_max=45, xp_made=3, xp_missed=0,
        fantasy_points_kicker=9.0,
    )
    base.update(kw)
    return _Row(base)


def _plays_row(**kw):
    """One per-season aggregate straight from nfl_plays."""
    base = dict(
        season=2024, fg_made=2, fg_missed=1, xp_made=3, xp_missed=0,
        attempts_null_kicker=0, unknown_result_attempts=0,
    )
    base.update(kw)
    return _Row(base)


# ---------------------------------------------------------------------------
# Guard 1: independent recomputation from nfl_plays
# ---------------------------------------------------------------------------


def test_counts_matching_the_play_by_play_produce_no_findings():
    findings, _ = check_kicker_counts_match_plays([_krow()], [_plays_row()])
    assert findings == []


def test_a_component_count_disagreeing_with_the_play_by_play_is_reported():
    findings, _ = check_kicker_counts_match_plays(
        [_krow()], [_plays_row(fg_made=7)],
    )
    assert len(findings) == 1
    assert "fg_made" in findings[0] and "2024" in findings[0]


def test_every_disagreeing_component_is_reported_separately():
    findings, _ = check_kicker_counts_match_plays(
        [_krow()], [_plays_row(fg_made=7, xp_made=9)],
    )
    assert len(findings) == 2


def test_counts_are_summed_across_the_seasons_rows_before_comparing():
    rows = [_krow(week=1), _krow(week=2)]
    findings, _ = check_kicker_counts_match_plays(
        rows, [_plays_row(fg_made=4, fg_missed=2, xp_made=6, xp_missed=0)],
    )
    assert findings == []


def test_a_dropped_null_kicker_attempt_surfaces_as_a_disagreement():
    """derive_kicker_weekly requires a non-null kicker_player_id, so an
    attempt without one is DROPPED rather than grouped under a null key. The
    play-by-play side must therefore count attempts WITHOUT that requirement --
    otherwise both sides drop the same rows and the loss is invisible. Measured
    2026-08-07 against the live nfl_plays: null kicker ids do occur (5 in 2002,
    4 in 2006/2007/2008, ... , 1 in 2014), so this is a real shape, not a
    hypothetical."""
    findings, notes = check_kicker_counts_match_plays(
        [_krow()], [_plays_row(fg_made=3, attempts_null_kicker=1)],
    )
    assert len(findings) == 1
    assert "fg_made" in findings[0]
    assert any("null" in n for n in notes)


def test_null_kicker_attempts_are_reported_even_when_the_counts_agree():
    _, notes = check_kicker_counts_match_plays(
        [_krow()], [_plays_row(attempts_null_kicker=4)],
    )
    assert any("4" in n and "2024" in n for n in notes)


def test_a_season_in_the_play_by_play_but_not_in_the_table_is_a_note_not_a_finding():
    """A partial backfill is a coverage state, not a data defect -- but it must
    still be visible, because the whole comparison silently skips such a
    season."""
    findings, notes = check_kicker_counts_match_plays(
        [_krow(season=2024)], [_plays_row(season=2024), _plays_row(season=2023)],
    )
    assert findings == []
    assert any("2023" in n for n in notes)


def test_a_season_in_the_table_with_no_play_by_play_at_all_is_a_finding():
    """The reverse direction is not a coverage state: the table cannot hold
    kicks that never appear in the play-by-play it was derived from."""
    findings, _ = check_kicker_counts_match_plays(
        [_krow(season=2024)], [_plays_row(season=2023)],
    )
    assert len(findings) == 1
    assert "2024" in findings[0]


def test_attempts_with_an_unrecognised_result_are_reported():
    """nflverse carries extra_point_result='aborted' in 2002-2014 (measured
    2026-08-07 on live nfl_plays: 5 in 2002, 4 in 2006; 31 across all 27
    seasons, all with a NULL kicker id). It falls in none of the four scored
    buckets on either side, so it cannot show up as a count disagreement, and
    the transform holds no row for it. Silence here would make those 31
    attempts an invisible subtraction from the source."""
    _, notes = check_kicker_counts_match_plays(
        [_krow()], [_plays_row(unknown_result_attempts=5)],
    )
    assert any("5" in n and "2024" in n for n in notes)


# ---------------------------------------------------------------------------
# Guard 2: per-season floors. Catches a component going silently zero, which a
# reconciliation structurally cannot -- both sides would agree on the zero if
# the loss happened upstream of the table AND of the count comparison.
# ---------------------------------------------------------------------------


def _full_season_rows(season=2024, weeks=22, **kw):
    """A season's worth of rows, spread over enough distinct weeks to clear the
    partial-season gate, and comfortably above every floor.

    Deliberately tier-consistent as well (50 makes with a 45-yard long is 150
    to 200 field-goal points; 175 - 10 + 60 = 225.0), so these rows can drive
    run_verify_kicker without tripping a guard other than the one under test.
    """
    base = dict(fg_made=50, fg_missed=10, xp_made=60, xp_missed=3,
                fg_made_yards_max=45, fantasy_points_kicker=225.0)
    base.update(kw)
    return [_krow(season=season, week=w + 1, **base) for w in range(weeks)]


def test_a_full_season_above_every_floor_produces_no_findings():
    findings, skipped = check_kicker_season_floors(_full_season_rows())
    assert findings == [] and skipped == []


def test_a_component_zero_across_a_whole_season_is_reported():
    findings, _ = check_kicker_season_floors(_full_season_rows(fg_made=0))
    assert len(findings) == 1
    assert "fg_made" in findings[0] and "2024" in findings[0]


def test_a_component_that_collapses_without_reaching_zero_is_also_reported():
    """The floor is not merely 'greater than zero' for the two large
    components: a season with 100 made field goals is not a football season,
    and a reconciliation against a table built from the same broken filter
    would agree with it."""
    findings, _ = check_kicker_season_floors(_full_season_rows(fg_made=4))
    assert any("fg_made" in f for f in findings)


def test_a_partial_season_is_skipped_rather_than_falsely_flagged():
    """A mid-season sync legitimately has a fraction of a season's kicks.
    Flagging it makes the guard fail on healthy data every week of September --
    the exact way a guard gets switched off."""
    findings, skipped = check_kicker_season_floors(_full_season_rows(weeks=4))
    assert findings == []
    assert skipped == [2024]


def test_each_season_is_judged_separately():
    rows = _full_season_rows(season=2024) + _full_season_rows(season=2023, xp_made=0)
    findings, _ = check_kicker_season_floors(rows)
    assert len(findings) == 1
    assert findings[0].startswith("2023")


def test_the_floors_sit_below_every_measured_season_and_above_zero():
    """The falsifiability invariant for this guard, in both directions.

    Measured 2026-08-07 against live nfl_plays for 1999-2025 (REG+POST), the
    per-season minima are: fg_made 731 (2004), fg_missed 140 (2013), xp_made
    1055 (2001), xp_missed 5 (2013). A floor at or above any of those fires on
    a healthy season; a floor at 0 can never fire at all.

    Note this contradicts the brief's 'on the order of 1,000 made FGs and 1,200
    XPs' -- true of recent seasons (2024: 982 and 1245) but NOT of the 1999-2020
    range this table backfills, where a 1,000 floor would fail on all 22 of
    them.
    """
    measured_minima = {"fg_made": 731, "fg_missed": 140, "xp_made": 1055,
                       "xp_missed": 5}
    assert set(KICKER_SEASON_FLOORS) == set(measured_minima)
    for col, floor in KICKER_SEASON_FLOORS.items():
        assert floor > 0, f"{col} floor of {floor} can never fire"
        assert floor < measured_minima[col], (
            f"{col} floor of {floor} fires on the measured minimum "
            f"{measured_minima[col]}"
        )


# ---------------------------------------------------------------------------
# Guard 3: tier sanity. Per row, against nothing but the row itself.
# ---------------------------------------------------------------------------


def test_a_row_whose_total_its_components_can_produce_is_accepted():
    assert check_kicker_tier_sanity([_krow()]) == []


def test_a_total_above_what_the_components_can_produce_is_reported():
    """Two made field goals cap out at 5+5=10; with one miss and three extra
    points the ceiling is 12.0."""
    findings = check_kicker_tier_sanity([_krow(fantasy_points_kicker=99.0)])
    assert len(findings) == 1
    assert "2024" in findings[0]


def test_a_total_below_what_the_components_can_produce_is_reported():
    # Floor is 3+3-1+3 = 8.0.
    assert len(check_kicker_tier_sanity([_krow(fantasy_points_kicker=7.0)])) == 1


def test_a_single_made_field_goal_scoring_outside_the_tier_values_is_reported():
    """The tier statement in its sharpest form: with exactly one make and no
    other scoring, the total IS that kick's tier value and must be 3, 4 or 5."""
    row = dict(fg_made=1, fg_missed=0, xp_made=0, xp_missed=0,
               fg_made_yards_max=45)
    assert check_kicker_tier_sanity([_krow(fantasy_points_kicker=4.0, **row)]) == []
    assert len(check_kicker_tier_sanity(
        [_krow(fantasy_points_kicker=3.5, **row)],
    )) == 1
    assert len(check_kicker_tier_sanity(
        [_krow(fantasy_points_kicker=6.0, **row)],
    )) == 1


def test_the_longest_make_bounds_the_tier_a_row_may_claim():
    """A 30-yard long make means every make that week was in the 3-point tier,
    so two makes are worth exactly 6 -- not the 10 the untightened bound would
    allow."""
    row = dict(fg_made=2, fg_missed=0, xp_made=0, xp_missed=0,
               fg_made_yards_max=30)
    assert check_kicker_tier_sanity([_krow(fantasy_points_kicker=6.0, **row)]) == []
    findings = check_kicker_tier_sanity([_krow(fantasy_points_kicker=10.0, **row)])
    assert len(findings) == 1


def test_a_fractional_total_is_reported():
    """Every tier value and weight is an integer, so no combination of them can
    produce a fractional total."""
    assert len(check_kicker_tier_sanity([_krow(fantasy_points_kicker=9.5)])) == 1


def test_a_made_field_goal_with_no_recorded_distance_is_reported():
    """fg_made_yards_max NULL beside a non-zero fg_made means the distance that
    selects the tier is gone -- the row's points cannot be checked at all."""
    assert len(check_kicker_tier_sanity([_krow(fg_made_yards_max=None)])) == 1


def test_a_longest_make_recorded_for_a_kicker_who_made_nothing_is_reported():
    row = dict(fg_made=0, fg_missed=1, xp_made=3, xp_missed=0,
               fg_made_yards_max=45, fantasy_points_kicker=2.0)
    assert len(check_kicker_tier_sanity([_krow(**row)])) == 1


def test_a_week_with_no_field_goals_at_all_is_accepted():
    row = dict(fg_made=0, fg_missed=0, xp_made=4, xp_missed=1,
               fg_made_yards_max=None, fantasy_points_kicker=4.0)
    assert check_kicker_tier_sanity([_krow(**row)]) == []


def test_a_null_total_is_reported():
    assert len(check_kicker_tier_sanity([_krow(fantasy_points_kicker=None)])) == 1


def test_a_null_component_count_is_reported():
    assert len(check_kicker_tier_sanity([_krow(xp_made=None)])) == 1


# ---------------------------------------------------------------------------
# Guard 4: coverage. Denominator comes from nfl_plays, never from the table.
# ---------------------------------------------------------------------------


def _cov(**kw):
    base = dict(season=2024, gsis_id="00-0039750", attempts=30, table_rows=12)
    base.update(kw)
    return _Row(base)


def test_a_kicker_with_rows_in_the_table_is_covered():
    assert check_kicker_coverage([_cov()]) == ([], [])


def test_a_kicker_who_kicked_but_has_no_rows_is_a_genuine_gap():
    findings, mismatches = check_kicker_coverage([_cov(table_rows=0)])
    assert len(findings) == 1
    assert "00-0039750" in findings[0] and "2024" in findings[0]
    assert mismatches == []


def test_an_attempt_with_no_kicker_id_is_a_mismatch_not_a_gap():
    """An attempt whose kicker_player_id is NULL can never have a row keyed by
    that id -- it is an upstream attribution hole, not a transform drop. Guard
    1 is what makes the resulting count loss visible; reporting it as a gap
    here would fail the check on every season from 2002 to 2014."""
    findings, mismatches = check_kicker_coverage([_cov(gsis_id=None, table_rows=0)])
    assert findings == []
    assert len(mismatches) == 1


def test_an_id_outside_the_gsis_vocabulary_is_a_mismatch_not_a_gap():
    findings, mismatches = check_kicker_coverage(
        [_cov(gsis_id="KICKER-17", table_rows=0)],
    )
    assert findings == []
    assert len(mismatches) == 1
    assert "KICKER-17" in mismatches[0]


# ---------------------------------------------------------------------------
# run_verify_kicker: the BigQuery-facing wrapper.
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


def _ns():
    return argparse.Namespace(
        kicker_table="p.d.ff_points_k_weekly", plays_table="p.d.nfl_plays",
    )


def _healthy_sequence():
    """Table rows, per-season play-by-play aggregate, coverage rows -- in the
    order run_verify_kicker queries them, all mutually consistent."""
    table_rows = _full_season_rows()
    plays_rows = [_plays_row(
        fg_made=50 * 22, fg_missed=10 * 22, xp_made=60 * 22, xp_missed=3 * 22,
    )]
    coverage_rows = [_cov()]
    return [table_rows, plays_rows, coverage_rows]


def test_run_verify_kicker_passes_on_a_healthy_table():
    assert run_verify_kicker(_ns(), bq_client=_query_returning_sequence(
        _healthy_sequence(),
    )) == 0


def test_run_verify_kicker_reports_an_empty_table_as_a_failure():
    """Same rule as the ADP and DST verifiers: no rows means the sync never
    ran, and every guard here trivially finds nothing in an empty table."""
    c = _query_returning_sequence([[]])
    assert run_verify_kicker(_ns(), bq_client=c) == 1


def test_run_verify_kicker_labels_every_guard_on_its_own_line(capsys):
    run_verify_kicker(_ns(), bq_client=_query_returning_sequence(_healthy_sequence()))
    out = capsys.readouterr().out
    for label in ("[kicker][recompute]", "[kicker][floor]", "[kicker][tier]",
                  "[kicker][coverage]"):
        assert label in out, f"missing summary line for {label}"


def test_run_verify_kicker_fails_when_the_recomputation_disagrees():
    seq = _healthy_sequence()
    seq[1] = [_plays_row(fg_made=999, fg_missed=10 * 22, xp_made=60 * 22,
                         xp_missed=3 * 22)]
    assert run_verify_kicker(_ns(), bq_client=_query_returning_sequence(seq)) == 1


def test_run_verify_kicker_fails_when_a_component_is_below_its_floor():
    """The corruption only the floor can see: the table and the play-by-play
    agree perfectly, and every row's total is consistent with its own
    components -- the component simply is not there in either."""
    seq = _healthy_sequence()
    # 175 field-goal points - 10 misses + 0 extra points = 165.0, so the row is
    # still internally consistent -- only the floor can see this.
    seq[0] = _full_season_rows(xp_made=0, fantasy_points_kicker=165.0)
    seq[1] = [_plays_row(fg_made=50 * 22, fg_missed=10 * 22, xp_made=0,
                         xp_missed=3 * 22)]
    assert run_verify_kicker(_ns(), bq_client=_query_returning_sequence(seq)) == 1


def test_run_verify_kicker_fails_when_a_rows_total_is_unreachable():
    seq = _healthy_sequence()
    seq[0] = _full_season_rows()
    seq[0][0] = _krow(week=1, fg_made=50, fg_missed=10, xp_made=60, xp_missed=3,
                      fg_made_yards_max=45, fantasy_points_kicker=9999.0)
    assert run_verify_kicker(_ns(), bq_client=_query_returning_sequence(seq)) == 1


def test_run_verify_kicker_fails_when_a_kicker_is_missing_from_the_table():
    seq = _healthy_sequence()
    seq[2] = [_cov(), _cov(gsis_id="00-0000001", table_rows=0)]
    assert run_verify_kicker(_ns(), bq_client=_query_returning_sequence(seq)) == 1


def test_run_verify_kicker_does_not_fail_on_an_id_vocabulary_mismatch(capsys):
    seq = _healthy_sequence()
    seq[2] = [_cov(), _cov(gsis_id=None, table_rows=0)]
    assert run_verify_kicker(_ns(), bq_client=_query_returning_sequence(seq)) == 0
    assert "[kicker][coverage-mismatch]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The two queries' shape. Both are the difference between a real check and a
# self-confirming one, and neither is visible from the return code.
# ---------------------------------------------------------------------------


def _sql_for(index):
    c = _query_returning_sequence(_healthy_sequence())
    run_verify_kicker(_ns(), bq_client=c)
    return c.query.call_args_list[index].args[0]


def test_the_recomputation_query_reads_the_play_by_play_not_the_table():
    sql = _sql_for(1)
    assert "p.d.nfl_plays" in sql
    assert "ff_points_k_weekly" not in sql


def test_the_recomputation_query_does_not_require_a_kicker_id():
    """If the play-by-play side filtered on kicker_player_id IS NOT NULL it
    would drop exactly the rows the transform drops, and the loss would
    reconcile to zero findings -- the DST failure, reproduced. The id is
    COUNTED (so the drop can be explained) and never used as a predicate on the
    four scored components."""
    sql = _sql_for(1)
    assert "kicker_player_id IS NOT NULL" not in sql
    assert "attempts_null_kicker" in sql


def test_the_recomputation_query_covers_the_postseason():
    """ff_points_k_weekly follows load_pbp()'s coverage (REG + POST), unlike
    ff_points_dst_weekly which is REG-only. A season_type filter here would
    disagree with the table by exactly the postseason kicks and read as a data
    bug."""
    assert "season_type" not in _sql_for(1)


def test_the_coverage_querys_denominator_comes_from_the_play_by_play():
    """The trap this guard is one edit away from: a denominator drawn from the
    same filtered set as the numerator, which can only ever report zero. The
    kicker set must come from nfl_plays and reach the table through an OUTER
    join."""
    sql = _sql_for(2)
    assert "LEFT JOIN" in sql
    plays_pos = sql.index("p.d.nfl_plays")
    table_pos = sql.index("LEFT JOIN")
    assert plays_pos < table_pos


def test_the_coverage_query_is_bounded_to_seasons_the_table_covers():
    """Without this, a table backfilled for 2024 alone reports every kicker of
    every other season as missing -- 1,100+ findings on healthy data, and a
    guard that always exits 1 gets switched off."""
    sql = _sql_for(2)
    assert "DISTINCT season" in sql


# ---------------------------------------------------------------------------
# Independence, structurally.
# ---------------------------------------------------------------------------


def test_the_verifier_does_not_import_the_kicker_transform_or_its_rules():
    """The whole point of this task. If the verifier imported the transform's
    helpers or its scoring rules, both sides of every comparison would move
    together and a rules regression would reconcile to zero findings."""
    src = inspect.getsource(verify_tables)
    for banned in ("derive.kicker_weekly", "derive.kicker_scoring",
                   "derive import kicker"):
        assert banned not in src, f"verify/tables.py imports {banned}"


# ---------------------------------------------------------------------------
# CLI dispatch.
# ---------------------------------------------------------------------------


def test_run_verify_cli_kicker_without_its_table_raises():
    from ffl_bigquery.verify import run_verify_cli

    ns = argparse.Namespace(checks="kicker", kicker_table=None,
                            plays_table="p.d.nfl_plays")
    with pytest.raises(ValueError, match="kicker-table"):
        run_verify_cli(ns, bq_client=MagicMock())


def test_run_verify_cli_kicker_without_a_plays_table_raises():
    """--plays-table is required, not optional: the independent recomputation
    IS the check, and an optional independence guard is one omitted flag away
    from never running while still printing a clean summary."""
    from ffl_bigquery.verify import run_verify_cli

    ns = argparse.Namespace(checks="kicker",
                            kicker_table="p.d.ff_points_k_weekly",
                            plays_table=None)
    with pytest.raises(ValueError, match="plays-table"):
        run_verify_cli(ns, bq_client=MagicMock())


def test_run_verify_cli_dispatches_to_the_kicker_check(monkeypatch):
    from ffl_bigquery.verify import run_verify_cli

    calls = []
    monkeypatch.setattr(
        "ffl_bigquery.verify.tables.run_verify_kicker",
        lambda ns, *, bq_client: calls.append("kicker") or 0,
    )
    ns = argparse.Namespace(checks="kicker",
                            kicker_table="p.d.ff_points_k_weekly",
                            plays_table="p.d.nfl_plays")
    assert run_verify_cli(ns, bq_client=MagicMock()) == 0
    assert calls == ["kicker"]
