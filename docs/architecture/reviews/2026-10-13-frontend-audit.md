# 前端工程与稳定性审查

日期：2026-10-13。审查基线：`codex/architecture-stability-upgrade`，`c25fea2bc261a2d0ed971c989d194de9f04ec963`。本次只读生产代码，仅新增此报告；未联网、未读取凭据、未调用真实账户或运行交易流程。

## 结论

主要缺口是异步任务缺少统一的会话、attempt 和页面代际所有权。SSE 连接自身已有 generation 检查，HTTP 请求已有超时，但页面启动的异步回调和历史快照写入绕开了这些保护。应先建立可测试的会话运行时与状态提交边界，再调整组件订阅和加载；单纯拆分 `Agent.tsx` 或 `api.ts` 不能解决这些问题。

已有值得保留的机制：路由 `lazy`、完成答案才加载 EnhancedMarkdown、MiniEquityChart 延迟加载、Markdown 错误边界、消息行显示窗口、流式文本批量 flush、SSE 事件去重和断线续传、HTTP 连接及响应体超时、写请求不自动重放、分析任务以 request_id 恢复。没有证据支持现在引入 SWR、额外状态库或新流式依赖。

## 优先问题

### F1 · P1 · 旧会话的异步回调可以修改新会话

源码位置：

- `frontend/src/pages/Agent.tsx:930`：completion 入口只在执行开始检查会话；`:1045` 等待报告后，`:1055` 使用先前捕获的 `s.addMessage` 写入全局当前消息。
- `frontend/src/pages/Agent.tsx:1547`：等待 A 的 `cancelSession` 后，`:1548–1553` 无所有权检查，直接 flush、archive 当前 activity、清空文本并设置 idle。
- `frontend/src/pages/Agent.tsx:1505–1516`、`:1595–1605`：send/continue 的成功和失败分支同样没有会话与 pending attempt 身份检查。
- `frontend/src/pages/Agent.tsx:1499–1502`：创建新会话的响应晚到时会主动切换 URL 和全局 sessionId。
- `frontend/src/stores/agent.ts:134–135`：action 闭包总是写入调用时的当前 store，并不因为从旧 snapshot 取出就绑定旧会话。

真实触发：A 完成后报告 GET 尚未返回，用户切到 B；或用户取消 A 后立即切到 B 并开始新的消息，A 的取消响应随后返回；或 A 的发送失败响应在 B 已打开后到达。请求越慢越容易出现。

影响：A 的报告卡出现在 B；B 的活动被标为 stopped/failed、文本被清空，显示状态与后端执行状态分离；旧创建请求可能把用户导航拉回 A。该缺陷涉及前端归属与展示，不能据此声称后端取消了 B。

验证：离线执行实际 store 代码，捕获 A 的 state、切到 B、调用捕获的 `addMessage`，输出 `{"sessionId":"session-b","runIds":["run-a"]}`。页面异步交错由上述源码路径确认，尚未新增 UI 测试。

修复与测试建议：所有异步操作捕获 `{sessionId, viewEpoch, attemptId?, operationId}`；读请求可在切换时 abort，任何 await 后的 UI/store 提交仍需核验 token。取消还必须比较 attempt，防止同一会话下一轮被上一轮响应影响。使用 deferred promises 测试 A→B、A→B→A、卸载重挂、相同会话启动下一轮、发送 reject、取消 resolve、报告 resolve；新会话状态必须逐字段保持不变。对写操作只隔离迟到结果，不将 HTTP abort 当成后端操作已撤回，也不自动重试。

### F2 · P1 · 旧历史快照能覆盖刚收到的完成答案

源码位置：`frontend/src/pages/Agent.tsx:1310–1311` / `:1338–1339` 并行启动历史 GET 与 SSE；`:527–528` 仅检查历史 generation；`:632–649` 写入历史。`frontend/src/stores/agent.ts:151–165` 的 `loadHistory` 只保留 swarm placeholder，其余消息全部被快照替换。SSE completion `Agent.tsx:983–1006` 添加活动和答案时没有使旧历史 generation 失效。

