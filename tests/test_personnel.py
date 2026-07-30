import pandas as pd

from ffl_bigquery.derive.personnel import (
    parse_personnel_2023plus,
    parse_personnel_legacy,
    personnel_counts,
)


def test_modern_parser_counts_rb_te_wr_from_positions():
    s = pd.Series(["C;G;QB;RB;T;T;T;TE;TE;WR;WR", "C;G;G;QB;RB;T;T;TE;WR;WR;WR"])
    out = parse_personnel_2023plus(s)
    assert list(out["n_rb"]) == [1, 1]
    assert list(out["n_te"]) == [2, 1]
    assert list(out["n_wr"]) == [2, 3]


def test_modern_parser_flags_a_defence_contaminated_row_as_implausible():
    # Real 2023 shape: defenders inside the OFFENSE column.
    s = pd.Series(["CB;CB;DE;DE;FS;MLB;MLB;OLB;RB;RB;TE"])
    out = parse_personnel_2023plus(s)
    assert bool(out["plausible"].iloc[0]) is False


def test_plausibility_requires_eleven_players_and_exactly_one_qb():
    good = pd.Series(["C;G;G;QB;RB;T;T;TE;WR;WR;WR"])
    no_qb = pd.Series(["C;G;G;RB;RB;T;T;TE;WR;WR;WR"])
    short = pd.Series(["C;G;QB;RB;WR"])
    assert bool(parse_personnel_2023plus(good)["plausible"].iloc[0]) is True
    assert bool(parse_personnel_2023plus(no_qb)["plausible"].iloc[0]) is False
    assert bool(parse_personnel_2023plus(short)["plausible"].iloc[0]) is False


def test_legacy_parser_reads_the_personnel_string():
    s = pd.Series(["1 RB, 1 TE, 3 WR", "2 RB, 2 TE, 1 WR"])
    out = parse_personnel_legacy(s)
    assert list(out["n_rb"]) == [1, 2]
    assert list(out["n_te"]) == [1, 2]
    assert list(out["n_wr"]) == [3, 1]


def test_legacy_parser_rejects_a_row_carrying_defensive_positions():
    s = pd.Series(["2 CB, 2 DE, 1 FS, 2 MLB, 1 OLB, 2 RB, 1 TE"])
    assert bool(parse_personnel_legacy(s)["plausible"].iloc[0]) is False


def test_counts_picks_the_parser_by_season_and_records_which_it_used():
    modern = pd.DataFrame({"offense_positions": ["C;G;G;QB;RB;T;T;TE;WR;WR;WR"],
                           "offense_personnel": [""]})
    legacy = pd.DataFrame({"offense_positions": [None],
                           "offense_personnel": ["1 RB, 1 TE, 3 WR"]})
    assert personnel_counts(modern, 2023)["personnel_source"].iloc[0] == "offense_positions"
    assert personnel_counts(legacy, 2019)["personnel_source"].iloc[0] == "offense_personnel"


def test_legacy_season_does_not_use_offense_positions_because_it_is_empty_then():
    # offense_positions is 0% filled before 2023; using it would yield all-NULL.
    df = pd.DataFrame({"offense_positions": [None], "offense_personnel": ["1 RB, 1 TE, 3 WR"]})
    out = personnel_counts(df, 2019)
    assert out["n_wr"].iloc[0] == 3
