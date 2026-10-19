"""Apply the close screen before choosing a strategy's target basket."""

from __future__ import annotations

import pandas as pd

from src.research.local_quant.models import QuantRequest


def build_targets(bars: pd.DataFrame, features: pd.DataFrame, request: QuantRequest, strategy: dict):
    frame = bars.sort_values(["code", "date"]).copy()
    grouped = frame.groupby("code", sort=False)["close"]
    frame["screen_momentum20"] = grouped.pct_change(20, fill_method=None)
    screen = request.screen
    keep = frame["close"].ge(screen.min_close) & frame["volume"].ge(screen.min_volume) & frame["volume"].gt(0)
    keep &= ~frame["display_name"].str.contains("ST", case=False, na=False)
    if screen.max_close is not None:
        keep &= frame["close"].le(screen.max_close)
    if screen.ma_window is not None:
        average = grouped.transform(lambda x: x.rolling(screen.ma_window, min_periods=screen.ma_window).mean())
        keep &= frame["close"].gt(average)
    if screen.min_momentum20 is not None:
        keep &= frame["screen_momentum20"].ge(screen.min_momentum20)
    if screen.min_turnover is not None:
        keep &= frame["turn"].ge(screen.min_turnover)
    if screen.max_turnover is not None:
        keep &= frame["turn"].le(screen.max_turnover)
    rank_column = {"momentum20": "screen_momentum20", "volume": "volume", "turnover": "turn"}[screen.rank_by]
    frame["screen_score"] = frame[rank_column]
    keep &= frame["screen_score"].notna()
    feature_column = strategy["feature"]
    if not isinstance(features, pd.DataFrame) or any(key not in features for key in ["date", "code", feature_column]):
        raise ValueError(f"source feature function must return date/code/{feature_column}")
    if features.duplicated(["date", "code"]).any():
        raise ValueError("source feature function returned duplicate keys")
    frame = frame.assign(screen_pass=keep).merge(
        features[["date", "code", feature_column]].rename(columns={feature_column: "strategy_feature"}),
        on=["date", "code"],
        how="left",
        validate="one_to_one",
    )
    dates = pd.DatetimeIndex(bars.attrs.get("market_calendar", sorted(frame["date"].unique())))
    dates = dates[dates >= frame["date"].min()]
    targets = pd.DataFrame(0.0, index=dates, columns=sorted(frame["code"].unique()))
    month_ends = set(dates[:-1][dates[:-1].to_period("M") != dates[1:].to_period("M")])
    decisions = []
    current = {}
    overnight_active = False
    grouped_dates = {day: rows for day, rows in frame.groupby("date", sort=True)}
    for day in dates:
        rows = grouped_dates.get(day, frame.iloc[:0])
        pool = (
            rows.loc[rows["screen_pass"]]
            .sort_values(["screen_score", "code"], ascending=[False, True])
            .head(screen.top_n)
            .copy()
        )
        key = strategy["id"]
        if key in {"ma20", "turtle20_10", "limitup_nextday"}:
            eligible = pool["strategy_feature"].eq(True)
        elif key == "ma60":
            eligible = pool["close"].gt(pool["strategy_feature"])
        else:
            eligible = pool["strategy_feature"].notna()
        pool["strategy_eligible"] = eligible
        candidates = pool.loc[eligible].copy()
        cadence = strategy["cadence"]
        decision_day = cadence != "monthly" or day in month_ends
        if decision_day:
            order_column = (
                "strategy_feature"
                if key in {"momentum20_top30", "momentum60_top50", "lowvol60_top50"}
                else "screen_score"
            )
            limit = min(
                request.max_positions,
                30
                if key == "momentum20_top30"
                else 50
                if key in {"momentum60_top50", "lowvol60_top50"}
                else request.max_positions,
            )
            picks = candidates.sort_values([order_column, "code"], ascending=[key == "lowvol60_top50", True]).head(
                limit
            )
            if cadence == "overnight" and overnight_active:
                picks = picks.iloc[:0]
            current = {code: 1 / len(picks) for code in picks["code"]} if len(picks) else {}
            if cadence == "overnight":
                overnight_active = bool(current)
        for code, weight in current.items():
            targets.at[day, code] = weight
        pool["decision_day"] = decision_day
        pool["selected"] = pool["code"].isin(current) & decision_day
        if pd.Timestamp(request.start_date) <= day <= pd.Timestamp(request.end_date):
            decisions.extend(
                pool[
                    [
                        "date",
                        "code",
                        "close",
                        "volume",
                        "screen_score",
                        "strategy_feature",
                        "strategy_eligible",
                        "decision_day",
                        "selected",
                    ]
                ].to_dict("records")
            )
    columns = [
        "date",
        "code",
        "close",
        "volume",
        "screen_score",
        "strategy_feature",
        "strategy_eligible",
        "decision_day",
        "selected",
    ]
    return targets, pd.DataFrame(decisions, columns=columns)
