"""Generate ColumnSpecs from an upstream nflverse frame plus a small overlay.

ff_adp's specs are hand-authored because its source is third-party JSON with no
published data dictionary. These tables are the opposite case: nflverse frames
with known dtypes, and wide (ff_opportunity is 159 columns). Hand-authoring
~400 specs would be error-prone and would rot the moment upstream adds a
column, so infer from dtypes and override only where a measured landmine
demands it.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ffl_bigquery.schema import INGESTED_AT_SPEC, BqType, ColumnSpec


def _bq_type(dtype: Any) -> BqType:
    name = str(dtype).lower()
    if name.startswith(("int", "uint")):
        return "INT64"
    if name.startswith("float"):
        return "FLOAT64"
    if name in ("bool", "boolean"):
        return "BOOL"
    if name.startswith("datetime"):
        return "TIMESTAMP"
    if name == "date":
        return "DATE"
    return "STRING"


def specs_from_frame(
    df: pd.DataFrame,
    *,
    table: str,
    enrichment: dict[str, dict[str, Any]] | None = None,
    type_overrides: dict[str, str] | None = None,
    required: tuple[str, ...] = (),
    drop: tuple[str, ...] = (),
) -> list[ColumnSpec]:
    enrichment = enrichment or {}
    type_overrides = type_overrides or {}
    cols = set(df.columns)

    # A typo'd column in an overlay must fail loudly, not vanish.
    for label, keys in (
        ("enrichment", enrichment.keys()),
        ("type_overrides", type_overrides.keys()),
        ("required", required),
        ("drop", drop),
    ):
        unknown = [k for k in keys if k not in cols]
        if unknown:
            raise ValueError(f"{label} keys not in frame for {table}: {unknown}")

    specs: list[ColumnSpec] = []
    for col in df.columns:
        if col in drop:
            continue
        e = enrichment.get(col, {})
        specs.append(
            ColumnSpec(
                name=col,
                type=e.get("type", type_overrides.get(col, _bq_type(df[col].dtype))),
                mode=e.get("mode", "REQUIRED" if col in required else "NULLABLE"),
                short_description=e.get(
                    "short_description", f"{col} from the nflverse {table} feed."
                ),
                business_definition=e.get(
                    "business_definition",
                    f"Column `{col}` as published in the nflverse {table} feed. "
                    "Carried through without transformation.",
                ),
                semantic_tags=e.get("semantic_tags", []),
                valid_range=e.get("valid_range"),
                valid_values=e.get("valid_values"),
                example_value=e.get("example_value"),
                gotchas=e.get("gotchas", []),
                source_field=e.get("source_field", col),
                deprecated_in_year=e.get("deprecated_in_year"),
            )
        )
    specs.append(INGESTED_AT_SPEC)
    return specs
