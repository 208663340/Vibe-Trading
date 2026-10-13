# 后端架构与稳定性审查

日期：2026-10-13。基线：`c25fea2bc261a2d0ed971c989d194de9f04ec963`，分支：`codex/architecture-stability-upgrade`。

审查依据：`AGENT_CONTRIBUTOR_GUIDE.md` 与 `backend-feature-design` 技能。此次只读生产代码，仅新增本文；未访问远端、真实账户或凭据，未启动服务器，未执行真实 Agent、Provider、MCP 或交易操作。下列代码行号对应基线。

## 结论与优先级

现有文件存储、服务层和模块化路由足以承载本轮修复，不需要引入新数据库或全面重写。优先修复任务所有权和终态一致性，再收敛生命周期与依赖注入。审查通过真实 SessionService/SessionStore/EventBus 加隔离的假执行器，复现了五个缺陷。

| 编号 | 优先级 | 问题 | 证据 |
|---|---|---|---|
| B1 | P1 | Task 首次运行前取消，永久泄漏 session claim | 离线复现 |
| B2 | P1 | 运行中删除会话，后台重新创建已删目录 | 离线复现 |
| B3 | P1 | 搜索索引故障把已完成 attempt 改写为 failed | 离线故障注入 |
| B4 | P2 | 删除会话后搜索索引仍返回其消息 | 临时 SQLite 复现 |
| B5 | P2 | SSE 队列满时 clear sentinel 丢失，连接无法终止 | 离线复现 |
| B6 | P1 | 应用关闭未管理会话 worker 生命周期 | 代码路径确认；真实线程退出场景未执行 |

## B1：首次执行前的取消无法释放会话

位置：`agent/src/session/service.py:298` 创建 Task，`:301` 保存句柄，`:329-332` 直接 cancel；释放只在 `_run_attempt` 的 `:464-468` finally 中。

触发：`await svc.send_message(...)` 返回后，在事件循环让新建 Task 第一次执行之前调用 `svc.cancel_current(sid)`。服务方法自身没有让出控制权，因此这在直接调用者、批处理和同一事件循环的紧邻调度中可发生。Task 在 coroutine 进入 try/finally 之前取消，finally 根本不会运行。

实测：取消返回 true，Task done 为 true，`sid in svc._inflight` 仍为 true；attempt 保持 `pending`；下一次发送抛 `SessionBusyError`。任务句柄和取消集合也未清理。已有 `test_cancel_before_the_agent_loop_exists_releases_the_claim` 在取消前睡眠 20ms，只覆盖 coroutine 已经开始的情形。

建议：用独立于 coroutine body 的任务所有权记录维护 claim；注册完成回调作为兜底，并通过 attempt/task identity 保证只清理当前任务。尚未开始的用户取消也必须持久化 terminal cancelled、发送一个终态通知，不能只清除 claim。完成回调必须读取异常，避免无主 Task 异常。

新增回归：发送后不 sleep，立即取消；await 该 Task；断言 cancelled、无活跃句柄/claim、第二次发送成功；覆盖连续取消及第二次任务不被旧回调清理。

## B2：运行中删除会话造成残留数据重新出现

位置：`agent/src/session/service.py:239-242` 清 EventBus 并直接删除目录；`agent/src/api/sessions_routes.py:656-667` 原样暴露该操作；`agent/src/session/store.py:155-166` 和 `:336-350` 自动创建父目录。

触发：执行器等待期间 DELETE 会话，随后执行器返回成功或继续发 checkpoint。删除没有检查 claim，也没有停止或等待活跃执行器。后续 append_message/update_attempt/save_partial_response 会重新创建目录。

实测：删除返回 true、目录短暂消失；放行假执行器后目录重新出现，含一条 assistant 消息，`session.json` 却不存在。REST 列表不能呈现这些孤儿数据。真实执行器还会继续消耗资源并调用已获许可的工具；这里未执行真实工具。

