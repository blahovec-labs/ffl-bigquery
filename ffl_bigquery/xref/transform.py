"""ff_playerids -> FF_XREF_SCHEMA."""
from __future__ import annotations

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.xref.schema import FF_XREF_KEY, FF_XREF_SCHEMA


def transform_xref(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # A row without the MERGE key cannot be upserted; drop rather than synthesize.
    if FF_XREF_KEY in df.columns:
        df = df.loc[df[FF_XREF_KEY].notna()]
    return align_to_schema(df, FF_XREF_SCHEMA)
