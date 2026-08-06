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


def _schedules() -> pd.DataFrame:
    """One real game: 2024 week 1, BAL at KC. Final BAL 20, KC 27."""
    return pd.DataFrame([{
        "game_id": "2024_01_BAL_KC", "season": 2024, "week": 1, "game_type": "REG",
        "home_team": "KC", "away_team": "BAL", "home_score": 27, "away_score": 20,
    }])


def _frame(rows: list[dict]) -> pd.DataFrame:
    """A play-by-play frame with every column derive_dst_weekly reads."""
    df = pd.DataFrame(rows)
    df["game_id"] = "2024_01_BAL_KC"
    df["season"] = 2024
    df["week"] = 1
    df["season_type"] = "REG"
    for col in ("sack", "interception", "safety", "touchdown"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)
    for col in ("fumble_recovery_1_team", "fumbled_1_team", "td_team",
                "play_type", "field_goal_result", "extra_point_result",
                "punt_blocked"):
        if col not in df.columns:
            df[col] = None
    return df


def _pbp() -> pd.DataFrame:
    """Real event counts for that game, measured 2026-08-05:
    BAL defense 2 sacks + 1 INT; KC defense 1 sack + 1 fumble recovery."""
    rows = []
    for _ in range(2):
        rows.append({"defteam": "BAL", "posteam": "KC", "play_type": "pass",
                     "sack": 1.0})
    rows.append({"defteam": "BAL", "posteam": "KC", "play_type": "pass",
                 "interception": 1.0})
    rows.append({"defteam": "KC", "posteam": "BAL", "play_type": "pass",
                 "sack": 1.0})
    rows.append({"defteam": "KC", "posteam": "BAL", "play_type": "run",
                 "fumble_recovery_1_team": "KC", "fumbled_1_team": "BAL"})
    return _frame(rows)


# ---------------------------------------------------------------------------
# Special-teams attribution. nflverse's posteam/defteam orientation is INVERTED
# between punts and kickoffs -- on a punt posteam is the punting team, on a
# kickoff posteam is the RECEIVING team. A fixture of scrimmage plays only
# cannot see that, which is exactly why the original one could not catch the
# two attribution bugs these tests pin down.
# ---------------------------------------------------------------------------


