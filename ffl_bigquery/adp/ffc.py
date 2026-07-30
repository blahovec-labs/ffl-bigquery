"""Fantasy Football Calculator ADP fetch.

Terms (their published API docs): free for personal and commercial use, attribution
requested, data updates once daily — do not poll frequently.

FFC signals "no data for this year/format" with HTTP 200 in two different shapes,
both observed 2026-07-29:

    standard/2007 -> {"status": "Success", "meta": {...}, "players": []}
    ppr/2007      -> {"status": "Error", "errors": "No ADP data found."}

Emptiness is therefore decided by the absence of players, never by `status`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ffl_bigquery.adp.schema import FFC_FORMATS
from ffl_bigquery.http import ThrottledSession

log = logging.getLogger(__name__)

FFC_BASE_URL = "https://fantasyfootballcalculator.com/api/v1/adp"


@dataclass(frozen=True)
class FfcResponse:
    players: list[dict]
    total_drafts: int | None
    window_start: str | None
    window_end: str | None

    @property
    def is_empty(self) -> bool:
        return not self.players


def fetch_ffc(
    session: ThrottledSession,
    *,
    season: int,
    scoring_format: str,
    teams: int,
) -> FfcResponse:
    if scoring_format not in FFC_FORMATS:
        raise ValueError(
            f"unknown scoring_format {scoring_format!r}; expected one of {FFC_FORMATS}"
        )
    payload = session.get_json(
        f"{FFC_BASE_URL}/{scoring_format}", {"teams": teams, "year": season}
    )
    players = payload.get("players") or []
    meta = payload.get("meta") or {}
    if not players:
        log.info(
            "FFC empty: season=%s format=%s teams=%s status=%s errors=%s",
            season, scoring_format, teams,
            payload.get("status"), payload.get("errors"),
        )
    return FfcResponse(
        players=list(players),
        total_drafts=meta.get("total_drafts"),
        window_start=meta.get("start_date"),
        window_end=meta.get("end_date"),
    )
