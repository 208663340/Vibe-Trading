# 后端 Task 1/2 设计交叉评审

日期：2026-10-13。审查对象仅为 `docs/architecture/2026-10-13-stability-design.md` 与 `docs/superpowers/plans/2026-10-13-architecture-stability-upgrade.md` 当前方案；不重新审查原生产代码，不实施、不新增测试、不重复全套验证。

**复核结论：可实施。B1–B5 均为 ADDRESSED，无剩余设计阻塞项。** 本次仅复核原五项意见对应的 spec/plan 补充，没有扩展审查或检查实现。修订后的 spec 是 Task 1/2/3 的完整约束，task brief 无需逐句重复；通过设计复核不等于实现或测试已经通过。

| 原意见 | 状态 | 本次复核依据 |
| --- | --- | --- |
| B1 条件取消 | ADDRESSED | spec `:96` 定义可选 JSON expected_attempt_id、同 gate 比较、mismatch no-active、legacy no-body 和 pending-send intent；plan Task 1/3 已同步协议与延迟请求回归。 |
| B2 执行与 finalization 所有权 | ADDRESSED | spec `:103` 定义 claim 状态机，实际 worker exit/确认未启动与 finalization 完成/记录可恢复失败两个释放条件；未启动取消由 supervisor completion path 持久化，旧 callback 检查 identity。 |
| B3 durable evidence 与恢复 | ADDRESSED | spec `:113`、`:121` 补齐 transcript/attempt/projection 分阶段决策、attempt_id 幂等、完整 JSONL terminal evidence、空文本失败/取消、partial line 非 commit，以及 process-interruption 保证范围。 |
| B4 关闭超时后 owner | ADDRESSED | spec `:108`、`:117` 给出 CloseSummary、OPEN/CLOSING/CLOSED、重复 close 幂等；超时保留 same-store owner，禁止旧 main worker 存活时恢复；plan Task 2 同步。释放仍遵守 B2 的 finalizer 条件。 |
| B5 SSE gap 与前端恢复 | ADDRESSED | spec `:129`、`:135` 固定 session.resync、reason/cursor、独立控制状态、overflow/expired cursor 后停止普通 delta、REST messages/attempt status、generation fence、reconciliation 后重连及失败未知状态。Task 2 必须先向 Task 3 交付 exact payload，已是明确的实施接口门槛。 |

以下保留原审查反例和修正建议作为实施验收依据；其中旧行号与“缺口”措辞描述的是首轮评审时的方案，不代表修订后仍未解决。

## B1 · P1 · ADDRESSED · 取消必须绑定目标 attempt，前端 guard 不能保护后端

位置：设计 `:83` 仅列 session cancel；计划 `:63` 要求“old cancel cannot stop a later attempt in A”，但 Task 1 `:37–42` 未定义 caller 指定目标 attempt 的契约。

反例：浏览器发送取消 A1 的 HTTP 请求，网络延迟；A1 已结束，用户在同一个 session 启动 A2；旧取消请求此时才到服务端。如果服务端只查当前 session owner，就会取消 A2。前端 operation token 可以忽略迟到响应，但无法撤销服务端对 A2 的取消。

具体修正：

- 在现有 cancel 请求中增加**可选** `expected_attempt_id`（请求体或 query 选择一种并写明）。supervisor 在同一 ownership gate 内比较身份并记录取消意图，不能先读再异步取消。
- expected ID 与当前 owner 不同或已终结时返回兼容的 no-active/no-op 成功结果；不取消新 owner，不把旧取消请求自动重定向到新 attempt。既有无参数调用保留“取消请求处理时的当前 attempt”语义，并记录该兼容限制。
- Task 3 获得 attempt ID 后始终发送条件取消。send 尚未返回 ID 时，只记录对该 pending send 的取消意图；拿到该次 send ACK 后按其 attempt ID 发 cancel，不能发送一个无目标取消去碰巧命中当前 attempt。
- 补充跨层测试：让取消**请求到达服务端**晚于 A2 admission，确认 A2 的 cancellation token 未变化；另测旧取消响应晚到时 UI 不变。两种竞态不可用同一个前端 mock 测试替代。

## B2 · P1 · ADDRESSED · ownership 的释放条件缺少 finalization 与未启动分支

位置：设计 `:14–16` 说 owner 保留到 worker exit；`:87–89` 分开观测 wrapper/worker。计划 `:39` 要求未开始就取消时持久化 cancelled 且可再次发送。

反例：外层 task 在第一次 coroutine 执行前被 cancel，函数体内的 try/finally 根本未安装；或 underlying worker 已返回，但 finalizer 尚在写 transcript/attempt，此时释放 claim 让 delete/new send 与 finalizer 竞争。只保留“worker future”不自动覆盖这两条路径。

