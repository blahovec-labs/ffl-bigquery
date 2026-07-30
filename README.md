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
