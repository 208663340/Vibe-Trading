# 前端 Task 3 交叉设计评审

日期：2026-10-13。评审对象：`docs/architecture/2026-10-13-stability-design.md` 与 `docs/superpowers/plans/2026-10-13-architecture-stability-upgrade.md` 的 Task 3，同时对照现有 Agent 页面、store、API、ticket/SSE/Alpha hook 和后端 session/alpha 路由。

## 结论

**通过：原 R1–R4 均为 ADDRESSED，当前设计可进入 Task 3 实施。** 2026-10-13 scoped 复核只检查原四项，没有新增范围或阻塞项。下文保留首次评审的证据与测试建议；各项开头的复核状态为最新结论。此次仅更新评审报告，未修改生产代码，未运行测试或联网；设计通过不代表实现已通过回归。

| 原项 | scoped 复核状态 | 当前设计依据 |
|---|---|---|
| R1 | ADDRESSED | 后端新增可选 expected_attempt_id，比较与取消在同一 supervisor gate；前端 pending cancel 等待 ACK 后定向取消，旧无 body 调用兼容 |
| R2 | ADDRESSED | view epoch 单调、operation 按类别隔离、pending 身份单次晋升、terminal 不被迟到 ACK 降级、functional store guard 与视图/缓存所有权分离 |
| R3 | ADDRESSED | 明确 user/answer/activity/report 实体键、REST linked_attempt_id 桥接、每实体 revision 和 provisional→durable ID 替换，禁止按内容去重 |
| R4 | ADDRESSED | 禁止自动重发 session send，同时显式保留 Alpha request_id 恢复与 reconnect mint；Task 3 保留 headers/body deadline、abort、单次 ticket 与 transient retry 回归要求 |

session runtime、纯 history reconciliation、独立 HTTP 期限层、SSE transport 和 guarded store 的分工合理。保留原 facade 和可选 signal 参数能够渐进迁移。先完成可观察并发正确性，再凭 render-count 证据调整叶子订阅，顺序合理。

## R1：收窄“旧 cancel 不会停止新 attempt”的保证

**复核：ADDRESSED。** 设计采用原评审指出的更强协议方案：新增向后兼容的 expected_attempt_id，而非仅收窄 UI 保证。总设计 Backend contract 中明确 mismatch 不取消其他 attempt，Task 3 Step 3 明确 pending-send cancel 只在取得该次 ACK 后定向发送。下方首次评审中“API 不变下暂缓新 send”的替代方案不再是本轮阻塞条件；实现仍需覆盖延迟请求与延迟 ACK 两类测试。

关联：Task 3 Step 1；总设计前端验收第 4 条、后端 cancel 契约。

现有 `frontend/src/lib/api.ts:533` 只发送 POST `/sessions/{sid}/cancel`，没有 attempt 参数；`agent/src/interfaces/api/sessions_routes.py:774-783` 直接调用 `svc.cancel_current(session_id)`。因此 operation/view/attempt guard 可以阻止**旧响应**清空新 attempt 的 UI，却不能阻止**旧请求**在服务器迟到后取消当时的新 attempt。

可触发顺序：A 的 attempt 1 运行 → 客户端发 cancel，网络延迟 → attempt 1 自然结束 → attempt 2 开始 → 旧 cancel 才抵达服务器。仅靠前端响应 token 无法解决后半段。

保持当前 API 的可实施约束：

- 文档把强保证写成“旧 cancel acknowledgement 不修改后续 attempt 的展示状态”。
- 本客户端对同会话存在未决 cancel 时，暂缓新 send；ack/terminal 都按捕获的 attempt 处理。ack 只是接受请求，不立即 archive 为 stopped，不清除仍在生成的文本。
- timeout/network error 属于取消结果未知；不自动重发 cancel，也不把一次陈旧 GET 当作证明服务器没有执行。
- 明确跨客户端或超时后人工继续情况下的残余能力边界。若要求服务器严格保证只能取消指定 attempt，需要另行协调向后兼容的 expected_attempt_id 前置条件，超出目前“仅前端且 API 不变”的 Task 3。

必须有两个不同测试：延迟旧 ACK 不污染新 UI；延迟旧请求的本客户端下一次发送不会自动越过未决取消。不能把只测前者表述为服务器执行身份已经受保护。

## R2：operation 身份必须能从 pending 晋升，不能用一个全局序号作全部请求的开关

**复核：ADDRESSED。** 总设计 Frontend responsibility boundaries 已明确单调 view epoch、按类别 operation token、创建 session 前的 opId、单次身份晋升、terminal-before-ACK 不退回 streaming、原子 functional commit，以及视图副作用和后台缓存确认的不同所有权。原实现风险已转为明确验收约束。