建议：第一阶段采用明确的 busy conflict：持有执行 claim 时删除返回 409，客户端先取消并等待终态，再删除。删除检查与新发送的 claim 获取必须共享同一个 session gate，防止 check-then-delete 竞争。保留当前成功响应。若后续支持“取消并删除”，必须 await worker 实际退出，再移除数据，而不能把 Task.cancel 当作线程已结束。`rmtree(ignore_errors=True)` 也应取消吞错行为；部分删除失败不得报告成功。

新增回归：运行中删除返回冲突且数据完整；完成后删除成功且不会复活；并发 send/delete 只允许一个取得操作权；模拟删除 I/O 失败，不能成功应答或提前 clear stream。

## B3：衍生索引故障篡改已经持久化的成功终态

位置：`agent/src/session/service.py:422-428` 依次提交回复、attempt、清除 checkpoint、更新索引；`:459-463` 将整段中的任何异常都当作执行失败，覆盖 attempt 并发送失败事件。

触发：模型执行成功且回复与 completed attempt 已落盘，随后 `index_message` 因 SQLite 锁、磁盘或连接异常抛错。索引实现 `agent/src/session/search.py:196-220` 没有吞掉写入异常。

实测：只对 assistant 索引操作注入 RuntimeError。持久化回复 `metadata.status=completed`，持久化 attempt 却变为 `failed`，最后事件为 `attempt.failed`。重启恢复只扫描 pending/running，无法修复这种终态冲突。用户可能重试已成功操作。

建议：分离三个阶段：执行结果判定 → 幂等终态提交 → 衍生索引/通知。终态提交成功后，索引错误只记录可重建投影的故障，不能反转业务结果。对于回复成功、attempt 写入失败的中间状态，保留 checkpoint 和回复证据，交由既有重启恢复补全，不要把所有 I/O 异常统一改写为执行失败。异常路径也需持久化可供重新加载的 assistant 终态记录。

新增回归：在 append reply、update attempt、delete checkpoint、index、emit 各阶段分别注入故障；断言不会出现完成回复与 failed attempt 冲突；已提交终态不可被索引/通知错误降级；重启恢复最终幂等且无重复回复。

## B4：删除不清理可搜索消息

位置：`agent/src/session/service.py:239-242` 完全不更新 `_search_index`；`agent/src/session/search.py:240-294` 搜索只查询索引，不与文件存储核对会话存在性。

触发：创建会话并索引一条消息，删除会话，再搜索消息中的唯一词。

实测：在临时目录创建真实 SessionSearchIndex，删除前匹配数为 1，删除后仍为 1；结果仍包含已删除消息片段。

建议：为已有 SQLite 索引提供按 session 删除的事务方法，同时删除 messages 与 sessions（保留 FTS trigger 清理）；由 SessionService 负责调用。定义索引删除失败后的重建策略，并在搜索结果输出前排除主存储已不存在的会话，避免衍生副本继续暴露已删历史。索引是可重建投影，不应变成会话删除语义的第二套真相。

新增回归：删除后搜索为空；删除重试幂等；模拟索引暂时故障后搜索仍不返回已删会话，恢复重建后没有僵尸记录。

## B5：满队列会吞掉关闭通知

位置：`agent/src/session/events.py:257` 队列上限 200；`:294-322` clear 先从订阅表移除队列，再用可能失败的 put_nowait/_safe_put 填入 sentinel；`:158-169` 满队列丢弃任何类型事件。

触发：SSE 消费者暂停，发布 200 条事件填满队列，再 clear。真实场景可由网络慢、客户端挂起和流式事件突发触发。

实测：注册一个 subscriber，消费第一条后暂停，填入 200 条消息并 clear；排空 200 条后下一条为 `heartbeat`，不是 `session_cleared` 或 StopAsyncIteration，且 subscriber 已从注册表移除。连接此后只会持续心跳直至客户端主动断开。

建议：关闭信号采用不能被普通数据挤掉的控制路径，或者在 clear 时移除足够的旧数据保证 sentinel 入队。终态事件也不能静默丢弃；普通 delta 溢出应显式提示需要重新同步，结合 REST 权威状态恢复。注册时记录 subscriber 所属 loop，避免单一全局 loop 假设扩散。

