# Changelog

All notable changes to this project will be documented in this file.

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
  team and wrongly credited 22 muffs the receiving team recovered itself.
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
