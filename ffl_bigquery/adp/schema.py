"""ff_adp: one row per player per ADP snapshot per league configuration.

Snapshot grain is not an aesthetic choice. FFC's start_date/end_date query
parameters are silently ignored (verified 2026-07-29: requests for 2026-06-01..15
and 2026-07-01..15 both returned the identical current window), so intra-preseason
ADP drift cannot be backfilled — it exists only if captured forward, daily.
"""
from __future__ import annotations

from ffl_bigquery.partition import TimePartition
from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec

ADP_SOURCES: tuple[str, ...] = ("ffc", "mfl")
FFC_FORMATS: tuple[str, ...] = (
    "standard", "ppr", "half-ppr", "2qb", "dynasty", "rookie",
)

FF_ADP_KEYS: list[str] = [
    "source", "season", "scoring_format", "teams", "snapshot_date", "source_player_id",
]

FF_ADP_PARTITION = TimePartition(
    field="snapshot_date",
    clustering=["season", "source", "scoring_format", "teams"],
)


def _spec(
    name: str,
    type: str,
    mode: str,
    short: str,
    definition: str,
    *,
    tags: list[str] | None = None,
    valid_range: tuple[float, float] | None = None,
    valid_values: list[str] | None = None,
    example: object | None = None,
    gotchas: list[str] | None = None,
    source_field: str = "",
) -> ColumnSpec:
    return ColumnSpec(
        name=name, type=type, mode=mode, short_description=short,  # type: ignore[arg-type]
        business_definition=definition, semantic_tags=tags or [],
        valid_range=valid_range, valid_values=valid_values, example_value=example,
        gotchas=gotchas or [], source_field=source_field or name,
        deprecated_in_year=None,
    )


