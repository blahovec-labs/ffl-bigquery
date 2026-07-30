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


def _mfl_map(xref: pd.DataFrame) -> pd.Series:
    x = xref[xref["gsis_id"].notna()]
    keys = pd.to_numeric(x["mfl_id"], errors="coerce").astype("Int64")  # type: ignore[union-attr]
    return pd.Series(x["gsis_id"].values, index=keys).dropna()  # type: ignore[union-attr]


def _name_map(xref: pd.DataFrame) -> dict[tuple[str, str], str]:
    x = xref[xref["gsis_id"].notna()].copy()
    x["_key"] = list(
        zip(
            x["merge_name"].map(normalize_merge_name),  # type: ignore[union-attr]
            x["position"].fillna("").astype(str),  # type: ignore[union-attr]
            strict=False,
        )
    )
    counts = x["_key"].value_counts()  # type: ignore[union-attr]
    # Refuse any (merge_name, position) that maps to more than one player.
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

    is_mfl = out["source"].eq("mfl")
    if is_mfl.any():  # type: ignore[union-attr]
        ids = pd.to_numeric(
            out.loc[is_mfl, "source_player_id"], errors="coerce"
        ).astype("Int64")  # type: ignore[union-attr]
        resolved.loc[is_mfl] = ids.map(_mfl_map(xref)).values  # type: ignore[union-attr]

    is_ffc = out["source"].eq("ffc")
    if is_ffc.any():  # type: ignore[union-attr]
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
