import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ffl_bigquery.adp.mfl import MFL_BASE_URL, fetch_mfl

FIXTURES = Path(__file__).parent / "fixtures"


def _session(payload: dict) -> MagicMock:
    s = MagicMock()
    s.get_json.return_value = payload
    return s


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_url_embeds_season_and_sends_type_adp_json():
    s = _session(_fixture("mfl_adp_2015.json"))
    fetch_mfl(s, season=2015)
    url, params = s.get_json.call_args[0]
    assert url == f"{MFL_BASE_URL}/2015/export"
    assert params["TYPE"] == "adp"
    assert params["JSON"] == 1


def test_optional_slicers_are_omitted_when_none_and_mapped_when_set():
    s = _session(_fixture("mfl_adp_2015.json"))
    fetch_mfl(s, season=2025)
    assert "FCOUNT" not in s.get_json.call_args[0][1]

    s2 = _session(_fixture("mfl_adp_2015.json"))
    fetch_mfl(s2, season=2025, teams=12, is_ppr=True, is_keeper=False, is_mock=False)
    params = s2.get_json.call_args[0][1]
    assert params["FCOUNT"] == 12
    assert params["IS_PPR"] == 1
    assert params["IS_KEEPER"] == 0
    assert params["IS_MOCK"] == 0


def test_parses_player_list_and_totals():
    r = fetch_mfl(_session(_fixture("mfl_adp_2015.json")), season=2015)
    assert r.is_empty is False
    assert len(r.players) == 2
    assert r.total_drafts == 15877
    assert r.total_picks == 421904
    # values remain source-shaped strings here; casting happens in transform
    assert r.players[0]["averagePick"] == "9.11"


def test_single_player_object_is_normalized_to_a_list():
    r = fetch_mfl(_session(_fixture("mfl_adp_single_player.json")), season=2026)
    assert isinstance(r.players, list)
    assert len(r.players) == 1
    assert r.players[0]["id"] == "16161"


def test_pre_2012_response_without_player_key_is_empty():
    r = fetch_mfl(_session(_fixture("mfl_adp_empty_2005.json")), season=2005)
    assert r.is_empty is True
    assert r.players == []
    assert r.total_drafts == 0


def test_missing_adp_envelope_is_empty():
    r = fetch_mfl(_session({"version": "1.0"}), season=2005)
    assert r.is_empty is True
    assert r.total_drafts is None


@pytest.mark.network
def test_live_mfl_2015_has_players():
    from ffl_bigquery.http import ThrottledSession
    s = ThrottledSession(user_agent="ffl-bigquery/0.1.0 (+tests)")
    r = fetch_mfl(s, season=2015)
    assert r.is_empty is False