关联：Task 3 Interfaces / Steps 1、3；设计中的 session/view/operation/attempt tokens。

发送响应含 `message_id` 和 `attempt_id`，但后端可以先发布 `message.received`、`attempt.created/started/completed`，再让浏览器拿到 POST 响应。现有页面 `Agent.tsx:1505` 等待 send 响应期间 SSE 已连接。同步 stub 或快速执行时，甚至 terminal 会先到。若 guard 要求当前 attempt 必须预先等于 response attempt，便会误丢第一轮 SSE；若晚到 ACK 无条件设置 streaming，则会复活已完成任务。

实施接口须明确：

- `viewEpoch` 单调递增，A→B→A 也产生新值；reset 和组件重挂不能让旧令牌重新有效。
- operation 以类别区分 send/cancel/history/goal/enrichment，不要让启动一次 history 或 goal GET 就作废仍在途的 send。
- send operation 在创建会话前就拥有唯一 opId。它能从 `{sessionId:null, pendingId}` 绑定到真实 session/message/attempt，并且只能晋升一次；晚到 ACK 只补身份映射，不回退 terminal 状态。
- token 检查和写入必须在同一个 Zustand functional updater 内执行，避免先检查再从旧 snapshot 调 action。
- 区分“当前视图副作用”（导航、清空 composer、toast、当前 activity）与“session 数据确认”（ACK 绑定、缓存更新）。切走后的旧结果不得导航回来；是否更新 A 的缓存要按 A 的 session/op 所有权处理，不能写进 B，也不能因 view 不在屏幕上就忘记已接受的后台执行。
- 同一 attempt 的 terminal 不退回 streaming；迟到的旧 attempt completion 可按身份补历史，但不得清除新 attempt 的 stream/activity。

最低序列测试：SSE created/terminal→POST ACK、POST ACK→SSE、A→B→A 后 ACK、createSession ACK 晚到、goal refresh 与 send 同时进行、旧 attempt 报告晚于新 attempt started。

## R3：history/live 的“恰好一份”需要显式身份映射，不能只比较 revision

**复核：ADDRESSED。** 总设计已列出 durable user、answer、activity、report 的独立实体键；REST linked_attempt_id 与 SSE attempt_id 桥接、每实体 revision、durable 替换 provisional 且不重复、相同文本不去重均已明确。原报告的缓存、乐观行交错与 metadata 测试仍作为实施验证建议，不新增设计阻塞项。

关联：Task 3 Step 3；设计前端验收第 4 条。

现有 terminal SSE 有 `attempt_id`，没有最终 assistant `message_id`；REST Message 有 `message_id` 和 `linked_attempt_id`。现页面 SSE 添加 answer (`Agent.tsx:997` 附近) 没有把 attempt 身份存进 answer，因此纯 merge 若只按 message.id 会保留两份答案。乐观 user 在 ACK 到来前也没有 durable message_id。同样内容可能是用户真实连续发送，不能按文本去重。

落地规则：

| UI 实体 | 合并键与优先级 |
|---|---|
| durable user message | 服务端 message_id；通过 send op 的 ACK 把 optimistic ID 映射过去 |
| assistant answer | session + linked_attempt_id / attempt_id + answer 槽位；REST 补 durable message_id 后替换 provisional 身份 |
| activity | session + attempt_id + activity 槽位，与同 attempt 的 answer 分开 |
| report card | session + attempt_id + runId + report 槽位；不能按 attempt 把 answer/activity/report 全折叠 |
| tool trail | attempt_id + call_id；无 call_id 的旧记录采用明确兼容回退，不把同名工具都合并 |

每条本地新增/更新行记录 revision/source；history 请求捕获 startedRevision。旧快照补齐历史并与相同实体合并，同时保留 startedRevision 之后的本地新增和终态。不能仅在 revision 变化时完全放弃历史，否则缺失的旧消息永远无法补齐；也不能保留所有旧缓存消息，导致服务器已不存在或超出窗口的数据无限累积。REST 返回的是有上限的 history window，窗口外保留策略应明确且有界。

消息 ID 使用独立命名空间（如 local UUID）且不在 switchSession/reset 时复用。durable ID 原样保留；派生 activity/report ID 需含实体类型。Retry 直接绑定原 send op/requestText，不反向扫描可能重复的 id。五会话缓存必须保存必要的身份/revision 信息，而不仅是渲染文本；如果更改持久化 schema，要继续读取现有版本或明确迁移。

必须覆盖：旧历史晚于 terminal；历史已含同 attempt；SSE 重放；相同文本两次发送；乐观 user→message.received→ACK→REST 任意可行交错；旧 attempt enriched report 晚到；缓存驱逐后重开。断言每种 UI 实体恰好一份、真实重复 user 保留两份、正确顺序、metadata/tool trail/provider identity 不丢失。

