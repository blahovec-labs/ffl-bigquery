from pathlib import Path

import pandas as pd
import pytest

from ffl_bigquery.nflverse.tables.depth_charts import (
    DEPTH_CHARTS_SCHEMA,
    DEPTH_CHARTS_SPEC,
    normalize_depth_charts,
)
from ffl_bigquery.schema import spec_names

FIX = Path(__file__).parent / "fixtures" / "nflverse"


def _legacy() -> pd.DataFrame:
    return pd.read_parquet(FIX / "depth_charts_legacy.parquet")


def _modern() -> pd.DataFrame:
    return pd.read_parquet(FIX / "depth_charts_modern.parquet")


def test_schema_has_the_normalized_core_and_a_source_era():
    names = spec_names(DEPTH_CHARTS_SCHEMA)
    for col in ("season", "week", "team", "gsis_id", "player_name", "position",
                "depth_rank", "depth_position", "source_era", "ingested_at"):
        assert col in names
    # Era-specific upstream names must NOT leak into the normalized table.
    for leaked in ("club_code", "depth_team", "pos_rank", "pos_abb"):
        assert leaked not in names


def test_season_and_source_era_are_required_and_season_is_int64():
    by = {s.name: s for s in DEPTH_CHARTS_SCHEMA}
    assert by["season"].mode == "REQUIRED" and by["season"].type == "INT64"
    assert by["source_era"].mode == "REQUIRED"


def test_legacy_rows_map_club_code_and_depth_team():
    raw = _legacy()
    out = normalize_depth_charts(raw, 2015)
    assert list(out.columns) == spec_names(DEPTH_CHARTS_SCHEMA)
    assert (out["source_era"] == "legacy").all()
    assert out["team"].notna().any()
    assert out["depth_rank"].notna().any()
    # Legacy rows carry a real upstream `season`; it must be preserved
    # verbatim, not overridden by the season-chunk argument (2015) -- the
    # argument only backstops rows with neither an upstream season nor a
    # derivable `dt`. This fixture's real season is 2001 (earliest legacy
    # year), which is why this assertion is NOT `== 2015`: that would only
    # pass by coincidence and wouldn't catch a regression to "always use the
    # argument."
    assert (out["season"] == raw["season"].astype("Int64")).all()


def test_modern_rows_map_team_and_pos_rank_and_derive_season():
    raw = _modern()
    # This fixture's `dt` is uniformly 2026-03-14 (measured 2026-07-29), which
    # derives to season 2026 (month > 2). Pass a *disagreeing* chunk argument
    # (2025) so this test can tell "derivation ran and got 2026" apart from
    # "derivation never ran and the argument (2025) leaked through" -- the
    # exact gap that made this test vacuous relative to its "derive_season"
    # name (see docs/superpowers/sdd/p2-task-5-report.md).
    out = normalize_depth_charts(raw, 2025)
    assert (out["source_era"] == "modern").all()
    assert out["team"].notna().any()
    assert out["depth_rank"].notna().any()
    assert (out["season"] == 2026).all()
    assert not (out["season"] == 2025).any()


@pytest.mark.parametrize(
    "dt,expected",
    [("2025-09-01", 2025), ("2025-12-31", 2025),
     ("2026-01-15", 2025), ("2026-02-05", 2025), ("2026-03-01", 2026)],
)
def test_january_and_february_belong_to_the_previous_season(dt, expected):
    # An NFL season spans calendar years; a Jan/Feb depth chart is last season's.
    raw = pd.DataFrame({
        "gsis_id": ["00-1"], "dt": pd.to_datetime([dt]), "team": ["DET"],
        "player_name": ["A B"], "pos_abb": ["RB"], "pos_rank": [1],
        "pos_name": ["Running Back"], "pos_slot": ["RB1"], "season": [pd.NA],
    })
    out = normalize_depth_charts(raw, expected)
    assert out["season"].iloc[0] == expected


def test_derived_season_wins_over_a_disagreeing_chunk_argument():
    # The parametrized boundary test above always passes `expected` as BOTH
    # the chunk argument and the expected season, so a broken implementation
    # that just did `season = argument` (ignoring `dt` entirely) would pass
    # every one of those cases too. Pin the real invariant by deliberately
    # disagreeing: dt=2026-03-01 derives season 2026, but the chunk argument
    # here is 1900. If derivation actually runs, 2026 wins; if it doesn't,
    # the argument leaks through as 1900.
    raw = pd.DataFrame({
        "gsis_id": ["00-1"], "dt": pd.to_datetime(["2026-03-01"]), "team": ["DET"],
        "player_name": ["A B"], "pos_abb": ["RB"], "pos_rank": [1],
        "pos_name": ["Running Back"], "pos_slot": ["RB1"], "season": [pd.NA],
    })
    out = normalize_depth_charts(raw, 1900)
    assert out["season"].iloc[0] == 2026


def test_era_is_detected_per_row_not_assumed_from_the_argument():
    # A frame containing BOTH eras must label each row correctly.
    mixed = pd.concat([_legacy().head(3), _modern().head(3)], ignore_index=True)
    out = normalize_depth_charts(mixed, 2020)
    assert set(out["source_era"]) == {"legacy", "modern"}


def test_spec_partitions_on_season_not_dt():
    # Partitioning on dt would strand every legacy row in a NULL partition.
    assert DEPTH_CHARTS_SPEC.partition.field == "season"
    assert "dt" not in spec_names(DEPTH_CHARTS_SCHEMA)
    assert DEPTH_CHARTS_SPEC.min_season == 2001


def test_no_row_is_dropped_by_normalization():
    raw = _legacy()
    assert len(normalize_depth_charts(raw, 2015)) == len(raw)