新增回归：满队列 clear 必须有界结束；满队列 terminal 仍可恢复；跨线程 publish/clear 在所属 loop 执行；已有 replay snapshot 与订阅注册原子性继续保持。

## B6：会话执行器没有与应用一致的关闭边界

位置：`agent/api_server.py:146-153` lifespan 只调用 `_stop_scheduled_research_on_shutdown`；后者 `agent/src/api/scheduled_routes.py:184-203` 停止 live/channels/scheduler，没有 SessionService；`agent/src/session/service.py:33` 模块全局 ThreadPoolExecutor；`:531-541` 和 `:555-573` 把 registry/agent 工作提交线程池。

触发：有会话任务运行或 registry 构建中关闭 app，或在同一进程启动下一次 app lifespan。Service 没有 close/shutdown 方法，executor 不归 app 管理。取消 asyncio 包装 Task 不会终止已经开始的 Python 线程，agent.cancel 也未在这个路径调用。旧线程可能继续回调 EventBus/checkpoint，而 Task 的 finally 已释放 claim。

风险：退出时间不可控、关闭后继续写入、服务重建误把仍有 writer 的 attempt 当作旧进程遗留，甚至同一会话出现新旧 worker。该项为代码确认，未模拟真实 Provider 阻塞，也不主张 Python 能安全强杀任意线程。

建议：应用拥有 RuntimeServices 容器及会话执行器。shutdown 先停止 scheduler/channel 入站，再停止接单，协作取消 active agent，取消未启动工作并在期限内等待在途工作，最后关闭资源和事件流。超过期限的在途 worker 必须继续持有 session 所有权并保持可恢复状态，不能假装终止；Provider/工具自身需要可控超时。单独保存底层 concurrent future，观察它真正结束的时刻。重启恢复只能在确认本服务拥有存储且无旧 writer 后执行。

## 建议的职责划分

1. **API assembler / RuntimeServices**：`api_server.py` 只创建 app、注册 middleware/router 和 lifespan；`src/api/state.py` 负责构造容器与 FastAPI dependency provider。服务挂在 `app.state`，首次创建绑定 running loop，关闭顺序明确。保留既有 api_server re-export/monkeypatch 兼容桥，但将其限制在边界，不让领域服务读取 `sys.modules`。
2. **会话应用服务**：SessionService 管理 create/send/cancel/delete、session gate、终态状态机。提取一个小型 attempt task supervisor 管理 admission、任务/worker 句柄和关闭；不改 Agent 推理策略。
3. **数据访问**：继续使用 SessionStore 的 JSON/JSONL、原子 replace 和已有 SQLite FTS。集中提供 `commit_terminal` 与按 session 的删除操作；索引作为投影有明确故障降级和重建路径。文件 atomic replace 不能等同于多文件事务，使用已存在的 transcript-first 恢复顺序，并补齐故障注入覆盖。
4. **执行适配器**：注入 async attempt runner，生产适配器封装 registry/LLM/AgentLoop；测试直接注入 stub，不再必须改私有方法。运行资源有明确 owner，ChatLLM/registry 是否由 AgentLoop 关闭需和 Agent 审查结论对齐后决定，避免重复关闭。
5. **事件与 checkpoint**：EventBus 负责有界数据流和可靠控制流；ResponseCheckpoint 负责部分输出快照。任务所有权失效/删除后禁止旧 attempt 写入；只有终态提交成功才清除 checkpoint。

不在本轮引入队列服务、数据库迁移、微服务拆分或多 worker 分布式锁。当前 claim 只在单进程有效；若部署支持多个 API worker，共享存储所有权与恢复策略必须另行设计，不能宣称本轮修改已支持。

### 可实施的 ownership 接口边界

