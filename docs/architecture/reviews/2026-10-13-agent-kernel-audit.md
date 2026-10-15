# Agent 内核稳定性与架构审查

日期：2026-10-13。审查基线：`c25fea2bc261a2d0ed971c989d194de9f04ec963`，分支 `codex/architecture-stability-upgrade`。范围：AgentLoop、provider stream、工具调度/重放、上下文预算、证据提交及运行生命周期。本报告只读审查生产代码，未实施修复。

已读取 `AGENT_CONTRIBUTOR_GUIDE.md`、`ecosystem-primer` 和 `langchain-fundamentals` 技能。当前是自定义同步循环使用 LangChain provider adapter；本次建议保持该实现与既有契约，不迁移框架，不增加依赖。没有访问远端、凭据或真实账户，没有运行真实工具/交易或恢复删除内容。

## 结论及实施优先级

优先解决三个可实际改变执行行为的边界：provider 等待必须可取消；工具完成观察必须独立于消息提交顺序；写操作必须构成重放失效屏障。其次让终态只有一个所有者。简单抽走循环代码但保留这些行为，不能视作稳定性升级完成。

| 编号 | 优先级 | 问题 | 证据强度 |
| --- | --- | --- | --- |
| K1 | P1 | 阻塞 stream next() 期间超时和取消都不生效 | 生产方法 AST 提取离线复现 |
| K2 | P1 | 并行快结果被整批阻塞，排队任务取消后继续启动 | 生产方法 AST 提取离线复现 |
| K3 | P1 | 重放在写屏障之前预先提交，失败写入也不失效缓存 | 两个生产方法 AST 提取离线复现 |
| K4 | P1 | watchdog 与主循环竞争终态，清理不在 finally 中 | 明确代码路径；并发交错需新增测试 |
| K5 | P2 | 上下文计量锚点忽略实际请求的工具 schema 变化 | 明确算式/调用路径；需请求级测试 |

## K1：stream 的 deadline/cancel 检查位于阻塞读取之后

**位置**：`agent/src/runtime/providers/chat.py:515-528`，空流回退 `:548-568`；调用方 `agent/src/runtime/agent/loop.py:678-690`。

`for chunk in llm.stream(...)` 只有当迭代器的 `next()` 返回后才能运行 idle 和 cancel 检查。提供方若连接建立后不产出下一分片，`idle_timeout_s` 永远不能自行触发，Stop 也不会生效。`config={"timeout": ...}` 只是传给 provider 的配置，不能替代本层等待边界；同仓库 compaction 实现 `loop.py:2645-2650` 已明确说明这类配置不构成可靠硬 deadline。空流回退的 `llm.invoke()` 同样没有外层可取消等待边界。

**触发与影响**：离线 fake provider 在第一次 yield 前等待 `threading.Event`；idle deadline 为 0.03 秒，设置 cancel 后等待 0.12 秒，caller 仍存活。直到释放 fake provider 才返回。真实结果是 run 线程/会话资源无法释放，即使 watchdog 写失败状态也仍然卡住。

**修复边界**：提供统一的有界 `ProviderCallRunner`，让 caller 在队列/condition 上以单调时钟等待结果、分片、取消和 idle deadline。provider producer 必须只向队列写入，用户回调由 caller 转发；取消/超时撤销 attempt token，之后分片全部丢弃。正常完成/失败时尽力关闭 iterator，不能在 caller 里直接等待被阻塞的 `next()` 或跨线程 `close()`。空流 invoke 和 compaction summary 也走同一资源治理边界。

不能只给每个请求无限新建 daemon：卡住线程仍会占用连接及内存。使用明确最大活动 producer 数、非阻塞准入失败，以及全局或 adapter 级资源计数；permit 在底层 producer 真正退出时才释放，caller 超时不能假装释放。线程无法强杀，应把迟到隔离与资源上限作为保证；以后仅在必要的 provider 引入可终止 transport/process，不在本次泛化实现。

**针对性测试**：首分片前阻塞、两片之间阻塞、空流 invoke 阻塞、连续细碎分片、正常聚合、取消先于启动、取消后迟到工具调用不执行、timeout 后重试隔离。所有 fake worker 在测试 finally 中释放/join。重复制造挂起至容量上限，验证拒绝新请求且线程数不继续增长；验证 producer 的 `contextvars.copy_context()` 保留 session identity。保持 DSML、thought signature、encrypted provider items、usage/finish_reason 和现有 `stream_reset` 契约。

## K2：完成观察与按序提交绑在同一整批屏障上

