# ffl-bigquery

Historical fantasy football ADP, usage, and coaching/scheme data → BigQuery. The fantasy
layer on top of [`nfl-bigquery`](https://github.com/blahovec-labs/nfl-bigquery). Sixth in
the `*-bigquery` family (`statcast-bigquery`, `yfinance-bigquery`, `nhl-bigquery`,
`nhl-hut-bigquery`, `nfl-bigquery`).

## Install

    pip install ffl-bigquery

Requires Python 3.11+. Writing to BigQuery needs Application Default Credentials with
permission to create/query tables in the target dataset.

## Quickstart

    ffl-bigquery --version

    ffl-bigquery sync-adp \
      --adp-table PROJECT.DATASET.ff_adp \
      --xref-table PROJECT.DATASET.ff_player_xref \
      --seasons 2010-2026 --sources ffc,mfl --formats ppr,standard --teams 12 \
      --resume

    ffl-bigquery sync-nflverse --dataset PROJECT.DATASET --seasons 1999-2025 --resume

    ffl-bigquery verify --checks adp --season 2026 --adp-table PROJECT.DATASET.ff_adp

Every `sync-*` command fans its work into independent chunks (by source/season/format for
ADP, by table/season for the nflverse surface) and records each attempt in a run log, so
one bad upstream season or source degrades coverage instead of aborting the whole run.
Pass `--resume` to skip chunks already recorded `success` or `empty`.

## What it writes

15 tables plus 2 run logs. Row counts below are measured from a real backfill against
live BigQuery (not estimates); `nfl_coordinators` is opt-in and its count depends on what
you choose to fetch.

| Table | Rows (measured) | Seasons | Written by |
| --- | --- | --- | --- |
| `ff_adp` | 18,309 | 2010–2026 | `sync-adp` |
| `ff_player_xref` | 12,468 | snapshot | `sync-xref` |
| `ff_rankings` | 6,391 | snapshot, forward-only | `sync-rankings` |
| `ff_opportunity` | 112,297 | 2006–2025 | `sync-nflverse` |
| `snap_counts` | 324,611 | 2013–2025 | `sync-nflverse` |
| `injuries` | 90,752 | 2009–2025 | `sync-nflverse` |
| `depth_charts` | 1,771,856 | 2001–2026 | `sync-nflverse` |
| `participation` | 478,989 | 2016–2025 | `sync-nflverse` |
| `ftn_charting` | 185,215 | 2022–2025 | `sync-nflverse` |
| `nfl_coaches` | 15,096 | 1999–2026 | `sync-nflverse` |
| `ff_points_weekly` | 476,156 | 1999–2025 | `sync-nflverse` |
| `ff_points_dst_weekly` | not yet backfilled | 1999–2025 (REG only) | `sync-nflverse` |
| `ff_points_k_weekly` | not yet backfilled | 1999–2025 (REG + POST) | `sync-nflverse` / `sync-kickers` |
| `team_scheme_week` | 14,546 | 1999–2025 | `sync-nflverse` |
| `nfl_coordinators` | opt-in, 46.2% measured fill | 2010–2025 (as backfilled) | `sync-coordinators` |
| `_ffl_ingest_runs` | run log, keyed `(source, season, scoring_format, teams)` | — | `sync-adp` |
| `_ffl_nflverse_runs` | run log, keyed `(table_name, season)` | — | `sync-nflverse` |

That's ~3.5M rows across the fourteen non-opt-in tables. Eleven of the fifteen
(`ff_opportunity`, `snap_counts`, `injuries`, `depth_charts`, `participation`,
`ftn_charting`, `nfl_coaches`, `ff_points_weekly`, `ff_points_dst_weekly`,
`ff_points_k_weekly`, `team_scheme_week`) share one driver —
`sync-nflverse` — because they're all the same shape: load a frame for season *S*, align
it to a schema, replace that season. `ff_adp` and `nfl_coordinators` are chunked
differently (by source/format and by team-season respectively) because their upstreams
are; `ff_player_xref` and `ff_rankings` aren't season-chunked at all — they're
whole-table/current-snapshot syncs.

### The two scoring tables `ff_points_weekly` cannot produce

`ff_points_weekly` derives from `load_player_stats()`, and that frame covers offensive
skill players only. Two fantasy roster slots therefore had nothing to join to, each for a
different reason, and each gets its own table derived from `load_pbp()`:

- **`ff_points_dst_weekly`** — team defenses had no rows at all. Regular season only, at
  `(season, week, team)` grain.
- **`ff_points_k_weekly`** — kickers had rows that were **always wrong in the same
  direction**: `ff_points_weekly` publishes K rows, they are arithmetically complete, and
  they score **exactly 0.0**. Measured 2026-08-07: 2024 alone carries 569 kicker rows
  across 43 players summing to precisely zero. Nothing upstream is broken — the join was
  never wrong, the scoring was simply never computed, because `load_player_stats()` carries
  no kicking columns whatsoever. A zero reads as a bad week, not as a missing feature,
  which is why the gap survived this long. This table is not redundant with those rows; it
  is the only place kicker points exist.

`ff_points_k_weekly` is one row per `(season, week, gsis_id)` — the grain is the **kicker**,
read off `kicker_player_id`, never inferred from `posteam` (on a kickoff `posteam` is the
receiving team; that orientation flip cost the DST build two Criticals). It ships every
scored component beside the total (`fg_made`, `fg_missed`, `fg_made_yards_max`, `xp_made`,
`xp_missed`, `fantasy_points_kicker`), so a league on other rules re-derives totals from
this table instead of re-deriving the table.

The scoring convention it encodes:

| Event | Points |
| --- | --- |
| Field goal made, 0–39 yards | **3** |
| Field goal made, 40–49 yards | **4** |
| Field goal made, 50+ yards | **5** |
| Field goal missed **or blocked** | **−1** |
| Extra point made | **1** |
| Extra point missed **or blocked** | **0** (no penalty, but the attempt is still counted) |
| Extra point **aborted** | **unattributed** — scored for nobody, counted as neither made nor missed |

Points are scored per *attempt* and then summed, never re-derived from the counts: three
made field goals are worth anywhere from 9 to 15 points, so the distance tier is
information the count columns do not carry. A blocked kick stays in as the kicker's own
attempt rather than being filtered out — dropping it would quietly inflate every kicker
who had one. An **aborted** extra point (a botched snap or hold) is the one attempt charged
to no one: nflverse attributes no kicker to it, and all 31 across 1999–2025 (11 seasons,
2002–2014) carry a NULL `kicker_player_id`. Charging it as a miss would penalise a player
who did nothing.

That convention is **swappable by replacing `ffl_bigquery/derive/kicker_scoring.py` alone**
— pure functions, no pandas, no I/O, no BigQuery. The tier table and the four point values
live nowhere else in the transform.

Two scope notes worth having before you join anything:

- **`ff_points_k_weekly` covers REG + POST; `ff_points_dst_weekly` is REG only.** The
  siblings genuinely disagree. The kicker table follows `load_pbp()`'s own coverage, which
  is what `ff_points_weekly` does too, so a kicker join against the player table does not
  silently drop January. The DST table derives its row set from `load_schedules()` filtered
  to `game_type='REG'`. Weeks 19+ therefore exist in one and not the other.
- A kicker who attempted nothing in a week gets **no row**, not a 0.0 row: the play-by-play
  cannot tell "inactive" from "never got in range", and only the latter is honestly a zero.

Sync it with either command — `sync-kickers` is `sync-nflverse` pinned to this one table,
sharing the driver, run log and chunk isolation verbatim, because this is the table an
operator re-runs on its own rather than paying a 27-season round trip through the other ten:

    ffl-bigquery sync-kickers --dataset PROJECT.DATASET --seasons 1999-2025 --resume

    ffl-bigquery verify --checks kicker \
      --kicker-table PROJECT.DATASET.ff_points_k_weekly \
      --plays-table PROJECT.DATASET.nfl_plays

`--checks kicker` runs four guards, each falsifiable on its own and each reporting on its
own labelled line. `--plays-table` (an `nfl_plays` table from
[`nfl-bigquery`](https://github.com/blahovec-labs/nfl-bigquery)) is **required**, not
optional: the first guard recomputes every component count straight from the play-by-play
in SQL, importing nothing from the transform and restating the tier weights rather than
sharing them, and it counts attempts *without* a kicker-id predicate so an attempt the
transform drops shows up as a disagreement instead of as a quietly smaller table. The
per-season floors are the second guard, and they are deliberately **not** the round
"1,000 made FGs / 1,200 XPs" a recent season suggests (2024: 982 and 1,245). Measured
minima across 1999–2025 are far lower — `fg_made` 731 (2004), `xp_made` 1,055 (2001),
`fg_missed` 140 (2013), `xp_missed` **5** (2013, before the 2015 XP-distance change) — so
a 1,000 floor would fire on 22 of 27 healthy seasons, and a guard that fails on healthy
data gets switched off. The shipped floors are `fg_made` 500, `fg_missed` 90, `xp_made` 700,
`xp_missed` 1: below every measured minimum, strictly above zero, and skipped entirely for
a season with too few distinct weeks to judge.

`team_scheme_week` is the marquee derived table: a per-`(season, week, team)` scheme
fingerprint (shotgun/no-huddle/pass rate/PROE/EPA, personnel groupings, coverage/pressure,
FTN's play-action/motion/RPO/blitz) joined to that week's head coach from `nfl_coaches` —
built for "what changed when the coach changed." Every `gsis_id` column resolves against
`ff_player_xref` (via `pfr_id` for `snap_counts`, directly elsewhere); see Known
limitations for the resolution ceiling that imposes.

## Known limitations

Every one of these was measured against the real feeds, not assumed:

- **FFC ignores `start_date`/`end_date`.** Requests for different date windows return the
  identical current window, so intra-preseason ADP drift is forward-capture-only and
  cannot be backfilled — it exists only if a sync actually ran that day.
- **FFC has no 2025 data.** Both `ppr` and `standard` return empty for 2025 while
  2010–2024 and 2026 return normally. This is an upstream gap, not a client bug — the
  sync records those chunks as `empty` and continues.
- **MFL starts at 2011.** 2010 and earlier return `{"adp": {"totalPicks": "0", ...}}`
  with no player data.
- **`gsis_id` is NULL for 37.9% of `ff_playerids` rows** (66.9% filled among rostered
  QB/RB/WR/TE/K; the rest are college prospects and players who never reached an NFL
  roster), which caps every downstream resolution rate. Measured on the real 2010–2026
  backfill (18,309 `ff_adp` rows): **FFC resolves 89.9%, MFL 91.8%**; the worst legitimate
  `(source, season)` is **MFL 2026 at 63.1%** (current-year rookies without a `gsis_id`
  yet). `verify`'s `--min-resolution-rate` defaults to a conservative **0.60**, just below
  that floor.
- **Coordinators are partially available — 46.2% measured.** A 24-team-season sample
  (6 teams x 2005/2012/2019/2024) suggested ~37%; the actual 2010-2025 backfill
  produced **473 rows of a possible 1,024** (16 seasons x 32 teams x 2 roles) =
  **46.2%**. Head coach, by contrast, was 24/24 in that sample and is already
  first-class in `nfl_coaches` at per-game grain — only coordinators are sparse.
  All six 2005 team-seasons sampled had neither coordinator field. Pro Football
  Reference returns HTTP 403 to automated fetches and is unusable, leaving Wikipedia
  team-season infoboxes as the only fetchable source. Hence `sync-coordinators` is
  never part of `sync-nflverse` — it is a separate, explicit, opt-in command. Also:
  the team-abbreviation to Wikipedia-page-title map uses each franchise's current
  name, so pre-relocation/rename seasons resolve poorly.
- **`offense_personnel` is unreliable from 2023 on.** It still reports 100% fill for
  2023–2025, but its content changed without notice — a real 2023 row reads
  `"2 CB, 2 ILB, 1 OLB, 1 RB, 1 SS, 2 TE, 2 WR"`, defensive players inside the offense
  column. `team_scheme_week` therefore parses personnel from `offense_positions` (a
  per-player position list) for 2023+, and falls back to `offense_personnel` for
  2016–2022, where `offense_positions` is 0% filled. Each row's `personnel_source` column
  records which parser ran.
- **Charted coverage/pressure/FTN metrics are a sample, never a census.** Coverage
  charting fill (`defense_man_zone_type`/`defense_coverage_type`) is **0.000 in
  2016–2017** and never exceeds **.496** afterward; `was_pressure` fill is ~.38 pre-2023
  and 1.000 from 2023 on. Every derived rate in `team_scheme_week` therefore ships beside
  its own denominator column (e.g. `plays_charted_coverage` next to `man_rate`), and the
  rate is `pd.NA` — never `0.0` — when that denominator is 0, so "nobody charted it" never
  reads as "this team never blitzed." Out-of-era columns are NULL, not 0, for the same
  reason.
- **Upstream dtypes are vintage-dependent.** `season`, `week`, `play_id`, and `pos_slot`
  each change type between years in the raw nflverse feeds (e.g. `season` arrives as a
  string in some tables, a float in others; `play_id` is Float64 in some seasons and
  Int32 in others). This library normalizes every one of them to a stable BigQuery type
  before writing — consumers reading nflverse directly should expect the raw dtype to
  vary by season and cast defensively before joining.

See `CHANGELOG.md` for the full per-release list, including two claims an earlier probe
got wrong and this backfill corrected (MFL's true start season, and FFC's missing 2025).

## CLI

    ffl-bigquery --version

    # ADP + the id bridge it resolves against
    ffl-bigquery sync-adp \
      --adp-table PROJECT.DATASET.ff_adp --xref-table PROJECT.DATASET.ff_player_xref \
      --seasons 2010-2026 --sources ffc,mfl --formats ppr,standard --teams 12 \
      --min-interval 1.0 --resume

    ffl-bigquery sync-xref --xref-table PROJECT.DATASET.ff_player_xref

    # the eleven season-chunked nflverse/derived tables, one dataset, one command
    ffl-bigquery sync-nflverse --dataset PROJECT.DATASET --seasons 1999-2025 --resume

    # a subset, if you only want a few
    ffl-bigquery sync-nflverse --dataset PROJECT.DATASET --seasons latest \
      --tables ff_opportunity,snap_counts,injuries --resume

    # current ECR snapshot -- not season-chunked
    ffl-bigquery sync-rankings --rankings-table PROJECT.DATASET.ff_rankings

    # ff_points_k_weekly on its own -- sync-nflverse pinned to that one table
    ffl-bigquery sync-kickers --dataset PROJECT.DATASET --seasons 1999-2025 --resume

    # opt-in, 46.2% measured fill -- never part of sync-nflverse
    ffl-bigquery sync-coordinators \
      --coordinators-table PROJECT.DATASET.nfl_coordinators \
      --seasons 2019-2024 --teams all --min-interval 1.0

    ffl-bigquery verify --checks adp --season 2026 --adp-table PROJECT.DATASET.ff_adp
    ffl-bigquery verify --checks points-weekly --season 2025 \
      --points-weekly-table PROJECT.DATASET.ff_points_weekly
    ffl-bigquery verify --checks scheme-denominators --season 2025 \
      --scheme-week-table PROJECT.DATASET.team_scheme_week
    ffl-bigquery verify --checks participation-coverage \
      --participation-table PROJECT.DATASET.participation
    ffl-bigquery verify --checks dst --dst-table PROJECT.DATASET.ff_points_dst_weekly \
      --adp-table PROJECT.DATASET.ff_adp
    ffl-bigquery verify --checks kicker \
      --kicker-table PROJECT.DATASET.ff_points_k_weekly \
      --plays-table PROJECT.DATASET.nfl_plays

Notes:

- `sync-adp` fans a season/source/format/teams matrix into independent chunks, so one dead
  upstream source degrades coverage instead of aborting the run; `--resume` skips chunks
  already recorded `success` or `empty` in `_ffl_ingest_runs`.
- `sync-nflverse` derives each table's ref as `project.dataset.<name>` from a single
  `--dataset` — no per-table flags needed. `--tables` defaults to all eleven and is
  validated against the known registry before any fetch, so a typo fails fast. Its
  `--resume` reads `_ffl_nflverse_runs`, a second run log keyed `(table_name, season)`
  — deliberately separate from ADP's `(source, season, scoring_format, teams)` log rather
  than a shared generalization of it.
- `sync-rankings` takes `--rankings-table` (not `--dataset`) because `ff_rankings` is a
  single current-snapshot table, not a season matrix.
- `sync-coordinators` takes `--coordinators-table`, `--seasons`, `--teams` (comma-separated
  abbreviations, or `all` for all 32), and `--min-interval`. A missing or unparseable
  Wikipedia page is a normal, exception-free outcome here (not every team-season has an
  infobox with the fields populated), so there's no separate failed/empty run log — just a
  fetched/missing/unavailable tally in the final log line.
- `--min-interval` (seconds, default `1.0`, both `sync-adp` and `sync-coordinators`)
  throttles the delay between requests to third-party sources — the minimum respectful
  spacing backing FFC's "do not poll frequently" terms and general politeness toward
  Wikipedia's API; see Data sources & attribution below.
- `sync-kickers` is `sync-nflverse` with `--tables` pinned to `ff_points_k_weekly` — same
  driver, same run log, same chunk isolation, and the table cannot be pointed elsewhere. It
  exists because that table is the one an operator re-runs by itself, and reaching it
  through `sync-nflverse` otherwise means a 27-season round trip through the other ten.
- `verify --checks` accepts a comma-separated subset of `adp`, `points-weekly`,
  `scheme-denominators`, `participation-coverage`, `dst`, `kicker`. Each group validates its
  own required flags at dispatch time (e.g. `scheme-denominators` needs `--scheme-week-table`
  and `--season`) rather than making every flag globally required. `kicker` requires BOTH
  `--kicker-table` and `--plays-table`; the second is not optional, because recomputing the
  expected counts from the play-by-play independently IS the check. `--min-resolution-rate`
  (default `0.60`) and `--ppr-tolerance` (default `0.01`) are the two numeric knobs.
- `sync-adp`, `sync-xref`, `sync-nflverse`, and `sync-coordinators` all accept `--dry-run`
  to print what would happen without writing or fetching.

## Data sources & attribution

- **nflverse** via [`nflreadpy`](https://nflreadpy.nflverse.com/) — player IDs, usage,
  snap counts, injuries, depth charts, participation, FTN charting, schedules/coaches,
  play-by-play, and weekly player stats.
- **[Fantasy Football Calculator](https://fantasyfootballcalculator.com/)** — historical
  ADP (2010→present, no 2025). Their ADP REST API is free for personal and commercial use;
  this project provides attribution as requested. Data updates once daily — do not poll
  frequently (`--min-interval` backs this).
- **[MyFantasyLeague](https://www.myfantasyleague.com/)** — historical ADP
  (2011→present) via the free `export?TYPE=adp` endpoint.
- **Wikipedia** — offensive/defensive coordinator names, via the `action=parse` API
  against team-season infobox pages (`nfl_coordinators`, opt-in, 46.2% measured fill; see Known
  limitations).

## Local development (Windows / TLS interception)

If HTTPS requests fail with `unable to get local issuer certificate` or
`CERTIFICATE_VERIFY_FAILED`, a local security product (e.g. Norton) is intercepting TLS
and re-signing it with a certificate that OpenSSL's bundled trust store doesn't trust,
even though the OS does. `--native-tls` alone does **not** fix this — it only affects
uv's own downloads of packages/pythons, not certificate validation inside the Python
process that makes the actual HTTP requests.

Running the test suite already handles this automatically: `tests/conftest.py` calls
`truststore.inject_into_ssl()` (which validates against the OS-native trust store
instead of OpenSSL's) for `network`-marked tests only. You just need `truststore`
installed, which the `dev` extra provides:

    uv run --native-tls --extra dev pytest -m network -v

Outside the test suite (e.g. exploring interactively), you have to invoke that
injection yourself, since it isn't compiled into the shipped package:

    uv run --native-tls --extra dev python -c "import truststore; truststore.inject_into_ssl(); ..."

MIT licensed.
