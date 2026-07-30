"""sync-adp orchestration.

Chunk isolation is the point: each (source, season, format, teams) chunk fetches,
transforms, resolves, writes and records independently, so a dead third-party
source degrades coverage instead of aborting the run.
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

import pandas as pd

from ffl_bigquery._version import __version__
from ffl_bigquery.adp.ffc import fetch_ffc
from ffl_bigquery.adp.mfl import fetch_mfl
from ffl_bigquery.adp.resolve import resolution_rate, resolve_gsis_ids
from ffl_bigquery.adp.schema import (
    FF_ADP_KEYS,
    FF_ADP_PARTITION,
    FF_ADP_SCHEMA,
)
from ffl_bigquery.adp.transform import transform_ffc, transform_mfl
from ffl_bigquery.http import SourceUnavailable, ThrottledSession
from ffl_bigquery.runs import AdpChunk, RunsTable, parse_seasons
from ffl_bigquery.schema import to_bq_schema
from ffl_bigquery.writer import BigQueryWriter, TableRef

log = logging.getLogger(__name__)

USER_AGENT = f"ffl-bigquery/{__version__} (+https://github.com/blahovec-labs/ffl-bigquery)"

# MFL has no per-format endpoint; its scoring axis is the IS_PPR flag alone, so
# FFC-only formats (dynasty, rookie, 2qb, half-ppr) cannot be requested from it.
MFL_FORMATS = {"ppr", "standard"}


def build_chunks(
    *, seasons: list[int], sources: list[str], formats: list[str], teams: list[int]
) -> list[AdpChunk]:
    chunks: list[AdpChunk] = []
    for source in sources:
        allowed = MFL_FORMATS if source == "mfl" else set(formats)
        for season in seasons:
            for fmt in formats:
                if fmt not in allowed:
                    continue
                for n in teams:
                    chunks.append(
                        AdpChunk(source=source, season=season,
                                 scoring_format=fmt, teams=n)
                    )
    return chunks


def _default_xref_loader(bq_client) -> Callable[[str], pd.DataFrame]:
    def _load(xref_table: str) -> pd.DataFrame:
        sql = (
            "SELECT mfl_id, gsis_id, merge_name, position "
            f"FROM `{xref_table}` WHERE gsis_id IS NOT NULL"
        )
        return bq_client.query(sql).result().to_dataframe()

    return _load


def _fetch_and_transform(
    chunk: AdpChunk, session: ThrottledSession, snapshot_date: date
) -> pd.DataFrame:
    if chunk.source == "ffc":
        resp = fetch_ffc(session, season=chunk.season,
                         scoring_format=chunk.scoring_format, teams=chunk.teams)
        return transform_ffc(resp, season=chunk.season,
                             scoring_format=chunk.scoring_format,
                             teams=chunk.teams, snapshot_date=snapshot_date)
    resp = fetch_mfl(session, season=chunk.season, teams=chunk.teams,
                     is_ppr=chunk.scoring_format == "ppr")
    return transform_mfl(resp, season=chunk.season,
                         scoring_format=chunk.scoring_format, teams=chunk.teams,
                         snapshot_date=snapshot_date)


def run_sync_adp(
    ns: argparse.Namespace,
    *,
    bq_client,
    session: ThrottledSession | None = None,
    load_xref: Callable[[str], pd.DataFrame] | None = None,
    today: date | None = None,
    runs: RunsTable | None = None,
    writer: BigQueryWriter | None = None,
) -> int:
    snapshot_date = today or datetime.now(UTC).date()
    seasons = parse_seasons(ns.seasons, snapshot_date.year)
    sources = [s.strip() for s in ns.sources.split(",") if s.strip()]
    formats = [f.strip() for f in ns.formats.split(",") if f.strip()]
    teams = [int(t) for t in str(ns.teams).split(",") if str(t).strip()]
    chunks = build_chunks(seasons=seasons, sources=sources, formats=formats,
                          teams=teams)

    if ns.dry_run:
        print(f"[dry-run] would sync {len(chunks)} ADP chunks "
              f"into {ns.adp_table} for snapshot_date={snapshot_date}")
        return 0

    adp_ref = TableRef.parse(ns.adp_table)
    runs_ref = TableRef.parse(ns.runs_table)
    writer = writer or BigQueryWriter(client=bq_client)
    runs = runs or RunsTable(client=bq_client)
    session = session or ThrottledSession(
        user_agent=USER_AGENT, min_interval=getattr(ns, "min_interval", 1.0),
    )

    writer.create_table_if_missing(
        adp_ref, to_bq_schema(FF_ADP_SCHEMA), FF_ADP_PARTITION,
    )
    runs.create_table_if_missing(runs_ref)

    done: set[tuple] = runs.completed_chunks(ref=runs_ref) if ns.resume else set()
    loader = load_xref or _default_xref_loader(bq_client)
    xref: pd.DataFrame | None = None

    succeeded = failed = runlog_failures = 0
    for chunk in chunks:
        if chunk.key in done:
            log.info("resume: skipping %s", chunk.key)
            continue
        try:
            df = _fetch_and_transform(chunk, session, snapshot_date)
            if df.empty:
                if not runs.record_empty(ref=runs_ref, chunk=chunk):
                    runlog_failures += 1
                succeeded += 1
                continue
            if xref is None:
                xref = loader(ns.xref_table)
            df = resolve_gsis_ids(df, xref)
            n = writer.merge_rows(ref=adp_ref, df=df, schema=FF_ADP_SCHEMA,
                                  keys=FF_ADP_KEYS)
            if not runs.record_success(ref=runs_ref, chunk=chunk, rows_written=n,
                                       resolution_rate=resolution_rate(df)):
                runlog_failures += 1
            succeeded += 1
        except SourceUnavailable as e:
            log.warning("chunk %s unavailable: %s", chunk.key, e)
            if not runs.record_failed(ref=runs_ref, chunk=chunk, error=str(e)):
                runlog_failures += 1
            failed += 1
        except Exception as e:  # noqa: BLE001 - one bad chunk must not abort the run
            log.exception("chunk %s failed", chunk.key)
            if not runs.record_failed(ref=runs_ref, chunk=chunk, error=str(e)):
                runlog_failures += 1
            failed += 1

    # A lost run-log row is not silent: a missing 'empty'/'success' row means
    # --resume would re-issue a request it already knows is void, wasting a
    # call against a source whose terms ask callers not to poll frequently.
    # It must not raise here -- the chunk's ADP rows (if any) are already
    # MERGE-written, and the MERGE is idempotent, so a retry is harmless.
    log_fn = log.error if runlog_failures else log.info
    log_fn(
        "sync-adp complete: %d ok, %d failed, %d run-log write failures",
        succeeded, failed, runlog_failures,
    )
    return 1 if succeeded == 0 and failed > 0 else 0
