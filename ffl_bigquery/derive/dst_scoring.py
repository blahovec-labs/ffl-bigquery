"""Pure DST scoring rules -- no pandas, no I/O, no BigQuery.

Isolated in its own module because these are the one part of DST scoring with
zero dependencies: a league with different rules swaps this file and nothing
else. The points-allowed tiers are also the single most error-prone piece --
inclusive ranges with no gaps, where an off-by-one silently mis-scores every
game in a band rather than failing loudly -- so keeping them testable without
constructing a DataFrame is deliberate.
"""
from __future__ import annotations

SACK_POINTS = 1
INTERCEPTION_POINTS = 2
FUMBLE_RECOVERY_POINTS = 2
SAFETY_POINTS = 2
TOUCHDOWN_POINTS = 6
BLOCKED_KICK_POINTS = 2

# (inclusive_upper_bound, bonus). Ordered ascending; the final entry's bound is
# None, meaning "everything above the previous bound".
_TIERS: list[tuple[int | None, int]] = [
    (0, 10),
    (6, 7),
    (13, 4),
    (20, 1),
    (27, 0),
    (34, -1),
    (None, -4),
]


def points_allowed_bonus(points_allowed: int | None) -> int | None:
    """Tiered bonus for the points this defense's team allowed.

    Returns None when the final score is unknown. That matters: defaulting an
    unknown score to 0 would award a +10 shutout bonus to every unresolvable
    team-week, which is both wrong and flattering, and would look like real
    data rather than a gap.
    """
    if points_allowed is None:
        return None
    if points_allowed < 0:
        raise ValueError(f"points_allowed cannot be negative, got {points_allowed}")
    for bound, bonus in _TIERS:
        if bound is None or points_allowed <= bound:
            return bonus
    raise AssertionError("unreachable: final tier has an open upper bound")
