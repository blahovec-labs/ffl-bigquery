from typing import cast
from unittest.mock import MagicMock

import pandas as pd
import pytest
from google.cloud import bigquery

from ffl_bigquery.partition import TimePartition
from ffl_bigquery.schema import BqMode, BqType, ColumnSpec
from ffl_bigquery.writer import (
    BigQueryWriter,
    TableRef,
    build_merge_sql,
    coerce_df_for_bq,
)


def _spec(name: str, type: str = "STRING", mode: str = "NULLABLE") -> ColumnSpec:
    return ColumnSpec(
        name=name, type=cast(BqType, type), mode=cast(BqMode, mode),
        short_description="x", business_definition="x", semantic_tags=[],
        valid_range=None, valid_values=None, example_value=None, gotchas=[],
        source_field=name, deprecated_in_year=None,
    )


def test_table_ref_roundtrip():
    ref = TableRef.parse("proj.ds.ff_adp")
    assert (ref.project, ref.dataset, ref.table) == ("proj", "ds", "ff_adp")
    assert str(ref) == "proj.ds.ff_adp"


def test_table_ref_rejects_bad_input():
    with pytest.raises(ValueError, match="expected project.dataset.table"):
        TableRef.parse("ds.ff_adp")


def test_merge_sql_uses_all_composite_keys_and_excludes_them_from_update():
    schema = [
        _spec("source", mode="REQUIRED"), _spec("season", "INT64", "REQUIRED"),
        _spec("snapshot_date", "DATE", "REQUIRED"), _spec("adp", "FLOAT64"),
    ]
    sql = build_merge_sql(
        target="p.d.ff_adp", source="p.d._stage", schema=schema,
        keys=["source", "season", "snapshot_date"],
    )
    assert "ON t.source = s.source AND t.season = s.season " \
           "AND t.snapshot_date = s.snapshot_date" in sql
    update_half = sql.split("WHEN NOT MATCHED")[0].split("UPDATE SET")[1]
    assert "adp = s.adp" in update_half
    for k in ("source", "season", "snapshot_date"):
        assert f"{k} = s.{k}" not in update_half
    insert_half = sql.split("WHEN NOT MATCHED")[1]
    assert "source" in insert_half and "snapshot_date" in insert_half


def test_coerce_casts_int64_from_float_and_preserves_na():
    schema = [bigquery.SchemaField("teams", "INT64")]
    df = coerce_df_for_bq(pd.DataFrame({"teams": [12.0, None]}), schema)
    assert str(df["teams"].dtype) == "Int64"
    assert df["teams"].iloc[0] == 12
    assert pd.isna(df["teams"].iloc[1])


def test_coerce_casts_date_strings_to_dates():
    schema = [bigquery.SchemaField("snapshot_date", "DATE")]
    df = coerce_df_for_bq(pd.DataFrame({"snapshot_date": ["2026-07-29"]}), schema)
    assert str(df["snapshot_date"].iloc[0]) == "2026-07-29"


def test_create_table_if_missing_sets_day_partition_and_clustering():
    c = MagicMock(spec=bigquery.Client)
    c.get_table.side_effect = __import__(
        "google.cloud.exceptions", fromlist=["NotFound"]
    ).NotFound("nope")
    BigQueryWriter(client=c).create_table_if_missing(
        TableRef.parse("p.d.ff_adp"),
        [bigquery.SchemaField("snapshot_date", "DATE")],
        TimePartition(field="snapshot_date", clustering=["season", "source"]),
    )
    table = c.create_table.call_args[0][0]
    assert table.time_partitioning.field == "snapshot_date"
    assert table.clustering_fields == ["season", "source"]


def test_merge_rows_stages_merges_then_drops():
    c = MagicMock(spec=bigquery.Client)
    schema = [_spec("source", mode="REQUIRED"), _spec("adp", "FLOAT64")]
    df = pd.DataFrame({"source": ["ffc"], "adp": [1.7]})
    n = BigQueryWriter(client=c).merge_rows(
        ref=TableRef.parse("p.d.ff_adp"), df=df, schema=schema, keys=["source"],
    )
    assert n == 1
    assert "MERGE" in c.query.call_args_list[0][0][0]
    assert "DROP TABLE" in c.query.call_args_list[1][0][0]
    jc = c.load_table_from_dataframe.call_args.kwargs["job_config"]
    assert jc.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE


def test_merge_rows_on_empty_df_is_a_noop():
    c = MagicMock(spec=bigquery.Client)
    n = BigQueryWriter(client=c).merge_rows(
        ref=TableRef.parse("p.d.ff_adp"), df=pd.DataFrame(),
        schema=[_spec("source")], keys=["source"],
    )
    assert n == 0
    assert not c.query.called
    assert not c.load_table_from_dataframe.called
