# 内核综合设计评审

日期：2026-10-13。评审对象：`docs/architecture/2026-10-13-stability-design.md` 与 `docs/superpowers/plans/2026-10-13-architecture-stability-upgrade.md` 的 Task 4/5/6，以及 Task 1/2 的 worker 所有权依赖。只读评审，未修改设计、计划或生产代码，未执行测试、远端或实际外部工具。

## 复核判定：可实施，R1–R6 全部 ADDRESSED

2026-10-13 scoped 复核仅检查原 R1–R6，没有扩展范围。修订后的 spec 已明确跨实例资源所有权、真实退出配额、单 lease provider 路径、终态 handoff、工具提交/写屏障和请求计量公式。原设计阻塞项已解决，可按 Task 1–6 既定顺序实施；实现与测试完成情况仍须经过各 task 的 red/green 和独立评审。

以下表格为当前结论。后续 R1–R6 正文保留首轮评审依据和实施验收建议，不代表仍未解决的设计缺口。

| 原项 | 状态 | 修订证据及判断 |
| --- | --- | --- |
| R1 | **ADDRESSED** | spec `:103-119` 明确 main writer/finalizer/abandoned readers 的 CloseSummary、claim 释放与同 store recovery；`:177-184,191-192` 定义 provider 8/read tool 32 的 process-scoped 预算、真实退出归还、跨 lifespan 不重置和 client lease cleanup。Task 1/2/4/5 接口与该约定一致。 |
| R2 | **ADDRESSED** | spec `:186-192` 明确单 logical lease、fallback 同 producer或退出后再执行、compaction不嵌套、独立请求快照、bounded queue、可撤销 put、独立 terminal slot、arrival idle 与总时限区分。Task 4 保留取消/metadata/context 和容量回归要求。 |
| R3 | **ADDRESSED** | spec `:194-203` 明确只有主 run flow提交终态、挂起 write不伪报结束、stall优先至terminal claim、启动前取消由supervisor完成、启动后使用唯一run outcome；`:103-127` 明确finalizer持claim、持久化阶段决策与恢复证据。Task 6已同步无返回write语义。 |
| R4 | **ADDRESSED** | spec `:177-184` 和 Task 5 Step 3 明确 readonly-tool 32 容量跨AgentLoop/lifespan共享，permit覆盖实际worker退出；迟到producer不再发事件或提交证据，活跃写工具仍归main owner。每batch准入上限与真实worker总预算不再混同。 |
| R5 | **ADDRESSED** | spec `:205-210` 明确只提前登记accepted true progress、所有执行/合成/重放结果统一ordered commit、worker不写证据，以及registry.execute前真实write准入立即失效；Task 5保留dispatch-time replay、原参数identity、normalization和existing authorization/recovery契约。 |
| R6 | **ADDRESSED** | spec `:212-217` 明确发送前shape快照、message+tools锚点增量公式、configured model变更清anchor、无效usage不覆盖、response alias不无谓重置、summary不校准主请求；Task 6 Step 2继续要求静态预算越界的明确失败测试。 |

## R1 · ADDRESSED · 原 P1：生产者预算必须跨 ChatLLM 实例及旧 lifespan 生效

**引用**：计划 Task 1 `:37`，Task 2 `:49-53`，Task 4 `:73-77`，Task 5 `:85-89`；设计 `:75-91,105-111`。

计划正确要求 permit 在 producer 实际退出时才释放，但没有确定预算的所有者、实例范围及 shutdown 返回内容。每个 ChatLLM 新建预算、每个请求新建 runner、或新 app lifespan 重置计数，均可绕过有限容量。主 `AgentLoop.run` 返回后 abandoned provider/readonly worker 可能仍存活，SessionService 只追踪主 worker future 并不能证明所有资源已退出。

**必须确定的最小契约**：