建议新增 `agent/src/session/execution.py`，集中放 `AttemptSupervisor` 与 `AttemptHandle`，不让 routes 直接操作 task 字典。Handle 至少记录 session_id、attempt_id、唯一 generation、包装 task、实际 worker future、cancel_requested、是否已进入 coroutine、是否已提交 terminal。Supervisor 的状态为 accepting/closing/closed；handle 的状态为 reserved/starting/running/finalizing/finished。状态转移通过同一个锁保护；terminal 与 release 都只能由当前 handle 执行一次。

建议接口（名称可按项目风格微调）：

- `reserve(session_id, attempt_id) -> AttemptHandle`：同步建立所有权，检查 closing/capacity/busy；任何持久化操作之前调用。
- `start(handle, coroutine_factory) -> None`：创建 Task 并在返回前注册 done callback；coroutine 尚未启动即 cancelled 也走统一 finalization。回调不可只释放 claim 而遗漏 cancelled 的持久化。
- `attach_worker(handle, concurrent_future, cancel_callback) -> None`：实际 worker 已结束之前保留所有权；wrapper 被取消后仍观察 future，阻止迟到结果覆盖新 attempt。
- `request_cancel(session_id) -> bool`：记录取消意图，区分未开始工作与已运行线程；复用目前 cancel API 的响应。
- `exclusive_idle(session_id)`：给 delete 或需要排斥新发送的操作提供原子的 idle guard；busy 抛 SessionBusyError。
- `aclose(timeout) -> ShutdownResult`：禁止新任务、发协作取消、取消排队工作并等待 worker；返回实际退出/仍未退出清单，不能把超时等同于成功关闭。

`agent/src/session/service.py` 保留应用入口，但统一调用 `finalize_attempt(handle, outcome)`；它负责建立 Message/Attempt 并执行主存储 commit，之后调用隔离的索引和事件投影。B1 的未开始取消、正常完成、异常失败和协作取消都复用这个终态路径；shutdown 中尚有实际 worker 的取消保持 recoverable，不伪装为用户取消。

`agent/src/session/store.py` 提供 terminal commit 所需的幂等读取/追加辅助和明确的删除失败；`agent/src/session/search.py` 增加事务性 `delete_session`；`agent/src/api/state.py` 定义 app 所有的 RuntimeServices/provider；`agent/api_server.py` lifespan 在统一容器上启动/关闭，兼容桥留在 API 外缘。通过假 worker Future、临时 store、假 projection 注入验证，不需要真实网络。

恢复必须在运行期开始接受请求之前完成；同一进程多次 app lifespan 不能复用未关闭的全局 service。采用单存储 owner 的部署约束，先停止旧 writer 再恢复 pending/running。若 worker shutdown 超时，进程内不能立即对同一存储再次启动 recovery；返回清楚的关闭失败并保留可恢复状态。

## 请求、响应和错误契约

保持既有成功状态码、字段与 SSE 事件名；路由只负责校验、鉴权、调用服务及错误映射。

| 操作 | 请求与成功响应 | 错误/边界 |
|---|---|---|
| POST `/sessions` | 保留 title/config 等已有字段和 session 响应 | 校验继续 422；runtime disabled 保留 501 |
| POST `/sessions/{id}/messages` | 保留 content 和 `{message_id, attempt_id}` | SessionNotFound→404；SessionBusy→409；关闭/容量不足→503 |
| POST `/sessions/{id}/cancel` | 保留 `{status: cancelled}` / `{status: no_active_loop}` | cancelled 表示取消已接受；真正结束以持久化 attempt/SSE terminal 为准 |
| DELETE `/sessions/{id}` | 保留 `{status: deleted, session_id}` | 不存在 404；在途 attempt 409；删除 I/O 失败不能回 200 |
| GET messages/session | 保留现有响应模型 | 先维持既有不存在 messages 返回空数组的兼容行为，避免顺手改语义 |
| GET events | 保留 Last-Event-ID、replay=active、terminal 事件名 | 满队列不得吞控制事件；新增同步提示须与前端一起落地 |

