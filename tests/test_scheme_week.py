import pandas as pd

from ffl_bigquery.derive.scheme_week import (
    TEAM_SCHEME_WEEK_SCHEMA,
    derive_scheme_week,
)
from ffl_bigquery.schema import spec_names


def _pbp() -> pd.DataFrame:
    return pd.DataFrame({
        "season": [2019] * 4, "week": [1] * 4, "posteam": ["GB"] * 4,
        "game_id": ["g"] * 4, "play_id": [1.0, 2.0, 3.0, 4.0],
        "play_type": ["pass", "run", "pass", "run"],
        "shotgun": [1, 0, 1, 0], "no_huddle": [1, 0, 0, 0],
        "pass_oe": [10.0, -2.0, 6.0, -4.0], "epa": [0.5, -0.2, 0.3, 0.1],
    })


def _coaches() -> pd.DataFrame:
    return pd.DataFrame({"season": [2019], "week": [1], "team": ["GB"],
                         "head_coach": ["Matt LaFleur"]})


def test_base_metrics_are_available_without_any_charting():
    out = derive_scheme_week(_pbp(), pd.DataFrame(), pd.DataFrame(), _coaches(), 2019)
    r = out.iloc[0]
    assert list(out.columns) == spec_names(TEAM_SCHEME_WEEK_SCHEMA)
    assert r["plays"] == 4
    assert r["shotgun_rate"] == 0.5
    assert r["no_huddle_rate"] == 0.25
    assert r["pass_rate"] == 0.5
    assert r["proe"] == 2.5           # mean(10,-2,6,-4)
    assert r["head_coach"] == "Matt LaFleur"


def test_out_of_era_columns_are_null_not_zero():
    # A zero would read as "they never blitzed" rather than "nobody charted it".
    out = derive_scheme_week(_pbp(), pd.DataFrame(), pd.DataFrame(), _coaches(), 2019)
    r = out.iloc[0]
    for col in ("blitz_rate", "play_action_rate", "motion_rate", "rpo_rate"):
        assert pd.isna(r[col]), f"{col} must be NULL when unavailable, not 0"
    assert pd.isna(r["plays_charted_ftn"]) or r["plays_charted_ftn"] == 0


def test_every_rate_has_a_denominator_column_in_the_schema():
    names = spec_names(TEAM_SCHEME_WEEK_SCHEMA)
    for denom in ("plays_with_personnel", "plays_charted_coverage",
                  "plays_charted_pressure", "plays_charted_ftn"):
        assert denom in names


def test_coverage_rates_use_the_charted_denominator_not_total_plays():
    # 4 plays, only 2 charted for coverage, 1 of them man.
    part = pd.DataFrame({
        "nflverse_game_id": ["g"] * 4, "play_id": [1, 2, 3, 4],
        "defense_man_zone_type": ["MAN_COVERAGE", "ZONE_COVERAGE", "", None],
        "defense_coverage_type": ["COVER_1", "COVER_3", "", None],
        "defenders_in_box": [6.0, 7.0, None, None],
        "was_pressure": [True, False, None, None],
        "offense_positions": [None] * 4, "offense_personnel": [None] * 4,
    })
    out = derive_scheme_week(_pbp(), part, pd.DataFrame(), _coaches(), 2019)
    r = out.iloc[0]
    assert r["plays_charted_coverage"] == 2
    assert r["man_rate"] == 0.5      # 1 of 2 CHARTED, not 1 of 4 plays
    assert r["zone_rate"] == 0.5


def test_ftn_metrics_appear_when_ftn_rows_are_present():
    ftn = pd.DataFrame({
        "nflverse_game_id": ["g"] * 4, "nflverse_play_id": [1, 2, 3, 4],
        "is_play_action": [True, False, False, False],
        "is_motion": [True, True, False, False],
        "is_rpo": [False] * 4, "is_screen_pass": [False] * 4,
        "n_blitzers": [1, 0, 0, 2], "n_pass_rushers": [5, 4, 4, 6],
    })
    out = derive_scheme_week(_pbp(), pd.DataFrame(), ftn, _coaches(), 2023)
    r = out.iloc[0]
    assert r["plays_charted_ftn"] == 4
    assert r["play_action_rate"] == 0.25
    assert r["motion_rate"] == 0.5
    assert r["blitz_rate"] == 0.5     # 2 of 4 had >=1 blitzer
    assert r["avg_pass_rushers"] == 4.75


def test_every_rate_is_between_zero_and_one():
    out = derive_scheme_week(_pbp(), pd.DataFrame(), pd.DataFrame(), _coaches(), 2019)
    for c in [c for c in out.columns if c.endswith("_rate")]:
        v = out[c].iloc[0]
        assert pd.isna(v) or 0.0 <= v <= 1.0


def test_grain_is_one_row_per_season_week_team():
    pbp = pd.concat([_pbp(), _pbp().assign(posteam="CHI")], ignore_index=True)
    coaches = pd.concat([_coaches(),
                         _coaches().assign(team="CHI", head_coach="Matt Nagy")],
                        ignore_index=True)
    out = derive_scheme_week(pbp, pd.DataFrame(), pd.DataFrame(), coaches, 2019)
    assert len(out) == 2
    assert not out.duplicated(subset=["season", "week", "team"]).any()