FF_ADP_SCHEMA: list[ColumnSpec] = [
    _spec("source", "STRING", "REQUIRED",
          "Which ADP market this row came from.",
          "Provider of the ADP observation. 'ffc' = Fantasy Football Calculator, "
          "'mfl' = MyFantasyLeague. The two are independent markets and their ADP "
          "values are not interchangeable.",
          tags=["identifier", "primary_key", "dimension"],
          valid_values=["ffc", "mfl"], example="ffc"),
    _spec("season", "INT64", "REQUIRED",
          "NFL season the draft was for.",
          "Season year of the fantasy draft. FFC has data from 2010; MFL from 2012. "
          "Earlier years return empty responses.",
          tags=["identifier", "primary_key"], valid_range=(2010.0, 2100.0), example=2026),
    _spec("scoring_format", "STRING", "REQUIRED",
          "Scoring system of the drafts aggregated into this ADP.",
          "League scoring format. FFC exposes standard/ppr/half-ppr/2qb/dynasty/rookie, "
          "though half-ppr history is shallow. For MFL rows this is derived from the "
          "IS_PPR request parameter.",
          tags=["identifier", "primary_key", "dimension"], example="ppr",
          gotchas=["half-ppr returned no data for 2010 or 2015; treat pre-recent "
                   "half-ppr as unavailable rather than zero."]),
    _spec("teams", "INT64", "REQUIRED",
          "League size the ADP was aggregated over.",
          "Number of teams in the drafts aggregated into this ADP value (FFC 'teams' "
          "parameter, MFL 'FCOUNT'). ADP is only comparable within a league size.",
          tags=["identifier", "primary_key", "dimension"],
          valid_range=(4.0, 32.0), example=12),
    _spec("snapshot_date", "DATE", "REQUIRED",
          "Date this ADP observation was captured.",
          "UTC date the sync ran. This is the drift axis: the same (source, season, "
          "format, teams, player) appears once per capture day, so day-over-day "
          "movement is queryable. Not the date the drafts occurred.",
          tags=["identifier", "primary_key", "partition_key"], example="2026-07-29",
          gotchas=["Rows only exist for days the cron actually ran. Gaps are real "
                   "gaps, not zero-movement days."]),
    _spec("source_player_id", "STRING", "REQUIRED",
          "The provider's own player identifier.",
          "Player id as issued by the source. For 'mfl' this is the MFL id and joins "
          "ff_player_xref.mfl_id directly. For 'ffc' this is FFC's internal id, which "
          "appears in NO nflverse id system — FFC rows resolve by name instead.",
          tags=["identifier", "primary_key"], example="925",
          gotchas=["FFC ids are not portable. Never attempt to join them to an "
                   "nflverse id column."]),
    _spec("gsis_id", "STRING", "NULLABLE",
          "Resolved nflverse player id, if resolution succeeded.",
          "Canonical nflverse gsis_id, resolved via ff_player_xref. NULL when "
          "resolution failed. Unresolved rows are retained deliberately — dropping "
          "them would silently thin the market.",
          tags=["identifier", "join_key"], example="00-0033873",
          gotchas=["Always NULL-guard before joining to nfl_plays or "
                   "ff_points_weekly, or unresolved players vanish from results."]),
    _spec("player_name", "STRING", "NULLABLE",
          "Player name as published by the source.",
          "Source-provided display name. Present for FFC; absent for MFL, whose ADP "
          "export returns ids only.",
          tags=["dimension"], example="Adrian Peterson"),
    _spec("position", "STRING", "NULLABLE",
          "Player position as published by the source.",
          "Source-provided position. Present for FFC; absent for MFL.",
          tags=["dimension"], example="RB"),
    _spec("team", "STRING", "NULLABLE",
          "NFL team as published by the source.",
          "Source-provided team abbreviation at time of capture. Present for FFC; "
          "absent for MFL. May disagree with nflverse abbreviations.",
          tags=["dimension"], example="MIN"),
    _spec("adp", "FLOAT64", "REQUIRED",
          "Average draft position.",
          "Mean overall pick number at which this player was selected across the "
          "aggregated drafts. Lower is earlier.",
          tags=["metric"], valid_range=(1.0, 500.0), example=1.8),
    _spec("adp_formatted", "STRING", "NULLABLE",
          "ADP rendered as round.pick.",
          "FFC's human-readable round.pick form of adp, e.g. '1.02'. Provided by FFC "
          "only; NULL for MFL.",
          tags=["metric"], example="1.02"),
    _spec("adp_stdev", "FLOAT64", "NULLABLE",
          "Standard deviation of pick number.",
          "Dispersion of the pick numbers behind adp — a consensus measure. High "
          "stdev means drafters disagreed. FFC only; NULL for MFL.",
          tags=["metric"], valid_range=(0.0, 200.0), example=1.0),
    _spec("adp_earliest_pick", "INT64", "NULLABLE",
          "Earliest (smallest) pick number observed.",
          "The soonest this player was taken in any aggregated draft. Maps from FFC's "
          "'high' field and MFL's 'minPick'.",
          tags=["metric"], valid_range=(1.0, 500.0), example=1,
          source_field="high|minPick",
          gotchas=["FFC calls this 'high' because it is the highest draft position — "
                   "which is the LOWEST number. Renamed here to prevent inverted "
                   "comparisons."]),
    _spec("adp_latest_pick", "INT64", "NULLABLE",
          "Latest (largest) pick number observed.",
          "The latest this player was taken in any aggregated draft. Maps from FFC's "
          "'low' field and MFL's 'maxPick'.",
          tags=["metric"], valid_range=(1.0, 500.0), example=5,
          source_field="low|maxPick"),
    _spec("times_drafted", "INT64", "NULLABLE",
          "Drafts this player was selected in.",
          "Count of aggregated drafts in which this player was taken. FFC "
          "'times_drafted'; MFL 'draftsSelectedIn'. The denominator for "
          "draft_selected_pct.",
          tags=["metric"], example=329, source_field="times_drafted|draftsSelectedIn"),
    _spec("draft_selected_pct", "FLOAT64", "NULLABLE",
          "Share of drafts the player was selected in.",
          "MFL 'draftSelPct' as a percentage (0-100). NULL for FFC.",
          tags=["metric"], valid_range=(0.0, 100.0), example=13.0,
          source_field="draftSelPct"),
    _spec("source_rank", "INT64", "NULLABLE",
          "The provider's own ADP rank ordering.",
          "Rank by adp as published by the source. MFL 'rank'; NULL for FFC, where "
          "row order conveys rank.",
          tags=["metric"], example=1, source_field="rank"),
    _spec("total_drafts", "INT64", "NULLABLE",
          "Drafts in the aggregation pool.",
          "Total drafts the provider aggregated for this request. FFC "
          "meta.total_drafts; MFL adp.totalDrafts. A sample-size caveat: a 2026 "
          "July snapshot may rest on far fewer drafts than a September one.",
          tags=["metric", "quality"], example=3673),
    _spec("bye", "INT64", "NULLABLE",
          "Player's bye week that season.",
          "Bye week as published by FFC. NULL for MFL.",
          tags=["dimension"], valid_range=(1.0, 18.0), example=6),
    _spec("window_start_date", "DATE", "NULLABLE",
          "First draft date in the provider's aggregation window.",
          "FFC meta.start_date — the earliest draft included. NULL for MFL, which "
          "does not publish a window.",
          tags=["quality"], example="2026-07-22",
          gotchas=["This is the provider's window, NOT a request parameter. FFC "
                   "ignores start_date/end_date on input."]),
    _spec("window_end_date", "DATE", "NULLABLE",
          "Last draft date in the provider's aggregation window.",
          "FFC meta.end_date — the latest draft included. NULL for MFL.",
          tags=["quality"], example="2026-07-29"),
    _spec("is_keeper", "BOOL", "NULLABLE",
          "Whether keeper leagues were included.",
          "MFL IS_KEEPER request slicer. NULL for FFC, which does not expose it.",
          tags=["dimension"], example=False),
    _spec("is_mock", "BOOL", "NULLABLE",
          "Whether mock drafts were included.",
          "MFL IS_MOCK request slicer. NULL for FFC. Mock-only ADP rests on very "
          "few drafts (measured: 12 for 2025) and should be read with care.",
          tags=["dimension"], example=False),
    INGESTED_AT_SPEC,
]
