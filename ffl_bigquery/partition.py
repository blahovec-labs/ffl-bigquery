"""Partitioning descriptors understood by BigQueryWriter.create_table_if_missing."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimePartition:
    """DAY time-partitioning on a DATE column, plus clustering."""

    field: str
    clustering: list[str]


@dataclass(frozen=True)
class SeasonRangePartition:
    """Integer RANGE partitioning on the `season` column, plus clustering."""

    clustering: list[str] = field(default_factory=list)
    field_name: str = "season"
    start: int = 1999
    end: int = 2100
    interval: int = 1

    @property
    def field(self) -> str:
        return self.field_name