错误正文继续包含可被现有前端消费的 `detail` 字符串。引入 `SessionNotFoundError`、`SessionBusyError`、`ServiceClosingError`、`PersistenceError` 等小型领域错误，由统一 API 边界映射。可以增添 `code`/`request_id` 字段但不把 detail 改为对象。未知异常返回稳定的 500 文本、记录内部日志，避免把底层异常和路径直接当作产品契约。不要继续把全部 ValueError 映射成“会话不存在”。

## 异步执行与一致性约束

- acceptance 之前完成容量检查和 session claim；拒绝的请求不留下新 user message 或 attempt。线程池 max_workers 只限制运行并发，不限制排队；增加可配置的全局在途上限。
- 一个 session 同时只能有一个拥有写权限的 attempt；claim 生命周期必须覆盖实际 worker，而不只是 asyncio 包装 Task。
- terminal commit 必须幂等；完成后的附属索引/推送失败不能改变 terminal 状态。事件在主存储提交后发布。
- 自动标题 `sessions_routes.py:696-749` 在 await LLM 后重新读取当前会话，只在标题仍符合自动更新前置条件时写入，避免覆盖 await 期间的手动重命名或 last_attempt_id；该风险为静态发现，本轮未调用真实 LLM。
- 当前 async routes 执行同步 fsync/目录扫描会阻塞 event loop。先修正确性，再把明确的存储事务整体送入受控 I/O executor；不要把每个小写操作随意 offload，否则反而引入更多竞争。

## 测试矩阵与实施顺序

| 层级 | 必须覆盖 |
|---|---|
| supervisor unit | 立即取消、构建中取消、运行中取消、重复取消、旧回调不能释放新任务、异常可观察、容量限制 |
| service unit | 单会话并发 409；运行中删除 409；成功终态不被索引故障反转；关闭后拒绝接单 |
| 文件持久化 integration | terminal 每个写入边界失败/恢复；删除失败；重复恢复无重复回复；checkpoint 保留/清除顺序 |
| 索引 integration | 临时 SQLite 写失败不改业务终态；删除后无搜索结果；重建一致性 |
| SSE unit | 200 队列溢出、clear 必达、terminal 恢复、跨线程投递、连接关闭清理、历史 replay 去重 |
| API integration | 原有成功响应不变；404/409/422/501；关闭/过载 503；两个独立 app 状态隔离；lifespan 关闭顺序与失败清理 |
| frontend integration | 收到 cancellation ack 后等待终态；DELETE busy 提示；SSE 重连从权威状态恢复 |

建议提交顺序：先 B1/B2 及回归，再 B3/B4 及终态提交边界，然后 B5，最后以这些测试保护 app 容器和 shutdown 改造。每步保留独立回滚能力。核心运行器生命周期变更应与 Agent 审查统一实施，避免双方重复改所有权逻辑。

## 实际执行的验证

使用仓库 `.venv/bin/python -B` 通过 stdin 执行两个短脚本，均使用 TemporaryDirectory 与 `VIBE_TRADING_HOME` 隔离；没有写入仓库测试文件。Service 实验只替换 runner 与搜索投影，事件实验使用真实 EventBus；索引删除实验使用临时 SQLite。未运行 full suite，未启动 API，未加载真实 Provider 或账户。

实际输出：

```json
{"case":"cancel_before_first_step","cancel_ack":true,"task_done":true,"still_inflight":true,"retry_busy":true,"attempt_status":"pending"}
{"case":"delete_during_run","delete_ack":true,"initially_removed":true,"directory_recreated":true,"session_metadata_exists":false,"orphan_messages":1}
{"case":"auxiliary_index_failure","persisted_reply_status":"completed","persisted_attempt_status":"failed","last_event":"attempt.failed"}
{"case":"deleted_session_search","matches_before":1,"matches_after":1}
{"case":"clear_full_queue","after_drain":"heartbeat","subscriber_registry_removed":true}
```

以上输出证明的是基线缺陷存在，并非修复后测试通过。实施阶段应将这些场景固化为对应模块的回归测试。