**位置**：`agent/src/runtime/agent/loop.py:1915-1933`；取消只在批次边界 `:1867-1875`；实际工具完成的 activity 更新在 `:2440-2441`；只读等待 `:2115-2139`。

全部任务一次性 submit，线程池最多 8 个 worker。收集结果按输入顺序 `f.result()`，且直到整个 executor context 退出后才 finalize。第二个工具已返回也无法更新 `_last_activity_wall` 或发 `tool_result`。较大 batch 中只要某个调用慢，就会掩盖其余持续完成的真实进展，watchdog 可误判 stalled。排队任务没有执行前取消检查；cancel 后 executor 会继续取队列任务。只读 `_invoke_tool` 的阻塞 `queue.get(timeout=timeout)` 也不轮询取消，timeout 禁用时会无限等待。

**离线复现**：10 个只读 fake calls，首调用等待，第二个立即完成，其余等待。第二个完成后 `finalized=0`；设置 cancel 再释放等待，调用 id `9` 在 cancel 已设置后才开始。没有外部工具或网络。

**修复边界**：`ToolBatchExecutor` 负责有限并发准入和写屏障；`ToolInvoker` 负责每次调用的 deadline/heartbeat/progress lease。最多保持 N 个 in-flight，并在每次启动/补充任务前检查 run cancellation；取消 pending futures，尚未执行的调用生成既有 error envelope 对应的 cancelled result。取消竞争要有明确线性化点：已准入执行的写工具等其真实结果，未准入工具不启动。

完成时由 coordinator 立即登记真实进展；观察完成与 model messages 提交分开。为了最小兼容，messages/grounding/trace 最终提交仍按原始 call index，每个 call_id 一次。UI 是否提前收到 tool_result 必须选定：本次可只提前登记 activity，保留既有结果事件顺序；若提前发事件则必须保留 commit 标记避免重复发。worker 不能直接修改 messages、grounding、cache 或 trace。

**针对性测试**：fast/slow 调换输入顺序、超过并发上限、cancel 时尚有排队项、单工具挂起、timeout=0 仍可取消等待、只读失败不终止同批其余结果、write 严格在前批实际完成后开始、同批相同调用的准入语义、每个 call_id 恰好一份最终记录。用受控 clock/Event 验证真实快完成重置 inactivity，heartbeat 本身不重置。若 caller 丢弃只读迟到结果，worker 的资源 permit 仍须等实际退出。

## K3：重放决定早于写操作，且 error 写操作不失效旧观察

**位置**：整批 preprocessing `agent/src/runtime/agent/loop.py:1525-1721`，立即提交 replay `:1611-1659`，之后才 dispatch `:1729-1768`；失效逻辑位于 `if success` 内 `:2489-2510`。

有两个独立触发路径：

1. 已有 compact 后 replay-ready 的读取结果 A，模型在同一 response 提出 `[write(B), read(A)]`。preprocessing 在执行任何 write 前就从缓存把 A 追加到 messages。随后 write 成功并清空缓存也无法撤回已提交 A。离线执行真实 `_process_tool_calls` 得到消息顺序 `read: old content`、`write: new content written`，因此所谓 write-serial barrier 并没有保护 replay。
2. 写工具先产生局部副作用再返回 error，`_finalize_tool_result` 因 `success=False` 跳过失效。下次 compact/replay 仍可恢复写前的旧结果。生产示例是 `bash` 非零退出/超时不保证没有先修改文件（`agent/src/runtime/tools/bash_tool.py:147-170`）；本审查没有执行该工具。离线真实 finalize 方法验证 error write 后缓存依然存在。

**影响**：模型拿到与当前文件/配置不一致的旧证据；由于 replay 具有正常 call_id/status，用户和下游易认为它代表最新一次调用结果。第二条不能以“写失败等于没有变更”作为假设。

**修复边界**：把 dedup/cache/replay eligibility 保留在 policy 中，但 replay 最终决定移至按序 dispatch 的读取屏障。已准入执行的任意 non-readonly tool 都增加 mutation epoch 并保守失效 readonly replay（包括 error/exception/unknown outcome）；授权阻断、未执行的取消不能记为外部写。工具返回纯合成错误与真实执行过但失败的结果通过 `executed` 标志区分。写失效不应清除纯确定性计算缓存；仍需保留 existing authorization-before-cache/no_cache/repeatable/visibility lease/recovery cap 语义。

