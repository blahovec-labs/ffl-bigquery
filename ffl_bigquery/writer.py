"""TableRef + BigQueryWriter: composite-key idempotent MERGE writes.

ff_adp is snapshot-grain and append-shaped, but re-running one day's sync must be a
no-op — so every write goes through a staging table and a MERGE on the full grain
key rather than a bare append.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from ffl_bigquery.partition import SeasonRangePartition, TimePartition
from ffl_bigquery.schema import ColumnSpec, to_bq_schema

log = logging.getLogger(__name__)

Partition = TimePartition | SeasonRangePartition | None


def coerce_df_for_bq(
    df: pd.DataFrame, schema: list[bigquery.SchemaField]
) -> pd.DataFrame:
    df = df.copy()
    by_name = {f.name: f for f in schema}
    for col in df.columns:
        field = by_name.get(col)
        if field is None:
            continue
        ft = field.field_type.upper()
        if ft == "DATE":
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
        elif ft in ("TIMESTAMP", "DATETIME"):
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        elif ft == "BOOL" and df[col].dtype == object:
            df[col] = df[col].astype("boolean")
        elif ft in ("INT64", "INTEGER"):
            # Third-party sources hand us ints as strings ("3.28" from MFL) or as
            # float64 carrying NA (nflverse). "Int64" is pandas' nullable integer,
            # so NA survives, and RANGE partitioning REQUIRES a true INT64 column.
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")  # type: ignore[union-attr]
        elif ft in ("FLOAT64", "FLOAT", "NUMERIC"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@dataclass(frozen=True)
class TableRef:
    project: str
    dataset: str
    table: str

    def __str__(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    @classmethod
    def parse(cls, s: str) -> TableRef:
        parts = s.split(".")
        if len(parts) != 3:
            raise ValueError(f"expected project.dataset.table, got {s!r}")
        return cls(*parts)


def build_merge_sql(
    *, target: str, source: str, schema: list[ColumnSpec], keys: list[str]
) -> str:
    cols = [s.name for s in schema]
    missing = [k for k in keys if k not in cols]
    if missing:
        raise ValueError(f"merge keys absent from schema: {missing}")
    non_key = [c for c in cols if c not in keys]
    on_clause = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    set_clause = ",\n  ".join(f"{c} = s.{c}" for c in non_key)
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"s.{c}" for c in cols)
    return (
        f"MERGE `{target}` t\n"
        f"USING `{source}` s\n"
        f"ON {on_clause}\n"
        f"WHEN MATCHED THEN UPDATE SET\n  {set_clause}\n"
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols})\n"
        f"VALUES ({insert_vals})"
    ).strip()


class BigQueryWriter:
    def __init__(self, client: bigquery.Client | None = None) -> None:
        self.client = client or bigquery.Client()

    def create_table_if_missing(
        self,
        ref: TableRef,
        schema: list[bigquery.SchemaField],
        partition: Partition = None,
    ) -> None:
        try:
            self.client.get_table(str(ref))
            return
        except NotFound:
            pass
        table = bigquery.Table(str(ref), schema=schema)
        if isinstance(partition, TimePartition):
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=partition.field,
            )
            table.clustering_fields = partition.clustering or None
        elif isinstance(partition, SeasonRangePartition):
            table.range_partitioning = bigquery.RangePartitioning(
                field=partition.field,
                range_=bigquery.PartitionRange(
                    start=partition.start, end=partition.end,
                    interval=partition.interval,
                ),
            )
            table.clustering_fields = partition.clustering or None
        self.client.create_table(table)
        log.info("created table %s", ref)

    def merge_rows(
        self,
        *,
        ref: TableRef,
        df: pd.DataFrame,
        schema: list[ColumnSpec],
        keys: list[str],
    ) -> int:
        """Stage df, MERGE on `keys`, drop stage. Returns rows staged."""
        if df.empty:
            log.info("nothing to merge into %s", ref)
            return 0
        bq_schema = to_bq_schema(schema)
        stage = f"{ref.project}.{ref.dataset}._stage_{ref.table}_{uuid.uuid4().hex[:8]}"
        self.client.load_table_from_dataframe(
            coerce_df_for_bq(df, bq_schema),
            stage,
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                schema=bq_schema,
            ),
        ).result()
        try:
            self.client.query(
                build_merge_sql(
                    target=str(ref), source=stage, schema=schema, keys=keys,
                )
            ).result()
        finally:
            self.client.query(f"DROP TABLE IF EXISTS `{stage}`").result()
        log.info("merged %d rows into %s", len(df), ref)
        return len(df)

    def write_season(
        self,
        ref: TableRef,
        df: pd.DataFrame,
        *,
        season: int,
        schema: list[ColumnSpec],
    ) -> int:
        """DELETE rows for `season`, then append `df`. Returns rows written.

        The DELETE runs even when df is empty: a season that legitimately goes
        from N rows to 0 upstream must not leave stale rows behind. This is the
        idempotency contract for season-chunked tables -- re-running a season
        replaces it wholesale.
        """
        bq_schema = to_bq_schema(schema)
        self.client.query(
            f"DELETE FROM `{ref}` WHERE season = @season",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("season", "INT64", season)
                ],
            ),
        ).result()
        if df.empty:
            log.info("season %s of %s is empty; deleted only", season, ref)
            return 0
        self.client.load_table_from_dataframe(
            coerce_df_for_bq(df, bq_schema),
            str(ref),
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                schema=bq_schema,
            ),
        ).result()
        log.info("wrote %d rows to %s season=%s", len(df), ref, season)
        return len(df)
