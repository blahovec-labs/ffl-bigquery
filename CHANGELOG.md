# Changelog

All notable changes to this project will be documented in this file.

## 0.3.1 — 2026-09-15

Season rollover fix for `sync-nflverse --seasons latest`. `run_sync_nflverse_cli` was
resolving "latest" as `datetime.now(UTC).year` — the calendar year. NFL seasons are
labeled by the year they START, and the postseason runs into the following January/
February, so a run on 2027-01-01 asked for season 2027 when the actual in-flight season
was still 2026's playoffs. Left alone, the eleven season-chunked nflverse tables would
have stopped refreshing 2026 mid-postseason and silently never picked up the 2026
playoffs at all.

### Fixed

- `nflverse_season_for_latest(now)` in `ffl_bigquery/nflverse/driver.py` rolls the season
  over in September (matching `nfl_bigquery.sync.current_season()` in the sibling
  library), not on the calendar new year. `run_sync_nflverse_cli` now calls it instead of
  reading `datetime.now(UTC).year` directly whenever the caller doesn't inject an explicit
  `current_season`.
- **Deliberately not applied to the ADP path.** `ffl_bigquery/adp/sync.py` still resolves
  its season from `snapshot_date.year` (plain calendar year) — that's correct there,
  because in January the ADP board anyone cares about is the *upcoming* draft season, not
  the one that just ended. This asymmetry between the two sync paths is intentional; don't
  "fix" it into consistency.

## 0.3.0 — 2026-08-07

Kicker fantasy scoring. Unlike the DST gap 0.2.0 closed, this one was invisible rather
than empty: `ff_points_weekly` *does* publish kicker rows, and they are arithmetically
complete — they just score **exactly 0.0**, every one of them. Measured 2026-08-07, 2024
alone carries 569 kicker rows across 43 players summing to precisely zero. Nothing was
broken upstream. The join was never wrong; the scoring was never computed, because
`load_player_stats()` carries no kicking columns at all. A zero reads as a bad week, not
as a missing feature, which is why the gap survived this long.

### Added

- `ff_points_k_weekly` — weekly kicker fantasy points at `(season, week, gsis_id)` grain,
  derived from `load_pbp()` field-goal and extra-point attempts. Registered as the
  eleventh season-chunked table, so it syncs through the existing `sync-nflverse`. Scoring
  convention: made FG **3/4/5** by distance tier (≤39 / 40–49 / 50+), made XP **1**, missed
  **or blocked** FG **−1**, missed **or blocked** XP **0**, aborted XP **unattributed**.
  Points are scored per attempt and then summed, never re-derived from the counts — three
  made field goals are worth anywhere from 9 to 15 points, so the tier is information the
  counts cannot carry. Every scored component ships beside the total anyway, so a league on
  other rules re-derives totals from this table instead of re-deriving the table.
- `sync-kickers` — `sync-nflverse` with `--tables` pinned to `ff_points_k_weekly`, sharing
  the driver, run log and chunk isolation verbatim. It exists because this is the table an
  operator re-runs on its own, and reaching it through `sync-nflverse` otherwise costs a
  27-season round trip through the other ten.
- `verify --checks kicker` — four guards, each independently falsifiable, each on its own
  labelled line. **Recomputation:** per-season component counts recomputed from `nfl_plays`
  in SQL, importing nothing from the transform and *restating* the tier weights rather than
  sharing them, so a rules change has to be made twice on purpose and until it is this is a
  second opinion instead of an echo. It counts attempts **without** a kicker-id predicate,
  deliberately, so an attempt the transform drops surfaces as a disagreement rather than as
  a quietly smaller table. **Per-season floors:** no component may fall below its floor,
  the only guard that survives an upstream column *rename* (an absent column reads as
  all-zeros, and both sides would agree on the zero). **Tier sanity:** a week's total must
  be reachable given its own components and its longest made kick. **Coverage:** every
  kicker with attempts in the play-by-play must have rows, with the denominator taken from
  `nfl_plays` — never from the table being checked. `--plays-table` is required, not
  optional: that independence *is* the check.

### Fixed

