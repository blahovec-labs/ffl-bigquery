from typing import cast

import pytest

from ffl_bigquery.schema import (
    INGESTED_AT_SPEC,
    BqMode,
    BqType,
    ColumnSpec,
    spec_names,
    to_bq_schema,
)


def _spec(name: str, type: str = "STRING", mode: str = "NULLABLE") -> ColumnSpec:
    return ColumnSpec(
        name=name, type=cast(BqType, type), mode=cast(BqMode, mode),
        short_description="x", business_definition="x", semantic_tags=[],
        valid_range=None, valid_values=None, example_value=None, gotchas=[],
        source_field=name, deprecated_in_year=None,
    )


def test_to_bq_schema_maps_name_type_mode_and_description():
    fields = to_bq_schema([_spec("adp", "FLOAT64"), _spec("season", "INT64", "REQUIRED")])
    assert [f.name for f in fields] == ["adp", "season"]
    assert fields[0].field_type == "FLOAT64"
    assert fields[1].mode == "REQUIRED"
    assert fields[0].description == "x"


def test_spec_names_preserves_order():
    assert spec_names([_spec("b"), _spec("a")]) == ["b", "a"]


def test_invalid_type_rejected():
    with pytest.raises(ValueError, match="invalid type"):
        _spec("x", "VARCHAR")


def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="invalid mode"):
        _spec("x", "STRING", "OPTIONAL")


def test_blank_business_definition_rejected():
    with pytest.raises(ValueError, match="business_definition required"):
        ColumnSpec(
            name="x", type="STRING", mode="NULLABLE", short_description="x",
            business_definition="   ", semantic_tags=[], valid_range=None,
            valid_values=None, example_value=None, gotchas=[], source_field="x",
            deprecated_in_year=None,
        )


def test_ingested_at_spec_is_required_timestamp():
    assert INGESTED_AT_SPEC.name == "ingested_at"
    assert INGESTED_AT_SPEC.type == "TIMESTAMP"
    assert INGESTED_AT_SPEC.mode == "REQUIRED"
