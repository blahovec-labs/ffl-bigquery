from unittest.mock import MagicMock

from google.cloud import bigquery

from ffl_bigquery.verify.adp import (
    check_no_duplicates_at_grain,
    check_resolution_rate,
    check_snapshot_idempotent,
)


def _query_returning(rows):
    c = MagicMock(spec=bigquery.Client)
    job = MagicMock()
    job.result.return_value = rows
    c.query.return_value = job
    return c


def test_resolution_rate_passes_when_above_floor():
    c = _query_returning([MagicMock(source="mfl", n=100, resolved=95, rate=0.95)])
    results = check_resolution_rate(c, adp_table="p.d.ff_adp", season=2026,
                                   min_rate=0.60)
    assert all(r.passed for r in results)
    assert "mfl" in results[0].detail


def test_resolution_rate_fails_per_source_when_below_floor():
    c = _query_returning([
        MagicMock(source="ffc", n=200, resolved=60, rate=0.30),
        MagicMock(source="mfl", n=100, resolved=95, rate=0.95),
    ])
    results = check_resolution_rate(c, adp_table="p.d.ff_adp", season=2026,
                                   min_rate=0.60)
    by_source = {r.name: r for r in results}
    assert by_source["resolution_rate[ffc]"].passed is False
    assert by_source["resolution_rate[mfl]"].passed is True


def test_resolution_rate_fails_loudly_when_no_rows_exist():
    results = check_resolution_rate(_query_returning([]), adp_table="p.d.ff_adp",
                                    season=2026, min_rate=0.60)
    assert len(results) == 1
    assert results[0].passed is False
    assert "no rows" in results[0].detail.lower()


def test_duplicate_check_passes_on_zero_and_names_the_grain():
    c = _query_returning([MagicMock(dupes=0)])
    r = check_no_duplicates_at_grain(c, adp_table="p.d.ff_adp", season=2026)
    assert r.passed is True
    sql = c.query.call_args[0][0]
    for col in ("source", "season", "scoring_format", "teams",
                "snapshot_date", "source_player_id"):
        assert col in sql


def test_duplicate_check_fails_when_dupes_present():
    r = check_no_duplicates_at_grain(_query_returning([MagicMock(dupes=3)]),
                                     adp_table="p.d.ff_adp", season=2026)
    assert r.passed is False
    assert "3" in r.detail


def test_snapshot_idempotency_passes_with_one_row_per_grain_per_day():
    c = _query_returning([MagicMock(max_rows_per_grain=1)])
    assert check_snapshot_idempotent(c, adp_table="p.d.ff_adp",
                                     season=2026).passed is True


def test_snapshot_idempotency_fails_when_a_grain_repeats_within_a_day():
    c = _query_returning([MagicMock(max_rows_per_grain=2)])
    assert check_snapshot_idempotent(c, adp_table="p.d.ff_adp",
                                     season=2026).passed is False