协议限制也要诚实：无幂等请求 ID 的 session send 若响应丢失，不能仅按 prompt 相同就证明某条服务端消息属于本次 operation。保持“待确认”并从 REST/SSE 恢复，不宣称严格识别所有未知写请求。

## R4：限定“不重试 POST”的范围，并把 ticket 的 timeout/retry/abort 分类写清楚

**复核：ADDRESSED。** Compatibility 已把禁止重放限定到 session send/未知写操作，并明确保留 Alpha 原 request_id 恢复和 SSE 新 ticket mint。Task 3 Step 4 继续要求 shared fetch/body deadline、disconnect abort、fresh ticket、无 key 同步路径及 timeout/cleanup/transient retry 回归；与仅修改 useAlphaJob ticket ownership 的边界一致。原报告详细分类用于测试实现，不再要求重写既有 Alpha 恢复协议。

关联：总设计 Compatibility 的“不自动重放 POST”；Task 3 Step 4；Task 3 `useAlphaJob.ts` 仅 ticket ownership 的文件边界。

当前 `useAlphaJob.ts:65-85` 已用原 request_id 恢复未确认的提交；后端 `alpha_routes.py:570-576` / `671-677` 对相同 request_id 返回已有 job、不同参数返回 409。此次限制该 hook 只改 ticket ownership，不能为满足广义“不重试 POST”而删除这条既有恢复行为，也不能把它推广给没有对应协议的 session send。

建议把规则写成：“通用 HTTP 层不新增自动 mutation retry；session/create/goal/send/cancel 的未知结果不自动重放；保留 Alpha 已有同 request_id 的恢复协议；每次 SSE 连接重新 mint 单次 ticket。”保留既有恢复不代表新增跨服务重启的幂等保证。

HTTP/ticket 的具体边界：

- 期限覆盖 fetch headers 和 body reader，不能在拿到 Response 时提前清 timer。调用者 abort 与 deadline 使用合成 signal；所有退出路径移除监听器、清理 timer。
- timeout/transient 网络错误：由当前 transport generation 安排一次指数退避；HTTP 基础层不再重试一次，避免双重重连。
- disconnect/superseded/unmount abort：不显示业务失败，不安排 retry。迟到 ticket 不能创建 EventSource；旧 generation 的 catch 不能清新 generation 的 timer。
- ticket HTTP 401/403 与错误响应体使用有限期限；认证失败有稳定可观察状态，不需要把认证失败当无限瞬时故障。是否停止重连须明确并保留现有 UI 调用面。
- 每次重连 mint 新 ticket；key/ticket 不进日志、缓存或错误展示。无 key loopback 保持原同步 EventSource 路径。
- 每个 generation 同时最多一个 handshake、一个 EventSource、一个 retry timer；hook cleanup 与新 connect 统一走 dispose。
- caller abort 对 mutation 仅终止客户端等待，保留结果未知语义；错误行不要成为默认“一键自动重发”的触发器。

测试用 fake timers 和 deferred fetch/body：headers 挂起、headers 已到而 body 挂起、timeout 后仅一条 retry 链、disconnect 后零 timer/source、A 的旧 ticket 后于 B、401/403、无 key 同步路径。Alpha 只验证 signal 传播及旧 request_id 恢复未受破坏，不在此任务重写其提交状态机。

## 非阻塞实施备注

- Task 2 若增加 resync control，Task 3 必须显式注册事件名；当前 useSSE 是已知事件白名单，不会自动收到任意新命名事件。控制事件负责触发 GET reconciliation，不自动发送业务 POST。
- 也应注册/处理既有 `session_cleared`：关闭当前 stream，按当前 view guard 更新或离开已删会话，不能用普通 reconnect 一直连接不存在的会话。
- 报告 enrichment 按 runId 去重、有限并发，但 commit 还要带 session/attempt/实体身份。不要因为当前 view 已开始下一 attempt 就把上一 attempt 的 report 写到新卡片；也不要用一次 report reject 撤销已渲染的基本历史。
- `ApiError` 只保留一个 constructor/export 身份；底层 request.ts 不反向 import api.ts 造成循环依赖。status/code/detail 的兼容映射应由现有契约测试保护。
- 公共层所有超时统一为有界，不需要把默认 120 秒变成全局更短值；ticket 可用独立 metadata 期限，避免顺手改变其他长任务的用户行为。

## 合并门槛

原 R1–R4 设计门槛均已满足，无需新增任务或更换技术栈。实施后审查应查看真实 page/store/hooks 的 deferred-promise 测试结果，而不只看新 helper 单元测试。重点断言是“新 session/new attempt 的字段不被旧回调改变”和“网络未知结果不被误报为确定失败/取消”，不能只断言调用了一个 token helper。
