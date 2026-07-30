"""ff_points_weekly: half-PPR + positional rank derived from load_player_stats().

load_player_stats() already ships `fantasy_points` (standard) and
`fantasy_points_ppr`, so this table mostly adds:

- `fantasy_points_half_ppr` = fantasy_points + 0.5 * receptions.
- `position_rank_ppr` = dense rank within (season, week, position) ordered
  by fantasy_points_ppr descending.
- `gsis_id` = player_id. load_player_stats() has no gsis_id column of its
  own; player_id IS the gsis_id under a different name (verified
  ``^00-\\d+$`` against the 2023 feed). This table clusters on gsis_id, so
  it must be populated rather than left to reindex to all-NULL.

`fantasy_points_ppr` is carried through UNCHANGED from upstream rather than
recomputed -- upstream's own column becomes a free correctness oracle a
later `verify` check can assert a recomputed value still matches. Do not
recompute it here.
"""
from __future__ import annotations

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.nflverse.spec import NflverseTableSpec
from ffl_bigquery.partition import SeasonRangePartition
from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec


def _c(name: str, type: str, mode: str, short: str, definition: str,
       tags: list[str] | None = None, gotchas: list[str] | None = None,
       source_field: str = "") -> ColumnSpec:
    return ColumnSpec(
        name=name, type=type, mode=mode, short_description=short,  # type: ignore[arg-type]
        business_definition=definition, semantic_tags=tags or [],
        valid_range=None, valid_values=None, example_value=None,
        gotchas=gotchas or [], source_field=source_field or name,
        deprecated_in_year=None,
    )


FF_POINTS_WEEKLY_SCHEMA: list[ColumnSpec] = [
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season the stat-week belongs to. Stamped from the sync loop's "
       "season argument, not read from the upstream frame, so a chunk's "
       "rows are always self-consistent.", ["identifier", "partition_key"]),
    _c("week", "INT64", "NULLABLE", "NFL week.",
       "Week of the season this row's stats were accumulated in, as "
       "published by load_player_stats()."),
    _c("player_id", "STRING", "NULLABLE", "nflverse player id.",
       "The nflverse player identifier as published by load_player_stats(). "
       "Joins ff_player_xref.", ["identifier", "join_key"]),
    _c("gsis_id", "STRING", "NULLABLE", "GSIS player id.",
       "load_player_stats() does not publish a separate gsis_id column -- "
       "its player_id IS the GSIS id under a different name. Verified "
       "against the live 2023 feed on 2026-07-30: 100% of non-null "
       "player_id values match ^00-\\d+$ (e.g. 00-0023459, 00-0023853, "
       "00-0025565). Set here as a copy of player_id so this table's "
       "clustering key (see POINTS_WEEKLY_SPEC) is populated and joins "
       "ff_player_xref on the canonical name.",
       ["identifier", "join_key"]),
    _c("player_name", "STRING", "NULLABLE", "Player display name.",
       "The player's display name as published by load_player_stats()."),
    _c("position", "STRING", "NULLABLE", "Position abbreviation.",
       "The player's position (e.g. QB/RB/WR/TE), as published by "
       "load_player_stats(). position_rank_ppr is computed within this "
       "column.", ["dimension"]),
    _c("team", "STRING", "NULLABLE", "Team abbreviation.",
       "The team the player was on for this stat-week.", ["dimension"]),
    _c("receptions", "INT64", "NULLABLE", "Receptions in the week.",
       "Total receptions the player recorded in this week. Feeds the "
       "fantasy_points_half_ppr calculation (0.5 * receptions)."),
    _c("fantasy_points_standard", "FLOAT64", "NULLABLE",
       "Standard (non-PPR) fantasy points.",
       "Carried through unchanged from upstream's fantasy_points column -- "
       "standard scoring, no points for receptions.",
       ["metric"], [], "fantasy_points"),
    _c("fantasy_points_half_ppr", "FLOAT64", "NULLABLE",
       "Half-PPR fantasy points.",
       "Computed as fantasy_points_standard + 0.5 * receptions. This is "
       "also exactly the midpoint of fantasy_points_standard and "
       "fantasy_points_ppr -- tested as an independent identity so a "
       "reception-column rename would surface as a disagreement rather "
       "than a silent skew.", ["metric"]),
    _c("fantasy_points_ppr", "FLOAT64", "NULLABLE", "Full-PPR fantasy points.",
       "Carried through UNCHANGED from upstream's fantasy_points_ppr "
       "column -- deliberately NOT recomputed, so upstream's own value "
       "remains a correctness oracle a later `verify` check can compare "
       "a recomputed total against.",
       ["metric"], ["Never recompute this column; carry it through as-is."]),
    _c("position_rank_ppr", "INT64", "NULLABLE",
       "Dense rank within (season, week, position) by fantasy_points_ppr.",
       "Dense rank (ties share a rank, no gaps) of fantasy_points_ppr "
       "descending, computed within each (season, week, position) group. "
       "Rank 1 is the top PPR scorer at that position in that week.",
       ["metric"]),
    INGESTED_AT_SPEC,
]


def derive_points_weekly(player_stats: pd.DataFrame, season: int) -> pd.DataFrame:
    if player_stats.empty:
        return align_to_schema(player_stats, FF_POINTS_WEEKLY_SCHEMA)

    df = player_stats.copy()
    df["season"] = season
    # load_player_stats() has no gsis_id column -- player_id IS the gsis_id
    # under a different name (verified ^00-\d+$ against the 2023 feed; see
    # gsis_id's business_definition above). This is also the table's
    # clustering key, so leaving it unset would ship an all-NULL cluster
    # column.
    df["gsis_id"] = df["player_id"]
    df["fantasy_points_standard"] = df["fantasy_points"]
    # Half-PPR: standard + 0.5 per reception. Also independently verified in
    # the test suite to equal (standard + ppr) / 2 -- see module docstring.
    df["fantasy_points_half_ppr"] = df["fantasy_points"] + 0.5 * df["receptions"]
    # fantasy_points_ppr is carried through as df["fantasy_points_ppr"]
    # unchanged (via align_to_schema's reindex below) -- never recomputed.
    df["position_rank_ppr"] = (
        df.groupby(["season", "week", "position"])["fantasy_points_ppr"]
        .rank(method="dense", ascending=False)
        .astype("Int64")
    )
    return align_to_schema(df, FF_POINTS_WEEKLY_SCHEMA)


def _load(season: int) -> pd.DataFrame:
    import nflreadpy as nfl

    return nfl.load_player_stats(seasons=[season]).to_pandas()


POINTS_WEEKLY_SPEC = NflverseTableSpec(
    name="ff_points_weekly",
    loader=_load,
    schema=FF_POINTS_WEEKLY_SCHEMA,
    partition=SeasonRangePartition(clustering=["week", "position", "gsis_id"]),
    transform=derive_points_weekly,
    min_season=1999,
)
