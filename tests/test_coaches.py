import pandas as pd

from ffl_bigquery.coaches.schema import NFL_COACHES_SCHEMA
from ffl_bigquery.coaches.transform import transform_coaches
from ffl_bigquery.schema import spec_names


def _sched() -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": ["2019_01_GB_CHI", "2019_02_GB_MIN"],
        "season": [2019, 2019],
        "week": [1, 2],
        "gameday": ["2019-09-05", "2019-09-15"],
        "home_team": ["CHI", "MIN"],
        "away_team": ["GB", "GB"],
        "home_coach": ["Matt Nagy", "Mike Zimmer"],
        "away_coach": ["Matt LaFleur", "Matt LaFleur"],
    })


def test_each_game_becomes_two_rows_one_per_team():
    out = transform_coaches(_sched(), 2019)
    assert len(out) == 4
    assert list(out.columns) == spec_names(NFL_COACHES_SCHEMA)


def test_home_and_away_coaches_collapse_into_one_column_keyed_by_team():
    out = transform_coaches(_sched(), 2019)
    g1 = out[out["game_id"] == "2019_01_GB_CHI"].set_index("team")
    assert g1.loc["CHI", "head_coach"] == "Matt Nagy"
    assert g1.loc["GB", "head_coach"] == "Matt LaFleur"
    assert bool(g1.loc["CHI", "is_home"]) is True
    assert bool(g1.loc["GB", "is_home"]) is False


def test_opponent_is_the_other_team():
    out = transform_coaches(_sched(), 2019).set_index(["game_id", "team"])
    assert out.loc[("2019_01_GB_CHI", "CHI"), "opponent"] == "GB"
    assert out.loc[("2019_01_GB_CHI", "GB"), "opponent"] == "CHI"


def test_grain_is_unique_on_game_id_and_team():
    out = transform_coaches(_sched(), 2019)
    assert not out.duplicated(subset=["game_id", "team"]).any()


def test_a_midseason_change_is_representable_without_special_casing():
    # The same team carries different coaches in different weeks; per-game grain
    # stores that as data rather than needing a rule.
    sched = pd.DataFrame({
        "game_id": ["2018_12_X_GB", "2018_14_Y_GB"],
        "season": [2018, 2018], "week": [12, 14],
        "gameday": ["2018-11-25", "2018-12-09"],
        "home_team": ["GB", "GB"], "away_team": ["X", "Y"],
        "home_coach": ["Mike McCarthy", "Joe Philbin"],
        "away_coach": ["A", "B"],
    })
    gb = transform_coaches(sched, 2018)
    gb = gb[gb["team"] == "GB"].sort_values("week")
    assert list(gb["head_coach"]) == ["Mike McCarthy", "Joe Philbin"]


def test_season_comes_from_the_loop_not_upstream():
    out = transform_coaches(_sched(), 1999)
    assert (out["season"] == 1999).all()


def test_game_date_is_a_date_not_a_string():
    out = transform_coaches(_sched(), 2019)
    assert str(out["game_date"].iloc[0]) == "2019-09-05"
