"""ff_player_xref: the cross-platform player id bridge.

Sourced from nflverse load_ff_playerids(). This is the only path from a fantasy
provider's id space to nflverse's gsis_id, and therefore to nfl_plays.

Two measured caveats drive the schema:
  * mfl_id is Int64 and unique across all 12,468 rows -> valid MERGE key
  * gsis_id is 37.9% NULL overall and only 66.9% filled among rostered
    QB/RB/WR/TE/K, because the feed carries college prospects with no NFL id.
    Resolution rates are bounded by this and cannot approach 100%.
"""
from __future__ import annotations

from ffl_bigquery.schema import INGESTED_AT_SPEC, ColumnSpec

FF_XREF_KEY = "mfl_id"

XREF_ID_COLUMNS: list[str] = [
    "mfl_id", "sportradar_id", "fantasypros_id", "gsis_id", "pff_id", "sleeper_id",
    "nfl_id", "espn_id", "yahoo_id", "fleaflicker_id", "cbs_id", "pfr_id",
    "cfbref_id", "rotowire_id", "rotoworld_id", "ktc_id", "stats_id",
    "stats_global_id", "fantasy_data_id", "swish_id",
]

# Id systems nflverse types as Int64; the rest arrive as strings.
_INT_ID_COLUMNS = {
    "mfl_id", "sleeper_id", "espn_id", "fleaflicker_id", "cbs_id",
    "rotowire_id", "rotoworld_id", "ktc_id", "stats_id", "fantasy_data_id",
    "swish_id", "nfl_id",
}


def _id_spec(name: str) -> ColumnSpec:
    is_key = name == FF_XREF_KEY
    return ColumnSpec(
        name=name,
        type="INT64" if name in _INT_ID_COLUMNS else "STRING",
        mode="REQUIRED" if is_key else "NULLABLE",
        short_description=f"{name} from the nflverse ff_playerids bridge.",
        business_definition=(
            "MyFantasyLeague player id. Unique across the feed and the MERGE key "
            "for this table; joins ff_adp.source_player_id for source='mfl'."
            if is_key
            else f"Player id in the {name.removesuffix('_id')} id system, or NULL "
            "when that system has no entry for this player."
        ),
        semantic_tags=["identifier", "primary_key"] if is_key else ["identifier"],
        valid_range=None, valid_values=None, example_value=8658 if is_key else None,
        gotchas=(
            ["gsis_id is NULL for 37.9% of rows (college prospects and players who "
             "never reached an NFL roster). NULL-guard before joining to nfl_plays."]
            if name == "gsis_id" else []
        ),
        source_field=name, deprecated_in_year=None,
    )


def _attr(name: str, type: str, short: str, definition: str,
          gotchas: list[str] | None = None) -> ColumnSpec:
    return ColumnSpec(
        name=name, type=type, mode="NULLABLE",  # type: ignore[arg-type]
        short_description=short, business_definition=definition,
        semantic_tags=["dimension"], valid_range=None, valid_values=None,
        example_value=None, gotchas=gotchas or [], source_field=name,
        deprecated_in_year=None,
    )


FF_XREF_SCHEMA: list[ColumnSpec] = (
    [_id_spec(c) for c in XREF_ID_COLUMNS]
    + [
        _attr("name", "STRING", "Player display name.",
              "Name as published in ff_playerids."),
        _attr("merge_name", "STRING",
              "Normalized name used for name-based resolution.",
              "ffverse's normalized join name: lowercased, name suffixes removed, "
              "periods removed, hyphens preserved. This is the ONLY way to resolve "
              "FFC ADP rows, because FFC's player_id appears in no id system here.",
              gotchas=["merge_name + position is NOT unique — 1,539 distinct pairs "
                       "across 1,553 rostered fantasy players. Ambiguous matches "
                       "must be refused, not guessed."]),
        _attr("position", "STRING", "Player position.",
              "Position as published in ff_playerids. Used with merge_name to "
              "disambiguate name-based resolution."),
        _attr("team", "STRING", "Current team abbreviation, or FA.",
              "Team at feed-refresh time. Not point-in-time; do not use it to "
              "reconstruct a historical roster."),
        _attr("birthdate", "DATE", "Player birth date.",
              "Birth date as published in ff_playerids."),
        _attr("age", "FLOAT64", "Age at feed-refresh time.",
              "Age in years at the time the feed was refreshed, not as of any "
              "season."),
        _attr("draft_year", "INT64", "NFL draft year.",
              "Year the player was drafted. Observed values include 0 for "
              "undrafted or not-yet-drafted players."),
        _attr("draft_round", "INT64", "NFL draft round.",
              "Round the player was drafted in, if drafted."),
        INGESTED_AT_SPEC,
    ]
)
