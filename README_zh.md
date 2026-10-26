# 量化策略研究工作台

面向独立量化研究者和小团队。外部 Codex 等 Code Agent 负责写代码、改策略；工作台负责执行实验、保存证据、展示报告和对照版本。

默认界面收敛为 **研究工作台、报告、实验比较、工作台设置**。启动时不再创建内部聊天助手、模型供应商、定时 Agent 或 Swarm，也不需要 LLM 密钥。

## 本地启动

需要 Python 3.11+；构建界面需要 Node 22.22+。在仓库根目录执行：

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
npm --prefix frontend ci
npm --prefix frontend run build
quant-workbench serve --workspace "$PWD"
```

访问 http://127.0.0.1:8899。默认实验目录是 `~/.vibe-trading/runs`，可通过 `--root /绝对路径` 单独保存。一个存储目录只允许一个服务持有；`--workers` 设置 1–8 个并发任务，队列最多容纳 64 个未结束任务。

已有开发环境也可以直接执行：

```sh
PYTHONPATH=agent .venv/bin/python -m src.interfaces.workbench_cli serve --workspace "$PWD"
```

前端开发时另开终端执行 `npm --prefix frontend run dev`，访问 http://localhost:5899。

## 跑通第一个研究闭环

另开终端，激活相同虚拟环境：

```sh
quant-workbench submit examples/workbench/experiment.json
quant-workbench experiments --project offline-demo
quant-workbench status <run_id>
quant-workbench logs <run_id>
quant-workbench result <run_id>
quant-workbench cancel <run_id>
```

命令输出 JSON。提交后立即获得稳定的实验 ID，任务由独立服务持有，关闭 CLI/MCP 不会停止执行。取消先进入 `cancelling`，停止执行后才进入终态。超时记为失败；服务重启后把未结束记录标为 `interrupted`，这不代表旧的孤儿进程已经停止。

示例使用合成日频价格，不联网，没有手续费和滑点，仅用于验证产物协议。复制清单，把 `args` 改为 `["--shares", "3"]`，填入首个实验的 `parent_run_id` 和修改说明后重新提交。页面选中两个成功实验，即可进入对应版本的比较。

## 外部 Agent 如何接入

优先使用上面的 CLI。也可启动 `quant-workbench-mcp --url http://127.0.0.1:8899`，接入支持 MCP 的外部 Agent。先启动工作台服务，MCP 仅作为客户端，不会额外启动执行器。

提供七个工具：`workbench_info`、`submit_experiment`、`list_experiments`、`get_experiment`、`get_experiment_result`、`get_experiment_logs`、`cancel_experiment`。具体 MCP 配置见 [英文说明](README.md#external-code-agents-and-mcp)。

推荐流程：查看允许的工作目录 → 写策略与实验清单 → 提交 → 检查状态和日志 → 读取报告与未验证项 → 带父实验 ID 提交改进。页面的“复制研究上下文”可把配置、文件哈希和结果交给外部 Agent。

## 产物与可信度边界

提交清单明确指定源码和输入文件，最多 512 个文件、100 MiB。服务先复制并记录 SHA-256，再排队执行。CLI 中的 `source_dir` 相对清单文件解析；HTTP/MCP 中的相对路径基于服务工作目录。点文件、符号链接和越界路径不允许提交。

自定义 Python 程序在源码快照目录运行，读取 `RUN_DIR/config.json`，输出以下三个 CSV：

| 文件 | 必需列 |
| --- | --- |
| `artifacts/equity.csv` | `timestamp,equity,ret,drawdown` |
| `artifacts/trades.csv` | `timestamp,code,side,pnl,reason,return_pct` |
| `artifacts/metrics.csv` | `final_value,total_return,annual_return,sharpe,max_drawdown,win_rate,trade_count` |

零交易允许只有交易表头；指标必须为有限数值，交易数量必须为非负整数。成功记录明确区分“产物格式校验通过”和“账务勾稽、样本外验证尚未执行”。M0 不会独立重算自定义程序的指标。

已有内置回测引擎可通过 `mode="builtin"` 和 `code/signal_engine.py` 使用。数据供应商、依赖包和未列入清单的文件不属于快照，源码哈希不等于完全可复现。Python 程序使用当前用户权限执行，工作目录检查不是操作系统沙箱。服务仅监听本机，沿用 API 密钥、Origin 与 Host 检查。

## 本次交付与后续

界面已统一为量化研究工作台。旧聊天、Swarm、模型与渠道设置、定时助手、实盘控制的前端实现已删除，同时清理了相关客户端 API、翻译和专用依赖。报告指标卡已移入报告组件目录。

推荐使用 `quant-workbench` 和 `quant-workbench-mcp`；原 `vibe-trading`、`vibe-trading-mcp` 命令保留为兼容别名。已有 `~/.vibe-trading` 存储与 API 密钥存储标识保持兼容，本次不迁移或删除用户实验及凭据。后端 Assistant/Session/Swarm 实现和 Python 依赖仍因历史反向依赖保留，不在默认工作台启动链路中运行。历史部署脚本需另行迁移，请以上面的启动命令为准。

下一步依次是：M1 深度亏损诊断与版本对照，M2 真正的训练/验证与稳健性检验，M3 批量实验及候选筛选。完整方针见 [ADR-003](docs/decisions/0003-external-agent-research-workbench.md)。

基于 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)，采用 [MIT](LICENSE) 协议，保留原有基础设施的署名与许可。