真实触发：打开 A，历史请求拿到尚未包含最终答案的快照；SSE 的 `attempt.completed` 先处理并展示答案；历史响应晚到（尤其还需串行补查 run），随后 `loadHistory` 删除刚展示的答案和已完成活动。用户在已有缓存的页面发送新消息、历史仍在加载时，也可能丢失本地新增行。

影响：已完成回复从界面消失，只有再次刷新才恢复；本地状态显示 idle，而新回复不在记录中。仅用 session 切换 generation 无法保护同一会话内的版本更新。

验证：离线执行实际 store 的 `addMessage(answer)` 后 `loadHistory(oldSnapshot)`，最终只剩旧 user 行。这证明替换语义；页面 GET/SSE 交错路径由源码确认。

修复与测试建议：history-load 记录请求启动时的 transcript revision，通过有明确优先级的 `reconcileHistory` 合并 durable message_id、attempt_id、以及启动后新增的本地事件。若最初只做保守修复，revision 已变化时应重拉权威快照或保留增量，不能丢弃实时行；随后从服务端增加 snapshot revision/cursor 可以获得更强一致性。用 deferred GET 覆盖 completion 先到、message.received 先到、乐观发送先到、SSE 重放重复、history 中已存在同 attempt。断言答案恰好一份、顺序正确、临时 activity 不重复。

### F3 · P1 · SSE ticket 握手没有截止时间，连接会永久卡住

源码位置：`frontend/src/lib/apiAuth.ts:36–46` 直接 `fetch` 和 `res.json()`，没有 signal、timeout；`frontend/src/hooks/useSSE.ts:149–155` 仅在 promise reject 时重连；`:196–205` 的 disconnect 不能终止 ticket 请求。`useAlphaJob.ts:31` 也使用同一个 ticket 帮助函数。

真实触发：配置 API key 的环境中，代理接受 `/auth/sse-ticket` 但不返回响应，或只发 headers 后响应体停住。此时 promise 不 resolve/reject，也尚未创建 EventSource。

影响：不会触发 onerror 或 scheduleReconnect；Agent 的“连接”状态停留在旧值，分析任务停在 streaming。切换页面可以忽略迟到结果，但挂起网络请求仍继续。现有普通 API 超时无法覆盖这条独立路径。

验证级别：源码确定的无界等待；现有 SSE 测试覆盖 ticket 晚到被忽略，但未覆盖永不完成和响应体停滞。

修复与测试建议：抽取无业务依赖的 bounded fetch 基础层，同时覆盖 fetch 与 body reader。ticket 使用该层的有限超时和调用方 AbortSignal；每次连接代际拥有 AbortController。保留每次重新连接获取新 ticket 的要求。用假时间测试 headers 前挂起、body 挂起、disconnect abort、切换后旧 ticket 无效、重连仅一次和所有 timer 清理；禁止输出或缓存 ticket/key。无 key 的 loopback 同步 EventSource 路径应继续通过现有测试。

### F4 · P2 · 历史恢复被不相关报告 GET 串行阻塞

源码位置：`frontend/src/pages/Agent.tsx:531–631` 的 for 循环内 `:605` 等待 `getRun`；直至 `:649` 才交付文本，`:666` 才结束 loading。`frontend/src/lib/api.ts:10–13` 默认单请求最长 120 秒。

真实触发：会话有多条含 run_id、但 metadata 没有 metrics 的记录；其中一个 run 查询慢或多个查询停滞。即使历史文字已返回，用户仍看 skeleton；多个请求顺序累计延迟，并且切走后还会继续遍历旧记录发请求，直到循环末的 generation 检查。

影响：恢复速度取决于最慢报告的累计耗时；浪费网络资源，放大 F2 的交错窗口。不能用简单无限 Promise.all 替换以免大型历史同时扇出大量 GET。

修复与测试建议：历史 DTO→基础消息的转换保持纯函数、立即发布文本；报告 enrichment 按 runId 去重、有限并发、独立占位/失败状态，按需加载可见卡片，并绑定会话代际。测试两个 GET 未完成时文本已经显示，重复 runId 仅一次 fetch，某个失败不阻塞其他条目，切换时停止排队并 abort 正在运行的只读查询。

### F5 · P2 · 恢复缓存后本地消息 ID 重复，重试可能选错提示词

