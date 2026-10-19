"""Read a causal view of the operator's daily panel, never its outcome labels."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from src.research.local_quant.catalog import contained_file
from src.research.local_quant.models import MAIN_PREFIXES, QuantRequest

_REQUIRED = {"date", "code", "open", "high", "low", "close", "volume"}
_COLUMNS = _REQUIRED | {"display_name", "pre_close", "amount"}


def _fingerprint(path: Path, before) -> dict:
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("dataset changed while reading; retry with a stable snapshot")
    return {"path": str(path), "bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest}


def _codes(series):
    return series.astype("string").str.extract(r"(\d{6})", expand=False)


def load_bars(root: Path, request: QuantRequest) -> tuple[pd.DataFrame, dict]:
    path = contained_file(root, "data/daily_from_minutes.csv")
    before = path.stat()
    columns = set(pd.read_csv(path, nrows=0).columns)
    if missing := _REQUIRED - columns:
        raise ValueError("daily CSV missing columns: " + ", ".join(sorted(missing)))
    chunks = []
    total = 0
    market_calendar = set()
    for chunk in pd.read_csv(
        path,
        usecols=sorted(_COLUMNS & columns),
        dtype={"code": "string", "date": "string", "display_name": "string"},
        chunksize=150000,
    ):
        total += len(chunk)
        chunk["code"] = _codes(chunk["code"])
        chunk["date"] = pd.to_datetime(chunk["date"], format="%Y-%m-%d", errors="raise")
        market_calendar.update(chunk.loc[chunk["date"].le(pd.Timestamp(request.end_date)), "date"].unique())
        mask = chunk["date"].le(pd.Timestamp(request.end_date)) & chunk["code"].str.startswith(MAIN_PREFIXES, na=False)
        if request.codes is not None:
            mask &= chunk["code"].isin(request.codes)
        chunks.append(chunk.loc[mask].copy())
    frame = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if frame.empty:
        raise ValueError("no local bars match the requested universe/window")
    if request.codes and (missing := set(request.codes) - set(frame["code"])):
        raise ValueError("codes absent from local data: " + ", ".join(sorted(missing)))
    if frame.duplicated(["date", "code"]).any():
        raise ValueError("duplicate symbol/date rows in daily data")
    frame = frame.sort_values(["code", "date"]).reset_index(drop=True)
    numeric = ["open", "high", "low", "close", "volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="raise")
    values = frame[numeric].to_numpy(dtype=float)
    invalid = (
        ~np.isfinite(values).all(axis=1)
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | frame["volume"].lt(0)
    )
    invalid |= frame["high"].lt(frame[["open", "close", "low"]].max(axis=1)) | frame["low"].gt(
        frame[["open", "close"]].min(axis=1)
    )
    if invalid.any() and request.data_quality == "strict":
        raise ValueError(
            f"daily data contains {int(invalid.sum())} invalid price/volume/OHLC rows; "
            "inspect the source or explicitly select data_quality='quarantine'"
        )
    excluded = frame.loc[invalid, ["date", "code", *numeric]].copy()
    frame = frame.loc[~invalid].copy()
    if frame.empty:
        raise ValueError("no valid daily bars remain after data-quality checks")
    if "display_name" not in frame:
        frame["display_name"] = ""
    frame["display_name"] = frame["display_name"].fillna("").astype(str)
    if "pre_close" not in frame:
        frame["pre_close"] = frame.groupby("code")["close"].shift(1)
    frame["pre_close"] = pd.to_numeric(frame["pre_close"], errors="raise")
    if "amount" not in frame:
        frame["amount"] = np.nan
    metadata = {
        **_fingerprint(path, before),
        "rows": len(frame),
        "file_rows": total,
        "codes": int(frame["code"].nunique()),
        "history_start": str(frame["date"].min().date()),
        "last_date": str(frame["date"].max().date()),
        "data_quality": request.data_quality,
        "quarantined_rows": len(excluded),
        "_excluded_rows": excluded.to_dict("records"),
    }
    screen = request.screen
    if screen.rank_by == "turnover" or screen.min_turnover is not None or screen.max_turnover is not None:
        turn_path = contained_file(root, "data/turnover.csv")
        turn_before = turn_path.stat()
        turns = []
        for part in pd.read_csv(
            turn_path, usecols=["date", "code", "turn"], dtype={"code": "string"}, chunksize=150000
        ):
            part["code"] = _codes(part["code"])
            part["date"] = pd.to_datetime(part["date"], format="%Y-%m-%d", errors="raise")
            turns.append(
                part.loc[part["code"].isin(frame["code"].unique()) & part["date"].le(pd.Timestamp(request.end_date))]
            )
        turnover = pd.concat(turns, ignore_index=True)
        if turnover.duplicated(["date", "code"]).any():
            raise ValueError("duplicate symbol/date rows in turnover data")
        turnover["turn"] = pd.to_numeric(turnover["turn"], errors="raise")
        frame = frame.merge(turnover, on=["date", "code"], how="left", validate="one_to_one")
        metadata["turnover"] = _fingerprint(turn_path, turn_before)
    frame.attrs["market_calendar"] = [str(pd.Timestamp(day).date()) for day in sorted(market_calendar)]
    return frame, metadata
