"""Schema shape and attribution for ff_points_k_weekly.

Mirrors tests/test_dst_weekly.py. The attribution tests here exist for the
same reason that file's do: the DST build lost two Criticals to play-type
orientation, and a fixture built only from the easy shape cannot see them.
"""
import pandas as pd

from ffl_bigquery.derive.kicker_weekly import (
    FF_POINTS_K_WEEKLY_SCHEMA,
    derive_kicker_weekly,
)
from ffl_bigquery.schema import spec_names


def test_grain_and_clustering_columns_present():
    names = spec_names(FF_POINTS_K_WEEKLY_SCHEMA)
    for col in ("season", "week", "gsis_id"):
        assert col in names


def test_every_scored_component_ships_beside_the_total():
    # A consumer re-scoring under different league rules -- and, more
    # immediately, the independent verifier -- needs the counts, not just our
    # total. A table carrying only the total cannot be audited without
    # trusting the transform, which is exactly how the DST Criticals survived.
    names = spec_names(FF_POINTS_K_WEEKLY_SCHEMA)
    for col in ("fg_made", "fg_missed", "fg_made_yards_max", "xp_made",
                "xp_missed"):
        assert col in names, f"missing raw count column: {col}"
    assert "fantasy_points_kicker" in names


def test_clustering_keys_are_not_float():
    # SeasonRangePartition clusters on week+gsis_id. BigQuery rejects FLOAT64
    # clustering keys with a 400 at table-creation time -- caught live during
    # the 0.1.0 build, so it is asserted here at the schema level too.
    by_name = {s.name: s for s in FF_POINTS_K_WEEKLY_SCHEMA}
    assert by_name["season"].type == "INT64"
    assert by_name["week"].type == "INT64"
    assert by_name["gsis_id"].type == "STRING"


def test_counts_are_int_and_points_are_float():
    by_name = {s.name: s for s in FF_POINTS_K_WEEKLY_SCHEMA}
    for col in ("fg_made", "fg_missed", "fg_made_yards_max", "xp_made",
                "xp_missed"):
        assert by_name[col].type == "INT64", f"{col} should be a count"
    assert by_name["fantasy_points_kicker"].type == "FLOAT64"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_KICKER = "00-0033862"        # one kicker id
_KICKER_NAME = "H.Butker"
_OTHER_KICKER = "00-0036854"  # a SECOND kicker on the SAME team (see below)
_OTHER_NAME = "M.Araiza"


def _frame(rows: list[dict]) -> pd.DataFrame:
    """A play-by-play frame carrying every column derive_kicker_weekly reads."""
    df = pd.DataFrame(rows)
    if "game_id" not in df.columns:
        df["game_id"] = "2024_01_BAL_KC"
    df["game_id"] = df["game_id"].fillna("2024_01_BAL_KC")
    df["season"] = 2024
    if "week" not in df.columns:
        df["week"] = 1
    for col in ("posteam", "defteam", "play_type", "kicker_player_id",
                "kicker_player_name", "field_goal_result",
                "extra_point_result", "kick_distance"):
        if col not in df.columns:
            df[col] = None
    return df


def _fg(result: str, distance: float | None, **kw) -> dict:
    row = {"play_type": "field_goal", "posteam": "KC", "defteam": "BAL",
           "kicker_player_id": _KICKER, "kicker_player_name": _KICKER_NAME,
           "field_goal_result": result, "kick_distance": distance}
    row.update(kw)
    return row


def _xp(result: str, **kw) -> dict:
    row = {"play_type": "extra_point", "posteam": "KC", "defteam": "BAL",
           "kicker_player_id": _KICKER, "kicker_player_name": _KICKER_NAME,
           "extra_point_result": result, "kick_distance": 33.0}
    row.update(kw)
    return row


def _fixture() -> pd.DataFrame:
    """One kicker, one week: three makes at three different tiers, one miss,
    one BLOCKED field goal, three good extra points and one failed one."""
    return _frame([
        _fg("made", 25.0),
        _fg("made", 45.0),
        _fg("made", 52.0),
        _fg("missed", 48.0),
        _fg("blocked", 35.0),
        _xp("good"),
        _xp("good"),
        _xp("good"),
        _xp("failed"),
    ])


def _one_row(pbp: pd.DataFrame) -> pd.Series:
    out = derive_kicker_weekly(pbp, 2024)
    assert len(out) == 1, f"expected exactly one kicker-week, got {len(out)}"
    return out.iloc[0]


# ---------------------------------------------------------------------------
# Components and total
# ---------------------------------------------------------------------------