源码位置：`frontend/src/stores/agent.ts:117–118` 全局递增 ID；`:308–309` 每次切会话重置为 0，但 `:312` 又恢复带原数字 ID 的消息。`Agent.tsx:1612–1623` 通过 ID 的第一个匹配位置寻找错误之前的 user 提示词。

真实触发：A 有多个本地乐观/完成行，切 B 再切回缓存 A；权威历史尚未完成或加载失败时继续提交并出现错误。新错误 ID 可与缓存中较早消息 ID 相同，findIndex 定位到旧行。

影响：点击 Retry 可能没有反应，或重发旧提示词。当前顶层 row key 包含 index，因此不把此问题误报为必现 React duplicate-key 警告。

验证：实际 store 离线复现两条消息 IDs 为 `["1","1"]`，unique 为 1。

修复与测试建议：本地消息使用带前缀的唯一 ID（例如 `local:${crypto.randomUUID()}`），服务端 message_id 原样保留；错误行直接保存 pending operation / requestText 的引用，避免反向扫描推断归属。测试跨会话缓存、失败历史恢复、reset 与新 session、多条错误分别 Retry，确保每个 Retry 使用原始对应 prompt。

## 建议的工程边界

下面是增量目标，可以按阶段合并，文件名可随实现风格调整：

| 边界 | 建议文件 / 接口 | 唯一职责与约束 |
| --- | --- | --- |
| 通用传输 | `lib/http/request.ts`：`performRequest<T>(path, init, read, policy)` | 连接和响应体截止时间、AbortSignal 合成、timer 清理；无 Agent/store 依赖。保留 ApiError 的 status/code 与本地化行为。 |
| ticket 与 SSE | `lib/stream/sseClient.ts`：`connect({url, signal, onEvent, onStatus}) → dispose`；现 `hooks/useSSE.ts` 作为 React 适配器 | ticket、连接代际、重连、cursor 和去重；只产出事件，不直接写会话 store。拥有并释放自己的全部资源。 |
| 领域 API | `lib/api/agent.ts`：getMessages/getGoal/getRun 接受可选 signal；现 `lib/api.ts` 保留 facade | 管理端点和 DTO；旧导入与调用签名继续兼容；业务 POST 不自动重试。先迁移 Agent 真正使用的端点，无需一次拆分所有 API。 |
| 会话控制 | `features/agent/sessionRuntime.ts`：`openSession`、`closeView`、`send`、`cancel`、`applyEvent`、`refreshHistory` | 统一令牌 `{sessionId, viewEpoch, attemptId, operationId}` 与资源所有权；迟到事件不能提交至当前错误会话。视图取消与后端取消是不同操作。 |
| 纯状态转换 | `features/agent/sessionReducer.ts` / `history.ts`：`reduceSession(state, event)`、`reconcileHistory(state, snapshot, startedRevision)` | 所有终态、增量、历史合并通过相同规则；以 message_id/attempt_id/call_id 定位；可独立用事件序列测试，无 React 和 fetch。 |
| Store | `stores/agent.ts` 暴露带 token 的提交 action 与 selector | 初期可保留“一个活动视图 + 5 个缓存”的结构；通过原子 guard 防误写。不要以为给 action 多加一个 sid 参数但不检查它就完成隔离。 |
| UI | `Agent.tsx` 路由/composer 编排；`AgentTranscript`、`AgentStream`、`AgentActivity` 选择自身所需状态 | 高频 streamingText/reasoningTail 不让整个页面重新渲染；runtime 与状态恢复逻辑不依赖组件是否在屏幕上。 |

安全的最小落地顺序：

1. 先建立 operation/view token 与 guarded commit 的纯接口，添加 F1、F2 的 deferred response 回归测试；完整接入所有 send/cancel/completion/goal/history await 路径。
2. 把现有 bounded fetch 抽到低层，接入 ticket；接通读请求 signal 和 disconnect 生命周期；保留 HTTP mutation 的不确定结果提示。
3. 把历史映射和 reducer 从页面迁出，建立 revision 合并与稳定 ID；同步维护已完成 attempt 的幂等处理，避免 REST/SSE 双重完成。
4. 再将报告 enrichment 与文本恢复分离；增加 runId 请求去重和有限并发，避免扩展为通用数据框架。
5. 最后迁移高频订阅到叶子组件，按证据优化 bundle；用 profiler 或受控 render 计数证实收益。现有图表/Markdown lazy 已经存在，不能把这些已有措施算成新升级。

