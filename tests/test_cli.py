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
