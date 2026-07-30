"""ffl-bigquery CLI entrypoint."""
from __future__ import annotations

import argparse
import logging

from ffl_bigquery._version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ffl-bigquery")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    sa = sub.add_parser("sync-adp", help="Fetch ADP snapshots and write to BigQuery")
    # Defaults reflect measured coverage: FFC from 2010, MFL from 2011.
    sa.add_argument("--seasons", default="2010-2026",
                    help="e.g. 2010-2026 | 2015,2020 | 2024 | latest")
    sa.add_argument("--sources", default="ffc,mfl")
    sa.add_argument("--formats", default="ppr,standard")
    sa.add_argument("--teams", default="12", help="comma-separated league sizes")
    sa.add_argument("--adp-table", required=True, help="project.dataset.ff_adp")
    sa.add_argument("--xref-table", required=True,
                    help="project.dataset.ff_player_xref")
    sa.add_argument("--runs-table", default=None)
    sa.add_argument("--min-interval", type=float, default=1.0,
                    help="seconds between third-party requests")
    sa.add_argument("--resume", action="store_true")
    sa.add_argument("--dry-run", action="store_true")

    sx = sub.add_parser("sync-xref", help="Upsert the ff_player_xref id bridge")
    sx.add_argument("--xref-table", required=True)
    sx.add_argument("--dry-run", action="store_true")

    vf = sub.add_parser("verify", help="Run ff_adp data-quality checks")
    vf.add_argument("--season", type=int, required=True)
    vf.add_argument("--adp-table", required=True)
    # Deliberately conservative: gsis_id is 37.9% NULL in ff_playerids upstream.
    vf.add_argument("--min-resolution-rate", type=float, default=0.60)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    ns = parser.parse_args(argv)
    if not getattr(ns, "command", None):
        parser.print_help()
        return 0

    if ns.command == "sync-adp":
        from google.cloud import bigquery

        from ffl_bigquery.adp.sync import run_sync_adp

        if ns.runs_table is None:
            project, dataset, _ = ns.adp_table.split(".")
            ns.runs_table = f"{project}.{dataset}._ffl_ingest_runs"
        return run_sync_adp(ns, bq_client=bigquery.Client())

    if ns.command == "sync-xref":
        from google.cloud import bigquery

        from ffl_bigquery.xref.sync import run_sync_xref

        return run_sync_xref(ns, bq_client=bigquery.Client())

    if ns.command == "verify":
        from google.cloud import bigquery

        from ffl_bigquery.verify.adp import run_verify

        return run_verify(ns, bq_client=bigquery.Client())

    parser.print_help()
    return 0
