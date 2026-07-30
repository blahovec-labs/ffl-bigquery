"""Resolve ff_adp rows to nflverse gsis_id.

The two sources take different paths, and this asymmetry is not incidental:

  * MFL publishes its own player id, which IS one of the 20 id systems in
    ff_playerids -> exact join on mfl_id.
  * FFC publishes an internal player_id that appears in NO nflverse id system
    (verified against all 20 columns) -> name-based join only.

Ambiguity is refused rather than resolved. A wrong gsis_id silently attributes one
player's draft market to another; a NULL is visible and countable.
"""
from __future__ import annotations

import logging
from typing import cast

import pandas as pd

log = logging.getLogger(__name__)

# ffverse merge_name convention, verified against load_ff_playerids():
# lowercase, drop name suffixes, drop periods and apostrophes, keep hyphens.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_merge_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.lower().replace(".", "").replace("'", "").replace("’", "")
    tokens = [t for t in s.split() if t]
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _notna_rows(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Boolean-mask row filtering (``df[df[col].notna()]``).

    Without pandas-stubs, pyright resolves ``DataFrame.__getitem__(Series[bool])``
    to an ambiguous overload, and the filtered frame's type then infects every
    downstream attribute access on it (``.map``, ``.fillna``, ``.values``, ...)
    with its own error. Asserting the known-correct return type once, at the
    filter itself, is cheaper and more precise than re-suppressing each of
    those cascaded call sites individually.
    """
    mask = cast(pd.Series, df[col].notna())
    return cast(pd.DataFrame, df[mask])


def _eq_mask(df: pd.DataFrame, col: str, value: str) -> pd.Series:
    """Same ambiguity as `_notna_rows`, for an equality mask instead of notna."""
    return cast(pd.Series, df[col].eq(value))


def _mfl_map(xref: pd.DataFrame) -> pd.Series:
    x = _notna_rows(xref, "gsis_id")
    # pd.to_numeric(...).astype("Int64") is a genuine pandas-stub gap (also
    # suppressed at writer.py:45 for the same reason); cast the result so the
    # gap doesn't cascade into every downstream use of `keys` below.
    keys = cast(pd.Series, pd.to_numeric(x["mfl_id"], errors="coerce").astype("Int64"))  # type: ignore[union-attr]
    # Filter on the parsed key's own null-ness, NOT Series.dropna() on the
    # constructed map: dropna() only drops NaN *values*, not a NaN *index*. A
    # xref row with a populated gsis_id but a missing/non-numeric mfl_id would
    # otherwise survive into the map at index pd.NA — and Series.map() treats
    # that as a real match against any ADP row whose own malformed
    # source_player_id also parsed to pd.NA, silently attributing one player's
    # draft market to a different, unrelated player.
    valid = keys.notna()
    return pd.Series(x["gsis_id"].values[valid.to_numpy()], index=keys[valid])


def _name_map(xref: pd.DataFrame) -> dict[tuple[str, str], str]:
    x = _notna_rows(xref, "gsis_id").copy()
    x["_key"] = list(
        zip(
            x["merge_name"].map(normalize_merge_name),
            x["position"].fillna("").astype(str),
            strict=False,
        )
    )
    counts = x["_key"].value_counts()
    # Refuse any (merge_name, position) that maps to more than one player.
    # Series.map() genuinely accepts a Series as a value-lookup mapping (not
    # just a function) at runtime; the bundled pandas stub's `.map()` overload
    # only types the function form, so this one has no cast-able boundary.
    unique = x[x["_key"].map(counts) == 1]  # type: ignore[arg-type]
    dropped = len(x) - len(unique)
    if dropped:
        log.info("refused %d ambiguous name matches in xref", dropped)
    return dict(zip(unique["_key"], unique["gsis_id"], strict=False))


def resolve_gsis_ids(adp: pd.DataFrame, xref: pd.DataFrame) -> pd.DataFrame:
    if adp.empty:
        return adp
    out = adp.copy()
    resolved = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")

    is_mfl = _eq_mask(out, "source", "mfl")
    if is_mfl.any():
        # pd.to_numeric(...).astype("Int64") is the same genuine stub gap as
        # in `_mfl_map` above.
        ids = cast(
            pd.Series,
            pd.to_numeric(
                out.loc[is_mfl, "source_player_id"], errors="coerce"
            ).astype("Int64"),  # type: ignore[union-attr]
        )
        resolved.loc[is_mfl] = ids.map(_mfl_map(xref)).values

    is_ffc = _eq_mask(out, "source", "ffc")
    if is_ffc.any():
        name_map = _name_map(xref)
        keys = list(
            zip(
                out.loc[is_ffc, "player_name"].map(normalize_merge_name),
                out.loc[is_ffc, "position"].fillna("").astype(str),
                strict=False,
            )
        )
        resolved.loc[is_ffc] = [name_map.get(k, pd.NA) for k in keys]

    out["gsis_id"] = resolved
    log.info("resolved %d/%d ADP rows to gsis_id", out["gsis_id"].notna().sum(), len(out))
    return out


def resolution_rate(adp: pd.DataFrame) -> float:
    if adp.empty:
        return 0.0
    return float(adp["gsis_id"].notna().mean())
