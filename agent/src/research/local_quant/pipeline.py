"""Run captured local feature code through the native shared-cash engine."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.research.local_quant.data import load_bars
from src.research.local_quant.execution import LocalQuantEngine, monthly_execution_dates
from src.research.local_quant.models import QuantRequest
from src.research.local_quant.selection import build_targets


def _json_value(value):
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path: Path, value):
    path.write_text(json.dumps(_json_value(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


class _Loader:
    source = "local"

    def __init__(self, data):
        self.data = data

    def fetch(self, codes, *args, **kwargs):
        return {code: self.data[code] for code in codes if code in self.data}


class _Signals:
    def __init__(self, targets):
        self.targets = targets

    def generate(self, data):
        return {code: self.targets[code] for code in data}


def execute_job(run_dir: Path) -> dict:
    job = json.loads((run_dir / "quant_job.json").read_text(encoding="utf-8"))
    request = QuantRequest.model_validate(job["request"])
    bars, dataset = load_bars(Path(job["quant_root"]), request)
    excluded_rows = dataset.pop("_excluded_rows", [])
    namespace = {}
    # Only the parent-prepared, captured adapter is executed. The strategy
    # scripts' main functions and publishers are never imported or called.
    code_path = run_dir / "code/signal_engine.py"
    exec(compile(code_path.read_text(encoding="utf-8"), str(code_path), "exec"), namespace)
    features = namespace["build_features"](bars.copy())
    targets, decisions = build_targets(bars, features, request, job["strategy"])
    dates = targets.index
    evaluated = dates[(dates >= pd.Timestamp(request.start_date)) & (dates <= pd.Timestamp(request.end_date))]
    if len(evaluated) < 2:
        raise ValueError("evaluation needs at least two available daily bars")
    first = max(0, dates.searchsorted(evaluated[0]) - 1)
    targets = targets.iloc[first:]
    active_codes = targets.columns[targets.ne(0).any()].tolist()
    if not active_codes:
        active_codes = [str(bars["code"].iloc[0])]
    targets = targets.reindex(columns=active_codes)
    suffixes = {code: code + (".SH" if code.startswith("6") else ".SZ") for code in active_codes}
    targets = targets.rename(columns=suffixes)
    data = {}
    for code, group in bars.loc[bars["code"].isin(active_codes)].groupby("code", sort=True):
        # Keep a genuine historical mark even when the stock is halted for the
        # whole evaluation window. Reindexed rows preserve the market clock;
        # LocalQuantEngine excludes those rows from execution, never filling OHLC.
        prior = group.loc[group["date"].lt(dates[first])].tail(1)
        current = group.loc[group["date"].ge(dates[first])]
        panel = pd.concat([prior, current]).set_index("date")
        index = targets.index.union(panel.index)
        data[suffixes[code]] = panel[["open", "high", "low", "close", "volume", "pre_close"]].reindex(index)
    config = {
        "mode": "local_quant",
        "engine": "china_a",
        "source": "local",
        "interval": "1D",
        "codes": sorted(data),
        "start_date": str(request.start_date),
        "end_date": str(request.end_date),
        "evaluation_start_date": str(request.start_date),
        "initial_cash": request.initial_cash,
        "position_adjustment": "rebalance",
        "liquidate_at_end": False,
        **{
            key: getattr(request, key)
            for key in ["commission_rate", "commission_min", "stamp_tax", "transfer_fee", "slippage"]
        },
        "local_quant": {
            "strategy_id": request.strategy_id,
            "screen": request.screen.model_dump(),
            "max_positions": request.max_positions,
            "cadence": job["strategy"]["cadence"],
            "source_sha256": job["strategy"]["source_sha256"],
            "dataset": dataset,
        },
        "content_filter_warnings": [
            "Local CSV price adjustment and historical membership/ST/delisting metadata are unverified.",
            "Open positions are marked to market at the final close; no synthetic terminal liquidation.",
            "The native engine benchmark is the internal basket, not an independent market benchmark.",
            "Historical strategy/screen selection is not an out-of-sample profitability certificate.",
        ],
    }
    if job["strategy"]["cadence"] == "monthly":
        config["rebalance_mask"] = monthly_execution_dates(dates, evaluated)
    if dataset["quarantined_rows"]:
        config["content_filter_warnings"].append(
            f"Explicit quarantine excluded {dataset['quarantined_rows']} invalid bars; "
            "excluded_bars.csv lists them. Missing prices are not fabricated; valuations carry prior valid marks."
        )
    write_json(run_dir / "config.json", config)
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(exist_ok=True)
    decisions.to_csv(artifacts / "candidates.csv", index=False)
    pd.DataFrame(excluded_rows, columns=["date", "code", "open", "high", "low", "close", "volume"]).to_csv(
        artifacts / "excluded_bars.csv", index=False
    )
    engine = LocalQuantEngine(config)
    metrics = engine.run_backtest(config, _Loader(data), _Signals(targets), run_dir)
    pd.DataFrame(engine.skipped_orders, columns=["date", "code", "reason"]).to_csv(
        artifacts / "execution_skips.csv", index=False
    )
    keys = ["final_value", "total_return", "annual_return", "sharpe", "max_drawdown", "win_rate", "trade_count"]
    if any(key not in metrics for key in keys):
        raise ValueError("native engine did not produce the required portfolio metrics")
    summary = _json_value(
        {
            "strategy": {key: job["strategy"][key] for key in ["id", "title", "source", "cadence", "source_sha256"]},
            "requested_start": str(request.start_date),
            "requested_end": str(request.end_date),
            "effective_start": str(evaluated[0].date()),
            "effective_end": str(evaluated[-1].date()),
            "initial_cash": request.initial_cash,
            "metrics": {key: metrics[key] for key in keys},
            "candidate_rows": len(decisions),
            "candidate_stocks": int(decisions["code"].nunique()),
            "selected_decisions": int(decisions["selected"].sum()),
            "filled_orders": len(engine.fill_records),
            "open_positions": len(engine.positions),
            "unavailable_execution_decisions": len(engine.skipped_orders),
            "by_symbol": metrics.get("by_symbol", {}),
            "dataset": dataset,
            "screen": request.screen.model_dump(),
            "costs": {
                key: config[key]
                for key in ["commission_rate", "commission_min", "stamp_tax", "transfer_fee", "slippage"]
            },
            "limitations": config["content_filter_warnings"],
            "execution": "收盘形成候选与目标，下一交易日开盘执行；月度策略在月末决策并持有至下次决策。",
            "blocked_order_policy": "缺失行情或零成交量当日不成交；月度未成交指令当日失效，等待下一月度决策。日度策略依下一收盘目标重新决策。评估首日按之前已知目标初始化。",
        }
    )
    write_json(artifacts / "summary.json", summary)

    def percentages(name):
        return f"{summary['metrics'][name]:.2%}" if summary["metrics"][name] is not None else "不可计算"

    report = [
        f"# 本地量化策略回测：{summary['strategy']['title']}",
        "",
        f"有效区间：{summary['effective_start']} 至 {summary['effective_end']}。",
        "",
        summary["execution"],
        summary["blocked_order_policy"],
        "筛选条件限制策略决策时的目标股票池；退出股票池将在下一次策略决策时移出目标。",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 初始资金 | {request.initial_cash:,.2f} |",
        f"| 期末权益（含持仓市值） | {metrics['final_value']:,.2f} |",
        f"| 总收益率 | {percentages('total_return')} |",
        f"| 年化收益率 | {percentages('annual_return')} |",
        f"| 最大回撤 | {percentages('max_drawdown')} |",
        f"| 夏普比率 | {metrics['sharpe']:.3f} |",
        f"| 已完成交易 | {metrics['trade_count']} |",
        f"| 实际成交次数 | {summary['filled_orders']} |",
        f"| 期末未平仓股票数 | {summary['open_positions']} |",
        f"| 缺失行情或零成交量跳过的股票决策 | {summary['unavailable_execution_decisions']} |",
        "",
        f"筛选候选记录 {len(decisions)} 条，覆盖 {summary['candidate_stocks']} 个代码。",
        f"数据质量模式：{request.data_quality}；隔离无效行情 {dataset['quarantined_rows']} 条（详见 excluded_bars.csv）。",
        "未入选时保持现金；上述结果为共享资金账户，已通过引擎计入配置的交易费用和滑点。",
        "",
        "## 筛选与费用",
        "",
        "```json",
        json.dumps(
            {"screen": summary["screen"], "max_positions": request.max_positions, "costs": summary["costs"]},
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 结果边界",
        "",
        "- 数据复权、历史股票池、ST 与退市状态尚未独立校验，不能据此宣称实盘盈利。",
        "- 隔离或缺失行情不填造开高低收；该股票当日无法按此数据成交，持仓估值沿用最近有效收盘价。",
        "- 期末持仓按收盘价估值，未假设最后一天全部卖出；平仓胜率不包含未实现盈亏。",
        "- 引擎参考篮子不是独立市场基准，策略及参数选择需要另做样本外验证。",
        "- candidates.csv 是当时决策记录；fills.jsonl 是实际成交，positions.csv 是实际仓位。",
        "- execution_skips.csv 记录缺失行情或零成交量导致跳过的股票决策。",
        "",
    ]
    (artifacts / "summary.md").write_text("\n".join(report), encoding="utf-8")
    return summary
