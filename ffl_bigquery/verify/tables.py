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

from ffl_bigquery.derive.dst_scoring import (
    BLOCKED_KICK_POINTS,
    FUMBLE_RECOVERY_POINTS,
    INTERCEPTION_POINTS,
    SACK_POINTS,
    SAFETY_POINTS,
    TOUCHDOWN_POINTS,
    points_allowed_bonus,
)
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


def check_dst_components_reconcile(rows) -> list[str]:
    """fantasy_points_dst must equal the sum of the components it publishes.

    This is the one DST invariant a schema test cannot express: it catches a
    scoring weight changed in the transform but not in dst_scoring (or vice
    versa), which would otherwise ship a table whose total silently disagrees
    with its own count columns. Imports the weights from dst_scoring rather
    than hardcoding them, so a weight change cannot drift between the
    transform and this verifier.

    NOT sufficient on its own, and deliberately not treated as such: it
    recomputes the total from the very columns the transform wrote, using the
    same constants, so a row can be internally consistent and still wrong.
    Both special-teams attribution bugs fixed in 0.2.0 reconciled to zero
    findings here -- the ARI 2024 week 1 row was a perfectly consistent 3.0
    where 9.0 was correct. check_dst_bonus_rederives and
    check_dst_season_signal_floor exist because of that.
    """
    findings: list[str] = []
    for r in rows:
        bonus = r.points_allowed_bonus
        if bonus is None:
            # Unresolvable final score -- a coverage gap, not an arithmetic
            # error. Reporting it here would bury real findings in noise.
            continue
        expected = (
            (r.sacks or 0) * SACK_POINTS
            + (r.interceptions or 0) * INTERCEPTION_POINTS
            + (r.fumble_recoveries or 0) * FUMBLE_RECOVERY_POINTS
            + (r.safeties or 0) * SAFETY_POINTS
            + (r.defensive_tds or 0) * TOUCHDOWN_POINTS
            + (r.blocked_kicks or 0) * BLOCKED_KICK_POINTS
            + bonus
        )
        actual = r.fantasy_points_dst
        if actual is None or abs(float(actual) - float(expected)) > 1e-6:
            findings.append(
                f"{r.season} wk{r.week} {r.team}: fantasy_points_dst={actual} "
                f"but components sum to {expected}"
            )
    return findings


def check_dst_bonus_rederives(rows) -> list[str]:
    """points_allowed_bonus must re-derive from points_allowed_total.

    Genuinely independent of check_dst_components_reconcile, which treats the
    bonus as a given input and never looks at points_allowed_total at all.
    This is the only check that can see a broken tier table, an off-by-one at
    a tier boundary, or a NULL score that was quietly resolved to 0.
    """
    findings: list[str] = []
    for r in rows:
        total = r.points_allowed_total
        expected = points_allowed_bonus(None if total is None else int(total))
        actual = r.points_allowed_bonus
        if expected != actual:
            findings.append(
                f"{r.season} wk{r.week} {r.team}: points_allowed_bonus="
                f"{actual} but points_allowed_total={total} re-derives to "
                f"{expected}"
            )
    return findings


_DST_COMPONENT_COLUMNS = (
    "sacks", "interceptions", "fumble_recoveries", "safeties",
    "defensive_tds", "blocked_kicks",
)

# A full regular season is 32 teams x 17 games = 544 team-weeks. The signal
# floor below requires this many team-weeks ACTUALLY PLAYED, not merely
# scheduled -- see check_dst_season_signal_floor's docstring for why row
# count alone is the wrong gate. Safeties are genuinely rare (15 league-wide
# in 2024), so a season with fewer played team-weeks than this can
# legitimately have zero of them, and the floor would be a guaranteed false
# positive -- the exact failure that gets a guard switched off. Partial
# seasons are announced by run_verify_dst, never silently dropped.
_DST_SIGNAL_FLOOR_MIN_ROWS = 400