- **`_version.py` was left at `0.1.0` by the 0.2.0 release.** `--version`, the HTTP
  User-Agent and every run log's `library_version` column read that file, so all of 0.2.0's
  run rows are stamped `0.1.0` and cannot be distinguished from 0.1.0's. Both files now say
  `0.3.0`, and a test pins them to each other — nothing else could see the drift, because
  each file was internally consistent.

### Derivation notes

- **An aborted extra point is charged to nobody.** `extra_point_result='aborted'` occurs 31
  times across 1999–2025 (11 seasons, 2002–2014) and `kicker_player_id` is **NULL on every
  one**: a botched snap or hold, no kick attempted, so nflverse attributes no kicker. It is
  counted as **neither made nor missed** — putting it in `xp_missed` would penalise a player
  who did nothing, and it sits outside the verifier's buckets on both sides, so the counts
  still reconcile. The transform excludes null-kicker attempts before scoring (that is the
  grain, not a data-quality filter) and `kicker_scoring` additionally scores an attributed
  abort at `0.0`, so a future season that *does* attribute one cannot kill a backfill. Every
  other unrecognised result string still raises: that fail-loud behaviour is what found this
  case, and an aborted *field goal* still raises, because `field_goal_result` carries only
  `made` (22,991), `missed` (4,195) and `blocked` (587) across all 27 seasons.
- **The kicker is `kicker_player_id`, never inferred from `posteam`.** `kicker_player_id` is
  populated on kickoffs and punts too, and nflverse's `posteam` means different things on
  those plays (on a kickoff it is the *receiving* team) — the same orientation flip that
  cost 0.2.0 two Criticals. Only field-goal and extra-point attempts are read, and on those
  `posteam` is unambiguously the kicking team, so it is safe as a label but is never part of
  the key.
- **A blocked kick is still the kicker's attempt.** Blocked FGs take the miss penalty and
  blocked XPs score zero; neither is filtered out. Dropping them would quietly inflate every
  kicker who had one (19 blocked FGs and 13 blocked XPs in 2024) and would do it invisibly,
  because the total would still look plausible.
- **The scoring convention is swappable by replacing `derive/kicker_scoring.py` alone** —
  pure functions, no pandas, no I/O, no BigQuery. The tier table and the four point values
  live nowhere else in the transform.
- **The shipped floors are not the plan's original 1,000 made FGs / 1,200 XPs.** Measured
  minima across 1999–2025 are far lower than a recent season suggests (2024: 982 and 1,245):
  `fg_made` **731** (2004), `fg_missed` **140** (2013), `xp_made` **1,055** (2001),
  `xp_missed` **5** (2013, before the 2015 XP-distance change). A 1,000 floor would fire on
  22 of 27 healthy seasons, and a guard that fails on healthy data gets switched off. The
  shipped floors — `fg_made` 500, `fg_missed` 90, `xp_made` 700, `xp_missed` 1 — sit below
  every measured minimum and strictly above zero, and a season with too few distinct weeks
  to judge is announced and skipped rather than falsely flagged.

### Known limitations

- **This table covers REG + POST; `ff_points_dst_weekly` is REG only.** The siblings
  genuinely disagree. The kicker table follows `load_pbp()`'s own coverage, matching
  `ff_points_weekly` — the table it exists to complete — so a join between the two does not
  silently drop January. The DST table derives its row set from `load_schedules()` filtered
  to `game_type='REG'`. Weeks 19+ exist in one and not the other.
- **A kicker who attempted nothing in a week has no row**, as opposed to a 0.0 row: the
  play-by-play cannot distinguish "inactive" from "never got in range", and only the latter
  is honestly a zero.
- **Not yet backfilled.** The table, its transform, its command and its guards ship here;
  the 1999–2025 backfill is an operator run.

## 0.2.0 — 2026-08-05

Team-defense fantasy scoring. `ff_points_weekly` covers players only (it derives from
`load_player_stats()`), so a fantasy DST slot previously had no scoreable outcome at all —
measured 2026-07-31, the ADP board carries 12 `DEF` entries per season against zero rows to
join them to.

### Added

