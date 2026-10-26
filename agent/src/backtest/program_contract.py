"""Artifact contract for a custom program backtest.

A program writes three CSVs into the run directory. The report page already
reads those shapes. This module checks the columns and turns the metrics row
into the scalar dict stored on the run card.
"""

from __future__ import annotations

import os
import math
from pathlib import Path

import pandas as pd

EQUITY_COLUMNS = ("timestamp", "equity", "ret", "drawdown")
TRADE_COLUMNS = ("timestamp", "code", "side", "pnl", "reason", "return_pct")
METRIC_COLUMNS = (
    "final_value",
    "total_return",
    "annual_return",
    "sharpe",
    "max_drawdown",
    "win_rate",
    "trade_count",
)


def program_root(config: dict) -> Path:
    """Directory that entrypoint paths are resolved against."""
    raw = str(config.get("program_root") or os.environ.get("VIBE_TRADING_PROGRAM_ROOT") or "").strip()
    if not raw:
        raise ValueError("program mode requires program_root or VIBE_TRADING_PROGRAM_ROOT")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"program_root is not a directory: {root}")
    return root


def resolve_entrypoint(config: dict) -> Path:
    """Resolve a workspace Python file and refuse paths that leave program_root."""
    raw = str(config.get("entrypoint") or "").strip()
    if not raw:
        raise ValueError("program mode requires entrypoint")
    root = program_root(config)
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if path.suffix != ".py" or not path.is_file():
        raise ValueError(f"entrypoint must be an existing .py file, got {path}")
    if not path.is_relative_to(root):
        raise ValueError(f"entrypoint must stay inside program_root: {path}")
    return path


def program_args(config: dict) -> list[str]:
    """Optional argv for the entrypoint. Strings only, no shell."""
    raw = config.get("args") or []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("args must be a list of strings")
    return list(raw)


def _require_columns(path: Path, required: tuple[str, ...], *, allow_empty: bool = False) -> pd.DataFrame:
    if not path.is_file():
        raise ValueError(f"missing {path.name}")
    frame = pd.read_csv(path)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} missing columns: {', '.join(missing)}")
    if frame.empty and not allow_empty:
        raise ValueError(f"{path.name} has no rows")
    return frame


def load_program_metrics(run_dir: Path) -> dict[str, float | int]:
    """Validate the three artifact CSVs and return the metrics row."""
    artifacts = Path(run_dir) / "artifacts"
    _require_columns(artifacts / "equity.csv", EQUITY_COLUMNS)
    _require_columns(artifacts / "trades.csv", TRADE_COLUMNS, allow_empty=True)
    metrics = _require_columns(artifacts / "metrics.csv", METRIC_COLUMNS)
    row = metrics.iloc[0]
    parsed: dict[str, float | int] = {}
    for column in METRIC_COLUMNS:
        value = pd.to_numeric(row[column], errors="coerce")
        if pd.isna(value) or not math.isfinite(float(value)):
            raise ValueError(f"metrics.csv column {column} must be finite and numeric")
        if column == "trade_count" and (value < 0 or float(value) != int(value)):
            raise ValueError("metrics.csv trade_count must be a nonnegative integer")
        parsed[column] = int(value) if column == "trade_count" else float(value)
    return parsed
