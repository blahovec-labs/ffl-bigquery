"""nfl_coordinators parsing + orchestration.

The fixture (tests/fixtures/wikipedia_seasons.json) holds three real Wikipedia
team-season pages, captured once and committed -- see the module docstring in
ffl_bigquery/coordinators/wikipedia.py for why these three:
  * 2019 New England -- has an OC but a BLANK DC (the bleed case)
  * 2005 Green Bay -- has NEITHER field in the infobox at all
  * 2024 Green Bay -- has BOTH
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from google.cloud import bigquery

from ffl_bigquery.coordinators.schema import (
    NFL_COORDINATORS_KEYS,
    NFL_COORDINATORS_SCHEMA,
)
from ffl_bigquery.coordinators.sync import USER_AGENT, run_sync_coordinators
from ffl_bigquery.coordinators.wikipedia import (
    TEAM_WIKI_NAMES,
    WikipediaPage,
    extract_infobox_field,
    fetch_season_wikitext,
    looks_like_bleed,
    page_title,
    parse_coordinators,
    strip_wiki_markup,
)
from ffl_bigquery.schema import spec_names

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "wikipedia_seasons.json").read_text()
)
NE_2019 = FIXTURES["2019_New_England_Patriots_season"]
GB_2005 = FIXTURES["2005_Green_Bay_Packers_season"]
GB_2024 = FIXTURES["2024_Green_Bay_Packers_season"]

RETRIEVED = datetime(2026, 7, 29, tzinfo=UTC)


# --------------------------------------------------------------------------
# The bleed case, made load-bearing in two parts:
#   1) prove the raw extractor actually bleeds into the next infobox key when
#      asked for a blank field (so the rejection test below isn't passing for
#      the trivial reason "the parser never captured anything")
#   2) prove parse_coordinators nonetheless emits no DC row for that page
# --------------------------------------------------------------------------

def test_raw_extraction_of_the_blank_field_bleeds_into_the_next_key():
    """Pins the actual failure mode the brief describes: def_coach is blank on
    the real 2019 Patriots page, and a value-extractor that captures up to the
    next '| key =' line (needed because some infobox values span multiple
    lines) has nothing to stop it consuming the next key's whole line too.
    """
    raw = extract_infobox_field(NE_2019, "def_coach")
    assert raw is not None
    assert "owner" in raw
    assert "|" in raw


def test_blank_def_coach_field_yields_no_row_on_the_real_bleed_page():
    """The load-bearing assertion: given the SAME raw bleed proven above,
    parse_coordinators must still produce zero DC rows for 2019 New England.
    If the '|'/'=' rejection in looks_like_bleed were deleted, this would fail
    -- the DC row would show up with name '| owner ... Robert Kraft' (proven
    by the previous test to be exactly what raw extraction returns).
    """
    rows = parse_coordinators(
        NE_2019, season=2019, team="NE", source="2019 New England Patriots season",
        retrieved_at=RETRIEVED,
    )
    roles = {r["role"]: r for r in rows}
    assert "DC" not in roles
    assert roles["OC"]["name"] == "Josh McDaniels"


def test_looks_like_bleed_flags_pipe_and_equals():
    assert looks_like_bleed("| owner = Robert Kraft") is True
    assert looks_like_bleed("Robert Kraft") is False


# --------------------------------------------------------------------------
# The other two fixture pages
# --------------------------------------------------------------------------

def test_missing_infobox_field_entirely_yields_no_rows():
    """2005 Green Bay's infobox has no off_coach/def_coach key at all (older
    infobox template revision) -- distinct from a blank value.
    """
    rows = parse_coordinators(
        GB_2005, season=2005, team="GB", source="2005 Green Bay Packers season",
        retrieved_at=RETRIEVED,
    )
    assert rows == []


def test_both_coordinators_present_yields_two_rows_with_high_confidence():
    rows = parse_coordinators(
        GB_2024, season=2024, team="GB", source="2024 Green Bay Packers season",
        retrieved_at=RETRIEVED,
    )
    by_role = {r["role"]: r for r in rows}
    assert by_role["OC"]["name"] == "Adam Stenavich"
    assert by_role["DC"]["name"] == "Jeff Hafley"
    assert by_role["OC"]["confidence"] == 1.0
    assert by_role["DC"]["confidence"] == 1.0
    assert all(r["retrieved_at"] == RETRIEVED for r in rows)


def test_grain_is_unique_within_a_single_page_parse():
    rows = parse_coordinators(
        GB_2024, season=2024, team="GB", source="x", retrieved_at=RETRIEVED,
    )
    keys = [(r["season"], r["team"], r["role"]) for r in rows]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------
# Cleanup rules not exercised by the three real fixtures
# --------------------------------------------------------------------------

def test_wikilinks_and_small_templates_are_stripped():
    assert strip_wiki_markup("[[Jeff Hafley]]") == "Jeff Hafley"
    assert strip_wiki_markup("[[Josh McDaniels|McDaniels]]") == "McDaniels"
    assert strip_wiki_markup("[[John Doe]] {{small|(interim)}}") == "John Doe"


def test_a_value_needing_cleanup_gets_lower_confidence_than_a_clean_link():
    # Synthetic infobox snippet: def_coach carries an interim annotation the
    # three real fixture pages happen not to exercise.
    wikitext = (
        "| coach     = [[Someone]]\n"
        "| off_coach = [[Adam Stenavich]]\n"
        "| def_coach = [[John Doe]] {{small|(interim)}}\n"
        "| owner     = [[Nobody]]\n}}"
    )
    rows = parse_coordinators(
        wikitext, season=2020, team="XX", source="x", retrieved_at=RETRIEVED,
    )
    by_role = {r["role"]: r for r in rows}
    assert by_role["DC"]["name"] == "John Doe"
    assert by_role["DC"]["confidence"] < by_role["OC"]["confidence"]
    assert by_role["OC"]["confidence"] == 1.0


# --------------------------------------------------------------------------
# fetch_season_wikitext: missing page -> None, no exception
# --------------------------------------------------------------------------

def test_fetch_returns_none_for_a_missing_page_without_raising():
    session = MagicMock()
    session.get_json.return_value = {
        "error": {"code": "missingtitle", "info": "The page does not exist"}
    }
    page = fetch_season_wikitext(session, page_title="1899_Nonexistent_Team_season")
    assert page is None


def test_fetch_returns_wikitext_and_title_on_success():
    session = MagicMock()
    session.get_json.return_value = {
        "parse": {"title": "2024 Green Bay Packers season", "wikitext": GB_2024}
    }
    page = fetch_season_wikitext(session, page_title="2024_Green_Bay_Packers_season")
    assert isinstance(page, WikipediaPage)
    assert page.title == "2024 Green Bay Packers season"
    assert page.wikitext == GB_2024


# --------------------------------------------------------------------------
# page_title / TEAM_WIKI_NAMES
# --------------------------------------------------------------------------

def test_page_title_matches_the_real_fixture_titles():
    assert page_title(2019, "NE") == "2019_New_England_Patriots_season"
    assert page_title(2005, "GB") == "2005_Green_Bay_Packers_season"
    assert page_title(2024, "GB") == "2024_Green_Bay_Packers_season"


def test_team_wiki_names_covers_all_32_current_teams():
    assert len(TEAM_WIKI_NAMES) == 32


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def test_schema_column_order():
    assert spec_names(NFL_COORDINATORS_SCHEMA) == [
        "season", "team", "role", "name", "source", "confidence",
        "retrieved_at", "ingested_at",
    ]


def test_grain_key_is_season_team_role():
    assert NFL_COORDINATORS_KEYS == ["season", "team", "role"]


def test_name_gotchas_carry_the_measured_fill_rate():
    # 46.2% (473/1,024, full backfill) is the headline figure a user should
    # see; 37% is kept only as color from an earlier, smaller sample -- both
    # must be present, but 46.2% is the one that must not go stale.
    name_spec = next(c for c in NFL_COORDINATORS_SCHEMA if c.name == "name")
    assert any("46.2%" in g for g in name_spec.gotchas)
    assert any("37%" in g for g in name_spec.gotchas)


def test_role_is_required_and_restricted_to_oc_dc():
    role_spec = next(c for c in NFL_COORDINATORS_SCHEMA if c.name == "role")
    assert role_spec.mode == "REQUIRED"
    assert role_spec.valid_values == ["OC", "DC"]


# --------------------------------------------------------------------------
# sync orchestration (no real network -- session is a MagicMock)
# --------------------------------------------------------------------------

def _ns(**kw):
    base = dict(
        seasons="2024", teams="GB", coordinators_table="p.d.nfl_coordinators",
        min_interval=0.0, dry_run=False,
    )
    base.update(kw)
    return MagicMock(**base)


def test_dry_run_makes_no_requests_and_no_writes():
    session, writer = MagicMock(), MagicMock()
    rc = run_sync_coordinators(
        _ns(dry_run=True), bq_client=MagicMock(spec=bigquery.Client),
        session=session, writer=writer, now=RETRIEVED,
    )
    assert rc == 0
    assert not session.get_json.called
    assert not writer.merge_rows.called


def test_unknown_team_raises_before_any_request():
    session = MagicMock()
    with pytest.raises(ValueError, match="ZZ"):
        run_sync_coordinators(
            _ns(teams="ZZ"), bq_client=MagicMock(spec=bigquery.Client),
            session=session, writer=MagicMock(), now=RETRIEVED,
        )
    assert not session.get_json.called


def test_successful_sync_merges_parsed_rows_with_the_grain_keys():
    session = MagicMock()
    session.get_json.return_value = {
        "parse": {"title": "2024 Green Bay Packers season", "wikitext": GB_2024}
    }
    writer = MagicMock()
    captured = {}
    writer.merge_rows.side_effect = lambda **kw: (
        captured.update(kw) or len(kw["df"])
    )
    rc = run_sync_coordinators(
        _ns(), bq_client=MagicMock(spec=bigquery.Client),
        session=session, writer=writer, now=RETRIEVED,
    )
    assert rc == 0
    assert writer.create_table_if_missing.called
    assert captured["keys"] == ["season", "team", "role"]
    df: pd.DataFrame = captured["df"]
    assert set(df["role"]) == {"OC", "DC"}
    assert (df["season"] == 2024).all()
    assert (df["team"] == "GB").all()
    assert list(df.columns) == spec_names(NFL_COORDINATORS_SCHEMA)


def test_missing_page_writes_nothing_and_does_not_raise():
    session = MagicMock()
    session.get_json.return_value = {"error": {"code": "missingtitle"}}
    writer = MagicMock()
    rc = run_sync_coordinators(
        _ns(seasons="1899"), bq_client=MagicMock(spec=bigquery.Client),
        session=session, writer=writer, now=RETRIEVED,
    )
    assert rc == 0
    assert not writer.merge_rows.called


def test_user_agent_is_descriptive():
    assert "ffl-bigquery" in USER_AGENT