**针对性测试**：`[write success, replay read]` 不恢复旧值；`[read replay, write]` 保持原顺序且后续再次 read 不可用旧 epoch；`write partial effect + error`、执行抛异常都失效；authorization-blocked write 不失效；cancelled-before-start write 不失效；成功正常 read 和 deterministic 计算仍可重放；失败结果不写入成功证据 ledger。

## K4：终态与资源清理由多个执行路径竞争

**位置**：watchdog 写 end/state/cancel `agent/src/runtime/agent/loop.py:1462-1480`；主线程计算和写终态 `:1362-1410`；异常出口 `:1336-1357`。`agent/src/platform/core/state.py:80-86` 直接 truncate/write state 文件，无原子替换或同 run transition guard。

watchdog 已写 `end` 后，主循环仍无条件在 `loop.py:1395` 再写一个 `end`。`resolve_final_status` 的 stall-first 只处理读取时 stall 已发生的情况；若主线程先 resolve success、随后 watchdog 写 failed，再主线程写 success，内存、state 和 trace 会冲突。`_run_done.set()` 在成功/异常路径最后，state/trace 写失败可能跳过它；初始化又在主 try 之前，退出清理没有总 finally。未收到 worker 最终结果时写失败状态，也不能证明底层写操作已停止。

**建议**：`RunLifecycle` 独占 terminal transition，持有 run-local done/cancel、monotonic activity、terminal outcome、锁及 generation；watchdog 只申请停止/失败，不自行绕开统一 finalize 写 end。终态 claim/commit 一次，`RunStateStore` 单文件 atomic replace；trace 与 state 两份文件无法成为单文件原子事务，必须声明 crash consistency 顺序及恢复规则。最外层 finally 停止 watchdog/撤销 event lease/关闭 trace，任何持久化失败也必须设置 done，不能用吞异常伪报成功。writer 仍活着时记录 terminal reason 保留 unknown-completion 信息，禁止自动重跑写工具。

**针对性测试**：barrier 精确控制 resolve 与 watchdog 的竞争，assert 一次 end、state/result 一致；state write/trace write/close 注入失败，done 必须设置；run 重用后旧 watchdog/迟到事件不能污染新 run；单实例并行 run 明确拒绝或隔离；初始化失败也清理已获取资源。K4 当前为静态验证，应在实施时补测试，不能称为已修复。

## K5：context accounting 没有标记实际请求形态

**位置**：`agent/src/runtime/agent/context_budget.py:178-202`；`agent/src/runtime/agent/loop.py:663-667,772-775`。锚点只记录 input_tokens 与 messages 估值，锚定后 `tokens(messages, tool_schema_tokens)` 忽略工具 schema 参数。强制 text-only/grounding correction 请求可不发 tools，同样消息长度却有不同真实输入开销；若后续恢复工具，锚点会低估；反向也可高估。

`CompactionBudget.conversation` 的 8K floor 在 static prompt 已占满小 window 时会让 compact threshold 大于 total (`context_budget.py:145-162`)。这不是 compaction 能压缩解决的问题，应明确检测静态部分不可容纳而 fail with actionable reason。

**建议/测试**：引入请求形态快照 `PromptShape(messages_estimate, tools_estimate, model_id, generation)`，observe 必须绑定实际发送的工具集合与模型；估值用输入锚点加 messages/schema 的增量。测试 tools→text-only→tools、不同返回 model、无 usage、异常 usage、static 超预算。仅处理数值记账，不改变系统 prompt、tool schemas 或 grounding 规则。

## 兼容性架构方案与接口

建议模块是真实职责边界，`AgentLoop.run/cancel` 和既有 helper re-exports 继续保留：

| 模块 | 拥有的状态/接口 | 不允许承担的职责 |
| --- | --- | --- |
| `providers/call_runner.py` | `run_stream(producer, on_chunk, cancel, idle_deadline, attempt)`；bounded producer admission、contextvars、late-event fence、iterator cleanup | 不生成业务结果，不执行工具，不改 run state |
| `agent/run_lifecycle.py` | `RunControl(run_id, generation)`、`note_progress()`、`request_stop(reason)`、`finish_once(outcome)`、`close()` | 不判断模型证据可信性、不杀正在执行的写操作 |
| `agent/tool_execution.py` | `ToolInvoker.invoke(PreparedCall, control) -> ToolExecutionResult`；`ToolBatchExecutor.execute(plan, control) -> ordered results` | 不写 memory/grounding/messages/cache |
| `agent/tool_replay.py` | `ReplayLedger.lookup(call, epoch)`、`note_context_loss(ids)`、`invalidate_for_executed_write()`、`consume_visibility(request_ids)` | 不创建外部观测、不绕过 authorization |
| `agent/tool_commit.py` | `commit(result)`：唯一工具证据、message、cache、trace/event 提交点 | 不做阻塞外部调用 |
| 现有 `context_budget.py` | 请求快照/真实 usage 锚定、预算计算 | 不决定成功/失败终态 |

