"""Verify checks 4-6 (spec sec 8): ff_points_weekly reconciliation,
team_scheme_week denominators, and the nfl_participation coverage-matrix
regression.

Checks 4 and 5 are the ones that catch real defects and are fully wired:
4. ff_points_weekly's fantasy_points_ppr is carried through UNCHANGED from
   upstream (see derive/points_weekly.py's module docstring) specifically so
   it becomes a free correctness oracle: recompute full-PPR from
   fantasy_points_standard + receptions and assert it still agrees.
5. team_scheme_week's charted-metric rates (personnel/coverage/pressure/FTN)
   ship beside their own denominator columns precisely so a 0% or 100%
   sample never reads as a census -- this check enforces the pairing.

Check 6 (coverage-matrix regression) is also implemented: a whole-table
regression against the fill-rate bounds documented in
derive/scheme_week.py's module docstring, catching an upstream backfill that
silently changes the data under a shipped chart.

Checks 7 (coach continuity) and 8 (personnel plausibility) are DEFERRED --
see Task 11's report for why.

A query returning no rows at all is a FAILURE, not a pass -- same rule as
verify/adp.py: it means the sync never ran, and an empty-table "all checks
passed" is the exact failure mode that rule exists to prevent. Because
checks 4 and 5 run bare aggregate queries (no GROUP BY), an empty underlying
table still returns exactly one result row with COUNT(*) == 0 -- so "no
rows" is detected via that zero count, not via an empty result set.
"""
from __future__ import annotations

import argparse

from ffl_bigquery.verify.adp import CheckResult, summarize_results
from ffl_bigquery.writer import TableRef

# (rate column, its denominator column) -- every pair documented in
# derive/scheme_week.py's TEAM_SCHEME_WEEK_SCHEMA as "pd.NA when <denom> is
# 0." shotgun_rate/no_huddle_rate/pass_rate are deliberately excluded: their
# implicit denominator is `plays` itself, a census from load_pbp(), not a
# charted sample -- they aren't the "sample read as a census" failure mode
# this check guards against.
SCHEME_RATE_DENOMINATOR_PAIRS: list[tuple[str, str]] = [
    ("personnel_11_rate", "plays_with_personnel"),
    ("personnel_12_rate", "plays_with_personnel"),
    ("personnel_21_rate", "plays_with_personnel"),
    ("man_rate", "plays_charted_coverage"),
    ("zone_rate", "plays_charted_coverage"),
    ("pressure_rate", "plays_charted_pressure"),
    ("play_action_rate", "plays_charted_ftn"),
    ("motion_rate", "plays_charted_ftn"),
    ("rpo_rate", "plays_charted_ftn"),
    ("screen_rate", "plays_charted_ftn"),
    ("blitz_rate", "plays_charted_ftn"),
]

# Measured 2026-07-30 (derive/scheme_week.py module docstring): coverage
# charting fill is 0.000 in 2016-2017 and never exceeds 49.6% afterward. The
# tolerance absorbs float rounding, not real drift.
_COVERAGE_ZERO_SEASONS = (2016, 2017)
_COVERAGE_CEILING = 0.496
_COVERAGE_TOLERANCE = 0.001


def _row_count(rows) -> int:
    if not rows or rows[0].n is None:
        return 0
    return int(rows[0].n)


def check_points_weekly_ppr_reconciles(
    bq_client, *, points_weekly_table: str, season: int, tolerance: float,
) -> CheckResult:
    """Recomputed full-PPR (standard + 1 pt/reception) vs upstream's carried-
    through fantasy_points_ppr. Not circular: this table never computes
    fantasy_points_ppr itself (see derive/points_weekly.py), so a disagreement
    here means either the standard/receptions columns or upstream's own PPR
    column drifted.
    """
    tol = float(tolerance)
    sql = f"""
    SELECT
      COUNT(*) AS n,
      COUNTIF(
        ABS((fantasy_points_standard + receptions) - fantasy_points_ppr) > {tol}
      ) AS mismatches
    FROM `{points_weekly_table}`
    WHERE season = {int(season)}
      AND fantasy_points_standard IS NOT NULL
      AND receptions IS NOT NULL
      AND fantasy_points_ppr IS NOT NULL
    """
    rows = list(bq_client.query(sql).result())
    n = _row_count(rows)
    if n == 0:
        return CheckResult(
            name="points_weekly_ppr_reconciles",
            passed=False,
            detail=f"no rows found for season {season} in {points_weekly_table} "
                    "-- did the sync run?",
        )
    mismatches = int(rows[0].mismatches)
    return CheckResult(
        name="points_weekly_ppr_reconciles",
        passed=mismatches == 0,
        detail=(
            f"{mismatches}/{n} row(s) where recomputed PPR "
            f"(fantasy_points_standard + receptions) disagrees with upstream "
            f"fantasy_points_ppr by more than {tol}"
        ),
    )


