"""Registry of the season-chunked NflverseTableSpecs `sync-nflverse` can drive.

`ALL_TABLE_NAMES` is plain strings, deliberately -- so a `--tables` CLI value
can be validated (and a typo reported with the full valid list, the same
posture `sync-adp` takes for `--sources`) without paying the cost of
importing every spec module. Each spec module reads a parquet schema-sample
at import time (`schema_gen.specs_from_frame`), so importing all nine
eagerly here would mean importing this registry module breaks `--version`/
`--help` on an installation without the dev fixtures on disk. `load_all_specs`
performs those imports, deferred to the caller that actually needs to run a
sync -- exactly the posture `cli.py`'s own lazy imports inside `main()` take
for `google.cloud.bigquery`.

Note: `nfl_coaches`/`ff_points_weekly`/`team_scheme_week` live under
`ffl_bigquery.coaches`/`ffl_bigquery.derive`, not `ffl_bigquery.nflverse.tables`
-- this registry aggregates every season-chunked NflverseTableSpec regardless
of which subpackage defines it. `nfl_coordinators` (`ffl_bigquery.coordinators`)
is deliberately absent: it's opt-in only (~37% measured fill; see its schema's
`name` gotcha), synced via its own `sync-coordinators` CLI command, and never
part of this registry or `sync-nflverse`.
"""
from __future__ import annotations

from ffl_bigquery.nflverse.spec import NflverseTableSpec

ALL_TABLE_NAMES: list[str] = [
    "ff_opportunity",
    "snap_counts",
    "injuries",
    "depth_charts",
    "participation",
    "ftn_charting",
    "nfl_coaches",
    "ff_points_weekly",
    "team_scheme_week",
]


def load_all_specs() -> list[NflverseTableSpec]:
    """Import and return every season-chunked NflverseTableSpec, in the same
    order as ALL_TABLE_NAMES."""
    from ffl_bigquery.coaches.sync import COACHES_SPEC
    from ffl_bigquery.derive.points_weekly import POINTS_WEEKLY_SPEC
    from ffl_bigquery.derive.scheme_week import SCHEME_WEEK_SPEC
    from ffl_bigquery.nflverse.tables.depth_charts import DEPTH_CHARTS_SPEC
    from ffl_bigquery.nflverse.tables.ftn_charting import FTN_CHARTING_SPEC
    from ffl_bigquery.nflverse.tables.injuries import INJURIES_SPEC
    from ffl_bigquery.nflverse.tables.opportunity import OPPORTUNITY_SPEC
    from ffl_bigquery.nflverse.tables.participation import PARTICIPATION_SPEC
    from ffl_bigquery.nflverse.tables.snap_counts import SNAP_COUNTS_SPEC

    return [
        OPPORTUNITY_SPEC,
        SNAP_COUNTS_SPEC,
        INJURIES_SPEC,
        DEPTH_CHARTS_SPEC,
        PARTICIPATION_SPEC,
        FTN_CHARTING_SPEC,
        COACHES_SPEC,
        POINTS_WEEKLY_SPEC,
        SCHEME_WEEK_SPEC,
    ]
