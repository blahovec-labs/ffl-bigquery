# Changelog

All notable changes to this project will be documented in this file.

## 0.1.0 — 2026-07-29

Initial release: the fantasy layer on top of `nfl-bigquery`.

### Added
- `ff_adp` — historical ADP at snapshot grain from Fantasy Football Calculator
  (2010→) and MyFantasyLeague (2012→), MERGE-upserted on
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
  `--min-resolution-rate` defaults to a conservative 0.60.
- FFC `half-ppr` history is shallow; pre-recent seasons return no data.
