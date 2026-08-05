"""Schema shape for ff_points_dst_weekly."""
import pandas as pd

from ffl_bigquery.derive.dst_weekly import FF_POINTS_DST_WEEKLY_SCHEMA, derive_dst_weekly
from ffl_bigquery.schema import spec_names


def test_grain_and_clustering_columns_present():
    names = spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)
    for col in ("season", "week", "team"):
        assert col in names


def test_every_scored_component_ships_beside_its_raw_count():
    # A consumer re-scoring under different league rules needs the counts, not
    # just our total. If a count column is ever dropped, the table silently
    # becomes un-rederivable -- so the pairing is asserted, not assumed.
    names = spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)
    for col in (
        "sacks", "interceptions", "fumble_recoveries", "safeties",
        "defensive_tds", "blocked_kicks", "points_allowed_total",
    ):
        assert col in names, f"missing raw count column: {col}"


def test_scoring_columns_present():
    names = spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)
    assert "points_allowed_bonus" in names
    assert "fantasy_points_dst" in names


def test_clustering_keys_are_not_float():
    # SeasonRangePartition clusters on week+team. BigQuery rejects FLOAT64
    # clustering keys with a 400 at table-creation time -- caught live during
    # the 0.1.0 build, so it is asserted here at the schema level too.
    by_name = {s.name: s for s in FF_POINTS_DST_WEEKLY_SCHEMA}
    assert by_name["week"].type == "INT64"
    assert by_name["team"].type == "STRING"
    assert by_name["season"].type == "INT64"


def test_counts_are_int_and_points_are_float():
    by_name = {s.name: s for s in FF_POINTS_DST_WEEKLY_SCHEMA}
    for col in ("sacks", "interceptions", "fumble_recoveries", "safeties",
                "defensive_tds", "blocked_kicks", "points_allowed_total"):
        assert by_name[col].type == "INT64", f"{col} should be a count"
    assert by_name["fantasy_points_dst"].type == "FLOAT64"


def test_every_column_has_a_business_definition():
    for s in FF_POINTS_DST_WEEKLY_SCHEMA:
        assert s.business_definition.strip(), f"{s.name} has no business_definition"


def _schedules() -> pd.DataFrame:
    """One real game: 2024 week 1, BAL at KC. Final BAL 20, KC 27."""
    return pd.DataFrame([{
        "game_id": "2024_01_BAL_KC", "season": 2024, "week": 1, "game_type": "REG",
        "home_team": "KC", "away_team": "BAL", "home_score": 27, "away_score": 20,
    }])


def _pbp() -> pd.DataFrame:
    """Real event counts for that game, measured 2026-08-05:
    BAL defense 2 sacks + 1 INT; KC defense 1 sack + 1 fumble recovery."""
    rows = []
    for _ in range(2):
        rows.append({"defteam": "BAL", "posteam": "KC", "sack": 1.0})
    rows.append({"defteam": "BAL", "posteam": "KC", "interception": 1.0})
    rows.append({"defteam": "KC", "posteam": "BAL", "sack": 1.0})
    rows.append({"defteam": "KC", "posteam": "BAL", "fumble_recovery_1_team": "KC"})
    df = pd.DataFrame(rows)
    df["game_id"] = "2024_01_BAL_KC"
    df["season"] = 2024
    df["week"] = 1
    df["season_type"] = "REG"
    for col in ("sack", "interception", "safety", "touchdown"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)
    for col in ("fumble_recovery_1_team", "td_team", "field_goal_result",
                "extra_point_result", "punt_blocked"):
        if col not in df.columns:
            df[col] = None
    return df


def test_bal_kc_components_are_exact():
    """The BAL/KC totals COINCIDENTALLY both equal 4.0 while their compositions
    are completely different (BAL: 2 sacks + 1 INT, no points-allowed bonus;
    KC: 1 sack + 1 fumble, +1 bonus). A test asserting only the total would
    therefore pass with several component bugs. Assert the components."""
    out = derive_dst_weekly(_pbp(), _schedules(), 2024)
    bal = out[out["team"] == "BAL"].iloc[0]
    kc = out[out["team"] == "KC"].iloc[0]

    assert int(bal["sacks"]) == 2
    assert int(bal["interceptions"]) == 1
    assert int(bal["fumble_recoveries"]) == 0
    assert int(bal["points_allowed_total"]) == 27   # KC's final score
    assert int(bal["points_allowed_bonus"]) == 0    # 21-27 tier
    assert float(bal["fantasy_points_dst"]) == 4.0  # 2*1 + 1*2 + 0

    assert int(kc["sacks"]) == 1
    assert int(kc["interceptions"]) == 0
    assert int(kc["fumble_recoveries"]) == 1
    assert int(kc["points_allowed_total"]) == 20    # BAL's final score
    assert int(kc["points_allowed_bonus"]) == 1     # 14-20 tier
    assert float(kc["fantasy_points_dst"]) == 4.0   # 1*1 + 1*2 + 1


def test_opponent_is_the_other_team():
    out = derive_dst_weekly(_pbp(), _schedules(), 2024)
    assert out[out["team"] == "BAL"].iloc[0]["opponent"] == "KC"
    assert out[out["team"] == "KC"].iloc[0]["opponent"] == "BAL"


def test_both_teams_get_a_row_even_with_zero_events():
    """A defense that records nothing still played, and still earns its
    points-allowed bonus. Dropping it would make a shutout-less quiet game
    indistinguishable from a bye."""
    pbp = _pbp()
    pbp = pbp[pbp["defteam"] == "BAL"]  # KC's defense recorded nothing
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert set(out["team"]) == {"BAL", "KC"}
    kc = out[out["team"] == "KC"].iloc[0]
    assert int(kc["sacks"]) == 0
    assert float(kc["fantasy_points_dst"]) == 1.0  # bonus only


def test_postseason_is_excluded():
    sched = _schedules()
    sched.loc[0, "game_type"] = "POST"
    out = derive_dst_weekly(_pbp(), sched, 2024)
    assert out.empty


def test_season_is_stamped_from_the_argument_not_the_frame():
    pbp = _pbp()
    pbp["season"] = 1901  # upstream disagreeing with the chunk
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert set(out["season"].unique()) == {2024}


def test_empty_input_returns_schema_shaped_frame():
    from ffl_bigquery.schema import spec_names
    out = derive_dst_weekly(pd.DataFrame(), pd.DataFrame(), 2024)
    assert out.empty
    assert list(out.columns) == spec_names(FF_POINTS_DST_WEEKLY_SCHEMA)


def test_shutout_earns_ten():
    sched = _schedules()
    sched.loc[0, "home_score"] = 0   # KC held scoreless
    out = derive_dst_weekly(_pbp(), sched, 2024)
    bal = out[out["team"] == "BAL"].iloc[0]
    assert int(bal["points_allowed_total"]) == 0
    assert int(bal["points_allowed_bonus"]) == 10
    assert float(bal["fantasy_points_dst"]) == 14.0  # 2 + 2 + 10