def check_dst_season_signal_floor(
    rows, *, min_rows: int = _DST_SIGNAL_FLOOR_MIN_ROWS,
) -> tuple[list[str], list[int]]:
    """No component column may be uniformly zero across a whole season.

    Returns (findings, seasons skipped as too partial to judge).

    This is the only check here that can survive an upstream column rename.
    derive.dst_weekly._flag returns all zeros for a column that is absent --
    by design, because nflverse dtypes and column sets are vintage-dependent
    -- so if nflverse renames `sack` tomorrow, every defense silently scores
    bonus-only and reconciliation, bonus re-derivation and the ADP coverage
    guard all still pass. A season-wide zero in any component is not a
    plausible football outcome; it means the column stopped arriving.

    The floor is gated on GAMES ACTUALLY PLAYED, never on raw row count.
    derive/dst_weekly.py's _team_game_rows docstring is explicit that the row
    SET comes from the schedule, not from played games -- a season has its
    full row count (544 for a complete 32-team, 17-game season) the instant
    the schedule syncs, before a single game is played. Gating on row count
    is therefore dead code (no 1999-2025 season has ever had fewer than 400
    scheduled rows) and actively wrong: measured against the live 2026
    schedule with play-by-play truncated, a schedule-only season (544 rows,
    0 games played) produced 6 findings -- one per component -- on perfectly
    healthy data, and even a Thursday-opener-only season (544 rows, 1 game
    played) still produced 3. This is not hypothetical: nflreadpy unlocks
    load_pbp() for the current season on the Thursday after Labor Day, so the
    season-opener window is exactly when `--seasons latest` writes a
    zero-or-one-game season.

    A row's points_allowed_total is NULL until load_schedules() resolves a
    final score (see FF_POINTS_DST_WEEKLY_SCHEMA's points_allowed_total
    entry), so counting non-NULL points_allowed_total per season is a played-
    game count, and that count -- not the row count -- is compared against
    min_rows.
    """
    totals: dict[int, dict[str, int]] = {}
    counts: dict[int, int] = {}
    played: dict[int, int] = {}
    for r in rows:
        season = int(r.season)
        acc = totals.setdefault(season, dict.fromkeys(_DST_COMPONENT_COLUMNS, 0))
        counts[season] = counts.get(season, 0) + 1
        if getattr(r, "points_allowed_total", None) is not None:
            played[season] = played.get(season, 0) + 1
        for col in _DST_COMPONENT_COLUMNS:
            acc[col] += int(getattr(r, col, None) or 0)

    findings: list[str] = []
    skipped: list[int] = []
    for season in sorted(totals):
        if played.get(season, 0) < min_rows:
            skipped.append(season)
            continue
        for col in _DST_COMPONENT_COLUMNS:
            if totals[season][col] == 0:
                findings.append(
                    f"{season}: {col} is 0 across all {counts[season]} row(s) "
                    f"({played[season]} played) in the season -- an upstream "
                    "column was probably renamed or dropped (absent columns "
                    "count as 0 by design)"
                )
    return findings, skipped


# FFC publishes the Rams as "LAR"; nflverse schedules and play-by-play use
# "LA". Without normalizing, any season in which the Rams DST is drafted
# yields a permanent, unfixable coverage finding.
#
# This is a STATIC, one-way alias, which only works because "LAR" and "LA"
# both name the same franchise in the same place today. It cannot express a
# relocation, where the correct mapping is SEASON-dependent -- see
# check_dst_covers_adp_defenses's docstring for why franchise moves are
# handled as a separate finding class instead of by growing this map.
#
# NOTE FOR FUTURE READERS: this is the SECOND team vocabulary in the package.
# ffl_bigquery/coordinators/wikipedia.py carries the other one (abbreviation ->
# Wikipedia article title, including its own pre-relocation caveats). If you
# add a franchise or an alias here, check whether that map needs it too.
DST_TEAM_ABBREV_ALIASES: dict[str, str] = {"LAR": "LA"}


def _normalized_team_sql(col: str) -> str:
    if not DST_TEAM_ABBREV_ALIASES:
        return col
    whens = " ".join(
        f"WHEN '{src}' THEN '{dst}'" for src, dst in DST_TEAM_ABBREV_ALIASES.items()
    )
    return f"CASE {col} {whens} ELSE {col} END"