def test_a_kickers_week_totals_and_components():
    row = _one_row(_fixture())
    assert row.fg_made == 3
    assert row.fg_missed == 2          # the block counts as a miss
    assert row.xp_made == 3
    assert row.xp_missed == 1
    assert row.fg_made_yards_max == 52
    # 3 + 4 + 5 (makes) - 1 - 1 (miss + block) + 3 (XPs) = 13
    assert row.fantasy_points_kicker == 13.0


def test_the_row_carries_the_kicker_and_the_kicking_team():
    row = _one_row(_fixture())
    assert row.season == 2024
    assert row.week == 1
    assert row.gsis_id == _KICKER
    assert row.player_name == _KICKER_NAME
    assert row.team == "KC"


def test_a_blocked_field_goal_is_still_the_kickers_attempt():
    """Filtering blocked kicks out would quietly inflate every kicker who had
    one -- 19 blocked FGs in 2024 alone. The block takes the miss penalty."""
    row = _one_row(_frame([_fg("blocked", 35.0)]))
    assert row.fg_made == 0
    assert row.fg_missed == 1
    assert row.fantasy_points_kicker == -1.0


def test_a_blocked_extra_point_costs_nothing_but_is_still_counted():
    """13 blocked XPs in 2024. A blocked XP is worth 0.0, not -1.0 -- but the
    attempt still has to appear in xp_missed or the counts stop reconciling."""
    row = _one_row(_frame([_xp("blocked")]))
    assert row.xp_made == 0
    assert row.xp_missed == 1
    assert row.fantasy_points_kicker == 0.0


def test_field_goal_points_are_tiered_by_distance():
    """Three makes, one per tier, scored separately so a flat per-make value
    cannot pass: 39 -> 3, 49 -> 4, 50+ -> 5."""
    assert _one_row(_frame([_fg("made", 39.0)])).fantasy_points_kicker == 3.0
    assert _one_row(_frame([_fg("made", 40.0)])).fantasy_points_kicker == 4.0
    assert _one_row(_frame([_fg("made", 49.0)])).fantasy_points_kicker == 4.0
    assert _one_row(_frame([_fg("made", 50.0)])).fantasy_points_kicker == 5.0


def test_fg_made_yards_max_ignores_kicks_that_were_not_made():
    """The longest ATTEMPT is not the longest MADE kick. A 55-yard miss beside
    a 40-yard make must publish 40."""
    row = _one_row(_frame([_fg("made", 40.0), _fg("missed", 55.0)]))
    assert row.fg_made_yards_max == 40


def test_fg_made_yards_max_is_null_when_nothing_was_made():
    """NULL, never 0 -- a 0 would read as a made 0-yard field goal."""
    row = _one_row(_frame([_fg("missed", 48.0), _xp("good")]))
    assert row.fg_made == 0
    assert pd.isna(row.fg_made_yards_max)


# ---------------------------------------------------------------------------
# Attribution. The kicker is read off kicker_player_id, never inferred from
# posteam -- see the module docstring for why posteam is not a safe key.
# ---------------------------------------------------------------------------


def test_two_kickers_in_one_game_do_not_merge():
    """Two kickers on the SAME team in the same game -- the shape a
    posteam-keyed grouping collapses into one row. (It happens for real: a
    kicker is hurt or ejected and a punter finishes the game.) Deliberately
    NOT two opposing kickers, because those have different posteams and so
    would survive a posteam-keyed merge unnoticed.
    """
    pbp = _frame([
        _fg("made", 25.0),                                  # KICKER: 3.0
        _xp("good"),                                        # KICKER: 1.0
        _fg("made", 52.0, kicker_player_id=_OTHER_KICKER,
            kicker_player_name=_OTHER_NAME),                # OTHER: 5.0
        _fg("missed", 33.0, kicker_player_id=_OTHER_KICKER,
            kicker_player_name=_OTHER_NAME),                # OTHER: -1.0
    ])
    out = derive_kicker_weekly(pbp, 2024)
    assert len(out) == 2, "two kicker ids must produce two rows, not one"
    assert set(out["gsis_id"]) == {_KICKER, _OTHER_KICKER}

    first = out[out["gsis_id"] == _KICKER].iloc[0]
    second = out[out["gsis_id"] == _OTHER_KICKER].iloc[0]

    assert first.fg_made == 1
    assert first.fg_missed == 0
    assert first.xp_made == 1
    assert first.fg_made_yards_max == 25
    assert first.player_name == _KICKER_NAME
    assert first.fantasy_points_kicker == 4.0    # 3 + 1

    assert second.fg_made == 1
    assert second.fg_missed == 1
    assert second.xp_made == 0
    assert second.fg_made_yards_max == 52
    assert second.player_name == _OTHER_NAME
    assert second.fantasy_points_kicker == 4.0   # 5 - 1
    # The totals match on purpose: only the components separate these two, so
    # a test asserting totals alone would pass with the rows swapped.


