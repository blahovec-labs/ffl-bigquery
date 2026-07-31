"""nfl_coordinators: offensive/defensive coordinator by (season, team), Wikipedia-sourced.

Opt-in and honestly caveated. Pro Football Reference -- the source that would
otherwise be authoritative for this -- returns HTTP 403 to automated fetches and
is not usable at all. Wikipedia team-season infobox pages are the only source this
library found that's actually fetchable, and even there the fields are far from
complete. MEASURED FILL RATE (full backfill, 16 seasons x 32 teams x 2 roles =
1,024 possible rows): 473/1,024 = 46.2%. An earlier, smaller sample (24 team-
seasons: 6 teams x 2005/2012/2019/2024) had measured off_coach/def_coach at
9/24 = 37% each and all six 2005 team-seasons with neither field populated --
that sample figure undershot the real backfill and is kept below only as
color, not the headline number.

This table therefore never runs as part of `sync-nflverse` -- only the
explicit `sync-coordinators` CLI command -- and every row carries its own
provenance (source page title, parse confidence, retrieved_at) rather than
presenting silence as completeness. Absence of a row for a (season, team, role)
does NOT mean the team had no coordinator that season; it means Wikipedia's
infobox didn't happen to publish one.
"""
from __future__ import annotations

from ffl_bigquery.partition import ClusterOnly
from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec

NFL_COORDINATORS_KEYS: list[str] = ["season", "team", "role"]

# Small table (a few hundred rows even at full backfill) -- clustering without
# time/range partitioning, per ffl_bigquery.partition.ClusterOnly.
NFL_COORDINATORS_PARTITION = ClusterOnly(clustering=["season", "team"])


def _c(name: str, type: str, mode: str, short: str, definition: str,
       tags: list[str] | None = None, valid_values: list[str] | None = None,
       valid_range: tuple[float, float] | None = None,
       gotchas: list[str] | None = None, source_field: str = "") -> ColumnSpec:
    return ColumnSpec(
        name=name, type=type, mode=mode, short_description=short,  # type: ignore[arg-type]
        business_definition=definition, semantic_tags=tags or [],
        valid_range=valid_range, valid_values=valid_values, example_value=None,
        gotchas=gotchas or [], source_field=source_field or name,
        deprecated_in_year=None,
    )


NFL_COORDINATORS_SCHEMA: list[ColumnSpec] = [
    _c("season", "INT64", "REQUIRED", "NFL season.",
       "Season this coordinator assignment applies to. Grain is one row per "
       "(season, team, role) -- a season-level summary, not a per-game record "
       "like nfl_coaches.head_coach, so a mid-season coordinator change is not "
       "representable here.", ["identifier", "partition_key"]),
    _c("team", "STRING", "REQUIRED", "Team abbreviation.",
       "The team this coordinator worked for in `season`. Resolved from a "
       "fixed team-abbreviation -> Wikipedia franchise-name table at fetch "
       "time, not read back out of the page content.",
       ["identifier", "dimension"],
       gotchas=["The abbreviation -> Wikipedia page-title mapping uses each "
                "team's CURRENT franchise name. A relocated/renamed franchise "
                "(e.g. the Raiders, Chargers, Rams, Commanders) will 404 for "
                "the seasons before its current name, which surfaces as a "
                "missing page (no rows, no exception) -- one more reason "
                "coverage skews toward recent seasons."]),
    _c("role", "STRING", "REQUIRED", "Coordinator role: OC or DC.",
       "Which coordinator seat this row fills. Only offensive and defensive "
       "coordinators are tracked -- special teams coordinator is not a "
       "consistently-published infobox field.",
       ["identifier", "dimension"], valid_values=["OC", "DC"]),
    _c("name", "STRING", "NULLABLE", "Coordinator's name.",
       "Coordinator name parsed from the Wikipedia infobox `off_coach` (role="
       "OC) / `def_coach` (role=DC) field, with [[wiki links]] and "
       "{{small|...}} annotation templates stripped. A row only exists when "
       "the field was present, non-blank, and parsed to a clean value -- a "
       "blank or garbled infobox field produces no row, never an empty-string "
       "or wrong name.",
       ["dimension"], gotchas=[
           "MEASURED FILL RATE: 46.2% (473 of 1,024 possible rows -- 16 "
           "seasons x 32 teams x 2 roles, full backfill) -- a genuine "
           "data-availability ceiling, not an ingestion bug. An earlier, "
           "smaller sample (24 team-seasons: 6 teams x "
           "2005/2012/2019/2024) measured 37% and all six 2005 "
           "team-seasons sampled had neither coordinator field populated "
           "at all; expect older seasons to be systematically worse than "
           "the 46.2% headline. Never treat a missing (season, team, role) "
           "row as 'no coordinator that year' -- it means Wikipedia "
           "didn't publish one.",
       ], source_field="off_coach|def_coach"),
    _c("source", "STRING", "NULLABLE", "Wikipedia page title this row was parsed from.",
       "The exact Wikipedia page title (e.g. '2024 Green Bay Packers season') "
       "fetched to produce this row. Provenance only -- the live page's "
       "content can change after ingestion; this is not a stable citation."),
    _c("confidence", "FLOAT64", "NULLABLE", "Parse confidence, 0.0-1.0.",
       "1.0 for a clean single wikilink value (e.g. the raw infobox field was "
       "exactly '[[Jeff Hafley]]', nothing else). Lower (0.75) when the raw "
       "value needed cleanup beyond a bare single link -- a piped link, "
       "trailing template annotation like '{{small|(interim)}}', or other "
       "surrounding text -- before it was usable as a name.",
       valid_range=(0.0, 1.0)),
    _c("retrieved_at", "TIMESTAMP", "REQUIRED",
       "UTC timestamp this (season, team) page was fetched.",
       "When the Wikipedia page was retrieved. Distinct from ingested_at: "
       "retrieved_at is fetch time, ingested_at is BigQuery write time -- they "
       "can differ if a row is re-merged from a re-run without a fresh fetch."),
    INGESTED_AT_SPEC,
]
