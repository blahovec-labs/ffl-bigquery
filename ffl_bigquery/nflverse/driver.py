"""The one season loop every mechanical nflverse table shares.

Chunk isolation is the point: each (table, season) fetches, transforms, writes
and records independently, so one bad table degrades coverage instead of
aborting the run.
"""
from __future__ import annotations

import logging

from ffl_bigquery.nflverse.runs import NflverseChunk, NflverseRunsTable
from ffl_bigquery.nflverse.spec import NflverseTableSpec
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