- `ff_points_dst_weekly` — weekly team-defense fantasy points at `(season, week, team)` grain,
  derived from `load_pbp()` event flags plus `load_schedules()` final scores. Registered as the
  tenth season-chunked table, so it syncs through the existing `sync-nflverse` command rather
  than a command of its own. Every scored component ships beside its own raw count column, so a
  league with different rules re-derives totals from this table instead of re-deriving the table.
- `verify --checks dst` — four guards, each reporting on its own labelled output line.
  **Arithmetic:** `fantasy_points_dst` must equal the sum of the components it publishes,
  catching a scoring weight changed in one place and not the other, which no schema test can
  see. Deliberately not trusted alone — it recomputes the total from the same columns the
  transform wrote, so a wrong row can still be internally consistent. **Bonus re-derivation:**
  `points_allowed_bonus` must re-derive from `points_allowed_total` via `dst_scoring`, the only
  guard that can see a broken tier table or a NULL score quietly resolved to 0. **Per-season
  signal floor:** no component column may be zero across a whole season — the only guard that
  survives an upstream column *rename*, since an absent column reads as all-zeros by design.
  Seasons too partial to judge are announced and skipped rather than falsely flagged.
  **Coverage:** every `DEF` on the ADP board must have DST rows for that season — the exact
  failure this table exists to prevent, and the one that franchise relocations (SD→LAC,
  STL→LAR, OAK→LV) would otherwise produce silently as a defense scoring zero every week. The
  coverage query is bounded to seasons the DST table actually covers (`ff_adp` is
  forward-looking) and normalizes FFC's `LAR` to nflverse's `LA`; without either bound it
  reported findings on a healthy dataset every run. The coverage half needs `--adp-table` and
  announces itself as SKIPPED when it is absent, rather than reporting a clean run it did not
  perform. An empty table is reported as a failure, not a pass.

### Derivation notes

- **`posteam`/`defteam` orientation is not consistent across play types, so attribution is
  per EVENT rather than per play.** On a run or pass `posteam` is the offense; on a **punt**
  `posteam` is the punting team; on a **kickoff** `posteam` is the *receiving* team. Punt and
  kickoff are inverted relative to each other, so no single upstream column means "this row's
  team". Sacks, interceptions, safeties and blocked kicks aggregate by `defteam` (those occur
  only where the orientation is unambiguous); touchdowns aggregate by `td_team` and fumble
  recoveries by `fumble_recovery_1_team`.
- **Touchdowns are credited by `td_team`, never `defteam`.** On a pick-six the scoring team is
  the defense, but on a punt-return touchdown the returning team was the *receiving* team on
  that play — `defteam` gets exactly one of those two cases wrong. `td_team != posteam` is not
  the answer either: it drops kickoff returns for the same reason. The rule is "every touchdown
  except an offensive scrimmage touchdown" — credit `td_team` unless the scorer was `posteam`
  on a play that is neither a kickoff nor a punt. Measured on 2024 REG: 64 defensive/special-
  teams touchdowns, versus 56 under either of the two naive rules.
- **Fumble recoveries use `fumble_recovery_1_team`, not `fumble_lost` credited to `defteam`.**
  `fumble_lost` is an offensive stat that only *implies* a defensive recovery, and it gets the
  awkward cases wrong (muffed punts, an offense recovering its own fumble, fumbles on a change
  of possession). A recovery counts only when `fumble_recovery_1_team != fumbled_1_team` — that
  difference *is* the change of possession. Testing the recoverer against `defteam` is wrong in
  both directions on punts: on 2024 REG it dropped 23 muffed punts recovered by the punting
  team and wrongly credited 25 self-recoveries (22 punts, 3 passes) where the receiving team
  recovered its own muff.
- **Known exception: safeties are attributed to `defteam`.** A safety on a punt play would be
  credited to the receiving team rather than the punting team's coverage unit. There are zero
  such plays in 2024 REG (all 15 safeties are run/pass/no_play), so the behaviour is documented
  rather than special-cased against no evidence.
