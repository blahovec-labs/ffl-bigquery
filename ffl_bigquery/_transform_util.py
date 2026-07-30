"""Shared transform helper: reindex to schema columns + stamp ingested_at."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from ffl_bigquery.schema import ColumnSpec, spec_names


def align_to_schema(
    df: pd.DataFrame, schema: list[ColumnSpec], *, stamp: bool = True
) -> pd.DataFrame:
    df = df.copy()
    if stamp:
        df["ingested_at"] = datetime.now(UTC)
    # Reindex adds missing NULLABLE columns as NA and drops columns not in schema.
    return df.reindex(columns=spec_names(schema))