- provider 与 readonly tool 使用各自有界资源池；生产者准入容量跨会话共享。可选进程级 pool 持续存活，或者 RuntimeServices 注入 owner 且旧 pool 未 drain 时禁止创建替代容量；二选一写明。测试可注入私有 pool，不能修改生产全局容量来互相干扰。
- `close(timeout)` 的内部结果至少区分 `pending_run_writers` 与 `abandoned_readers/producers`。前者阻止释放相同 session/root 的写所有权与 recovery；后者已撤销 callback/commit 能力，可允许主 attempt 完结，但继续占用原 pool 配额并由 owner 保留 handle。
- 活跃写工具始终在 run writer 的所有权树内；不能因为 wrapper cancelled、watchdog requested stop 或 readonly abandon 就释放它。Task 1 的 claim 包含 registry build 与实际 run；底层写工作真实退出前保持 busy/delete 409。
- provider clients 的 close 与 runner lease 绑定。既有 `ChatLLM.close/aclose` 会遍历 owned clients（`chat.py:355-433`）；定义是等活跃 lease 退出再 close，还是允许 adapter 支持的显式 transport cancellation。关闭 client 不能被报告为 producer 已退出，不能误关共享 client。
- RuntimeServices shutdown 先停止 admission，再请求 run cancellation，收集上述 close summary；旧 owner 未完全退出时不能恢复同根目录中的中断写 attempt。CLI/MCP 没有 API lifespan，也要使用有效的默认 pool owner。

**新增跨任务验收**：run caller 已返回但 fake producer 不退出，连续创建多个 ChatLLM/会话、新 lifespan，活跃 producer 总数仍不超过原上限；shutdown 超时后 pending writer 阻止新 recovery/send/delete，释放 fake 后才解除；已 fenced reader 不再写 checkpoint/tool trail/event。

## R2 · ADDRESSED · 原 P1：runner 需要单次 lease、可终止队列和独立请求快照

**引用**：计划 Task 4 `:73-78`；Task 6 `:97-101`。

“queue-only producer”和“同一个 resource budget”还不够规定执行路径。若 `stream_chat` 占一个 permit，空流 fallback 再通过同一 runner 调 `chat`，容量为 1 时可能自锁；compaction runner 包住已受 runner 管理的 `ChatLLM.chat` 也一样。若队列无界，快速细碎流可消耗无限内存；若 `put` 永久阻塞于满队列，取消 caller 后 producer 无法退出，配额永远不能归还。若 producer 保留可变 messages 引用，caller abandon 后 compaction/后续 turn 的原地修改可能改变仍在读取的请求。

**建议固定 API 语义**：

```python
pool.try_start(owner_id, request_id, producer, copied_context) -> CallHandle
handle.iter_events(cancel_event, idle_timeout_s, total_timeout_s=None)
handle.abandon(reason)  # revokes delivery; never releases permit itself
pool.close(timeout_s) -> PoolCloseResult
```

- 一个 logical provider attempt 只取得一次 lease；同一 producer 内执行 stream→空流 invoke，或者先让前 producer 真实退出归还 lease 再开始 fallback。明确禁止隐式嵌套 acquire。compaction 仅一层 runner，不同时 wrapper 和 chat 内再占容量。
- `CallHandle` 有 bounded queue、request/attempt generation、producer-exited event。producer 的 `put` 必须短间隔检查 revoked，取消后丢弃排队/新消息并退出；completion/error 不能因 queue full 丢失，可用独立 terminal slot/event。
- permit 仅在 producer 最外层 finally 归还，覆盖异常、迭代器清理及 thread-start 失败；如果清理自身卡住也继续占 permit。
- producer 接收独立 request payload 快照，保留 provider metadata；caller 后续修改 messages 不影响已准入请求。`contextvars.copy_context()` 在准入线程采集，每个 producer 独立 copy。
- idle deadline 从 producer 的有效分片到达时间计算，不能让 caller 回调/队列 drain 延迟冒充 provider idle。total deadline 是另一概念：stream 保持现有 idle 语义，compaction 保持原 wall-clock 上限。取消和 timeout=0 的语义明确分开。
- caller 负责文本/推理回调及最终 response 解析。abandoned request 的尾分片、工具调用和 summary 不能触发这些回调；新 retry 用新 request_id。
- ChatLLM 现有公开签名保持不变；内层 private runner entry 可接收 RunControl，或通过明确的 context-local cancellation。不要为了 signature compatibility 使 compaction 无法接收 cancel。

**新增验收**：pool capacity=1 的空流 fallback 和 compaction 均完成；caller 回调暂停使 queue 满，取消后 producer 可以退出/归还 permit；cancel 后改写原 messages，fake provider 仍只见旧快照；terminal 在 queue 满时仍可消费；迟到 producer 永远不能完成新 request。

## R3 · ADDRESSED · 原 P1：Task 6 的终态规则必须与 Task 1 attempt 持久化对齐

**引用**：计划 Task 1 `:39-42`、Task 6 `:97-101`；设计验收 1/2/9。

