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

    An unrecognised result string raises rather than scoring 0.0. nflverse
    has carried values like 'aborted' in some seasons; treating an unknown
    result as 0.0 would silently drop real kicks.
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

    An unrecognised result string raises rather than scoring 0.0, for the
    same reason as `field_goal_points`: a silent zero looks like real data.
    """
    if result == "good":
        return XP_POINTS
    if result in ("failed", "blocked"):
        return MISSED_XP_POINTS
    raise ValueError(f"unrecognised extra point result: {result!r}")
