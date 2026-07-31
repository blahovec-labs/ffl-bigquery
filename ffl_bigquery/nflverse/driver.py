"""The one season loop every mechanical nflverse table shares.

Chunk isolation is the point: each (table, season) fetches, transforms, writes
and records independently, so one bad table degrades coverage instead of
aborting the run.
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from ffl_bigquery.nflverse.runs import NflverseChunk, NflverseRunsTable
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.runs import parse_seasons
from ffl_bigquery.schema import to_bq_schema
from ffl_bigquery.writer import BigQueryWriter, TableRef

log = logging.getLogger(__name__)


def run_sync_nflverse(
    specs: list[NflverseTableSpec],
    *,
    seasons: list[int],
    writer: BigQueryWriter,
    runs: NflverseRunsTable,
    runs_ref: TableRef,
    table_refs: dict[str, TableRef],
    resume: bool = False,
    dry_run: bool = False,
) -> int:
    if dry_run:
        print(
            f"[dry-run] would sync {len(specs)} table(s) x {len(seasons)} season(s)"
        )
        return 0

    # Resolve every ref up front so a typo fails before any third-party fetch.
    refs = {s.name: table_refs[s.name] for s in specs}

    runs.create_table_if_missing(runs_ref)
    for spec in specs:
        writer.create_table_if_missing(
            refs[spec.name], to_bq_schema(spec.schema), spec.partition
        )

    done = runs.completed_chunks(ref=runs_ref) if resume else set()
    succeeded = failed = runlog_failures = 0

    for spec in specs:
        for season in seasons:
            chunk = NflverseChunk(spec.name, season)
            if chunk.key in done:
                log.info("resume: skipping %s", chunk.key)
                continue
            if season < spec.min_season:
                # The source never had this season; a settled fact, not a failure.
                if not runs.record_empty(ref=runs_ref, chunk=chunk):
                    runlog_failures += 1
                succeeded += 1
                continue
            try:
                df = spec.transform(spec.loader(season), season)
                n = writer.write_season(
                    refs[spec.name], df, season=season, schema=spec.schema
                )
                if not runs.record_success(
                    ref=runs_ref, chunk=chunk, rows_written=n
                ):
                    runlog_failures += 1
                succeeded += 1
            except Exception as e:  # noqa: BLE001 - one bad chunk must not abort the run
                log.exception("chunk %s failed", chunk.key)
                if not runs.record_failed(ref=runs_ref, chunk=chunk, error=str(e)):
                    runlog_failures += 1
                failed += 1

    log_fn = log.error if runlog_failures else log.info
    log_fn(
        "sync-nflverse complete: %d ok, %d failed, %d run-log write failures",
        succeeded, failed, runlog_failures,
    )
    return 1 if succeeded == 0 and failed > 0 else 0


def run_sync_nflverse_cli(
    ns: argparse.Namespace,
    *,
    bq_client,
    load_specs: Callable[[], list[NflverseTableSpec]] | None = None,
    writer: BigQueryWriter | None = None,
    runs: NflverseRunsTable | None = None,
    current_season: int | None = None,
) -> int:
    """CLI orchestration for `sync-nflverse`: resolve --dataset/--tables/
    --seasons into run_sync_nflverse's explicit arguments.

    Every one of the nine season-chunked tables lands in the same dataset, so
    table refs are derived as project.dataset.<spec.name> from a single
    --dataset argument rather than making the operator pass nine per-table
    flags. `--tables` is validated against the registry's known names before
    any spec is even loaded, so a typo fails fast with the full valid list --
    the same posture `sync-adp` takes for `--sources` (see
    ffl_bigquery.nflverse.tables.ALL_TABLE_NAMES for why that list is plain
    strings rather than the specs themselves).
    """
    from ffl_bigquery.nflverse.tables import ALL_TABLE_NAMES
    from ffl_bigquery.nflverse.tables import load_all_specs as _load_all_specs

    parts = ns.dataset.split(".")
    if len(parts) != 2:
        raise ValueError(f"expected --dataset project.dataset, got {ns.dataset!r}")
    project, dataset = parts

    requested = (
        [t.strip() for t in ns.tables.split(",") if t.strip()]
        if ns.tables else list(ALL_TABLE_NAMES)
    )
    unknown = [t for t in requested if t not in ALL_TABLE_NAMES]
    if unknown:
        raise ValueError(
            f"unknown --tables value(s) {unknown!r}; valid tables are {ALL_TABLE_NAMES!r}"
        )

    all_specs = (load_specs or _load_all_specs)()
    specs = [s for s in all_specs if s.name in requested]
    table_refs = {name: TableRef(project, dataset, name) for name in requested}

    runs_table = ns.runs_table or f"{project}.{dataset}._ffl_nflverse_runs"
    runs_ref = TableRef.parse(runs_table)

    season_for_latest = current_season or datetime.now(UTC).year
    seasons = parse_seasons(ns.seasons, season_for_latest)

    writer = writer or BigQueryWriter(client=bq_client)
    runs = runs or NflverseRunsTable(client=bq_client)

    return run_sync_nflverse(
        specs, seasons=seasons, writer=writer, runs=runs, runs_ref=runs_ref,
        table_refs=table_refs, resume=ns.resume, dry_run=ns.dry_run,
    )
