"""Partitioning descriptors understood by BigQueryWriter.create_table_if_missing."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimePartition:
    """DAY time-partitioning on a DATE column, plus clustering."""

    field: str
    clustering: list[str]


@dataclass(frozen=True)
class ClusterOnly:
    """Clustering with no partitioning at all.

    For tables too small to benefit from partition pruning (nfl_coordinators is a
    few hundred rows at most -- one per sampled (season, team, role)), BigQuery
    still allows clustering without a partition column. Plain `None` already means
    "no partitioning, no clustering"; this is the third case those two don't cover.
    """

    clustering: list[str] = field(default_factory=list)


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