def check_scheme_rate_denominators(
    bq_client, *, scheme_week_table: str, season: int,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rate_col, denom_col in SCHEME_RATE_DENOMINATOR_PAIRS:
        sql = f"""
        SELECT
          COUNT(*) AS n,
          COUNTIF({rate_col} IS NOT NULL AND {denom_col} IS NULL) AS null_denom,
          COUNTIF({rate_col} IS NOT NULL AND {rate_col} > 1.0) AS over_one
        FROM `{scheme_week_table}`
        WHERE season = {int(season)}
        """
        rows = list(bq_client.query(sql).result())
        n = _row_count(rows)
        if n == 0:
            results.append(CheckResult(
                name=f"scheme_denominators[{rate_col}]",
                passed=False,
                detail=f"no rows found for season {season} in "
                        f"{scheme_week_table} -- did the sync run?",
            ))
            continue
        null_denom = int(rows[0].null_denom)
        over_one = int(rows[0].over_one)
        results.append(CheckResult(
            name=f"scheme_denominators[{rate_col}]",
            passed=null_denom == 0 and over_one == 0,
            detail=(
                f"{null_denom} row(s) with non-null {rate_col} but null "
                f"{denom_col}; {over_one} row(s) with {rate_col} > 1.0"
            ),
        ))
    return results


def check_participation_coverage_matrix(
    bq_client, *, participation_table: str,
) -> CheckResult:
    """Regression guard: measured per-season coverage-charting fill still
    matches the documented shape (0.000 in 2016-2017, never above 49.6%
    afterward). This spans every season in the table, not one season at a
    time -- unlike checks 4/5, that is the actual shape of the thing being
    regressed, so there is no --season argument here.
    """
    sql = f"""
    SELECT
      season,
      COUNT(*) AS n,
      SAFE_DIVIDE(
        COUNTIF(defense_man_zone_type IS NOT NULL AND TRIM(defense_man_zone_type) != ''),
        COUNT(*)
      ) AS coverage_fill_rate
    FROM `{participation_table}`
    GROUP BY season
    ORDER BY season
    """
    rows = list(bq_client.query(sql).result())
    if not rows:
        return CheckResult(
            name="participation_coverage_matrix",
            passed=False,
            detail=f"no rows found in {participation_table} -- did the sync run?",
        )
    violations = []
    for r in rows:
        season = int(r.season)
        rate = float(r.coverage_fill_rate or 0.0)
        if season in _COVERAGE_ZERO_SEASONS:
            if rate > _COVERAGE_TOLERANCE:
                violations.append(
                    f"season {season}: expected ~0.000, measured {rate:.4f}"
                )
        elif rate > _COVERAGE_CEILING + _COVERAGE_TOLERANCE:
            violations.append(
                f"season {season}: expected <= {_COVERAGE_CEILING}, measured {rate:.4f}"
            )
    return CheckResult(
        name="participation_coverage_matrix",
        passed=not violations,
        detail=(
            "all seasons within documented coverage bounds" if not violations
            else "; ".join(violations)
        ),
    )


def run_verify_points_weekly(ns: argparse.Namespace, *, bq_client) -> int:
    table = str(TableRef.parse(ns.points_weekly_table))
    result = check_points_weekly_ppr_reconciles(
        bq_client, points_weekly_table=table, season=ns.season,
        tolerance=ns.ppr_tolerance,
    )
    return summarize_results([result])


def run_verify_scheme_denominators(ns: argparse.Namespace, *, bq_client) -> int:
    table = str(TableRef.parse(ns.scheme_week_table))
    results = check_scheme_rate_denominators(
        bq_client, scheme_week_table=table, season=ns.season,
    )
    return summarize_results(results)


def run_verify_participation_coverage(ns: argparse.Namespace, *, bq_client) -> int:
    table = str(TableRef.parse(ns.participation_table))
    result = check_participation_coverage_matrix(bq_client, participation_table=table)
    return summarize_results([result])
