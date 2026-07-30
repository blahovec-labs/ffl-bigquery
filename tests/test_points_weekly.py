import pandas as pd

from ffl_bigquery.derive.points_weekly import (
    FF_POINTS_WEEKLY_SCHEMA,
    derive_points_weekly,
)
from ffl_bigquery.schema import spec_names


def _stats() -> pd.DataFrame:
    return pd.DataFrame({
        "season": [2023] * 4, "week": [1] * 4,
        "player_id": ["a", "b", "c", "d"],
        "player_name": ["A", "B", "C", "D"],
        "position": ["WR", "WR", "RB", "RB"],
        "team": ["DET", "KC", "DET", "KC"],
        "receptions": [10, 4, 2, 0],
        "fantasy_points": [20.0, 12.0, 8.0, 15.0],
        "fantasy_points_ppr": [30.0, 16.0, 10.0, 15.0],
    })


def test_output_columns_match_schema():
    out = derive_points_weekly(_stats(), 2023)
    assert list(out.columns) == spec_names(FF_POINTS_WEEKLY_SCHEMA)


def test_half_ppr_is_standard_plus_half_a_point_per_reception():
    out = derive_points_weekly(_stats(), 2023).set_index("player_id")
    assert out.loc["a", "fantasy_points_half_ppr"] == 25.0   # 20 + 0.5*10
    assert out.loc["d", "fantasy_points_half_ppr"] == 15.0   # 15 + 0.5*0


def test_half_ppr_also_equals_the_midpoint_of_standard_and_ppr():
    # Independent identity: a reception-column rename would break this even if
    # the primary formula still ran.
    out = derive_points_weekly(_stats(), 2023)
    mid = (out["fantasy_points_standard"] + out["fantasy_points_ppr"]) / 2
    assert (out["fantasy_points_half_ppr"] - mid).abs().max() < 1e-9


def _stats_with_oracle_ppr() -> pd.DataFrame:
    # Separate fixture (not _stats()) so this test's PPR values don't have to
    # satisfy the half-PPR midpoint identity, and vice versa. Player "a"'s
    # ppr is set to something that is NOT fantasy_points + receptions
    # (20 + 10 = 30.0), so an implementation that recomputes PPR instead of
    # carrying it through would produce 30.0 and visibly disagree with the
    # asserted 42.5.
    df = _stats()
    df.loc[df["player_id"] == "a", "fantasy_points_ppr"] = 42.5
    return df


def test_ppr_is_carried_through_unchanged_from_upstream():
    # upstream's own column is the correctness oracle; we must not recompute it.
    out = derive_points_weekly(_stats_with_oracle_ppr(), 2023).set_index("player_id")
    assert out.loc["a", "fantasy_points_ppr"] == 42.5


def test_gsis_id_is_player_id_under_its_canonical_name():
    # load_player_stats() has no gsis_id column of its own -- player_id IS
    # the gsis_id (verified ^00-\d+$ against the live 2023 feed). gsis_id is
    # also this table's clustering key, so a missing assignment would ship
    # it all-NULL and useless for pruning.
    out = derive_points_weekly(_stats(), 2023)
    assert out["gsis_id"].notna().all()
    assert (out["gsis_id"] == out["player_id"]).all()


def test_position_rank_is_within_season_week_and_position():
    out = derive_points_weekly(_stats(), 2023).set_index("player_id")
    assert out.loc["a", "position_rank_ppr"] == 1   # WR, 30.0
    assert out.loc["b", "position_rank_ppr"] == 2   # WR, 16.0
    assert out.loc["d", "position_rank_ppr"] == 1   # RB, 15.0
    assert out.loc["c", "position_rank_ppr"] == 2   # RB, 10.0


def test_season_comes_from_the_loop():
    assert (derive_points_weekly(_stats(), 1999)["season"] == 1999).all()


def test_empty_input_yields_empty_frame_with_schema_columns():
    out = derive_points_weekly(pd.DataFrame(), 2023)
    assert out.empty
    assert list(out.columns) == spec_names(FF_POINTS_WEEKLY_SCHEMA)
