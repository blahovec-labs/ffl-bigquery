# ffl-bigquery

Fantasy football ADP → BigQuery. The fantasy layer on top of
[`nfl-bigquery`](https://github.com/blahovec-labs/nfl-bigquery). Sixth in the `*-bigquery`
family (`statcast-bigquery`, `yfinance-bigquery`, `nhl-bigquery`, `nhl-hut-bigquery`,
`nfl-bigquery`).

## Install

    pip install ffl-bigquery

Requires Python 3.11+. Writing to BigQuery needs Application Default Credentials with
permission to create/query tables in the target dataset.

## What it writes

| Table | Grain | Written by |
| --- | --- | --- |
| `ff_adp` | one row per player per ADP snapshot per league configuration: `(source, season, scoring_format, teams, snapshot_date, source_player_id)` | `sync-adp` |
| `ff_player_xref` | one row per player, keyed on `mfl_id` — the 20-system nflverse id bridge (MFL, Sportradar, FantasyPros, gsis, PFF, Sleeper, NFL, ESPN, Yahoo, Fleaflicker, CBS, PFR, CFBref, Rotowire, Rotoworld, KTC, Stats, StatsGlobal, FantasyData, Swish) | `sync-xref` |
| `_ffl_ingest_runs` | one row per `(source, season, scoring_format, teams)` chunk attempted, powering `--resume` | `sync-adp` |

Every ADP row carries a `gsis_id` resolved against `ff_player_xref` where possible: an
exact `mfl_id` join for MyFantasyLeague rows, a normalized-name join for Fantasy Football
Calculator rows (FFC's own player id appears in no nflverse id system). Ambiguous name
matches are refused rather than guessed, so `gsis_id` is left `NULL` — never wrong.

## CLI

    ffl-bigquery --version

    ffl-bigquery sync-adp \
      --adp-table PROJECT.DATASET.ff_adp \
      --xref-table PROJECT.DATASET.ff_player_xref \
      --seasons 2010-2026 --sources ffc,mfl --formats ppr,standard --teams 12 \
      --resume

    ffl-bigquery sync-xref --xref-table PROJECT.DATASET.ff_player_xref

    ffl-bigquery verify --season 2026 --adp-table PROJECT.DATASET.ff_adp

`sync-adp` fans a season/source/format/teams matrix out into independent chunks, so one
dead upstream source degrades coverage instead of aborting the whole run; `--resume`
skips chunks already recorded `success` or `empty` in the runs table. `verify` checks
`gsis_id` resolution rate (floor `--min-resolution-rate`, default a conservative `0.60`
— see Known limitations below), grain uniqueness, and same-day MERGE idempotency. Both
`sync-adp` and `sync-xref` accept `--dry-run` to print what would happen without writing.

## Coverage windows

- Fantasy Football Calculator: seasons 2010→present. `half-ppr` history is shallow —
  pre-recent seasons return no data for that format.
- MyFantasyLeague: seasons 2011→present. No per-format endpoint; only `ppr`/`standard`
  can be requested from it (the scoring axis is a single `IS_PPR` flag).

See `CHANGELOG.md` for the full list of known limitations, including why `ff_adp`
snapshot-date drift is forward-capture-only for FFC.

## Data sources & attribution

- **nflverse** via [`nflreadpy`](https://nflreadpy.nflverse.com/) — player IDs.
- **[Fantasy Football Calculator](https://fantasyfootballcalculator.com/)** — historical ADP
  (2010→present). Their ADP REST API is free for personal and commercial use; this project
  provides attribution as requested. Data updates once daily; do not poll frequently.
- **[MyFantasyLeague](https://www.myfantasyleague.com/)** — historical ADP (2011→present)
  via the free `export?TYPE=adp` endpoint.

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
