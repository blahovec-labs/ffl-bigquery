"""Descriptor for one season-chunked nflverse table.

Seven tables in this library are the same shape -- load a frame for season S,
align it to a schema, replace that season -- so they share one driver and differ
only in this descriptor.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from ffl_bigquery.schema import ColumnSpec
from ffl_bigquery.writer import Partition


@dataclass(frozen=True)
class NflverseTableSpec:
    name: str
    loader: Callable[[int], pd.DataFrame]
    schema: list[ColumnSpec]
    partition: Partition
    transform: Callable[[pd.DataFrame, int], pd.DataFrame]
    min_season: int
