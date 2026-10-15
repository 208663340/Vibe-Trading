"""Regression tests for the social-media sentiment factor examples."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


SKILL_MD = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "research"
    / "skills"
    / "social-media-intelligence"
    / "SKILL.md"
)


def _load_python_block(section_title: str) -> dict[str, object]:
    """Load the first Python block from a named skill section."""
    text = SKILL_MD.read_text(encoding="utf-8")
    section = text.split(section_title, 1)[1]
    code = section.split("```python\n", 1)[1].split("```", 1)[0]
    namespace: dict[str, object] = {"np": np, "pd": pd}
    exec(code, namespace)
    return namespace


def _sentiment_rows(date_order: list[int]) -> pd.DataFrame:
    dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])
    rows = []
    for day_number in date_order:
        date = dates[day_number - 1]
        for ticker_number, ticker in enumerate(["A", "B", "C", "D", "E", "F"]):
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "sentiment_score": float(ticker_number),
                    "return": float(day_number),
                }
            )
    return pd.DataFrame(rows)


def test_build_sentiment_factor_uses_computed_normalization() -> None:
    """The example should not require a sentiment_norm input column."""
    namespace = _load_python_block("### 4.1 Social-Sentiment Factor Construction")
    build_sentiment_factor = namespace["build_sentiment_factor"]
    raw_data = _sentiment_rows([1, 2, 3, 4])

    result = build_sentiment_factor(raw_data, forward_days=1)

    assert len(result["factor"]) == len(raw_data)
    assert not raw_data.columns.isin(["sentiment_norm"]).any()


def test_build_sentiment_factor_sorts_forward_dates() -> None:
    """Forward horizons should follow date order rather than input row order."""
    namespace = _load_python_block("### 4.1 Social-Sentiment Factor Construction")
    raw_data = _sentiment_rows([3, 1, 4, 2])
    raw_data["sentiment_norm"] = raw_data.groupby("date")["sentiment_score"].transform(
        lambda x: (x - x.mean()) / (x.std() + 1e-8)
    )

    namespace["compute_ic"] = lambda _factor, returns: returns.iloc[0]
    result = namespace["build_sentiment_factor"](raw_data, forward_days=2)

    first_date = pd.Timestamp("2026-01-01")
    assert result["ic_series"].loc[first_date] == 3.0


def test_orthogonalize_sentiment_preserves_missing_observations() -> None:
    """Missing sentiment or factor rows should stay missing after regression."""
    namespace = _load_python_block("### 4.2 Orthogonalization Against Traditional Factors")
    sentiment = pd.Series([1.0, np.nan, 3.0, 4.0], index=["a", "b", "c", "d"])
    factors = pd.DataFrame(
        {"size": [40.0, np.nan, 20.0, 10.0]},
        index=["d", "c", "b", "a"],
    )

    residual = namespace["orthogonalize_sentiment"](sentiment, factors)

    assert residual.index.equals(sentiment.index)
    assert residual[["b", "c"]].isna().all()
    assert residual[["a", "d"]].notna().all()
