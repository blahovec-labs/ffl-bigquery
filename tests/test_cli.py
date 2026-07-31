import pytest

from ffl_bigquery._version import __version__
from ffl_bigquery.cli import build_parser, main


def test_version_flag_exits_zero_and_prints_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_returns_zero(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_sync_adp_defaults_match_the_measured_source_coverage():
    ns = build_parser().parse_args(
        ["sync-adp", "--adp-table", "p.d.ff_adp", "--xref-table", "p.d.x"]
    )
    assert ns.seasons == "2010-2026"
    assert ns.sources == "ffc,mfl"
    assert ns.teams == "12"
    assert ns.resume is False


def test_verify_min_resolution_rate_default_is_conservative():
    ns = build_parser().parse_args(
        ["verify", "--season", "2026", "--adp-table", "p.d.ff_adp"]
    )
    # 0.85 is unreachable: gsis_id is 37.9% NULL upstream. Task 16 tightens this.
    assert ns.min_resolution_rate == 0.60


def test_sync_adp_requires_adp_and_xref_tables():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync-adp"])


def test_dry_run_is_available_on_both_sync_commands():
    for cmd in (["sync-adp", "--adp-table", "p.d.a", "--xref-table", "p.d.x"],
                ["sync-xref", "--xref-table", "p.d.x"]):
        assert build_parser().parse_args([*cmd, "--dry-run"]).dry_run is True


def test_sync_nflverse_defaults():
    ns = build_parser().parse_args(["sync-nflverse", "--dataset", "p.d"])
    assert ns.seasons == "latest"
    assert ns.dataset == "p.d"
    assert ns.tables is None
    assert ns.runs_table is None
    assert ns.resume is False
    assert ns.dry_run is False


def test_sync_nflverse_requires_dataset():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync-nflverse"])


def test_sync_nflverse_accepts_explicit_seasons_tables_and_flags():
    ns = build_parser().parse_args([
        "sync-nflverse", "--dataset", "p.d", "--seasons", "2020-2022",
        "--tables", "ff_points_weekly,team_scheme_week",
        "--runs-table", "p.d.custom_runs", "--resume", "--dry-run",
    ])
    assert ns.seasons == "2020-2022"
    assert ns.tables == "ff_points_weekly,team_scheme_week"
    assert ns.runs_table == "p.d.custom_runs"
    assert ns.resume is True
    assert ns.dry_run is True


def test_no_sync_coordinators_subcommand():
    # Task 11 explicitly excludes it -- that table is a later task and does
    # not exist yet.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync-coordinators"])


def test_sync_rankings_requires_rankings_table():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync-rankings"])


def test_sync_rankings_parses_table():
    ns = build_parser().parse_args(["sync-rankings", "--rankings-table", "p.d.ff_rankings"])
    assert ns.rankings_table == "p.d.ff_rankings"


def test_verify_defaults_to_the_adp_check_only():
    ns = build_parser().parse_args(["verify"])
    assert ns.checks == "adp"
    assert ns.season is None
    assert ns.adp_table is None


def test_verify_accepts_new_check_flags():
    ns = build_parser().parse_args([
        "verify", "--checks", "points-weekly,scheme-denominators",
        "--season", "2023", "--points-weekly-table", "p.d.ff_points_weekly",
        "--scheme-week-table", "p.d.team_scheme_week", "--ppr-tolerance", "0.05",
    ])
    assert ns.checks == "points-weekly,scheme-denominators"
    assert ns.points_weekly_table == "p.d.ff_points_weekly"
    assert ns.scheme_week_table == "p.d.team_scheme_week"
    assert ns.ppr_tolerance == 0.05