def test_opposing_kickers_get_their_own_rows():
    pbp = _frame([
        _fg("made", 25.0),
        _fg("made", 52.0, posteam="BAL", defteam="KC",
            kicker_player_id=_OTHER_KICKER, kicker_player_name=_OTHER_NAME),
    ])
    out = derive_kicker_weekly(pbp, 2024)
    assert len(out) == 2
    assert out[out["gsis_id"] == _KICKER].iloc[0].team == "KC"
    assert out[out["gsis_id"] == _OTHER_KICKER].iloc[0].team == "BAL"


def test_a_kickoff_does_not_create_or_pollute_a_kicker_week():
    """kicker_player_id is populated on KICKOFFS too -- and on a kickoff
    nflverse's posteam is the RECEIVING team, the inversion that cost the DST
    build a Critical. A kickoff is not a scoreable attempt, so it must neither
    add a row nor drag a wrong team onto one."""
    pbp = _frame([
        _fg("made", 25.0),
        {"play_type": "kickoff", "posteam": "BAL", "defteam": "KC",
         "kicker_player_id": _KICKER, "kicker_player_name": _KICKER_NAME},
    ])
    row = _one_row(pbp)
    assert row.team == "KC", "the kicking team, not the kickoff's posteam"
    assert row.fg_made == 1
    assert row.fantasy_points_kicker == 3.0


def test_weeks_do_not_merge():
    pbp = _frame([
        _fg("made", 25.0, week=1),
        _fg("made", 52.0, week=2, game_id="2024_02_KC_ATL"),
    ])
    out = derive_kicker_weekly(pbp, 2024)
    assert len(out) == 2
    assert sorted(out["week"]) == [1, 2]
    assert float(out[out["week"] == 1].iloc[0].fantasy_points_kicker) == 3.0
    assert float(out[out["week"] == 2].iloc[0].fantasy_points_kicker) == 5.0


# ---------------------------------------------------------------------------
# Coverage and frame shape
# ---------------------------------------------------------------------------


def test_a_kicker_with_only_extra_points_still_gets_a_row():
    """A week with no FG attempts is a real week, not a missing one. Dropping
    it would make a kicker whose offense never got into range look like a
    kicker who was inactive."""
    row = _one_row(_frame([_xp("good"), _xp("good")]))
    assert row.gsis_id == _KICKER
    assert row.fg_made == 0
    assert row.fg_missed == 0
    assert pd.isna(row.fg_made_yards_max)
    assert row.xp_made == 2
    assert row.xp_missed == 0
    assert row.fantasy_points_kicker == 2.0


def test_a_kicker_with_only_field_goals_still_gets_a_row():
    row = _one_row(_frame([_fg("made", 45.0)]))
    assert row.xp_made == 0
    assert row.xp_missed == 0
    assert row.fantasy_points_kicker == 4.0


def test_season_is_stamped_from_the_argument_not_the_frame():
    pbp = _fixture()
    pbp["season"] = 1901  # upstream disagreeing with the chunk
    out = derive_kicker_weekly(pbp, 2024)
    assert set(out["season"].unique()) == {2024}


def test_empty_input_returns_schema_shaped_frame():
    out = derive_kicker_weekly(pd.DataFrame(), 2024)
    assert out.empty
    assert list(out.columns) == spec_names(FF_POINTS_K_WEEKLY_SCHEMA)


def test_a_frame_with_no_kicking_plays_returns_schema_shaped_frame():
    pbp = _frame([{"play_type": "pass", "posteam": "KC", "defteam": "BAL"}])
    out = derive_kicker_weekly(pbp, 2024)
    assert out.empty
    assert list(out.columns) == spec_names(FF_POINTS_K_WEEKLY_SCHEMA)


def test_output_columns_are_exactly_the_schema():
    out = derive_kicker_weekly(_fixture(), 2024)
    assert list(out.columns) == spec_names(FF_POINTS_K_WEEKLY_SCHEMA)


def test_the_spec_targets_the_kicker_table_and_clusters_on_the_grain():
    from ffl_bigquery.derive.kicker_weekly import KICKER_WEEKLY_SPEC
    assert KICKER_WEEKLY_SPEC.name == "ff_points_k_weekly"
    partition = KICKER_WEEKLY_SPEC.partition
    assert partition is not None
    assert partition.clustering == ["week", "gsis_id"]
    assert KICKER_WEEKLY_SPEC.transform is derive_kicker_weekly
