"""ff_rankings: FantasyPros ECR, current snapshot only.

Unlike every other table here this is NOT season-chunked -- the loader returns
"as of now" with a scrape_date and no season column, so it is snapshot-grain
like ff_adp: MERGE on (scrape_date, ecr_type, id) so re-running the same day is
a no-op, and it becomes a time series only if captured repeatedly.
"""
from __future__ import annotations

import pandas as pd

from ffl_bigquery._schema_samples import read_sample
from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.partition import TimePartition
from ffl_bigquery.schema import to_bq_schema
from ffl_bigquery.schema_gen import specs_from_frame
from ffl_bigquery.writer import BigQueryWriter, TableRef

FF_RANKINGS_KEYS = ["scrape_date", "ecr_type", "id"]
FF_RANKINGS_PARTITION = TimePartition(
    field="scrape_date", clustering=["ecr_type", "pos"]
)
FF_RANKINGS_SCHEMA = specs_from_frame(
    read_sample("ff_rankings"), table="ff_rankings",
    type_overrides={"scrape_date": "DATE"},
    required=("scrape_date", "ecr_type", "id"),
    enrichment={
        "ecr": {"semantic_tags": ["metric"],
                "business_definition": "Expert consensus rank."},
        "id": {"semantic_tags": ["identifier", "primary_key"],
               "business_definition": "FantasyPros player id; joins "
                                      "ff_player_xref.fantasypros_id."},
    },
)


def transform_rankings(df: pd.DataFrame) -> pd.DataFrame:
    return align_to_schema(df, FF_RANKINGS_SCHEMA)


def sync_ff_rankings(bq_client, *, ref: TableRef, loader=None) -> int:
    def _default() -> pd.DataFrame:
        import nflreadpy as nfl

        return nfl.load_ff_rankings().to_pandas()

    writer = BigQueryWriter(client=bq_client)
    writer.create_table_if_missing(
        ref, to_bq_schema(FF_RANKINGS_SCHEMA), FF_RANKINGS_PARTITION
    )
    df = transform_rankings((loader or _default)())
    return writer.merge_rows(
        ref=ref, df=df, schema=FF_RANKINGS_SCHEMA, keys=FF_RANKINGS_KEYS
    )