具体修正：

- supervisor 注册 claim 必须早于 wrapper/worker 调度，claim 至少包含 `attempt_id`、generation、cancel reason、underlying future、terminal commit 状态；完成回调只能对自己身份的 claim 提交。
- 声明状态机，例如 `reserved → submitted → running → finalizing → released`。cancel 是正交的 requested flag；`reserved/submitted` 若成功取消真实 executor future、确认从未运行，由 supervisor 主动走同一个 finalizer，不能依赖未启动 coroutine 的 finally。
- admission lease 的释放条件是“真实执行者已经结束或确认从未启动，且 finalization 已结束或进入明确的可恢复失败状态”。只有 wrapper.done/cancelled 或 worker.done 都不充分。
- 明确返回 503/409 前的持久化边界：没有成功 admission 时不写 user message/attempt；若 admission 后初始化失败，则有一个可恢复的失败记录并清理 claim，不能留下无 owner 的 running attempt。
- 用 barrier 覆盖：cancel 在调度前/调度后未开始/刚开始三个边界，worker.exit 但 finalizer 被阻塞，delete 与 send 仍拒绝；旧 finalizer 完成不得释放新 claim。

## B3 · P1 · ADDRESSED · “transcript-first”尚未定义可靠的恢复判据

位置：设计 `:17–19`、`:56–58`；计划 `:41–42` 约定 transcript-first/idempotent 与 fault injection，却没有说明 durable commit 点、幂等键和中途崩溃恢复规则。

反例：assistant 回复已写入，attempt 仍为 running，进程退出；重启按 running→interrupted 处理会与已完成回复冲突。或者 cancelled attempt 没有回复，单凭“是否有 assistant 文本”不能判断其已终结。又或者 terminal 已持久化但 SSE publish 失败，重连只有 active replay，前端收不到 terminal。

具体修正：文档增加一张 finalization 决策表，至少明确以下内容。

| 崩溃点 / 恢复证据 | 必须采取的行为 |
| --- | --- |
| 只有 admitted/running attempt，没有完整 terminal evidence | 恢复为 interrupted/原有兼容状态；不伪造 successful reply，不重放用户写操作。 |
| 完整 terminal evidence 已落盘，但 attempt/checkpoint 未更新 | 按 evidence 的同一 outcome/attempt_id/message_id 完成后续阶段；不能改判 failed/interrupted，也不能追加第二份回复。 |
| attempt/checkpoint 已收敛，FTS/notify/publish 失败 | terminal outcome 保持原值；投影可重建/重试，通知失败不可进入“执行失败”通路。 |
| JSONL 最后记录只写了半行、JSON replace 失败 | 不把不完整行当 commit；明确修复/隔离或跳过规则，确保后续 append 不拼接成不可解析记录。 |
| cancelled/failed 且没有自然语言回复 | 必须仍有可识别的 terminal evidence，能被恢复与 REST 观测；不能只靠内存状态或 SSE。 |

- 可使用已有 transcript 元数据表达完整 terminal evidence，无需新数据库；明确包括 attempt_id、稳定 message_id 或 terminal key、outcome、时间与所需 metadata。若现有记录不能表达无回复终态，再选最小的原子 terminal 记录方案。
- 声明保证范围是进程崩溃还是断电持久性；若只保证 process crash，明说。不要把 rename 原子性等同于多文件事务或 fsync 后的断电保证。
- finalizer/recovery 共用相同幂等判据；恢复只在所有旧 writer 已退出后运行，与 B4 同一个 lifecycle gate 联动。
- Task 1 需交付 exact terminal evidence 和恢复排序，Task 2 只在 commit 后 publish terminal；Task 3 需能通过权威读接口发现无回复 cancelled/failed 的终态。

## B4 · P1 · ADDRESSED · aclose 超时后的 runtime 资源去向未定义

位置：设计 `:89–91` “honest close result”及 shutdown order；计划 `:49–53` “restart-safe close/reset”“next app lifespan never reuses an unclosed writer”。这些目标还缺少可以实现和测试的返回协议。

反例：aclose 超时返回 incomplete，应用仍关闭 EventBus、store 或 loop，然后下一 lifespan 创建新 service 对同一目录做 interrupted recovery；旧 thread 随后返回并 finalizes。既可能写坏新状态，也可能向已关闭 loop 调度；仅日志记录 incomplete 无法阻止。

具体修正：

