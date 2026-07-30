"""sync-xref: load ff_playerids and MERGE-upsert on mfl_id."""
from __future__ import annotations

import argparse
import logging
from collections.abc import Callable

import pandas as pd

from ffl_bigquery.schema import to_bq_schema
from ffl_bigquery.writer import BigQueryWriter, TableRef
from ffl_bigquery.xref.schema import FF_XREF_KEY, FF_XREF_SCHEMA
from ffl_bigquery.xref.transform import transform_xref

log = logging.getLogger(__name__)


def _default_loader() -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_ff_playerids().to_pandas()


def run_sync_xref(
    ns: argparse.Namespace,
    *,
    bq_client,
    load_playerids: Callable[[], pd.DataFrame] = _default_loader,
) -> int:
    ref = TableRef.parse(ns.xref_table)
    if ns.dry_run:
        print(f"[dry-run] would upsert ff_player_xref into {ref}")
        return 0
    writer = BigQueryWriter(client=bq_client)
    writer.create_table_if_missing(ref, to_bq_schema(FF_XREF_SCHEMA), None)
    df = transform_xref(load_playerids())
    n = writer.merge_rows(ref=ref, df=df, schema=FF_XREF_SCHEMA, keys=[FF_XREF_KEY])
    log.info("ff_player_xref upsert: %d rows", n)
    return 0
