"""write_season must never leave a season deleted but not replaced.

It used to run `DELETE ... WHERE season = @season` as its own query and then a
separate load job. A BigQuery load job can't join a transaction, so those were two
commits: a failed load left the whole season gone until the next successful run.
That is the failure that wiped 2026-06-02 from statcast-bigquery (fixed there in
0.5.1).

Contract now: load into a short-lived staging table FIRST, then DELETE + INSERT ...
SELECT in ONE multi-statement transaction, then drop the staging table. A failed
load issues no DELETE at all; a failed swap rolls back.
"""
from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pandas as pd
import pytest
from google.cloud import bigquery

from ffl_bigquery.schema import BqMode, BqType, ColumnSpec
from ffl_bigquery.writer import BigQueryWriter, TableRef


def _spec(name: str, type: str = "STRING", mode: str = "NULLABLE") -> ColumnSpec:
    return ColumnSpec(
        name=name, type=cast(BqType, type), mode=cast(BqMode, mode),
        short_description="x", business_definition="x", semantic_tags=[],
        valid_range=None, valid_values=None, example_value=None, gotchas=[],
        source_field=name, deprecated_in_year=None,
    )


SCHEMA = [_spec("season", "INT64", "REQUIRED"), _spec("val", "FLOAT64")]
REF = TableRef.parse("p.d.tbl")
TARGET = "p.d.tbl"


def _df() -> pd.DataFrame:
    return pd.DataFrame({"season": [2025, 2025], "val": [1.0, 2.0]})


def _sql(client: MagicMock) -> list[str]:
    return [c.args[0] for c in client.query_and_wait.call_args_list] + [
        c.args[0] for c in client.query.call_args_list
    ]


def test_failed_load_deletes_nothing():
    c = MagicMock(spec=bigquery.Client)
    c.load_table_from_dataframe.return_value.result.side_effect = RuntimeError(
        "400 load failed"
    )
    with pytest.raises(RuntimeError):
        BigQueryWriter(client=c).write_season(REF, _df(), season=2025, schema=SCHEMA)
    assert not any("DELETE" in sql for sql in _sql(c))


def test_load_targets_staging_not_the_live_table():
    c = MagicMock(spec=bigquery.Client)
    BigQueryWriter(client=c).write_season(REF, _df(), season=2025, schema=SCHEMA)
    destination = str(c.load_table_from_dataframe.call_args.args[1])
    assert destination != TARGET
    assert "_staging_" in destination


def test_delete_and_insert_commit_in_one_transaction():
    c = MagicMock(spec=bigquery.Client)
    BigQueryWriter(client=c).write_season(REF, _df(), season=2025, schema=SCHEMA)
    swaps = [sql for sql in _sql(c) if "DELETE" in sql]
    assert len(swaps) == 1
    sql = swaps[0]
    assert sql.lstrip().startswith("BEGIN TRANSACTION")
    assert sql.rstrip().endswith("COMMIT TRANSACTION;")
    assert "DELETE FROM `p.d.tbl` WHERE season = @season" in sql
    assert sql.index("DELETE") < sql.index("INSERT INTO")
    staging = str(c.load_table_from_dataframe.call_args.args[1])
    assert f"FROM `{staging}`" in sql
    params = c.query_and_wait.call_args.kwargs["job_config"].query_parameters
    assert params[0].name == "season" and params[0].value == 2025


def test_staging_table_dropped_even_when_swap_fails():
    c = MagicMock(spec=bigquery.Client)
    c.query_and_wait.side_effect = RuntimeError("transaction aborted")
    with pytest.raises(RuntimeError):
        BigQueryWriter(client=c).write_season(REF, _df(), season=2025, schema=SCHEMA)
    staging = str(c.load_table_from_dataframe.call_args.args[1])
    dropped = [str(call.args[0]) for call in c.delete_table.call_args_list]
    assert staging in dropped


def test_empty_season_is_a_transactional_delete_with_no_staging():
    c = MagicMock(spec=bigquery.Client)
    BigQueryWriter(client=c).write_season(
        REF, pd.DataFrame(columns=["season", "val"]), season=2025, schema=SCHEMA,
    )
    assert not c.load_table_from_dataframe.called
    assert not c.create_table.called
    swaps = [sql for sql in _sql(c) if "DELETE" in sql]
    assert len(swaps) == 1
    assert swaps[0].lstrip().startswith("BEGIN TRANSACTION")
