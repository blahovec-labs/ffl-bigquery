import pandas as pd
import pytest

from ffl_bigquery.adp.resolve import (
    normalize_merge_name,
    resolution_rate,
    resolve_gsis_ids,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Adrian Peterson", "adrian peterson"),
        ("Mike Washington Jr.", "mike washington"),
        ("Chris Hilton Jr", "chris hilton"),
        ("Robert Griffin III", "robert griffin"),
        ("Michael Pittman Sr.", "michael pittman"),
        ("A.J. Brown", "aj brown"),
        ("Emmanuel McNeil-Warren", "emmanuel mcneil-warren"),
        ("  Le'Veon   Bell  ", "leveon bell"),
        ("Amon-Ra St. Brown", "amon-ra st brown"),
    ],
)
def test_normalize_matches_ffverse_merge_name_rules(raw, expected):
    assert normalize_merge_name(raw) == expected


def _xref() -> pd.DataFrame:
    return pd.DataFrame({
        "mfl_id": [8658, 11192, 9001, 9002],
        "gsis_id": ["00-0025394", "00-0031234", "00-0040001", "00-0040002"],
        "merge_name": ["adrian peterson", "leveon bell", "john smith", "john smith"],
        "position": ["RB", "RB", "WR", "WR"],
    })


def _adp(rows: list[dict]) -> pd.DataFrame:
    base = {"source": "ffc", "source_player_id": "1", "player_name": None,
            "position": None, "gsis_id": None, "adp": 1.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_mfl_resolves_by_id_across_the_string_int_boundary():
    adp = _adp([{"source": "mfl", "source_player_id": "8658"}])
    out = resolve_gsis_ids(adp, _xref())
    assert out.iloc[0]["gsis_id"] == "00-0025394"


def test_ffc_resolves_by_normalized_name_and_position():
    adp = _adp([{"source": "ffc", "player_name": "Adrian Peterson", "position": "RB"}])
    out = resolve_gsis_ids(adp, _xref())
    assert out.iloc[0]["gsis_id"] == "00-0025394"


def test_ffc_name_with_suffix_still_resolves():
    xref = pd.DataFrame({"mfl_id": [1], "gsis_id": ["00-0000001"],
                         "merge_name": ["mike washington"], "position": ["WR"]})
    adp = _adp([{"player_name": "Mike Washington Jr.", "position": "WR"}])
    assert resolve_gsis_ids(adp, xref).iloc[0]["gsis_id"] == "00-0000001"


def test_ambiguous_name_match_is_refused_not_guessed():
    adp = _adp([{"player_name": "John Smith", "position": "WR"}])
    out = resolve_gsis_ids(adp, _xref())
    assert pd.isna(out.iloc[0]["gsis_id"])


def test_ffc_id_is_never_used_as_an_mfl_id():
    # FFC player_id 8658 collides numerically with a real mfl_id; it must NOT match.
    adp = _adp([{"source": "ffc", "source_player_id": "8658",
                 "player_name": "Nobody Here", "position": "RB"}])
    out = resolve_gsis_ids(adp, _xref())
    assert pd.isna(out.iloc[0]["gsis_id"])


def test_unresolved_rows_are_retained():
    adp = _adp([{"player_name": "Unknown Guy", "position": "TE"},
                {"player_name": "Adrian Peterson", "position": "RB"}])
    out = resolve_gsis_ids(adp, _xref())
    assert len(out) == 2
    assert out["gsis_id"].isna().sum() == 1


def test_xref_rows_without_gsis_id_do_not_resolve():
    xref = pd.DataFrame({"mfl_id": [5], "gsis_id": [None],
                         "merge_name": ["ghost player"], "position": ["RB"]})
    adp = _adp([{"source": "mfl", "source_player_id": "5"}])
    assert pd.isna(resolve_gsis_ids(adp, xref).iloc[0]["gsis_id"])


def test_column_set_is_unchanged_by_resolution():
    adp = _adp([{"player_name": "Adrian Peterson", "position": "RB"}])
    before = list(adp.columns)
    out = resolve_gsis_ids(adp, _xref())
    assert list(out.columns) == before


def test_resolution_rate_counts_non_null_share():
    adp = _adp([{"player_name": "Adrian Peterson", "position": "RB"},
                {"player_name": "Unknown Guy", "position": "TE"}])
    assert resolution_rate(resolve_gsis_ids(adp, _xref())) == 0.5


def test_resolution_rate_of_empty_frame_is_zero():
    assert resolution_rate(pd.DataFrame({"gsis_id": []})) == 0.0
