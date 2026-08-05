"""Tier boundaries are the most error-prone part of DST scoring: they are
inclusive ranges with no gaps, and an off-by-one silently mis-scores every
game in a whole band. Every boundary value is asserted, not just midpoints."""
import pytest

from ffl_bigquery.derive.dst_scoring import (
    BLOCKED_KICK_POINTS,
    FUMBLE_RECOVERY_POINTS,
    INTERCEPTION_POINTS,
    SACK_POINTS,
    SAFETY_POINTS,
    TOUCHDOWN_POINTS,
    points_allowed_bonus,
)


@pytest.mark.parametrize(
    "points_allowed,expected",
    [
        (0, 10),
        (1, 7), (6, 7),
        (7, 4), (13, 4),
        (14, 1), (20, 1),
        (21, 0), (27, 0),
        (28, -1), (34, -1),
        (35, -4), (70, -4),
    ],
)
def test_points_allowed_tier_boundaries(points_allowed, expected):
    assert points_allowed_bonus(points_allowed) == expected


def test_points_allowed_bonus_is_none_when_score_unknown():
    # A team-week with no resolvable final score must NOT silently score as a
    # shutout (+10). None propagates so the row is nullable rather than wrong.
    assert points_allowed_bonus(None) is None


def test_negative_points_allowed_is_rejected():
    with pytest.raises(ValueError):
        points_allowed_bonus(-1)


def test_event_weights():
    assert SACK_POINTS == 1
    assert INTERCEPTION_POINTS == 2
    assert FUMBLE_RECOVERY_POINTS == 2
    assert SAFETY_POINTS == 2
    assert TOUCHDOWN_POINTS == 6
    assert BLOCKED_KICK_POINTS == 2
