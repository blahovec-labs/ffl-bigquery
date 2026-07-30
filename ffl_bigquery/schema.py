"""ColumnSpec: single source of truth for one BigQuery column.

Hand-authored per table (unlike nfl-bigquery, which seeds specs from nflverse
dictionary CSVs) because ff_adp originates in third-party JSON with no published
data dictionary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from google.cloud import bigquery

SCHEMA_VERSION = "0.1.0"

BqType = Literal["INT64", "FLOAT64", "STRING", "BOOL", "DATE", "TIMESTAMP", "NUMERIC"]
BqMode = Literal["REQUIRED", "NULLABLE", "REPEATED"]

_VALID_TYPES = set(BqType.__args__)  # type: ignore[attr-defined]
_VALID_MODES = set(BqMode.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: BqType
    mode: BqMode
    short_description: str
    business_definition: str
    semantic_tags: list[str]
    valid_range: tuple[float, float] | None
    valid_values: list[str] | None
    example_value: object | None
    gotchas: list[str]
    source_field: str
    deprecated_in_year: int | None

    def __post_init__(self) -> None:
        if self.type not in _VALID_TYPES:
            raise ValueError(f"{self.name}: invalid type {self.type!r}")
        if self.mode not in _VALID_MODES:
            raise ValueError(f"{self.name}: invalid mode {self.mode!r}")
        if not self.business_definition.strip():
            raise ValueError(f"{self.name}: business_definition required")


def to_bq_schema(specs: list[ColumnSpec]) -> list[bigquery.SchemaField]:
    return [
        bigquery.SchemaField(
            name=s.name, field_type=s.type, mode=s.mode,
            description=s.short_description[:1024],
        )
        for s in specs
    ]


def spec_names(specs: list[ColumnSpec]) -> list[str]:
    return [s.name for s in specs]


INGESTED_AT_SPEC = ColumnSpec(
    name="ingested_at", type="TIMESTAMP", mode="REQUIRED",
    short_description="UTC timestamp this row was written to BigQuery.",
    business_definition="Ingestion timestamp set by ffl-bigquery at write time.",
    semantic_tags=["lineage"], valid_range=None, valid_values=None,
    example_value=None, gotchas=[], source_field="(synthetic)",
    deprecated_in_year=None,
)