Task 6 明定 watchdog 请求 failure、main flow 持久化一次，这是可实施的单 owner 方案。但 main flow 若卡在无法安全中断的 write，就不可能保证立即持久化终态或“run 已结束”。原 watchdog 会主动写 failed end；迁移必须明确这个可见行为变化。另 Task 1 wrapper 被取消时可能先把 attempt 写 cancelled，Task 6 同期产生 stall failure，两个各自“一次”的持久化仍可能冲突。

**建议终态协议**：

1. 区分 `request_stop`、`terminal_claimed`、`terminal_persisted`、`writer_exited`；`done` 仅指 run facade 退出/清理完成，不能代表所有 detached readers 退出。
2. RunLifecycle 以锁/CAS 一次性决定 outcome，持有 `run_id + generation + attempt identity`。同一临界区检查 stop reason 并 claim terminal；禁止先计算 success 再解锁提交。
3. 明确定义 stop 冲突优先级，建议保留既有已成立 stall failure 优先于 user cancel 的规则；terminal claim 之后到达的 stop 不改已提交结果。采用“最早停止原因”亦可，但必须一致测试，不能 Task 1/6 各自推导。
4. 未启动任何 run worker 的立即取消由 SessionService finalize cancelled。已启动 run 的用户 cancel 只请求停止；SessionService 持有并观察 shielded/原始 worker future，使用该 run 的唯一结果 finalize attempt，不凭 wrapper CancelledError重新合成另一个终态。服务 shutdown 的 interruption 与 user cancel 不混用。
5. 写工具无限挂起时允许 `stalled`/停止已请求的可见事件，但不宣称 writer 已结束、不释放 claim、不自动重跑。若需要 watchdog 先持久化失败，也必须通过同一个 terminal owner 并明确 failure-terminal 与 writer-live 是两个状态；不能保留两条直接写 end 的路径。
6. state atomic replace 只保证单文件完整性，无法把 trace/state/attempt/checkpoint组成事务。明确顺序及恢复：先保留唯一 terminal decision 的可恢复记录，再做其余投影；持久化异常保持不确定/失败，不反向重写已持久化 success。一次逻辑终态不等于每个失败 I/O 都已落盘。

**新增验收**：user cancel/stall/normal completion 三方 barrier 竞争后 result/state/attempt/terminal SSE 一致；wrapper shutdown cancel 不释放 active writer；trace/state 写失败后 done 置位且 SessionService有可恢复失败信息；永不返回 write 的 watchdog 不宣称 worker 已退出。

## R4 · ADDRESSED · 原 P1：有限工具准入必须约束实际 worker，而不只是返回给 caller 的等待槽

**引用**：计划 Task 5 `:85-90`；Task 4 `:75`；Task 1 `:37-39`。

Task 5 提到 bounded capacity/late fencing，但没有 Task 4 那样明确“真实退出才释放”。只读工具 timeout 后如果释放 invoker slot，下轮仍可再启动更多 hung workers，最多 8 个 in-flight caller 并不等于最多 8 个实际执行线程。线程池 coordinator 再套 daemon worker还可能产生两套不同上限。

**必须补入**：actual readonly invocation lease 与 provider lease 一样直到底层 execute 退出才释放；caller 的 ordered result slot 可提前填 timeout/cancelled，真实 worker仍占资源。全局 reader pool与每 batch并发上限分开。已弃用 worker不能更新 true progress，也不能刷新 later run watchdog；所有更新经 generation 检查。处于 saturation 的新调用返回一致 error envelope，不能无限排队，也不能开启替代线程绕过。

**新增验收**：多个 batch/多个 run 连续 timeout，实际 registry.execute 并发总量不超过池上限；cancel 后返回的 readonly late result不刷新下一 run progress、不会写 evidence/cache/trace；当前写工具仍真实等待，不走上述 detached-reader路径。

## R5 · ADDRESSED · Task 5 应写明 observation 与 commit 的接口及 replay 屏障位置

当前“及时 observe、ordered commit、executed write invalidates”方向足够，建议把下面约定直接加进 worker dispatch，避免实现各自理解：

```python
on_observed(result_index, generation, observed_at)  # coordinator; true completion only
commit_next(result)  # run thread; original index order; exactly once
replay.decide(prepared_call, mutation_epoch)  # after preceding write barrier
replay.note_write_started(call_id)  # immediately at real execution admission
```