def check_dst_covers_adp_defenses(rows) -> tuple[list[str], list[str]]:
    """Every team defense on the ADP board must have DST rows for that season.

    Returns (findings, vocabulary_mismatches). Only `findings` fails the
    check -- see below.

    This is the coverage guard for the exact bug this table was built to
    kill: a drafted DEF with nothing to score against.

    FFC PUBLISHES CURRENT FRANCHISE ABBREVIATIONS RETROACTIVELY -- this is
    non-obvious and someone will otherwise "fix" this guard back into
    failing. Worked example, verified against the live FFC API and real
    nflverse schedules: FFC's *2010* ADP board names the Chargers' defense
    "LAC" -- the abbreviation the franchise uses TODAY -- even though the
    team did not move to LA (or adopt the LAC code) until 2017; nflverse's
    2010 schedule and play-by-play, correctly, call that team "SD". Same
    pattern for "LAR" (nflverse: STL, 2013-2015) and "LV" (nflverse: OAK,
    2016). A static one-way alias table such as DST_TEAM_ABBREV_ALIASES
    cannot express this, because the correct mapping is SEASON-dependent:
    "LAC" means "SD" in 2010 but "LAC" in 2020.

    Because of that, this function separates two classes that look identical
    from a single (season, team) row alone:

    * A GENUINE GAP (`findings`, fails the check): a drafted defense with 0
      ff_points_dst_weekly rows for its season, AND whose abbreviation
      appears nowhere in the DST table for ANY season. This is the real bug
      the guard exists to catch -- an abbreviation the DST table's team
      vocabulary has simply never produced.
    * A VOCABULARY MISMATCH (`mismatches`, reported but does NOT fail): 0
      rows for this season, but the abbreviation IS present in the DST table
      for some other season -- the signature of a franchise relocation or a
      source (like FFC) publishing today's abbreviation for a past season.
      Reported on its own clearly-labelled line so it stays visible without
      taking the check down.

    Rows are expected to be already bounded to seasons the DST table can
    cover, already normalized through DST_TEAM_ABBREV_ALIASES, and carrying
    a `team_seen_any_season` flag (true if the row's `team` appears in the
    DST table for at least one season) -- see run_verify_dst's query. Every
    one of those omissions made this guard fail on healthy data every single
    run.
    """
    findings: list[str] = []
    mismatches: list[str] = []
    for r in rows:
        if r.dst_weeks or 0:
            continue
        adp_team = getattr(r, "adp_team", None)
        label = (
            r.team if not adp_team or adp_team == r.team
            else f"{adp_team} (normalized to {r.team})"
        )
        base = (
            f"{r.season} {label}: on the ADP board but has 0 "
            "ff_points_dst_weekly rows"
        )
        if getattr(r, "team_seen_any_season", False):
            mismatches.append(
                f"{base} in {r.season} -- {r.team} DOES have rows in other "
                "seasons, so this looks like a franchise relocation or a "
                "retroactively-current abbreviation, not a genuine gap"
            )
        else:
            findings.append(base)
    return findings, mismatches


