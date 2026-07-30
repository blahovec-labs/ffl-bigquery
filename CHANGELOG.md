# Changelog

All notable changes to this project will be documented in this file.

## 0.1.0 — 2026-07-29

Initial release: the fantasy layer on top of `nfl-bigquery`.

### Added
- `ff_adp` — historical ADP at snapshot grain from Fantasy Football Calculator
  (2010→) and MyFantasyLeague (2011→), MERGE-upserted on
  `(source, season, scoring_format, teams, snapshot_date, source_player_id)` so a
  repeated daily sync is a no-op.
- `ff_player_xref` — the 20-system nflverse id bridge, MERGE-upserted on `mfl_id`.
- `gsis_id` resolution: exact `mfl_id` join for MFL; normalized-name join for FFC,
  which publishes an id present in no nflverse id system. Ambiguous name matches
  are refused rather than guessed.
- `_ffl_ingest_runs` chunk-keyed run log powering `--resume`.
- `verify` — resolution rate, grain uniqueness, snapshot idempotency.

### Known limitations
- FFC ignores `start_date`/`end_date`, so intra-preseason ADP drift is
  forward-capture-only and cannot be backfilled.
- `gsis_id` is NULL for 37.9% of `ff_playerids` rows, which caps resolution.
  `--min-resolution-rate` defaults to a conservative 0.60. Measured on a real
  2010–2026 backfill (18,309 rows): FFC resolves 89.9%, MFL 91.8%; the worst
  legitimate `(source, season)` is MFL 2026 at 63.1%, where unresolved rookies
  have no `gsis_id` yet. The 0.60 default sits just below that.
- FFC `half-ppr` history is shallow; pre-recent seasons return no data.
- **FFC has no 2025 data.** Both `ppr` and `standard` return empty for 2025
  while 2010–2024 and 2026 return normally. This is an upstream gap, not a
  client bug — the sync records those chunks as `empty` and continues.
- MFL returns nothing for 2010 and earlier; 2011 is its first season with data.
