import pandas as pd
import pytest

from ffl_bigquery.schema_gen import specs_from_frame


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "season": pd.Series(["2020"], dtype="string"),
        "week": pd.Series([1], dtype="Int64"),
        "pct": pd.Series([0.5], dtype="float64"),
        "flag": pd.Series([True], dtype="boolean"),
        "ts": pd.Series(pd.to_datetime(["2020-01-01"])),
        "junk": pd.Series(["x"], dtype="string"),
    })


def test_infers_types_from_dtypes():
    by = {s.name: s for s in specs_from_frame(_frame(), table="t")}
    assert by["week"].type == "INT64"
    assert by["pct"].type == "FLOAT64"
    assert by["flag"].type == "BOOL"
    assert by["ts"].type == "TIMESTAMP"
    assert by["junk"].type == "STRING"


def test_type_override_beats_inference():
    # season arrives as String upstream but MUST be INT64 for RANGE partitioning.
    by = {s.name: s for s in specs_from_frame(
        _frame(), table="t", type_overrides={"season": "INT64"})}
    assert by["season"].type == "INT64"


def test_required_columns_are_marked_and_others_are_nullable():
    specs = specs_from_frame(_frame(), table="t",
                             type_overrides={"season": "INT64"},
                             required=("season",))
    by = {s.name: s for s in specs}
    assert by["season"].mode == "REQUIRED"
    assert by["week"].mode == "NULLABLE"


def test_dropped_columns_are_excluded():
    names = [s.name for s in specs_from_frame(_frame(), table="t", drop=("junk",))]
    assert "junk" not in names


def test_ingested_at_is_appended_last_exactly_once():
    specs = specs_from_frame(_frame(), table="t")
    assert specs[-1].name == "ingested_at"
    assert [s.name for s in specs].count("ingested_at") == 1


def test_every_spec_has_a_non_blank_business_definition():
    # ColumnSpec.__post_init__ rejects a blank one, so a generated default is required.
    for s in specs_from_frame(_frame(), table="t"):
        assert s.business_definition.strip()


def test_enrichment_overrides_the_generated_definition_and_adds_gotchas():
    specs = specs_from_frame(
        _frame(), table="t",
        enrichment={"week": {"business_definition": "NFL week number.",
                             "gotchas": ["Week 18 only exists from 2021."],
                             "semantic_tags": ["dimension"]}},
    )
    wk = next(s for s in specs if s.name == "week")
    assert wk.business_definition == "NFL week number."
    assert wk.gotchas == ["Week 18 only exists from 2021."]
    assert wk.semantic_tags == ["dimension"]


def test_column_order_follows_the_frame():
    names = [s.name for s in specs_from_frame(_frame(), table="t")]
    assert names[:3] == ["season", "week", "pct"]


def test_unknown_enrichment_key_raises_rather_than_being_silently_ignored():
    # A typo'd column name in an overlay must not vanish.
    with pytest.raises(ValueError, match="not in frame"):
        specs_from_frame(_frame(), table="t", enrichment={"weke": {}})


def test_unknown_override_or_required_key_also_raises():
    with pytest.raises(ValueError, match="not in frame"):
        specs_from_frame(_frame(), table="t", type_overrides={"nope": "INT64"})
    with pytest.raises(ValueError, match="not in frame"):
        specs_from_frame(_frame(), table="t", required=("nope",))
