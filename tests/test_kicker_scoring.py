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


@pytest.mark.parametrize("result", ["aborted", "", "MADE", "unknown"])
def test_an_unrecognised_result_raises_rather_than_scoring_zero(result):
    # nflverse has carried 'aborted' in some seasons. Scoring an unknown value
    # as 0.0 would silently drop real kicks; 2024 has none, other seasons may.
    with pytest.raises(ValueError):
        field_goal_points(30.0, result)
    with pytest.raises(ValueError):
        extra_point_points(result)