- **The row set comes from the schedule, not from events.** A defense that records nothing
  still played and still earns its points-allowed bonus; deriving rows from events would drop
  it, making a quiet game indistinguishable from a bye.
- **`points_allowed_bonus` is NULL — never 0 — when the final score is unresolvable, and
  `fantasy_points_dst` is NULL with it.** Defaulting the bonus to 0 would award a +10 shutout
  bonus to every gap; resolving the *total* to `0.0` is just as wrong the other way, reading as
  "this defense scored nothing" for a week that has not been played. That is the normal case
  mid-season: `sync-nflverse --seasons latest` sees all 18 scheduled weeks from
  `load_schedules()` but only the played ones from `load_pbp()`. Against the live 2026 schedule
  this is 544 rows, all NULL.

### Known limitations

- **`points_allowed_total` is the opponent's TOTAL final score**, deliberately not net of
  points the opponent's own defense or special teams scored against our offense. Some league
  rules exclude those; most public ones do not. The simplification is named in the column
  rather than hidden.
- Regular season only (`game_type = 'REG'`).

## 0.1.0 — 2026-07-30

Initial release: the fantasy layer on top of `nfl-bigquery`. 13 tables plus 2 run logs,
covering fantasy ADP/rankings, nine season-chunked nflverse/derived tables, and an
opt-in coordinators table. Still the first public release — Plan 1's ADP-only surface was
never published, so there is no 0.0.x history to account for.

### Added

**ADP + id bridge**
- `ff_adp` — historical ADP at snapshot grain from Fantasy Football Calculator (2010→,
  no 2025) and MyFantasyLeague (2011→), MERGE-upserted on
  `(source, season, scoring_format, teams, snapshot_date, source_player_id)` so a
  repeated daily sync is a no-op.
- `ff_player_xref` — the 20-system nflverse id bridge, MERGE-upserted on `mfl_id`.
- `gsis_id` resolution on `ff_adp`: exact `mfl_id` join for MFL; normalized-name join
  for FFC, which publishes an id present in no nflverse id system. Ambiguous name
  matches are refused rather than guessed.
- `_ffl_ingest_runs` — chunk-keyed run log powering `sync-adp --resume`.
- `sync-xref` — standalone `ff_player_xref` upsert.

**nine season-chunked nflverse/derived tables, one shared driver (`sync-nflverse`)**
- `ff_opportunity` — weekly fantasy opportunity/usage metrics (2006–2025, 159 cols).
- `snap_counts` — weekly offense/defense/special-teams snap counts (2013–2025), joining
  `ff_player_xref` via `pfr_id`.
- `injuries` — weekly injury reports (2009–2025).
- `depth_charts` — two disjoint upstream schemas (legacy 2001–2024 season-keyed, modern
  2025+ timestamp-keyed) reconciled into one normalized table with a `source_era`
  discriminator (2001–2026).
- `participation` — play-level offense/defense personnel and coverage (2016–2025), with
  `season`/`week` derived from `nflverse_game_id` (upstream publishes neither directly).
- `ftn_charting` — FTN's manually-charted play-level features: play action, motion, RPO,
  screen, blitz (2022–2025).
- `nfl_coaches` — one row per `(game_id, team)`, unpivoted from `load_schedules()`'s
  side-by-side home/away coach columns so a mid-season firing is just two different
  values across two weeks (1999–2026).
- `ff_points_weekly` — half-PPR + positional rank derived from `load_player_stats()`.
  `fantasy_points_ppr` is carried through unchanged from upstream (never recomputed) so
  it stays a correctness oracle a `verify` check can compare a recomputed total against.