建议数据形状（内部对象，不改变外部 JSON）：

```python
PreparedCall(index, call_id, name, original_arguments, normalized_arguments,
             readonly, repeatable, deterministic, mutation_epoch)
ToolExecutionResult(call, raw_result, elapsed_ms, executed,
                    outcome, attempt_id)  # outcome: completed/error/timeout/cancelled
RunControl(run_id, generation, cancel_event, done_event, clock)
```

原始参数用于保持既有 canonical identity，normalized 参数用于真实执行与 grounding；不要在本次静默重定义缓存 key。`raw_result` 一直保留到 grounding 与 trace redaction/truncation 分界：证据 ingest 使用完整未截断 payload，trace/event 使用同一个 redaction 结果，messages 使用既有 truncation。重放记录必须指向原始 observation，不能被重复计算成新外部证据。event 必须保留 `tool_call/tool_result/tool_progress/tool_heartbeat`、`call_id`、`status` 和 `stream_reset` 语义。

## 实施顺序、风险与回归

1. 先写 K1/K2/K3 的失败定向测试，保留现有 prompt/event fixture。以现有代码运行时必须失败于目标断言而非环境问题。
2. 提取 provider 等待边界并修复 K1；紧接资源容量/迟到 fence/contextvars 回归。不要只复制 compaction 的 daemon-per-call 方案。
3. 提取 ToolInvoker/ToolBatchExecutor，修复 K2；完成观察与顺序提交分离。接入 cancellation 不强杀写工具，保留 serial barrier。
4. replay 改为 dispatch 决策并统一 executed-write epoch，修复 K3；再提取 replay/commit，可先用 facade 减少同时改动面积。
5. 收敛 RunLifecycle，统一 K4 终态与 finally；修正 K5 请求形态记账。最后由主任务进行全量离线回归。

主要风险是线程不能强杀导致资源并存、取消后未知写结果、事件时序变化、replay visibility lease 丢失、provider 工具元数据被压缩破坏。用 run/attempt fence 和容量上限显式约束，避免新增隐式全局共享 mutable state。回滚按独立提交还原实现，保持外部 schema 不变，出现副作用不确定时禁止自动重试写操作。

建议 targeted regression 集合：`test_tool_timeout.py`、`test_agent_loop_stall_watchdog.py`、`test_agent_loop_terminal_state.py`、`test_agent_loop_stream_retry.py`、`test_agent_loop_deterministic_cache.py`、`test_readonly_replay_after_microcompact.py`、`test_technical_indicator_compaction_replay.py`、`test_agent_loop_no_progress.py`、`test_context_budget.py`、DSML/thought-signature/no-assistant-prefill/provider replay tests，以及涉及工具证据的 grounding/release 测试。新增竞争测试使用受控 Event/barrier/clock，不依赖外部服务和长 sleep。最终回归由主任务负责，本审查未跑 full suite。

## 已完成验证

- 通过标准库 AST 提取并执行四个真实生产方法 body，使用全 fake dependencies，不导入应用、不读取环境凭据、不调用外部服务。输出：

```text
STREAM: cancelled+idle_deadline_elapsed_but_still_blocked= True
PARALLEL: fast_tool_completed_but_results_finalized= 0
PARALLEL: tools_launched_after_cancel= ['9']
REPLAY: prior_read_survives_failed_write= True
BATCH_REPLAY: effects=['write executed']; messages=[read old content, write new content written]
```

- 定向基线：`.venv/bin/python -m pytest agent/tests/test_agent_loop_stall_watchdog.py agent/tests/test_tool_timeout.py agent/tests/test_readonly_replay_after_microcompact.py -q --tb=short` → **23 passed in 1.16s**。基线通过说明现有测试没有覆盖上述交错，并非缺陷不存在。
- 最初使用系统 `python3`（3.9）的同一命令在 conftest collection 因 `int | None` 类型注解失败；项目要求 `>=3.11`，切换仓库 `.venv` Python 3.12.13 后通过。没有为此修改依赖或系统配置。
- 未执行 full suite、真实 provider/tool/broker 流程或任何外部写入。
