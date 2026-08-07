from ffl_bigquery.nflverse.tables import ALL_TABLE_NAMES, load_all_specs


def test_all_table_names_matches_loaded_spec_names_in_order():
    """Guards the two lists (plain strings vs actual specs) against drift --
    ALL_TABLE_NAMES exists specifically so --tables can be validated without
    importing every spec module, so nothing enforces the two lists agree
    except this test.
    """
    specs = load_all_specs()
    assert [s.name for s in specs] == list(ALL_TABLE_NAMES)


def test_all_table_names_has_eleven_entries_and_no_duplicates():
    assert len(ALL_TABLE_NAMES) == 11
    assert len(set(ALL_TABLE_NAMES)) == 11


def test_coordinators_is_not_in_the_registry():
    # Task 11 explicitly excludes sync-coordinators -- that table is a later
    # task and does not exist yet.
    assert "nfl_coordinators" not in ALL_TABLE_NAMES
    assert "coordinators" not in ALL_TABLE_NAMES


def test_dst_weekly_is_registered():
    from ffl_bigquery.nflverse.tables import ALL_TABLE_NAMES, load_all_specs
    assert "ff_points_dst_weekly" in ALL_TABLE_NAMES
    specs = {s.name for s in load_all_specs()}
    assert "ff_points_dst_weekly" in specs


def test_kicker_weekly_is_registered():
    """Without this the spec exists but no CLI path can reach it: --tables
    validation rejects the name, and it is absent from a default sync."""
    from ffl_bigquery.nflverse.tables import ALL_TABLE_NAMES, load_all_specs
    assert "ff_points_k_weekly" in ALL_TABLE_NAMES
    specs = {s.name for s in load_all_specs()}
    assert "ff_points_k_weekly" in specs


def test_dst_weekly_clusters_on_non_float_keys():
    from ffl_bigquery.nflverse.tables import load_all_specs
    spec = next(s for s in load_all_specs() if s.name == "ff_points_dst_weekly")
    assert spec.partition is not None
    assert spec.partition.clustering == ["week", "team"]
    by_name = {c.name: c for c in spec.schema}
    for key in spec.partition.clustering:
        assert by_name[key].type in ("INT64", "STRING")