- 最小兼容选择：仅提前登记 progress，保持 `tool_result`/trace/grounding/messages 的既有按序提交；不需要先给 UI 一条结果再重发。`ToolExecutionResult` 是不可变且含 executed/outcome 的内部对象。
- executed 的线性化点是取消/授权检查通过后、即将调用 registry.execute 的时刻。write epoch 在这里保守失效，而非 commit 成功之后；即使 execute抛异常或后续 trace/grounding 写失败也已构成 barrier。拒绝/never-started的合成结果保持 executed=False。
- ordered commit覆盖所有输入 calls：执行、cache/replay、compact、授权阻断、取消合成结果。否则把 replay移到 dispatch仍可能保留旧的“blocked result先 append，再 flush前序工具”乱序路径。
- 本轮全部 authorization仍可使用既有 batch snapshot，不把 replay调整误变为放宽身份授权。缓存key使用原始参数；真实execute/grounding使用normalized参数；replay不伪装为新的外部 observation。
- 针对 cancellation 尚未启动的 call，可以返回兼容 `status:error` envelope带内部取消reason；公开 JSON若新增 error_code仅为 additive。已发tool_call必须有可解释的收束，messages/tool_pairs不能孤立。
- true progress只登记接受的真实完成，不把每个 heartbeat/cache hit或丢弃的迟到结果记成外部进展。现有 no-progress/replay context-restored计算不应被统一“全部完成都进展”覆盖。

## R6 · ADDRESSED · Task 6 的请求 shape 应明确公式和模型变更规则

目前计划提供充分场景，接口还需把“实际”定义为发送快照而非返回时的 mutable messages：

```python
RequestShape(request_id, configured_model, messages_estimate, tools_estimate)
observe(actual_input_tokens, sent_shape, response_model)
estimate(next_shape)
```

同一已校准模型形态下：`estimated_next = anchor_actual + (next_messages - anchor_messages) + (next_tools - anchor_tools)`；不能只把工具参数加到旧 API而仍忽略锚点工具开销。模型变更不沿用上一模型 token锚点，回退估值直到新 usage；response_model记录与configured alias的对照，避免 alias每次不同字符串导致无意义重置。Anthropic cache-message转换等要有一致发送形态估值约定。无usage/不合法usage不覆盖有效锚点，summary 请求不覆盖主conversation锚点。静态 prompt+schema超过可用 window时返回清晰错误，不能通过8K conversation floor抬高上限。

**补测试**：发送请求后原messages被修改，observe仍按sent_shape；tools→text-only→tools的精确增量；模型变更失去旧校准；compaction summary usage不能冒充主请求usage；provider usage含缓存token时沿用现有统一定义。

## Task 1/2 交接给 Task 4/5/6 的最小清单

| 提供方 | 必须交接 | 后续依赖 |
| --- | --- | --- |
| Task 1 | run worker实际future、attempt generation、cooperative cancel入口、未启动与已启动取消区分、单次durable finalize | Task 6回传唯一outcome；Task 4/5不能自行解除session claim |
| Task 2 | pool owner作用域、shutdown admission顺序、close summary、旧writer阻止recovery的规则 | Task 4/5复用有界资源、旧lifespan不重置容量 |
| Task 4 | CallHandle撤销/退出区别、shared budget、cancel/deadline异常语义、metadata/context传播 | Task 6协调结果；Task 5借鉴reader lease但不共享写执行语义 |
| Task 5 | observation callback、ordered commit、executed线性化点、write epoch和late fence | Task 6 progress/terminal不能被旧worker污染 |
| Task 6 | RunControl最小协议和result handoff | 可先在Task 4/5用小Protocol适配，最后接入具体RunLifecycle，避免临时全局cancel |

建议 controller 在 Task 1/2 完成报告中固化以上语义；Task 4/5可先消费最小 `cancel_event + generation + note_progress` 协议，Task 6替换具体实现。这样不需要改任务顺序或并行编辑loop.py。

## 可直接保持的部分

- 不迁移框架、不新增依赖、公用 facade/re-export/事件schema保留。
- 实现串行推进、每task先red/green、独立review及signed本地提交；full suite只在集成门禁执行。
- provider metadata、DSML、thought-signature、no-prefill、grounding和replay已有测试作为兼容性覆盖。
- 原始工具结果进入grounding后再截断/脱敏，缓存授权先行，写工具不被强杀也不做不确定重试。
- RunStateStore原子替换、finally清理、可控Event/barrier避免长sleep，都是正确实施方向。

本轮仅 scoped 设计复核，不把待新增交错测试视作已执行证据。当前结论：**R1–R6 全部 ADDRESSED，设计可实施；无原评审项仍处于 NOT ADDRESSED。**