def run_verify_dst(ns: argparse.Namespace, *, bq_client) -> int:
    """Runs four DST guards: arithmetic reconciliation, bonus re-derivation
    and the per-season signal floor always; ADP coverage only when
    --adp-table is given. The coverage half prints an explicit SKIPPED line
    rather than being silently omitted -- a check that omits half its work
    while printing "0 findings" reads exactly like a pass, which has already
    cost real debugging time on this project.

    The coverage guard itself splits into two labelled lines: `[dst][coverage]`
    (genuine gaps, fails the check) and `[dst][coverage-mismatch]` (franchise
    relocations / FFC's retroactive-current-abbreviation habit, reported but
    never failing -- see check_dst_covers_adp_defenses's docstring).

    Each finding type gets its own labelled summary line so a run's output
    says which guards actually ran and what each concluded.
    """
    dst_table = str(TableRef.parse(ns.dst_table))
    sql = f"""
        SELECT season, week, team, sacks, interceptions, fumble_recoveries,
               safeties, defensive_tds, blocked_kicks, points_allowed_total,
               points_allowed_bonus, fantasy_points_dst
        FROM `{dst_table}`
    """
    rows = list(bq_client.query(sql).result())
    if not rows:
        # Same rule as verify/adp.py: an empty table is a failure, not a pass.
        print(f"[dst] no rows at all in {dst_table} -- did the sync run?")
        return 1

    findings = check_dst_components_reconcile(rows)
    for f in findings:
        print(f"[dst][arithmetic] {f}")
    print(
        f"[dst][arithmetic] {len(findings)} finding(s) across {len(rows)} row(s)"
    )

    bonus_findings = check_dst_bonus_rederives(rows)
    for f in bonus_findings:
        print(f"[dst][bonus] {f}")
    print(
        f"[dst][bonus] {len(bonus_findings)} finding(s) re-deriving "
        f"points_allowed_bonus from points_allowed_total"
    )

    floor_findings, floor_skipped = check_dst_season_signal_floor(rows)
    for f in floor_findings:
        print(f"[dst][signal-floor] {f}")
    print(
        f"[dst][signal-floor] {len(floor_findings)} finding(s); "
        f"{len(floor_skipped)} season(s) too partial to judge"
        + (f" ({floor_skipped})" if floor_skipped else "")
    )

    coverage_findings: list[str] = []
    if ns.adp_table:
        adp_table = str(TableRef.parse(ns.adp_table))
        # Two bounds without which this guard fails on healthy data forever:
        #  * ff_adp is FORWARD-LOOKING (FFC already publishes DEF entries for
        #    next season) while this table can only cover completed seasons,
        #    so unbounded it reports every drafted defense of the upcoming
        #    season as missing.
        #  * FFC's team vocabulary is not nflverse's -- see
        #    DST_TEAM_ABBREV_ALIASES for the static (non-relocation) case and
        #    check_dst_covers_adp_defenses's docstring for why relocations
        #    need `team_seen_any_season` instead of a bigger alias table.
        norm = _normalized_team_sql("team")
        coverage_sql = f"""
            WITH drafted AS (
              SELECT DISTINCT season, team AS adp_team, {norm} AS team
              FROM `{adp_table}`
              WHERE position IN ('DEF', 'DST') AND team IS NOT NULL
            ),
            covered AS (
              SELECT MAX(season) AS max_season FROM `{dst_table}`
            )
            SELECT a.season, a.adp_team, a.team,
                   COUNT(DISTINCT d.week) AS dst_weeks,
                   EXISTS(
                     SELECT 1 FROM `{dst_table}` dt WHERE dt.team = a.team
                   ) AS team_seen_any_season
            FROM drafted a
            CROSS JOIN covered c
            LEFT JOIN `{dst_table}` d
              ON d.season = a.season AND d.team = a.team
            WHERE a.season <= c.max_season
            GROUP BY a.season, a.adp_team, a.team
        """
        coverage_rows = list(bq_client.query(coverage_sql).result())
        coverage_findings, coverage_mismatches = check_dst_covers_adp_defenses(
            coverage_rows,
        )
        for f in coverage_findings:
            print(f"[dst][coverage] {f}")
        print(
            f"[dst][coverage] {len(coverage_findings)} finding(s) across "
            f"{len(coverage_rows)} drafted (season, team) defense(s) in "
            "seasons this table covers"
        )
        for f in coverage_mismatches:
            print(f"[dst][coverage-mismatch] {f}")
        print(
            f"[dst][coverage-mismatch] {len(coverage_mismatches)} vocabulary "
            "mismatch(es) (franchise relocation or a retroactively-current "
            "abbreviation) -- reported, not failed"
        )
    else:
        # Fails VISIBLY rather than silently reporting a clean run -- a check
        # that skips its own coverage half while printing "0 findings" reads
        # exactly like a pass.
        print("[dst][coverage] SKIPPED (no --adp-table given)")

    return 1 if (
        findings or bonus_findings or floor_findings or coverage_findings
    ) else 0
