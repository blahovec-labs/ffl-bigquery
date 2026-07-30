from typing import cast
from unittest.mock import MagicMock

import pandas as pd
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


def test_write_season_deletes_that_season_then_loads():
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": [2020, 2020], "val": [1.0, 2.0]})
    n = BigQueryWriter(client=c).write_season(REF, df, season=2020, schema=SCHEMA)
    assert n == 2
    sql = c.query.call_args[0][0]
    assert "DELETE FROM `p.d.tbl` WHERE season = @season" in sql
    params = c.query.call_args.kwargs["job_config"].query_parameters
    assert params[0].name == "season" and params[0].value == 2020
    assert c.load_table_from_dataframe.called


def test_write_season_uses_write_append_not_truncate():
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": [2020], "val": [1.0]})
    BigQueryWriter(client=c).write_season(REF, df, season=2020, schema=SCHEMA)
    jc = c.load_table_from_dataframe.call_args.kwargs["job_config"]
    assert jc.write_disposition == bigquery.WriteDisposition.WRITE_APPEND


def test_write_season_still_deletes_when_df_is_empty():
    # An upstream season that goes from N rows to 0 must not leave stale rows behind.
    c = MagicMock(spec=bigquery.Client)
    n = BigQueryWriter(client=c).write_season(
        REF, pd.DataFrame(columns=["season", "val"]), season=2020, schema=SCHEMA,
    )
    assert n == 0
    assert "DELETE FROM" in c.query.call_args[0][0]
    assert not c.load_table_from_dataframe.called


def test_write_season_coerces_dtypes_before_load():
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": ["2020"], "val": ["1.5"]})
    BigQueryWriter(client=c).write_season(REF, df, season=2020, schema=SCHEMA)
    loaded = c.load_table_from_dataframe.call_args[0][0]
    assert str(loaded["season"].dtype) == "Int64"
    assert str(loaded["val"].dtype) == "float64"
