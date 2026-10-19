"""Read trusted strategy definitions without importing their scripts."""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

from src.platform.config.accessor import get_env_config

_TACTICS = "simple_tactics_backtest.py"
_LONG = "simple_long_backtest.py"
_STRATEGIES = (
    ("ma20", "站上20日均线", _TACTICS, "load", "daily", "above_ma"),
    ("turtle20_10", "20日突破、10日退出", _TACTICS, "load", "daily", "turtle"),
    ("momentum20_top30", "20日动量前30", _TACTICS, "load", "monthly", "mom20"),
    ("limitup_nextday", "涨停次日持有一夜", _TACTICS, "load", "overnight", "limit_up"),
    ("ma60", "月末站上60日均线", _LONG, "load_bars", "monthly", "ma60"),
    ("momentum60_top50", "月末60日动量前50", _LONG, "load_bars", "monthly", "mom60"),
    ("lowvol60_top50", "月末60日低波动前50", _LONG, "load_bars", "monthly", "vol60"),
    ("board_overnight", "竞价隔夜", "board_overnight_backtest.py", "", "auction", ""),
)


def quant_root() -> Path:
    configured = get_env_config().data.vibe_trading_quant_root.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    checkout = Path(__file__).resolve().parents[4]
    if (checkout / "pyproject.toml").is_file() and (checkout.parent / "quant").is_dir():
        return (checkout.parent / "quant").resolve()
    return (Path.home() / ".vibe-trading" / "quant").resolve()


def contained_file(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("quant source/data must remain inside the trusted root")
    if not path.is_file():
        raise FileNotFoundError(f"Missing local quant file: {relative}")
    return path


def list_strategies(root: Path) -> list[dict]:
    result = []
    for key, title, source, function, cadence, feature in _STRATEGIES:
        try:
            contained_file(root, source)
            if source == _TACTICS:
                contained_file(root, _LONG)
            available = True
        except (ValueError, FileNotFoundError):
            available = False
        result.append(
            {
                "id": key,
                "title": title,
                "source": source,
                "feature_function": function,
                "cadence": cadence,
                "feature": feature,
                "available": available,
                "close_only_supported": cadence != "auction",
            }
        )
    return result


def _frame_key(node):
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "frame":
        return node.slice.value if isinstance(node.slice, ast.Constant) else None
    return None


def _check_causal(tree):
    forbidden_names = {"eval", "exec", "compile", "__import__", "open", "globals", "locals"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("feature function must be causal and cannot import code")
        if isinstance(node, ast.Name) and (node.id in forbidden_names or node.id.startswith("next_day")):
            raise ValueError("feature function contains an unsupported causal/future operation")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("next_day"):
            raise ValueError("future label columns are forbidden in strategy features")
        if isinstance(node, ast.Attribute):
            if (
                node.attr.startswith("_")
                or node.attr in {"bfill", "backfill", "interpolate", "to_csv", "to_pickle", "to_json"}
                or node.attr.startswith("read_")
            ):
                raise ValueError("feature function contains an unsupported causal/future operation")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "shift":
                period = next(
                    (k.value for k in node.keywords if k.arg == "periods"),
                    node.args[0] if node.args else ast.Constant(1),
                )
                if not isinstance(period, ast.Constant) or not isinstance(period.value, int) or period.value < 0:
                    raise ValueError("future or dynamic shift is forbidden")
            if node.func.attr == "rolling" and any(
                k.arg == "center" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
                for k in node.keywords
            ):
                raise ValueError("centered rolling windows are not causal")


def load_strategy(root: Path, strategy_id: str) -> dict:
    strategy = next((x for x in list_strategies(root) if x["id"] == strategy_id), None)
    if strategy is None:
        raise ValueError(f"Unknown local quant strategy: {strategy_id}")
    if not strategy["close_only_supported"]:
        raise ValueError("Auction strategy requires auction-time inputs; use a dedicated auction adapter")
    names = [_LONG, _TACTICS] if strategy["source"] == _TACTICS else [_LONG]
    sources = {}
    trees = {}
    for name in names:
        path = contained_file(root, name)
        if path.stat().st_size > 1_000_000:
            raise ValueError("strategy source exceeds 1 MB")
        sources[name] = path.read_text(encoding="utf-8-sig")
        trees[name] = ast.parse(sources[name], filename=name)
    original = trees[strategy["source"]]
    function = next(
        (x for x in original.body if isinstance(x, ast.FunctionDef) and x.name == strategy["feature_function"]), None
    )
    if function is None or not function.body:
        raise ValueError("unsupported strategy feature function")
    first = function.body[0]
    if not (
        isinstance(first, ast.Assign)
        and len(first.targets) == 1
        and isinstance(first.targets[0], ast.Name)
        and first.targets[0].id == "frame"
        and isinstance(first.value, ast.Call)
        and isinstance(first.value.func, ast.Attribute)
        and first.value.func.attr == "read_csv"
    ):
        raise ValueError("feature function must begin with its frame CSV read")
    body = []
    for node in function.body[1:]:
        if isinstance(node, ast.Assign) and any(_frame_key(t) in {"next_open", "oo"} for t in node.targets):
            continue
        body.append(copy.deepcopy(node))
    adapted = ast.FunctionDef(
        name="build_features",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="frame")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[ast.parse("frame = frame.copy()").body[0], *body],
        decorator_list=[],
    )
    nodes = []
    for tree in trees.values():
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"MAIN", "PREFIXES", "TOP_N"}
            ):
                ast.literal_eval(node.value)
                nodes.append(copy.deepcopy(node))
    helper = next((x for x in original.body if isinstance(x, ast.FunctionDef) and x.name == "turtle_position"), None)
    if strategy["source"] == _TACTICS:
        if helper is None:
            raise ValueError("missing causal turtle helper")
        nodes.append(copy.deepcopy(helper))
    nodes.append(adapted)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    _check_causal(module)
    code = (
        "from __future__ import annotations\nimport numpy as np\nimport pandas as pd\n\n" + ast.unparse(module) + "\n"
    )
    compile(code, "local_quant_features.py", "exec")
    return {
        **strategy,
        "feature_code": code,
        "source_files": sources,
        "source_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
    }