def test_kickoff_return_td_credits_the_returning_team():
    """The ARI 2024 week 1 shape: DeeJay Dallas' 96-yard kickoff return TD vs
    BUF. On a kickoff the returner is POSTEAM, so both `td_team == defteam` and
    `td_team != posteam` drop it -- the table published 3.0 where 9.0 was
    correct. BAL here is the returning team."""
    pbp = _frame([{
        "play_type": "kickoff", "posteam": "BAL", "defteam": "KC",
        "touchdown": 1.0, "td_team": "BAL",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    bal = out[out["team"] == "BAL"].iloc[0]
    kc = out[out["team"] == "KC"].iloc[0]
    assert int(bal["defensive_tds"]) == 1
    assert float(bal["fantasy_points_dst"]) == 6.0   # 6 + 0 bonus (27 allowed)
    assert int(kc["defensive_tds"]) == 0


def test_punt_return_td_credits_the_receiving_team():
    """On a punt the receiving team is DEFTEAM -- the case the old code got
    right. Pinned so a fix aimed at kickoffs cannot regress it."""
    pbp = _frame([{
        "play_type": "punt", "posteam": "KC", "defteam": "BAL",
        "touchdown": 1.0, "td_team": "BAL",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert int(out[out["team"] == "BAL"].iloc[0]["defensive_tds"]) == 1
    assert int(out[out["team"] == "KC"].iloc[0]["defensive_tds"]) == 0


def test_ordinary_offensive_touchdown_credits_nobody():
    """A receiving TD is scored BY posteam on a scrimmage play. Crediting it to
    anyone would turn every offense into a 6-point defense."""
    pbp = _frame([{
        "play_type": "pass", "posteam": "KC", "defteam": "BAL",
        "touchdown": 1.0, "td_team": "KC",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert int(out[out["team"] == "BAL"].iloc[0]["defensive_tds"]) == 0
    assert int(out[out["team"] == "KC"].iloc[0]["defensive_tds"]) == 0


def test_muffed_punt_recovered_by_the_punting_team_credits_the_punting_team():
    """The receiving team muffs; the PUNTING team recovers. Possession changed,
    so the punting team's coverage unit earns it -- but the recoverer is
    posteam, not defteam, so the old `recovery == defteam` rule dropped it.
    Measured 23 times in 2024 REG. KC punts here, BAL muffs, KC recovers."""
    pbp = _frame([{
        "play_type": "punt", "posteam": "KC", "defteam": "BAL",
        "fumbled_1_team": "BAL", "fumble_recovery_1_team": "KC",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert int(out[out["team"] == "KC"].iloc[0]["fumble_recoveries"]) == 1
    assert int(out[out["team"] == "BAL"].iloc[0]["fumble_recoveries"]) == 0


def test_self_recovered_muff_credits_nobody():
    """The receiving team muffs and recovers its own muff: no change of
    possession, no fantasy credit. The old `recovery == defteam` rule wrongly
    credited these -- 22 of them in 2024 REG."""
    pbp = _frame([{
        "play_type": "punt", "posteam": "KC", "defteam": "BAL",
        "fumbled_1_team": "BAL", "fumble_recovery_1_team": "BAL",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert int(out[out["team"] == "BAL"].iloc[0]["fumble_recoveries"]) == 0
    assert int(out[out["team"] == "KC"].iloc[0]["fumble_recoveries"]) == 0


def test_pick_six_still_credits_the_defense():
    """play_type='pass', td_team == defteam -- the case that always worked."""
    pbp = _frame([{
        "play_type": "pass", "posteam": "KC", "defteam": "BAL",
        "interception": 1.0, "touchdown": 1.0, "td_team": "BAL",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    bal = out[out["team"] == "BAL"].iloc[0]
    assert int(bal["interceptions"]) == 1
    assert int(bal["defensive_tds"]) == 1
    assert float(bal["fantasy_points_dst"]) == 8.0  # 2 + 6 + 0 bonus


def test_fumble_return_td_on_a_run_credits_the_defense():
    pbp = _frame([{
        "play_type": "run", "posteam": "KC", "defteam": "BAL",
        "fumbled_1_team": "KC", "fumble_recovery_1_team": "BAL",
        "touchdown": 1.0, "td_team": "BAL",
    }])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    bal = out[out["team"] == "BAL"].iloc[0]
    assert int(bal["fumble_recoveries"]) == 1
    assert int(bal["defensive_tds"]) == 1
    assert float(bal["fantasy_points_dst"]) == 8.0  # 2 + 6 + 0 bonus


def test_touchdown_on_a_null_play_type_play_credits_only_a_non_posteam_scorer():
    """play_type is NULL on plays carrying a between-downs penalty -- 8 such
    touchdowns in 1999 REG, 6 of them ordinary offensive run/pass scores. A
    rule phrased as "not a run or pass" would credit all 6 to a defense."""
    pbp = _frame([
        {"play_type": None, "posteam": "KC", "defteam": "BAL",
         "touchdown": 1.0, "td_team": "KC"},    # offensive, penalty-nulled
        {"play_type": None, "posteam": "KC", "defteam": "BAL",
         "touchdown": 1.0, "td_team": "BAL"},   # pick-six, penalty-nulled
    ])
    out = derive_dst_weekly(pbp, _schedules(), 2024)
    assert int(out[out["team"] == "BAL"].iloc[0]["defensive_tds"]) == 1
    assert int(out[out["team"] == "KC"].iloc[0]["defensive_tds"]) == 0


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


def test_unresolvable_final_score_leaves_the_total_null_not_zero():
    """A 0.0 total reads as "this defense scored nothing"; NULL reads as "not
    known". The distinction is not academic: a mid-season
    `sync-nflverse --seasons latest` sees every scheduled week from
    load_schedules() but only the played ones from load_pbp(), so a fillna(0)
    total shipped a concrete 0.0 for every future week of the season."""
    sched = _schedules()
    sched.loc[0, "home_score"] = None   # KC's score unknown -- not played yet
    out = derive_dst_weekly(_pbp(), sched, 2024)
    bal = out[out["team"] == "BAL"].iloc[0]        # BAL allowed KC's unknown score
    assert pd.isna(bal["points_allowed_bonus"])
    assert pd.isna(bal["fantasy_points_dst"]), (
        "NULL bonus must propagate to a NULL total, not collapse to 0.0"
    )
    kc = out[out["team"] == "KC"].iloc[0]          # BAL's 20 is still known
    assert float(kc["fantasy_points_dst"]) == 4.0


def test_shutout_earns_ten():
    sched = _schedules()
    sched.loc[0, "home_score"] = 0   # KC held scoreless
    out = derive_dst_weekly(_pbp(), sched, 2024)
    bal = out[out["team"] == "BAL"].iloc[0]
    assert int(bal["points_allowed_total"]) == 0
    assert int(bal["points_allowed_bonus"]) == 10
    assert float(bal["fantasy_points_dst"]) == 14.0  # 2 + 2 + 10
