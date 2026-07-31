"""verify: dispatches `--checks` groups to the right check-runner.

Which CLI flags are mandatory depends on which check groups were selected
(e.g. --scheme-week-table is only needed for the scheme-denominators group),
so that requirement is validated here at dispatch time rather than via
argparse `required=True` -- the same reason sync-adp validates --sources
itself instead of constraining it with argparse `choices`.
"""
from __future__ import annotations

import argparse

CHECK_GROUPS = ("adp", "points-weekly", "scheme-denominators", "participation-coverage")


def run_verify_cli(ns: argparse.Namespace, *, bq_client) -> int:
    checks = [c.strip() for c in ns.checks.split(",") if c.strip()]
    unknown = [c for c in checks if c not in CHECK_GROUPS]
    if unknown:
        raise ValueError(
            f"unknown --checks value(s) {unknown!r}; valid checks are {list(CHECK_GROUPS)!r}"
        )
    if not checks:
        raise ValueError(f"--checks must name at least one of {list(CHECK_GROUPS)!r}")

    any_failed = False

    if "adp" in checks:
        if not ns.adp_table or ns.season is None:
            raise ValueError("--checks adp requires --adp-table and --season")
        from ffl_bigquery.verify.adp import run_verify as _run_adp
        any_failed = _run_adp(ns, bq_client=bq_client) != 0 or any_failed

    if "points-weekly" in checks:
        if not ns.points_weekly_table or ns.season is None:
            raise ValueError(
                "--checks points-weekly requires --points-weekly-table and --season"
            )
        from ffl_bigquery.verify.tables import run_verify_points_weekly
        any_failed = run_verify_points_weekly(ns, bq_client=bq_client) != 0 or any_failed

    if "scheme-denominators" in checks:
        if not ns.scheme_week_table or ns.season is None:
            raise ValueError(
                "--checks scheme-denominators requires --scheme-week-table and --season"
            )
        from ffl_bigquery.verify.tables import run_verify_scheme_denominators
        any_failed = run_verify_scheme_denominators(ns, bq_client=bq_client) != 0 or any_failed

    if "participation-coverage" in checks:
        if not ns.participation_table:
            raise ValueError(
                "--checks participation-coverage requires --participation-table"
            )
        from ffl_bigquery.verify.tables import run_verify_participation_coverage
        any_failed = (
            run_verify_participation_coverage(ns, bq_client=bq_client) != 0 or any_failed
        )

    return 1 if any_failed else 0
