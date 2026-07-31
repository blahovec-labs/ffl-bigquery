"""ff_adp data-quality checks.

Two independent guarantees: resolution rate (a silently degraded xref thinning
joins) and grain uniqueness (a duplicated grain double-counting a player). Grain
uniqueness is checked twice, as check_no_duplicates_at_grain and
check_snapshot_idempotent -- since the grain already includes snapshot_date, "no
grain key repeats" and "no day's MERGE appends instead of upserting" are the same
underlying fact, run as separate queries only so a failure names which framing is
clearer in the report.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from ffl_bigquery.adp.schema import FF_ADP_KEYS
from ffl_bigquery.writer import TableRef

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def check_resolution_rate(
    bq_client, *, adp_table: str, season: int, min_rate: float
) -> list[CheckResult]:
    sql = f"""
    SELECT source,
           COUNT(*) AS n,
           COUNTIF(gsis_id IS NOT NULL) AS resolved,
           SAFE_DIVIDE(COUNTIF(gsis_id IS NOT NULL), COUNT(*)) AS rate
    FROM `{adp_table}`
    WHERE season = {int(season)}
    GROUP BY source
    ORDER BY source
    """
    rows = list(bq_client.query(sql).result())
    if not rows:
        return [
            CheckResult(
                name="resolution_rate",
                passed=False,
                detail=f"no rows found for season {season} — did the sync run?",
            )
        ]
    results: list[CheckResult] = []
    for r in rows:
        rate = float(r.rate or 0.0)
        results.append(
            CheckResult(
                name=f"resolution_rate[{r.source}]",
                passed=rate >= min_rate,
                detail=(
                    f"{r.source}: {r.resolved}/{r.n} resolved "
                    f"({rate:.3f}) vs floor {min_rate:.2f}"
                ),
            )
        )
    return results


def check_no_duplicates_at_grain(
    bq_client, *, adp_table: str, season: int
) -> CheckResult:
    grain = ", ".join(FF_ADP_KEYS)
    sql = f"""
    SELECT COUNT(*) AS dupes FROM (
      SELECT {grain}, COUNT(*) AS c
      FROM `{adp_table}`
      WHERE season = {int(season)}
      GROUP BY {grain}
      HAVING c > 1
    )
    """
    dupes = int(list(bq_client.query(sql).result())[0].dupes)
    return CheckResult(
        name="no_duplicates_at_grain",
        passed=dupes == 0,
        detail=f"{dupes} grain keys appear more than once",
    )


def check_snapshot_idempotent(
    bq_client, *, adp_table: str, season: int
) -> CheckResult:
    """A given grain must appear at most once per snapshot_date.

    Re-running one day's sync MERGEs in place, so a count above 1 means the MERGE
    key drifted from the grain and the cron is appending instead of upserting.
    """
    grain = ", ".join(FF_ADP_KEYS)
    sql = f"""
    SELECT MAX(c) AS max_rows_per_grain FROM (
      SELECT {grain}, COUNT(*) AS c
      FROM `{adp_table}`
      WHERE season = {int(season)}
      GROUP BY {grain}
    )
    """
    worst = list(bq_client.query(sql).result())[0].max_rows_per_grain
    worst_n = int(worst or 0)
    return CheckResult(
        name="snapshot_idempotent",
        passed=worst_n <= 1,
        detail=f"max rows per (grain) = {worst_n}; expected 1",
    )


def summarize_results(results: list[CheckResult]) -> int:
    """Print each result's PASS/FAIL line plus a summary tally.

    Shared by every `verify` check group (see ffl_bigquery.verify.tables) so
    the report format -- and the "N/M checks passed" tail line -- stays
    identical no matter which group produced the results.
    """
    failed = [r for r in results if not r.passed]
    for r in results:
        print(f"[{'PASS' if r.passed else 'FAIL'}] {r.name}: {r.detail}")
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


def run_verify(ns: argparse.Namespace, *, bq_client) -> int:
    adp_table = str(TableRef.parse(ns.adp_table))
    results = [
        *check_resolution_rate(bq_client, adp_table=adp_table, season=ns.season,
                               min_rate=ns.min_resolution_rate),
        check_no_duplicates_at_grain(bq_client, adp_table=adp_table,
                                     season=ns.season),
        check_snapshot_idempotent(bq_client, adp_table=adp_table, season=ns.season),
    ]
    return summarize_results(results)
