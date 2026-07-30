import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ffl_bigquery.adp.ffc import FFC_BASE_URL, fetch_ffc

FIXTURES = Path(__file__).parent / "fixtures"


def _session(payload: dict) -> MagicMock:
    s = MagicMock()
    s.get_json.return_value = payload
    return s


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_requests_the_format_in_the_path_and_year_teams_as_params():
    s = _session(_fixture("ffc_ppr_2015.json"))
    fetch_ffc(s, season=2015, scoring_format="ppr", teams=12)
    url, params = s.get_json.call_args[0]
    assert url == f"{FFC_BASE_URL}/ppr"
    assert params == {"teams": 12, "year": 2015}


def test_parses_players_and_window_metadata():
    r = fetch_ffc(_session(_fixture("ffc_ppr_2015.json")),
                  season=2015, scoring_format="ppr", teams=12)
    assert r.is_empty is False
    assert len(r.players) == 2
    assert r.total_drafts == 844
    assert r.window_start == "2015-09-06"
    assert r.window_end == "2015-09-09"
    assert r.players[0]["name"] == "Adrian Peterson"
    assert r.players[0]["high"] == 1 and r.players[0]["low"] == 5


def test_status_success_with_empty_players_is_empty_not_an_error():
    r = fetch_ffc(_session(_fixture("ffc_empty_success.json")),
                  season=2007, scoring_format="standard", teams=12)
    assert r.is_empty is True
    assert r.players == []
    # metadata is still carried through even on an empty year
    assert r.total_drafts == 998


def test_status_error_is_empty_not_a_raise():
    r = fetch_ffc(_session(_fixture("ffc_empty_error.json")),
                  season=2007, scoring_format="ppr", teams=12)
    assert r.is_empty is True
    assert r.players == []
    assert r.total_drafts is None


def test_missing_players_key_entirely_is_empty():
    r = fetch_ffc(_session({"status": "Success"}),
                  season=2007, scoring_format="ppr", teams=12)
    assert r.is_empty is True


def test_rejects_unknown_scoring_format_before_making_a_request():
    s = _session({})
    with pytest.raises(ValueError, match="unknown scoring_format"):
        fetch_ffc(s, season=2026, scoring_format="superflex", teams=12)
    assert not s.get_json.called


@pytest.mark.network
def test_live_ffc_current_season_returns_players():
    import truststore
    truststore.inject_into_ssl()
    from ffl_bigquery.http import ThrottledSession
    s = ThrottledSession(user_agent="ffl-bigquery/0.1.0 (+tests)")
    r = fetch_ffc(s, season=2026, scoring_format="ppr", teams=12)
    assert r.is_empty is False
    assert r.total_drafts and r.total_drafts > 0