- 固定 `CloseResult` 的内部结构，至少 `complete`、仍存活 attempt IDs/worker count、finalizing count；不要混入敏感消息内容。超时不能清空 registry 或声称 executor 已退出。
- lifecycle 状态明确为 `OPEN → CLOSING → CLOSED`，超时停留 `CLOSING`。重复 close 幂等并等待同一关闭过程，不能新建第二个 finalizer。
- 选择并固定策略：推荐超时后拒绝在同一 runtime/storage identity 上开启新 lifespan，直到旧 workers/finalizers 真实退出；保持必要的 finalization 资源，或者将阻塞 executor 整体视为尚未关闭。不能通过 reset 绕过 ownership gate。
- 断开外部 SSE subscriber 可以早于旧 worker exit，但 late publish 必须成为安全 no-op 或写入仍有效的 replay 层，不能调用已关闭 loop；finalizer 所需 store 不能先释放。
- startup partial failure 只反向释放已经成功创建的资源；若 lazy 入口在 lifespan 外创建 runtime，也必须注册到同一个 owner，shutdown 不能漏掉它。
- 增加 barrier 测试：超时 → 第二 startup 被拒绝 → 旧 worker exit/finalization 完成 → close 完成 → 才允许新 startup/recovery。不能只验证“不重用同一个 Python 对象”。

## B5 · P1 · ADDRESSED · SSE 溢出恢复协议仍是跨任务待定项

位置：设计 `:20–21`、`:85`；计划 Task 2 `:53–55` 允许临时增加 resync control，Task 3 `:61` 仅说消费该控制，没有定义事件名称、cursor、重复与恢复完毕的条件。

反例：队列满时丢弃若干 delta 以容纳 terminal，客户端虽然收到 completion，但丢失中间工具/goal/swarm状态；若客户端最后 cursor 已越过被丢事件，正常 reconnect 不会补回。另一个反例是 EventSource open 被当作恢复完成，而真正的 gap 和持久化 terminal 仍未读取。

具体修正：Task 2 开始前选择一个明确策略并交付给 Task 3。推荐：

- 每个 subscriber 有不依赖数据队列容量的 closed/gap 控制状态；慢 subscriber 不阻塞 publish/其他 subscriber。溢出时标记 gap 并终止受损流，绝不继续把“有缺口”的 delta 当完整流。
- 若发 additive `resync_required` 控制，固定最小字段与含义，例如 `{session_id, reason: "overflow" | "cursor_expired"}`；它是传输控制，不是 domain completion，不能复用 done/attempt.completed。若不加新事件，则同样要定义 disconnect 后恢复判据。
- replay buffer 的保留窗口、cursor 是否过期、snapshot+subscribe 的原子边界明确；cursor 超出保留窗口时不得静默从“最近若干事件”继续。
- Task 3 收到 gap 后失效当前 transient generation、刷新权威历史和终态/活动状态，再按明确 replay 起点重连；不能盲目拼接旧 streamingText。HTTP open/收到 heartbeat 不证明同步完成，UI 的 reconnect 状态必须等 reconciliation 成功。
- REST 必须提供足够信息区分“有完整回复已完成”“无回复已取消/失败”“仍 running”；既有 DTO 若已经足够，Task 1 列明字段；若不够，则增加兼容字段或只读 endpoint，不能要求前端通过缺少回复猜测。
- EOF 自身可能出现在 terminal commit 前后，因此恢复读失败时继续显示可恢复的未知/断开状态，禁止伪造成功或自动 POST 重发。
- 测试：满队列+close；满队列+terminal；重放窗口已轮转；REST 读与新事件交错；worker thread publish+loop shutdown；慢读者不影响快读者；terminal commit 后 publish 抛错仍可由 REST 收敛。除传输级有界性，需至少一项前后端 fixture 协议测试。

## 接口交付清单与可实施条件

Task 1 → Task 2：supervisor claim/worker/finalizer 释放条件；conditional cancel；terminal evidence 的幂等键、commit 点和恢复排序；`aclose` 的类型和超时后资源所有者。

Task 1/2 → Task 3：cancel 目标参数及 no-op 语义；无回复终态/active attempt 的 REST 观测字段；SSE gap control、cursor 过期语义、重连恢复顺序；send/closing/busy 的既有 success/error 格式。除明确新增的可选字段或控制事件，保留原路径、success DTO、detail 字符串、既有事件 payload 与 facade。

Task 3 → Task 1/2：pending send 的取消必须等 identity 后条件执行；导航只取消视图读请求、不会暗中取消服务端工作；恢复只读取/订阅，不重发 mutation；“忽略旧响应”不等于“服务器未执行该请求”。

以上五项契约已补入 spec，相关 conditional cancel/resync 接口已同步 plan，方案可实施。后续按原计划用 barrier/fault-injection 与跨层恢复测试验证，不在本轮追加设计范围；无需替换框架、增加消息中间件、数据库或通用工作流引擎。
