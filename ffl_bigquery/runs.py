"""Chunk-keyed run log for resumable ADP syncs.

The unit of work is a (source, season, scoring_format, teams) chunk, not a season —
one season fans out across two sources and six FFC formats.

--resume skips chunks recorded 'success' OR 'empty'. Empty is a settled fact, not a
transient failure: FFC has no 2007 PPR data and never will, so retrying empties
would burn most of the backfill's call budget on known-void requests.
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

ChunkKey = tuple[str, int, str, int]


def parse_seasons(arg: str, current_season: int) -> list[int]:
    """Expand '2010-2026' | '2015,2020' | '2024' | 'latest' into a list of ints."""
    arg = arg.strip().lower()
    if arg == "latest":
        return [current_season]
    if "-" in arg and "," not in arg:
        lo, hi = arg.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(p) for p in arg.split(",") if p.strip()]


@dataclass(frozen=True)
class AdpChunk:
    source: str
    season: int
    scoring_format: str
    teams: int

    @property
    def key(self) -> ChunkKey:
        return (self.source, self.season, self.scoring_format, self.teams)


@dataclass
class RunsTable:
    client: bigquery.Client

    def create_table_if_missing(self, ref: TableRef) -> None:
        try:
            self.client.get_table(str(ref))
            return
        except NotFound:
            pass
        schema = [
            bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("season", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("scoring_format", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("teams", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("rows_written", "INT64"),
            bigquery.SchemaField("resolution_rate", "FLOAT64"),
            bigquery.SchemaField("error", "STRING"),
            bigquery.SchemaField("run_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("library_version", "STRING"),
        ]
        self.client.create_table(bigquery.Table(str(ref), schema=schema))
        log.info("created runs table %s", ref)

    def completed_chunks(self, *, ref: TableRef) -> set[ChunkKey]:
        sql = (
            f"SELECT source, season, scoring_format, teams FROM `{ref}` "
            "WHERE status IN ('success', 'empty')"
        )
        return {
            (r.source, r.season, r.scoring_format, r.teams)
            for r in self.client.query(sql).result()
        }

    def record_success(
        self,
        *,
        ref: TableRef,
        chunk: AdpChunk,
        rows_written: int,
        resolution_rate: float | None = None,
    ) -> None:
        self._record(ref=ref, chunk=chunk, status="success",
                     rows_written=rows_written, resolution_rate=resolution_rate,
                     error=None)

    def record_empty(self, *, ref: TableRef, chunk: AdpChunk) -> None:
        self._record(ref=ref, chunk=chunk, status="empty", rows_written=0,
                     resolution_rate=None, error=None)

    def record_failed(self, *, ref: TableRef, chunk: AdpChunk, error: str) -> None:
        self._record(ref=ref, chunk=chunk, status="failed", rows_written=None,
                     resolution_rate=None, error=error[:4000])

    def _record(
        self,
        *,
        ref: TableRef,
        chunk: AdpChunk,
        status: str,
        rows_written: int | None,
        resolution_rate: float | None,
        error: str | None,
    ) -> None:
        row = {
            "source": chunk.source,
            "season": chunk.season,
            "scoring_format": chunk.scoring_format,
            "teams": chunk.teams,
            "status": status,
            "rows_written": rows_written,
            "resolution_rate": resolution_rate,
            "error": error,
            "run_at": datetime.now(UTC).isoformat(),
            "library_version": __version__,
        }
        errors = self.client.insert_rows_json(str(ref), [row])
        if errors:
            log.warning("runs insert errors: %s", errors)
