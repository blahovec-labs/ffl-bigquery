"""Run log for season-chunked nflverse table loads.

Keyed on (table_name, season). This is deliberately a SECOND run log rather
than a generalization of runs.AdpChunk: that one is keyed on ADP's
(source, season, scoring_format, teams) chunk, is covered by tests, and is
already in production use. Widening it to serve both would put a tested,
live-exercised contract at risk to save a small amount of duplication.

--resume skips chunks recorded 'success' OR 'empty'. An empty season is a
settled fact upstream, not a transient failure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from ffl_bigquery._version import __version__
from ffl_bigquery.writer import TableRef

log = logging.getLogger(__name__)

NflverseKey = tuple[str, int]


@dataclass(frozen=True)
class NflverseChunk:
    table_name: str
    season: int

    @property
    def key(self) -> NflverseKey:
        return (self.table_name, self.season)


@dataclass
class NflverseRunsTable:
    client: bigquery.Client

    def create_table_if_missing(self, ref: TableRef) -> None:
        try:
            self.client.get_table(str(ref))
            return
        except NotFound:
            pass
        schema = [
            bigquery.SchemaField("table_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("season", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("rows_written", "INT64"),
            bigquery.SchemaField("error", "STRING"),
            bigquery.SchemaField("run_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("library_version", "STRING"),
        ]
        self.client.create_table(bigquery.Table(str(ref), schema=schema))
        log.info("created nflverse runs table %s", ref)

    def completed_chunks(self, *, ref: TableRef) -> set[NflverseKey]:
        sql = (
            f"SELECT table_name, season FROM `{ref}` "
            "WHERE status IN ('success', 'empty')"
        )
        return {(r.table_name, r.season) for r in self.client.query(sql).result()}

    def record_success(
        self, *, ref: TableRef, chunk: NflverseChunk, rows_written: int
    ) -> bool:
        return self._record(ref=ref, chunk=chunk, status="success",
                            rows_written=rows_written, error=None)

    def record_empty(self, *, ref: TableRef, chunk: NflverseChunk) -> bool:
        return self._record(ref=ref, chunk=chunk, status="empty",
                            rows_written=0, error=None)

    def record_failed(
        self, *, ref: TableRef, chunk: NflverseChunk, error: str
    ) -> bool:
        return self._record(ref=ref, chunk=chunk, status="failed",
                            rows_written=None, error=error[:4000])

    def _record(
        self,
        *,
        ref: TableRef,
        chunk: NflverseChunk,
        status: str,
        rows_written: int | None,
        error: str | None,
    ) -> bool:
        row = {
            "table_name": chunk.table_name,
            "season": chunk.season,
            "status": status,
            "rows_written": rows_written,
            "error": error,
            "run_at": datetime.now(UTC).isoformat(),
            "library_version": __version__,
        }
        try:
            errors = self.client.insert_rows_json(str(ref), [row])
        except Exception:  # noqa: BLE001 - must never abort the caller's loop
            log.exception("nflverse runs insert failed for %s", chunk.key)
            return False
        if errors:
            log.error("nflverse runs insert errors: %s", errors)
            return False
        return True
