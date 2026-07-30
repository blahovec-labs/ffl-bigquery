# ffl-bigquery

Fantasy football ADP + coaching/scheme context → BigQuery. The fantasy layer on top of
[`nfl-bigquery`](https://github.com/blahovec-labs/nfl-bigquery). Sixth in the `*-bigquery`
family (`statcast-bigquery`, `yfinance-bigquery`, `nhl-bigquery`, `nhl-hut-bigquery`,
`nfl-bigquery`).

> Under active development — install/sync usage will be documented as the CLI lands.

## Data sources & attribution

- **nflverse** via [`nflreadpy`](https://nflreadpy.nflverse.com/) — player IDs.
- **[Fantasy Football Calculator](https://fantasyfootballcalculator.com/)** — historical ADP
  (2010→present). Their ADP REST API is free for personal and commercial use; this project
  provides attribution as requested. Data updates once daily; do not poll frequently.
- **[MyFantasyLeague](https://www.myfantasyleague.com/)** — historical ADP (2012→present)
  via the free `export?TYPE=adp` endpoint.

## Local development (Windows / TLS interception)

If HTTPS requests fail with `unable to get local issuer certificate`, a local security product
is intercepting TLS. Use the system trust store rather than disabling verification:

    uv run --native-tls --with truststore pytest -v

MIT licensed.