- `team_scheme_week` — the marquee derived table: per-`(season, week, team)` scheme
  fingerprint (shotgun/no-huddle/pass rate/PROE/EPA from `load_pbp()`, personnel
  groupings, coverage/pressure, FTN's play-action/motion/RPO/blitz) joined to that
  week's head coach. Every charted-metric rate ships beside its own denominator column
  and is `pd.NA` — never `0.0` — when that denominator is zero.
- `_ffl_nflverse_runs` — a second, separately-keyed `(table_name, season)` run log
  powering `sync-nflverse --resume`; kept independent of ADP's run log rather than
  generalized, since that one is tested and already in production use.

**opt-in**
- `nfl_coordinators` — offensive/defensive coordinator by `(season, team)`, scraped from
  Wikipedia team-season infobox pages (Pro Football Reference returns HTTP 403 and is
  unusable). Every row carries its own provenance (`source`, `confidence`,
  `retrieved_at`) rather than presenting silence as completeness. Deliberately never
  part of `sync-nflverse` — only the explicit `sync-coordinators` command.
- `ff_rankings` — FantasyPros ECR, current snapshot only (not season-chunked).

**verify**
- `--checks adp` — `gsis_id` resolution rate (floor `--min-resolution-rate`, default a
  conservative 0.60), plus grain uniqueness (which, since the grain includes
  `snapshot_date`, also guarantees same-day MERGE idempotency — the same underlying
  fact, reported as two checks for clearer failure messages).
- `--checks points-weekly` — recomputes full-PPR from `fantasy_points_standard +
  receptions` and asserts it still agrees with upstream's carried-through
  `fantasy_points_ppr`.
- `--checks scheme-denominators` — asserts every charted rate in `team_scheme_week` has
  a populated denominator and never exceeds 1.0.
- `--checks participation-coverage` — a whole-table regression guard: measured
  per-season coverage-charting fill still matches the documented shape (0.000 in
  2016–2017, never above .496 afterward), catching an upstream backfill that silently
  changes the data under a shipped chart.

### Known limitations

- **FFC ignores `start_date`/`end_date`**, so intra-preseason ADP drift is
  forward-capture-only and cannot be backfilled.
- **FFC has no 2025 data.** Both `ppr` and `standard` return empty for 2025 while
  2010–2024 and 2026 return normally. This is an upstream gap, not a client bug — the
  sync records those chunks as `empty` and continues.
- FFC `half-ppr` history is shallow; pre-recent seasons return no data.
- **MFL starts at 2011** (2010 and earlier return empty). An earlier probe had this
  wrong as 2012 — it tested 2010 and 2012 and never tried 2011, which turns out to have
  812 rows; corrected once a real backfill was run.
- **`gsis_id` is NULL for 37.9% of `ff_playerids` rows** (66.9% filled among rostered
  QB/RB/WR/TE/K), which caps resolution everywhere it's used. Measured on the real
  2010–2026 backfill (18,309 `ff_adp` rows): FFC resolves 89.9%, MFL 91.8%; the worst
  legitimate `(source, season)` is MFL 2026 at 63.1%, where unresolved rookies have no
  `gsis_id` yet. `--min-resolution-rate`'s 0.60 default sits just below that floor.
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
- **`offense_personnel` is unreliable from 2023 on** — it reports 100% fill while
  carrying defensive players in the offense column (a real 2023 row reads "2 CB, 2 ILB,
  1 OLB, 1 RB, 1 SS, 2 TE, 2 WR"). `team_scheme_week` therefore parses personnel from
  `offense_positions` for 2023+, and from `offense_personnel` for 2016–2022 (where
  `offense_positions` is 0% filled). `personnel_source` records which parser ran.
- **Charted coverage/pressure/FTN metrics are a sample, never a census.**
  `defense_man_zone_type`/`defense_coverage_type` fill is 0.000 in 2016–2017 and never
  exceeds .496 thereafter; `was_pressure` fill is ~.38 pre-2023 and 1.000 from 2023 on.
  Every derived rate in `team_scheme_week` ships beside its own denominator column, and
  a zero denominator yields `pd.NA`, never `0.0`. Out-of-era columns are NULL, not 0.
- **Upstream dtypes are vintage-dependent** — `season`, `week`, `play_id`, and
  `pos_slot` each change type between years across the nflverse feeds this library
  reads (e.g. `injuries.season` is Float64 for older seasons, Int32 for others;
  `participation.play_id` is Float64 in some seasons, Int32 in others). Every one is
  normalized to a stable BigQuery type before writing; a float↔int join left uncast
  silently under-matches rather than erroring, so casts are applied even where a single
  sampled season looks fine on its own.
