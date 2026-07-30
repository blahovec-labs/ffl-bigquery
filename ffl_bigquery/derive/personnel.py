"""Offense personnel parsing: two eras, two upstream columns, one plausibility gate.

`offense_personnel` (participation, 2016-2025) is a free-text string like
"1 RB, 1 TE, 3 WR" -- but starting in 2023 that string's CONTENT changed without
its FILL RATE changing: a real 2023 row reads
"2 CB, 2 ILB, 1 OLB, 1 RB, 1 SS, 2 TE, 2 WR" -- defensive players inside the
offense column. The field still reports 100% fill for 2023-2025, so naive "11
personnel rate" parsing off `offense_personnel` alone produces confident garbage
for exactly the seasons a caller is most likely to trust it.

The verified mitigation (measured 2026-07-30): parse from `offense_positions`
instead for 2023+ -- a semicolon-delimited list of all 11 offensive players'
individual position labels (e.g. "C;G;QB;RB;T;T;T;TE;TE;WR;WR"), which recovers
the right modern personnel shapes (1 RB/1 TE/3 WR = 23,786 plays; 1 RB/2 TE/2 WR
= 7,584) -- and fall back to `offense_personnel` for 2016-2022, because
`offense_positions` is 0% filled before 2023. Two parsers, dispatched purely on
season by `personnel_counts`, each producing its own `plausible` flag so a
special-teams / trick-play row (or a still-contaminated one) can be dropped from
the rate's numerator AND denominator rather than silently miscounted.
"""
from __future__ import annotations

import re
from typing import cast

import pandas as pd

_MODERN_PERSONNEL_START = 2023

# offense_positions lists exactly the 11 players on the field for a normal
# offensive snap: 1 QB is the plausibility signature a defense-contaminated or
# special-teams row won't have.
_REQUIRED_PLAYER_COUNT = 11
_REQUIRED_QB_COUNT = 1

# offense_personnel (legacy, pre-2023) enumerates only skill-position counts,
# never a full 11-man lineup -- so its plausibility gate is different in kind:
# any label outside this set (e.g. CB/DE/FS/MLB/OLB, the real 2023 contamination
# shape) means the row is not a genuine offensive personnel grouping.
_LEGACY_ALLOWED_POSITIONS = {"QB", "RB", "FB", "TE", "WR"}
_LEGACY_TOKEN_RE = re.compile(r"(\d+)\s*([A-Za-z]+)")


def parse_personnel_2023plus(positions: pd.Series) -> pd.DataFrame:
    """Parse `offense_positions` (semicolon-delimited per-player labels).

    Plausible iff the row lists exactly 11 players with exactly 1 QB among
    them -- the signature of a genuine offensive snap. A defense-contaminated
    row (real 2023 shape: CB/DE/FS/MLB/OLB tokens, no QB) fails on the QB
    check; a special-teams/short row fails on the count check.
    """
    tokens = positions.fillna("").astype(str).apply(
        lambda v: [p.strip() for p in v.split(";") if p.strip()]
    )
    n_rb = tokens.apply(lambda ts: ts.count("RB")).astype("Int64")
    n_te = tokens.apply(lambda ts: ts.count("TE")).astype("Int64")
    n_wr = tokens.apply(lambda ts: ts.count("WR")).astype("Int64")
    n_qb = tokens.apply(lambda ts: ts.count("QB")).astype("Int64")
    n_total = tokens.apply(len)
    plausible = (n_total == _REQUIRED_PLAYER_COUNT) & (n_qb == _REQUIRED_QB_COUNT)
    return pd.DataFrame(
        {"n_rb": n_rb, "n_te": n_te, "n_wr": n_wr, "n_qb": n_qb, "plausible": plausible},
        index=positions.index,
    )


def _parse_legacy_row(value: object) -> dict[str, int]:
    if not isinstance(value, str) or not value.strip():
        return {}
    counts: dict[str, int] = {}
    for count_str, pos in _LEGACY_TOKEN_RE.findall(value.upper()):
        counts[pos] = counts.get(pos, 0) + int(count_str)
    return counts


def parse_personnel_legacy(personnel: pd.Series) -> pd.DataFrame:
    """Parse `offense_personnel` (e.g. "1 RB, 1 TE, 3 WR").

    Plausible iff every parsed label is a genuine offensive skill position
    (QB/RB/FB/TE/WR). A row carrying defensive labels (CB/DE/FS/MLB/OLB -- the
    same contamination shape that hits offense_positions in 2023+, just found
    here on an older row) is flagged implausible rather than silently counted.
    """
    parsed = personnel.apply(_parse_legacy_row)
    n_rb = parsed.apply(lambda d: d.get("RB", 0)).astype("Int64")
    n_te = parsed.apply(lambda d: d.get("TE", 0)).astype("Int64")
    n_wr = parsed.apply(lambda d: d.get("WR", 0)).astype("Int64")
    n_qb = parsed.apply(lambda d: d.get("QB", 0)).astype("Int64")
    plausible = parsed.apply(
        lambda d: bool(d) and all(pos in _LEGACY_ALLOWED_POSITIONS for pos in d)
    )
    return pd.DataFrame(
        {"n_rb": n_rb, "n_te": n_te, "n_wr": n_wr, "n_qb": n_qb, "plausible": plausible},
        index=personnel.index,
    )


def personnel_counts(df: pd.DataFrame, season: int) -> pd.DataFrame:
    """Dispatch to the right era parser by `season` and record which ran.

    Season alone decides the parser -- not per-row column presence -- because
    the corruption/emptiness split is a season-wide upstream fact:
    `offense_positions` is 0% filled before 2023 (using it there would yield an
    all-NULL parse, not a fallback), and `offense_personnel` is the one that's
    unreliable from 2023 on.
    """
    if season >= _MODERN_PERSONNEL_START:
        positions = (
            cast(pd.Series, df["offense_positions"]) if "offense_positions" in df.columns
            else pd.Series([pd.NA] * len(df), index=df.index, dtype="object")
        )
        out = parse_personnel_2023plus(positions)
        source = "offense_positions"
    else:
        personnel = (
            cast(pd.Series, df["offense_personnel"]) if "offense_personnel" in df.columns
            else pd.Series([pd.NA] * len(df), index=df.index, dtype="object")
        )
        out = parse_personnel_legacy(personnel)
        source = "offense_personnel"
    out["personnel_source"] = source
    return out
