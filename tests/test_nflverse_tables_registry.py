from ffl_bigquery.nflverse.tables import ALL_TABLE_NAMES, load_all_specs


def test_all_table_names_matches_loaded_spec_names_in_order():
    """Guards the two lists (plain strings vs actual specs) against drift --
    ALL_TABLE_NAMES exists specifically so --tables can be validated without
    importing every spec module, so nothing enforces the two lists agree
    except this test.
    """
    specs = load_all_specs()
    assert [s.name for s in specs] == list(ALL_TABLE_NAMES)


def test_all_table_names_has_nine_entries_and_no_duplicates():
    assert len(ALL_TABLE_NAMES) == 9
    assert len(set(ALL_TABLE_NAMES)) == 9


def test_coordinators_is_not_in_the_registry():
    # Task 11 explicitly excludes sync-coordinators -- that table is a later
    # task and does not exist yet.
    assert "nfl_coordinators" not in ALL_TABLE_NAMES
    assert "coordinators" not in ALL_TABLE_NAMES
