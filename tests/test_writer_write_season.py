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


def test_write_season_drops_rows_whose_season_disagrees_with_the_chunk(caplog):
    # A boundary-derivation row (e.g. depth_charts' dt->season rule) can land
    # in the wrong chunk's frame. write_season must not trust it -- it would
    # survive this chunk's DELETE, land in the wrong partition, and later be
    # destroyed by that season's own DELETE, making the table depend on chunk
    # ordering. The offending row must be dropped, not written.
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": [2025, 2025, 2026], "val": [1.0, 2.0, 3.0]})
    with caplog.at_level("WARNING"):
        n = BigQueryWriter(client=c).write_season(REF, df, season=2025, schema=SCHEMA)
    assert n == 2
    loaded = c.load_table_from_dataframe.call_args[0][0]
    assert list(loaded["season"]) == [2025, 2025]
    assert "2026" in caplog.text


def test_write_season_drops_null_season_rows():
    # A null season can't be placed in any partition -- it must be dropped and
    # counted, not written or silently left in the frame.
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": [2025, pd.NA], "val": [1.0, 2.0]})
    n = BigQueryWriter(client=c).write_season(REF, df, season=2025, schema=SCHEMA)
    assert n == 1
    loaded = c.load_table_from_dataframe.call_args[0][0]
    assert list(loaded["season"]) == [2025]


def test_write_season_with_no_season_column_passes_through_untouched():
    # Some callers legitimately have no season column at all (per the writer's
    # own docstring) -- this must not raise or filter anything.
    c = MagicMock(spec=bigquery.Client)
    schema = [_spec("val", "FLOAT64")]
    df = pd.DataFrame({"val": [1.0, 2.0, 3.0]})
    n = BigQueryWriter(client=c).write_season(REF, df, season=2025, schema=schema)
    assert n == 3
    loaded = c.load_table_from_dataframe.call_args[0][0]
    assert len(loaded) == 3


def test_write_season_deletes_even_when_filtering_empties_the_frame():
    # Every row disagrees with the chunk season -- filtering empties the
    # frame, but the DELETE must still have fired (idempotency contract: a
    # season going to 0 rows upstream must not leave stale rows behind), and
    # no load should be attempted on an empty frame.
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": [2026, 2026], "val": [1.0, 2.0]})
    n = BigQueryWriter(client=c).write_season(REF, df, season=2025, schema=SCHEMA)
    assert n == 0
    assert "DELETE FROM" in c.query.call_args[0][0]
    assert not c.load_table_from_dataframe.called


def test_write_season_return_value_is_rows_actually_written_not_rows_passed():
    # The return value must reflect the post-filter count, not len(df).
    c = MagicMock(spec=bigquery.Client)
    df = pd.DataFrame({"season": [2025, 2026, 2026, pd.NA], "val": [1.0, 2.0, 3.0, 4.0]})
    n = BigQueryWriter(client=c).write_season(REF, df, season=2025, schema=SCHEMA)
    assert n == 1
    assert n != len(df)