## 兼容契约

- 保留 `api` facade、`ApiError` 的 name/status/code、现有路径和 JSON 字段；signal 参数保持可选，旧调用方不需要一次迁移。
- 保留 SSE 事件名及 payload，包括 attempt.created/started/completed/failed/cancelled、text_delta/reasoning_delta、tool 事件、goal/swarm/live 事件；未知/缺字段事件保守忽略或报告可观察错误，不能构造假的完成状态。
- 保留 `Last-Event-ID` 查询续传、replay=active、单次使用 ticket、无 key loopback 路径；所有 reconnect 和 replay 不得再发送用户消息。
- 保留用户发送/附件/goal/swarm metadata、provider/model identity、reasoning 展示边界、partial answer、活动原始 startedAt 及历史 tool trail 的兼容映射。
- 网络断开、视图离开和客户端等待超时只改变客户端观测状态；不能据此宣称后端任务已取消。显式 cancel 请求及确认按原 API 契约处理，不引入自动取消后台任务。
- 继续保留 5 会话缓存与草稿恢复的用户体验；会话缓存必须按 session 隔离，刷新后由 REST/SSE 恢复，不持久化新增认证材料。
- 幂等键优先使用服务端 message_id、attempt_id、call_id；客户端本地 ID 使用独立命名空间；状态 schema 变化如涉及 sessionStorage，要保留 v1 读取或提供显式迁移/回退。

## 验证记录与回归范围

已读取 `AGENT_CONTRIBUTOR_GUIDE.md`、`vercel-react-best-practices/SKILL.md` 及 `client-localstorage-schema`、`client-swr-dedup`、`rerender-defer-reads`、`rerender-memo`、`bundle-dynamic-imports`、`async-parallel` 规则。规则作为设计依据，没有照搬 Next.js 专用 API 或引入新依赖。

本次执行：

```text
cd frontend
npm run test:run -- src/hooks/__tests__/useSSE.test.ts src/stores/__tests__/agent.test.ts src/lib/__tests__/api.test.ts src/pages/__tests__/AgentBackgroundStreamCompletion.test.tsx
Test Files 4 passed (4)
Tests 66 passed (66)
```

另外以 TypeScript transpileModule 在内存中加载实际 `stores/agent.ts`，完成三个无网络序列：ID 重复、捕获旧 state 的 action 写入新 session、历史替换删除新答案。未写入测试或生产文件。已有用例全部通过不代表上述交错已覆盖；F1–F4 的 UI/transport 故障注入测试仍需在实施阶段新增。

实施后最低回归范围：

- 纯 reducer 和 runtime：A→B→A、unmount/remount、同 session 下一 attempt、乱序 completion、重复 replay、历史先到/后到、abort 后迟到 resolve、request reject、稳定 message/attempt/call ID。
- SSE：原 useSSE 测试 + ticket handshake/body timeout、disconnect、重连 timer/AbortController 回收、cursor/dedupe 不串 session、认证失败 UI 行为。
- Agent 页面：BackgroundStreamCompletion、ActivityTimerPersistence、GroundingStatus、RuntimeIdentity、streaming_area/streaming_dom，以及 send/cancel/report 的 deferred-response 场景。
- 内容与状态恢复：MessageBubble/math、ThinkingTimeline、ConversationTimeline、RunCompleteCard、SwarmStatusCard、GoalPanel 行为，工具 progress/heartbeat 和 partial answer。
- API 与分析：api/apiAuth、useAlphaJob；公共传输抽取不得破坏 job request_id 恢复、单次 ticket、只读请求取消和 mutation 不重试。
- 构建：`npm run build`；比较生产 chunk 依赖图后再决定是否进一步切分。检查 Agent 首屏不额外拉入图表/高亮/数学字体；不凭文件行数或未测量的推测给出性能收益数值。

本次按任务边界未运行完整 frontend suite、production build、后端 suite 或端到端真实网络测试。
