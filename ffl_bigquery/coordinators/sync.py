"""sync-coordinators orchestration.

Deliberately NOT part of run_sync_nflverse / the `sync-nflverse` CLI command --
this table is opt-in only, invoked via its own `sync-coordinators` subcommand.
Unlike sync-adp's chunk fan-out, a bad/missing page for one (season, team) is
already a normal, exception-free outcome (see wikipedia.fetch_season_wikitext),
so there is no separate failed/empty run-log here -- just a fetched/missing
tally in the final log line.
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery._version import __version__
from ffl_bigquery.coordinators.schema import (
    NFL_COORDINATORS_KEYS,
    NFL_COORDINATORS_PARTITION,
    NFL_COORDINATORS_SCHEMA,
)
from ffl_bigquery.coordinators.wikipedia import (
    TEAM_WIKI_NAMES,
    fetch_season_wikitext,
    page_title,
    parse_coordinators,
)
from ffl_bigquery.http import SourceUnavailable, ThrottledSession
from ffl_bigquery.runs import parse_seasons
from ffl_bigquery.schema import to_bq_schema
from ffl_bigquery.writer import BigQueryWriter, TableRef

log = logging.getLogger(__name__)

USER_AGENT = f"ffl-bigquery/{__version__} (+https://github.com/blahovec-labs/ffl-bigquery)"


def _parse_teams(arg: str) -> list[str]:
    if arg.strip().lower() == "all":
        return sorted(TEAM_WIKI_NAMES)
    teams = [t.strip().upper() for t in arg.split(",") if t.strip()]
    unknown = [t for t in teams if t not in TEAM_WIKI_NAMES]
    if unknown:
        raise ValueError(
            f"unknown --teams value(s) {unknown!r}; valid teams are "
            f"{sorted(TEAM_WIKI_NAMES)!r}"
        )
    return teams


def run_sync_coordinators(
    ns: argparse.Namespace,
    *,
    bq_client,
    session: ThrottledSession | None = None,
    writer: BigQueryWriter | None = None,
    now: datetime | None = None,
) -> int:
    current_year = (now or datetime.now(UTC)).year
    seasons = parse_seasons(ns.seasons, current_year)
    teams = _parse_teams(ns.teams)  # validated before any request, dry-run or not

    if ns.dry_run:
        print(
            f"[dry-run] would fetch {len(seasons) * len(teams)} team-season "
            f"page(s) into {ns.coordinators_table}"
        )
        return 0

    ref = TableRef.parse(ns.coordinators_table)
    writer = writer or BigQueryWriter(client=bq_client)
    session = session or ThrottledSession(
        user_agent=USER_AGENT, min_interval=getattr(ns, "min_interval", 1.0),
    )
    retrieved_at = now or datetime.now(UTC)

    writer.create_table_if_missing(
        ref, to_bq_schema(NFL_COORDINATORS_SCHEMA), NFL_COORDINATORS_PARTITION,
    )

    rows: list[dict] = []
    fetched = missing = unavailable = 0
    for season in seasons:
        for team in teams:
            title = page_title(season, team)
            try:
                page = fetch_season_wikitext(session, page_title=title)
            except SourceUnavailable as e:
                log.warning("season=%s team=%s unavailable: %s", season, team, e)
                unavailable += 1
                continue
            if page is None:
                missing += 1
                continue
            fetched += 1
            rows.extend(
                parse_coordinators(
                    page.wikitext, season=season, team=team, source=page.title,
                    retrieved_at=retrieved_at,
                )
            )

    if not rows:
        log.info(
            "sync-coordinators complete: 0 rows parsed (%d pages fetched, "
            "%d missing, %d unavailable)", fetched, missing, unavailable,
        )
        return 0

    df = align_to_schema(pd.DataFrame(rows), NFL_COORDINATORS_SCHEMA)
    n = writer.merge_rows(
        ref=ref, df=df, schema=NFL_COORDINATORS_SCHEMA, keys=NFL_COORDINATORS_KEYS,
    )
    log.info(
        "sync-coordinators complete: %d rows merged (%d pages fetched, %d "
        "missing, %d unavailable)", n, fetched, missing, unavailable,
    )
    return 0
