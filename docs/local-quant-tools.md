# 本地量化策略：加载、收盘筛选、组合回测

在聊天中要求“使用 quant 里的策略，按收盘指标筛选股票，回测并汇总”，Agent 可以调用：

- `list_local_quant_strategies`：发现现有策略、源码位置、决策频率和支持情况。
- `load_local_quant_strategy`：加载策略的实际特征计算表达式和源码哈希。
- `backtest_local_quant_strategy`：筛选候选，再执行一个共享资金账户的回测并生成报告。

主 Agent 自动发现工具；默认 Autonomous Research Team 的两个研究节点也已开放这三个工具及本地策略技能，可自主选择和执行。其他自定义 Swarm Agent 需在 `tools` 中允许这三个名称，并在 `skills` 中允许 `local-quant-strategy`。
新 skill `local-quant-strategy` 描述了主动选择策略、执行、检查与汇总的流程。

## 数据与目录

源码与数据从操作员配置的 `VIBE_TRADING_QUANT_ROOT` 读取。源码检出环境默认使用
项目同级 `quant`，安装环境回退到 `~/.vibe-trading/quant`。模型参数不能指定数据路径、
Python 文件或替换受信任目录。原始文件不做修改。

必需：`data/daily_from_minutes.csv` 的 date/code/open/high/low/close/volume。
可选：display_name/pre_close/amount。换手率筛选或排序额外使用
`data/turnover.csv` 的 date/code/turn；缺少数据会明确报错。

## 策略

| ID | 源码 | 决策 |
|---|---|---|
| ma20 | simple_tactics_backtest.py | 每日站上20日均线 |
| turtle20_10 | simple_tactics_backtest.py | 每日20日突破、10日退出 |
| momentum20_top30 | simple_tactics_backtest.py | 月末20日动量前30 |
| limitup_nextday | simple_tactics_backtest.py | 涨停次日持有一夜，隔开同一开盘的卖出回款与再买入 |
| ma60 | simple_long_backtest.py | 月末站上60日均线 |
| momentum60_top50 | simple_long_backtest.py | 月末60日动量前50 |
| lowvol60_top50 | simple_long_backtest.py | 月末60日波动最低50 |

目录也显示 board_overnight，但它需要竞价时点适配，当前收盘回测不会执行它。
新 Python 文件不会自动获得执行权限；扩展策略需要增加明确的适配契约。

保留原源码的 load/load_bars 特征表达式及 turtle_position，替换 CSV 读取入口，去掉
next_open/oo 事后收益计算。不会导入原脚本、运行 main 或调用其发布函数。
适配代码只在现有 Runner 子进程内执行。该功能信任操作员维护的源码；AST 检查用于
拒绝已识别的未来数据操作，不是任意 Python 代码的安全隔离器。

## 调用示例

```json
{
  "strategy_id": "ma20",
  "start_date": "2026-08-01",
  "end_date": "2026-10-14",
  "screen": {
    "min_close": 3,
    "ma_window": 20,
    "rank_by": "momentum20",
    "top_n": 50
  },
  "max_positions": 10,
  "data_quality": "strict",
  "initial_cash": 1000000
}
```

可选 codes 如 `["000001", "600519.SH"]`，最多500个显式主板代码。省略时研究可用
主板股票池。参数值由 Pydantic 校验；未知字段（尤其路径和未来收益字段）被拒绝。

`screen` 还支持 max_close/min_volume/min_momentum20/min_turnover/max_turnover。
ma_window=null 可关闭均线筛选；rank_by 可用 momentum20/volume/turnover。
候选 top_n 先于策略选择生效，max_positions 再限制最终目标数量。离开候选池会在
策略下一次决策时移出目标；月度策略中途不重新选股。
月度组合也不在月中恢复等权：仅在月末后的开盘调仓。评估首日用此前已知目标
初始化账户。缺失行情或零成交量日跳过交易；受阻的月度指令当日失效，等待下月
决策，日度策略依下一收盘目标重新决策。

## 时点、收益与产物

只读取因果行情列，next_day_* 从不进入策略输入。指标计算可读评估前历史，评估前
不交易、不计入净值。收盘目标由引擎移到下一交易日开盘执行。一次调用只有一个资金
账户，现金约束、手数、费用、滑点和交易限制均使用 ChinaAEngine。

原脚本的近似组合收益被统一记账替代，结果不保证和原脚本数值相等。月末判断使用
数据中的观察到的交易日期；窗口末尚未发生的下一次交易不计入。期末头寸按收盘价
估值，不做强制终结成交；已平仓胜率和期末未实现盈亏分别理解。

每次调用创建新的应用 run，保存源码快照与哈希、数据文件哈希、请求和配置。
Agent/Swarm 自动注入的工作目录作为内部上下文处理。执行完成后，在当前 Agent
工作目录内保存独立的结果证据副本，返回其中的文件路径，使其可以通过 read_file
检查；原始应用 run 仍保留全部源码、配置和原生结果。回测指标也会登记到 Grounding
证据账本，供最终汇总校验。未接外部模型的测试使用脚本化模型验证完整工具调用流程。
原生 equity/trades/positions/metrics/fills/run_card 产物继续供报告页读取，另外提供：

- artifacts/candidates.csv：每次收盘筛选的候选及策略是否入选。
- artifacts/execution_skips.csv：因缺失行情或零成交量跳过的股票决策。
- artifacts/summary.json：实际组合指标、覆盖范围、筛选条件、成本和限制。
- artifacts/summary.md：中文回测汇总。

空候选是现金账户结果。超时或不完整产物标为 failed，不能作为成功回测交付。
工具最长执行900秒，若配置的工具超时更短则服从该配置。

无效 OHLC/量默认报错。可显式使用 `data_quality="quarantine"`，隔离无效行并输出
excluded_bars.csv，报告中记录数量。不会借助未来价格修复数据；缺失日期无法成交，
已有仓位沿用最近有效价格估值。月度决策使用全数据的交易日期，不把个股停牌前最后
一个报价日误当成月末。

数据复权、历史股票池/ST/退市标记尚未独立校验；股票名是快照。内置参考篮子不是
独立市场基准。候选和策略在历史数据上的选择也可能过拟合；报告不等于实盘盈利证明。
