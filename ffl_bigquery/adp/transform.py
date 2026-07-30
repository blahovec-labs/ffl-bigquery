"""Normalize FFC and MFL ADP responses into FF_ADP_SCHEMA.

The two sources overlap only partially: FFC supplies names/positions/stdev/bye and
no rank; MFL supplies rank/selection-pct and ids only. Columns a source does not
publish are left NULL rather than defaulted, so "absent" stays distinguishable
from "zero".
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ffl_bigquery._transform_util import align_to_schema
from ffl_bigquery.adp.ffc import FfcResponse
from ffl_bigquery.adp.mfl import MflResponse
from ffl_bigquery.adp.schema import FF_ADP_SCHEMA
from ffl_bigquery.schema import spec_names


def _empty() -> pd.DataFrame:
    # pandas' inline Axes stub rejects plain list[str] here (a known pandas-stubs
    # false positive: list[str] doesn't structurally match SequenceNotStr because
    # str.index's signature isn't Sequence.index's) — same pattern as
    # adp/schema.py's `type: ignore[arg-type]`.
    return pd.DataFrame(columns=spec_names(FF_ADP_SCHEMA))  # type: ignore[arg-type]


def transform_ffc(
    resp: FfcResponse,
    *,
    season: int,
    scoring_format: str,
    teams: int,
    snapshot_date: date,
) -> pd.DataFrame:
    if resp.is_empty:
        return _empty()
    df = pd.DataFrame(resp.players)
    out = pd.DataFrame(
        {
            "source": "ffc",
            "season": season,
            "scoring_format": scoring_format,
            "teams": teams,
            "snapshot_date": snapshot_date,
            # FFC player_id arrives as an int; the column is STRING because MFL
            # ids are strings and one column must hold both.
            "source_player_id": df["player_id"].astype("string"),
            "gsis_id": pd.NA,
            "player_name": df.get("name"),
            "position": df.get("position"),
            "team": df.get("team"),
            "adp": pd.to_numeric(df.get("adp"), errors="coerce"),
            "adp_formatted": df.get("adp_formatted"),
            "adp_stdev": pd.to_numeric(df.get("stdev"), errors="coerce"),
            # "high" is FFC's highest draft POSITION = the smallest pick number.
            "adp_earliest_pick": pd.to_numeric(df.get("high"), errors="coerce"),
            "adp_latest_pick": pd.to_numeric(df.get("low"), errors="coerce"),
            "times_drafted": pd.to_numeric(df.get("times_drafted"), errors="coerce"),
            "draft_selected_pct": pd.NA,
            "source_rank": pd.NA,
            "total_drafts": resp.total_drafts,
            "bye": pd.to_numeric(df.get("bye"), errors="coerce"),
            "window_start_date": pd.to_datetime(
                resp.window_start, errors="coerce"
            ).date() if resp.window_start else pd.NaT,
            "window_end_date": pd.to_datetime(
                resp.window_end, errors="coerce"
            ).date() if resp.window_end else pd.NaT,
            "is_keeper": pd.NA,
            "is_mock": pd.NA,
        }
    )
    return align_to_schema(out, FF_ADP_SCHEMA)


def transform_mfl(
    resp: MflResponse,
    *,
    season: int,
    scoring_format: str,
    teams: int,
    snapshot_date: date,
    is_keeper: bool | None = None,
    is_mock: bool | None = None,
) -> pd.DataFrame:
    if resp.is_empty:
        return _empty()
    df = pd.DataFrame(resp.players)
    out = pd.DataFrame(
        {
            "source": "mfl",
            "season": season,
            "scoring_format": scoring_format,
            "teams": teams,
            "snapshot_date": snapshot_date,
            "source_player_id": df["id"].astype("string"),
            "gsis_id": pd.NA,
            "player_name": pd.NA,
            "position": pd.NA,
            "team": pd.NA,
            # Every MFL numeric arrives as a string.
            "adp": pd.to_numeric(df.get("averagePick"), errors="coerce"),
            "adp_formatted": pd.NA,
            "adp_stdev": pd.NA,
            "adp_earliest_pick": pd.to_numeric(df.get("minPick"), errors="coerce"),
            "adp_latest_pick": pd.to_numeric(df.get("maxPick"), errors="coerce"),
            "times_drafted": pd.to_numeric(
                df.get("draftsSelectedIn"), errors="coerce"
            ),
            "draft_selected_pct": pd.to_numeric(
                df.get("draftSelPct"), errors="coerce"
            ),
            "source_rank": pd.to_numeric(df.get("rank"), errors="coerce"),
            "total_drafts": resp.total_drafts,
            "bye": pd.NA,
            "window_start_date": pd.NaT,
            "window_end_date": pd.NaT,
            "is_keeper": is_keeper if is_keeper is not None else pd.NA,
            "is_mock": is_mock if is_mock is not None else pd.NA,
        }
    )
    return align_to_schema(out, FF_ADP_SCHEMA)
