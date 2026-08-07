"""Pure kicker scoring rules -- no pandas, no I/O, no BigQuery.

Isolated in its own module because these are the one part of kicker scoring
with zero dependencies: a league with different rules swaps this file and
nothing else. The field-goal distance tiers are also the single most
error-prone piece -- inclusive ranges with no gaps, where an off-by-one
silently mis-scores every kick in a band rather than failing loudly -- so
keeping them testable without constructing a DataFrame is deliberate.
"""
from __future__ import annotations

XP_POINTS = 1.0
MISSED_FG_POINTS = -1.0  # a blocked FG counts as missed
MISSED_XP_POINTS = 0.0  # no penalty, blocked or failed
ABORTED_XP_POINTS = 0.0  # no kick, no kicker -- see extra_point_points

# (inclusive_upper_bound_yards, points). Ordered ascending; the final entry's
# bound is None, meaning "everything beyond the previous bound".
_FG_TIERS: list[tuple[int | None, float]] = [
    (39, 3.0),
    (49, 4.0),
    (None, 5.0),
]


def field_goal_points(distance: float | None, result: str) -> float:
    """Tiered points for a single field-goal attempt.

    A made kick with an unknown distance raises: distance drives the whole
    tier table, so defaulting it would silently score every unresolvable
    kick at the lowest tier and look like real data rather than a gap. A
    missed or blocked kick with an unknown distance is fine -- the penalty
    doesn't depend on distance.

    An unrecognised result string raises rather than scoring 0.0; treating an
    unknown result as 0.0 would silently drop real kicks. field_goal_result
    carries exactly three values across all 27 seasons -- made (22,991),
    missed (4,195), blocked (587) -- so unlike `extra_point_points` there is no
    measured fourth case here, and 'aborted' raises if it ever appears on a
    field goal.
    """
    if result in ("missed", "blocked"):
        return MISSED_FG_POINTS
    if result != "made":
        raise ValueError(f"unrecognised field goal result: {result!r}")
    if distance is None:
        raise ValueError("made field goal has unknown distance")
    for bound, points in _FG_TIERS:
        if bound is None or distance <= bound:
            return points
    raise AssertionError("unreachable: final tier has an open upper bound")


def extra_point_points(result: str) -> float:
    """Points for a single extra-point attempt.

    'aborted' scores 0.0 and is counted as NEITHER made nor missed. It is a
    botched snap or hold: no kick is attempted, and nflverse attributes no
    kicker -- enumerated across all 27 seasons, extra_point_result='aborted'
    occurs 31 times, in 11 seasons from 2002 to 2014, and kicker_player_id is
    NULL on every one. Charging it as a missed extra point would penalise a
    player who did nothing, and inflating anyone's attempt count would break
    the reconciliation `verify --checks kicker` depends on. In practice the
    null kicker id means derive_kicker_weekly excludes these plays before they
    reach this function at all; the case is handled here as well so that a
    future season which DOES attribute an abort cannot kill a backfill.

    Any OTHER unrecognised result string still raises rather than scoring 0.0,
    for the same reason as `field_goal_points`: a silent zero looks like real
    data. That behaviour is what surfaced 'aborted' in the first place -- it
    crashed a real season instead of quietly scoring it -- so it is narrowed by
    exactly one measured value and no further.
    """
    if result == "good":
        return XP_POINTS
    if result in ("failed", "blocked"):
        return MISSED_XP_POINTS
    if result == "aborted":
        return ABORTED_XP_POINTS
    raise ValueError(f"unrecognised extra point result: {result!r}")
