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

    sn = sub.add_parser(
        "sync-nflverse", help="Sync the nine season-chunked nflverse/derived tables"
    )
    sn.add_argument("--seasons", default="latest",
                    help="e.g. 1999-2025 | 2015,2020 | 2024 | latest")
    sn.add_argument("--dataset", required=True,
                    help="project.dataset -- each table lands at project.dataset.<name>")
    sn.add_argument("--tables", default=None,
                    help="comma-separated subset of table names; default is all nine")
    sn.add_argument("--runs-table", default=None)
    sn.add_argument("--resume", action="store_true")
    sn.add_argument("--dry-run", action="store_true")

    sr = sub.add_parser("sync-rankings", help="Sync ff_rankings (current ECR snapshot)")
    sr.add_argument("--rankings-table", required=True, help="project.dataset.ff_rankings")

    vf = sub.add_parser("verify", help="Run ffl-bigquery data-quality checks")
    vf.add_argument("--checks", default="adp",
                    help="comma-separated subset of adp,points-weekly,"
                         "scheme-denominators,participation-coverage")
    vf.add_argument("--season", type=int, default=None,
                    help="required by the adp/points-weekly/scheme-denominators checks")
    vf.add_argument("--adp-table", default=None)
    # Deliberately conservative: gsis_id is 37.9% NULL in ff_playerids upstream.
    vf.add_argument("--min-resolution-rate", type=float, default=0.60)
    vf.add_argument("--points-weekly-table", default=None)
    vf.add_argument("--ppr-tolerance", type=float, default=0.01)
    vf.add_argument("--scheme-week-table", default=None)
    vf.add_argument("--participation-table", default=None)

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

    if ns.command == "sync-nflverse":
        from google.cloud import bigquery

        from ffl_bigquery.nflverse.driver import run_sync_nflverse_cli

        return run_sync_nflverse_cli(ns, bq_client=bigquery.Client())

    if ns.command == "sync-rankings":
        from google.cloud import bigquery

        from ffl_bigquery.nflverse.tables.rankings import sync_ff_rankings
        from ffl_bigquery.writer import TableRef

        sync_ff_rankings(bigquery.Client(), ref=TableRef.parse(ns.rankings_table))
        return 0

    if ns.command == "verify":
        from google.cloud import bigquery

        from ffl_bigquery.verify import run_verify_cli

        return run_verify_cli(ns, bq_client=bigquery.Client())

    parser.print_help()
    return 0
