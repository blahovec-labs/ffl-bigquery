"""Parquet schema samples shipped inside the wheel.

Six of the season-chunked nflverse tables (ff_opportunity, snap_counts,
injuries, ftn_charting, participation) plus ff_rankings build their BigQuery
schema at import time by sampling a real upstream frame's dtypes
(`schema_gen.specs_from_frame`). That sample has to be readable from an
*installed* package, not just a repo checkout: the previous approach --
`Path(__file__).resolve().parents[3] / "tests/fixtures/nflverse/<x>.parquet"`
-- resolves to the repo root only when `ffl_bigquery/` sits inside a checkout
three directories below that fixture. Once pip unpacks the wheel into
`site-packages/`, `parents[3]` lands outside site-packages entirely (`tests/`
isn't even shipped), so every one of those six modules raised
`FileNotFoundError` at import time -- and because `load_all_specs()` imports
all of them eagerly, that took down `sync-nflverse` for every `--tables`
value, including `--dry-run`.

These parquet files (~230 KB total) are the fix: they live inside the
installed package itself and are located via `importlib.resources`, which
resolves correctly in a repo checkout, an editable install, and a built
wheel alike (`pyproject.toml` force-includes them into the wheel since
hatchling does not pick up non-Python files under `packages=` by default).

The same files also back the fidelity tests in
`tests/test_play_level_tables.py` and `tests/test_nflverse_tables_simple.py`,
via this same `read_sample`/`sample_path` helpers, so there is exactly one
copy of each parquet on disk -- not a package copy plus a test copy.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

import pandas as pd


@contextmanager
def sample_path(name: str) -> Iterator[Path]:
    """Yield a real on-disk Path to `<name>.parquet`.

    `importlib.resources.as_file` (not a bare `Path(__file__)`) is required
    because a zipped/compressed install has no on-disk file to hand back
    directly -- `as_file` materializes one (a temp copy, if needed) for the
    duration of the `with` block.
    """
    with resources.as_file(resources.files(__name__) / f"{name}.parquet") as p:
        yield p


def read_sample(name: str) -> pd.DataFrame:
    """Read `<name>.parquet` from this package's bundled schema samples."""
    with sample_path(name) as p:
        return pd.read_parquet(p)
