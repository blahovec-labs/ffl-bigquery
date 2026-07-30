"""MyFantasyLeague ADP fetch (export?TYPE=adp).

Free, no key, but the endpoint refuses requests without a real User-Agent.

Three shapes matter, all observed 2026-07-29:
  * every numeric field is a STRING ("averagePick": "3.28") — casting is the
    transform's job, not this module's
  * pre-2012 returns {"adp": {"totalPicks": "0", "totalDrafts": "0"}} with no
    "player" key at all
  * MFL's JSON derives from XML, so a single-element list collapses to a bare
    object; "player" may be a dict
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ffl_bigquery.http import ThrottledSession

log = logging.getLogger(__name__)

MFL_BASE_URL = "https://api.myfantasyleague.com"


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MflResponse:
    players: list[dict]
    total_drafts: int | None
    total_picks: int | None

    @property
    def is_empty(self) -> bool:
        return not self.players


def fetch_mfl(
    session: ThrottledSession,
    *,
    season: int,
    teams: int | None = None,
    is_ppr: bool | None = None,
    is_keeper: bool | None = None,
    is_mock: bool | None = None,
) -> MflResponse:
    params: dict[str, Any] = {"TYPE": "adp", "JSON": 1}
    if teams is not None:
        params["FCOUNT"] = teams
    if is_ppr is not None:
        params["IS_PPR"] = int(is_ppr)
    if is_keeper is not None:
        params["IS_KEEPER"] = int(is_keeper)
    if is_mock is not None:
        params["IS_MOCK"] = int(is_mock)

    payload = session.get_json(f"{MFL_BASE_URL}/{season}/export", params)
    adp = payload.get("adp") or {}
    raw = adp.get("player")
    if raw is None:
        players: list[dict] = []
    elif isinstance(raw, dict):
        # XML-derived JSON collapses a single-element list into an object.
        players = [raw]
    else:
        players = list(raw)
    if not players:
        log.info("MFL empty: season=%s params=%s", season, params)
    return MflResponse(
        players=players,
        total_drafts=_as_int(adp.get("totalDrafts")),
        total_picks=_as_int(adp.get("totalPicks")),
    )
