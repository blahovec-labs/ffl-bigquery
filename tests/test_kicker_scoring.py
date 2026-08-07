"""Kicker scoring tiers are the same error-prone shape as DST's points-allowed
tiers: inclusive ranges with no gaps, where an off-by-one silently mis-scores
every kick in a band rather than failing. Every boundary value is asserted,
not just midpoints."""
import pytest

from ffl_bigquery.derive.kicker_scoring import (
    extra_point_points,
    field_goal_points,
)


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(19.0, 3.0), (39.0, 3.0), (40.0, 4.0), (49.0, 4.0), (50.0, 5.0), (65.0, 5.0)],
)
def test_made_field_goals_score_by_distance_tier(distance, expected):
    assert field_goal_points(distance, "made") == expected


def test_the_tier_boundaries_are_inclusive_and_have_no_gap():
    # An off-by-one here silently mis-scores every kick in a band rather than
    # failing, so pin both sides of both boundaries.
    assert field_goal_points(39.0, "made") == 3.0
    assert field_goal_points(40.0, "made") == 4.0
    assert field_goal_points(49.0, "made") == 4.0
    assert field_goal_points(50.0, "made") == 5.0


@pytest.mark.parametrize("result", ["missed", "blocked"])
def test_a_missed_or_blocked_field_goal_is_penalised_regardless_of_distance(result):
    assert field_goal_points(55.0, result) == -1.0
    assert field_goal_points(20.0, result) == -1.0


def test_a_made_field_goal_with_unknown_distance_raises():
    # Distance drives the whole tier table; defaulting it would silently score
    # every unknown kick at the lowest tier and look like real data.
    with pytest.raises(ValueError, match="distance"):
        field_goal_points(None, "made")


def test_a_missed_field_goal_with_unknown_distance_still_scores():
    # The penalty does not depend on distance, so a null here is harmless.
    assert field_goal_points(None, "missed") == -1.0


def test_extra_points():
    assert extra_point_points("good") == 1.0
    assert extra_point_points("failed") == 0.0
    assert extra_point_points("blocked") == 0.0


def test_an_aborted_extra_point_scores_zero_and_is_neither_made_nor_missed():
    # Enumerated across all 27 seasons: extra_point_result='aborted' occurs 31
    # times, in 11 seasons from 2002 to 2014, and kicker_player_id is NULL on
    # every one. It is a botched snap or hold -- no kick is attempted and
    # nflverse attributes no kicker -- so it cannot be charged to anybody.
    # Zero, not the missed-XP zero: the two agree numerically here only because
    # a missed XP happens to cost nothing under these rules. The distinction
    # that matters is in the COUNTS, and it is enforced in kicker_weekly, where
    # an aborted attempt lands in neither xp_made nor xp_missed.
    assert extra_point_points("aborted") == 0.0


@pytest.mark.parametrize("result", ["", "MADE", "unknown", "safety"])
def test_an_unrecognised_result_raises_rather_than_scoring_zero(result):
    # Scoring an unknown value as 0.0 would silently drop real kicks. This is
    # the behaviour that FOUND the aborted extra point -- it crashed a real
    # backfill season instead of quietly scoring it -- so 'aborted' becoming
    # known must not soften it for anything else.
    with pytest.raises(ValueError):
        field_goal_points(30.0, result)
    with pytest.raises(ValueError):
        extra_point_points(result)


def test_an_aborted_field_goal_still_raises():
    # field_goal_result carries exactly three values across all 27 seasons --
    # made (22,991), missed (4,195), blocked (587) -- and never 'aborted'. The
    # extra-point exception is a measured data condition, not a licence to
    # accept the string anywhere it appears.
    with pytest.raises(ValueError, match="field goal"):
        field_goal_points(30.0, "aborted")
