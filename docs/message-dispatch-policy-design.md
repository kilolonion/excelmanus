# 消息发送策略全链路设计

状态：三种策略的单进程执行链路已实现；下文原始设计保留作后续演进参考。

## 当前实现 (2026-09-24)

- `message_dispatch_default` 支持 `steer | interrupt | queue`，通过 `/api/v1/config/runtime` 读写，保存在已有 config_kv。发送方式只在系统设置中选择，输入框直接使用已保存的配置。
- 空闲发送沿用 `/chat/stream`；运行中通过 `POST /chat/{session_id}/dispatch` 提交完整消息载荷 (`message`, `images`, `chat_mode`, `sheet_context(s)`, `workbook_action`, `client_message_id`, `mode`)。该入口固定目标会话，不再做工作区重路由。没有活跃 owner 时返回 `409 SESSION_IDLE`，保留草稿。
- `steer` 在 step 边界吸收，不打断当前工具批；来不及吸收的引导转到下一轮，排序为已有 interrupt、晚到引导、普通 queue。模式变更使用 queue 或 interrupt，steer 不改变当前回合权限模式。
- `interrupt` 取消当前 turn task，保留外层 session actor。当前回合所属子代理也停止。旧线程、Code Mode 子调用尚未结算时不启动新轮，不以超时假装收尾完成；已提交修改保留，不自动回滚。
- SSE owner 与 `in_flight` 锁覆盖整个队列，浏览器断开不释放运行中的写入权。`turn_start`, `turn_reply` 明确分隔每轮，`dispatch_state` 携带完整版本化回执；重连补 `dispatch_snapshot`。
- 回执使用 `(session_id, client_message_id)` 和规范载荷摘要去重。相同 ID/内容返回原回执，不同内容返回 `409 DISPATCH_CONFLICT`。前端丢失回执后重试保留原 ID，即使原任务已经结束也先查询原投递结果。
- 回执、未消费 Inbox、当前输入随 SessionState 快照保存；已认领但没有终态的记录在进程恢复后标为 interrupted，不自动重放有副作用的操作。开启历史持久化时使用现有 SQLite；无数据库的调用方只具备进程内语义。
- `GET /chat/{session_id}/dispatches` 回源；`POST /chat/{session_id}/dispatches/{dispatch_id}/cancel` 仅撤回未认领消息。停止/重启后队列通过输入框“继续队列”显式恢复，不会自动恢复旧审批。
- 图片和工作簿上下文继续走发送前保存/版本检查。已保存 dispatch 身份不进入 provider prompt。界面根据回执 revision 防止旧 HTTP ACK 覆盖新 SSE 状态。

当前运行时仍要求同会话路由到同一进程。跨 worker 分布式 owner 租约、独立 dispatch 表和跨进程事件总线不在本次实现内。原设计中的独立 retry/edit 接口与更多调度配置也尚未开放。

验证入口：`tests/test_message_dispatch.py`、`web/src/__tests__/message-dispatch.test.ts`；浏览器测试使用 `tests/dispatch_browser_server.py` 与 `web/scripts/dispatch-preview.mjs`，由真实 API/Driver/SSE 执行，可控模型不访问外部供应商。测试 API 仅监听 loopback，临时工作区不使用真实文件。

以下为最初的目标设计，字段及端点以本节的当前实现为准。

本文只把附件中的界面作为交互参考。用户需求是：为消息发送提供 `steer`、`interrupt`、`queue` 三种策略配置，并贯通前端、API、会话 Driver、Inbox、SSE、持久化和恢复。

## 1. 目标与非目标

### 目标

- 用户可以配置消息在“当前助手正在执行”时的默认策略。
- 每条消息在服务端被记录为一个有身份、有状态、有顺序的 dispatch item。
- 前端能看到“已发送、已接收、已生效、已排队、已取消、失败”等确定状态。
- 刷新、SSE 断线、服务重启后，未生效消息不会静默丢失或重复执行。
- Excel 工作簿写入、工具调用、审批和问答在 interrupt 下仍保持可恢复性。

### 非目标

- 不把 `interrupt` 实现为无条件杀掉 Python task。
- 不让普通 streaming 请求进入没有 SSE owner 的隐形队列。
- 不改变已有 `next-step` / `next-turn` 的历史语义；新协议在其上增加明确的 dispatch 层。

## 2. 三种策略的精确定义

### `steer`：引导当前执行

适用于“当前任务仍然继续，但补充方向”。消息进入当前 turn 的 `next-step`，在下一个模型/工具 step 边界被认领，作为新的用户上下文加入 memory，然后继续同一个 turn。

- 不取消当前 LLM 请求。
- 不打断正在执行的工具批。
- 多条 steer 按顺序合并，保留每条消息的 dispatch id。
- 当前 turn 已经自然结束时，降级为下一轮 `queue`，不能被空 turn 吞掉。

### `interrupt`：中断当前执行后立即处理

适用于“当前方向已经不对，马上改做另一件事”。服务端先进入 `interrupt_requested`，向 Driver 和 ToolDispatcher 传播取消；当前 step 必须完成可取消收尾和已提交写入的结算，再结束当前 turn，然后以新的 turn 处理该消息。

- 不是直接丢弃任务；必须等待 cooperative cancellation 或明确标记为 `non_cooperative`。
- 当前工具已提交的文件事务、审批状态、工具结果必须先落库/结算。
- 新消息只能在旧 turn 达到 `interrupted` 终态后执行。
- 如旧 turn 不能在策略超时内安全停止，消息保持 `interrupt_pending`，前端显示等待，不偷偷执行。

### `queue`：排队等待

适用于“不要影响当前执行，当前 turn 完成后再处理”。消息进入 `next-turn`，等待当前 turn 结束后按 FIFO 开新 turn。

- 不进入当前模型上下文。
- 不创建新的 streaming owner；当前 SSE 必须通过 `queued` 事件告知客户端。
- 队列上限、去重、取消和恢复均由服务端决定，不能只依赖浏览器内存。

## 3. 配置模型

### 3.1 配置字段

建议新增运行时配置：

```text
message_dispatch_default: "steer" | "interrupt" | "queue"
message_dispatch_when_idle: "send" | "queue"
message_dispatch_interrupt_timeout_seconds: int, default 10
message_dispatch_queue_max: int, default 32
message_dispatch_coalesce_steer: bool, default false
```

第一阶段只暴露 `message_dispatch_default`；其余字段保留服务端默认值，避免一次引入过多用户可调参数。

配置应进入现有 `config_kv`，由 `settings_runtime.get_setting()` 读取，并接入现有 `/api/v1/config/runtime` GET/PUT。`ExcelManusConfig` 只保存启动时默认值，运行时读取以配置 store 为权威；已打开的 Engine 通过 `SessionManager` 的 dispatch policy resolver 获取最新值。

### 3.2 策略解析优先级

```text
request.dispatch_mode
  > session override（未来可选）
  > global message_dispatch_default
  > steer
```

配置与请求均使用稳定英文枚举；UI 只显示中文标签。

### 3.3 请求字段

在 `ChatRequest` 增加：

```python
dispatch_mode: Literal["steer", "interrupt", "queue"] | None = None
client_message_id: str = Field(min_length=1, max_length=128)
```

`client_message_id` 是客户端重试幂等键，不是数据库 message id。对于普通空闲发送也必须传，保证网络重试不会创建两轮。

## 4. 服务端数据模型

新增一个会话级 `DispatchItem`，可以先用 SQLite session event + runtime snapshot 实现，稳定后再抽表：

```text
dispatch_id
client_message_id
session_id
mode                 steer | interrupt | queue
content
extra_json
status               accepted | queued | applying | applied |
                     interrupt_requested | interrupted |
                     cancelled | rejected | failed
sequence
created_at
accepted_at
applied_turn_id
applied_step_id
failure_code
```

状态转移必须单向且带条件：

```text
accepted -> queued | applying | rejected
queued -> applying | cancelled | failed
applying -> applied | interrupt_requested | failed
interrupt_requested -> interrupted | failed
interrupted -> applying（新 turn）
```

同一个 `client_message_id` 在同一 session 只能对应一个 dispatch item。重复请求返回原 item 当前状态，不重复 push Inbox。

## 5. API 契约

### 5.1 发送入口

`POST /api/v1/chat/stream` 保持 SSE，但在请求体加入 `dispatch_mode` 和 `client_message_id`。

服务端先完成轻量 dispatch admission，再决定是否创建/复用 stream：

- 空闲：创建正常 stream，发送 `dispatch_accepted`，然后执行。
- 运行中 + steer：复用当前 session 的 stream owner，push `next-step`，返回 `dispatch_accepted`。
- 运行中 + queue：复用当前 session 的 stream owner，push `next-turn`，返回 `dispatch_accepted`。
- 运行中 + interrupt：设置 `interrupt_requested`，触发 Driver 中断；当前连接发送状态事件，之后同一 owner 继续发送新 turn 的事件。

不能为 steer/queue 单独创建第二个 `chat_task`，否则会绕过 `SessionManager.in_flight` 和单 Driver 所有权。

### 5.2 非流式兼容入口

`POST /api/v1/chat` 仍可保留，但不应再把 busy 消息静默写入“兼容队列”。建议返回结构化 202：

```json
{
  "status": "queued",
  "dispatch_id": "dsp_...",
  "session_id": "...",
  "mode": "queue"
}
```

旧客户端仍可收到原文本，但新客户端必须使用 dispatch 状态，不依赖自然语言提示。

### 5.3 Dispatch 控制接口

```text
GET    /api/v1/sessions/{session_id}/dispatches?status=active
GET    /api/v1/sessions/{session_id}/dispatches/{dispatch_id}
POST   /api/v1/sessions/{session_id}/dispatches/{dispatch_id}/cancel
POST   /api/v1/sessions/{session_id}/dispatches/{dispatch_id}/retry
POST   /api/v1/sessions/{session_id}/interrupt
```

`interrupt` 端点只负责请求中断，不接受第二份消息；消息已经由原始 dispatch item 保存。

## 6. Driver 与 Inbox 改造

现有 `Inbox` 已有 `next-turn` 和 `next-step`，可保留为执行队列，但必须补齐身份和状态：

1. `InboxItem` 增加 `dispatch_id`、`client_message_id`、`mode`。
2. `push_followup` / `push_steer` 写入 dispatch item 后再入队，失败不能产生半条 Inbox。
3. `claim()` 在同一个锁内将 dispatch 状态从 `queued` 改为 `applying`。
4. `_record_claim()` 同步写 `applied_turn_id`、`applied_step_id`。
5. `_append_step_items()` 只负责 memory 投影；dispatch 状态不能依赖 memory 是否成功追加。
6. `runtime_state()` 保存未认领 dispatch 的完整 payload 和 sequence；已认领但未结算的 item 写入 `active_dispatch`，恢复时标为 `applying_recovery`，不能直接丢弃。

### 6.1 steer

`Driver.consume_next_step()` 继续作为唯一消费点。它必须在 `STEP_START` 前后发出明确事件：

```text
dispatch_applying { dispatch_id, mode, turn_id, step_id }
inbox_claimed     { dispatch_id, ... }
dispatch_applied  { dispatch_id, ... }
```

若 pre-step attachment 或 context compile 拒绝，状态转 `failed`，不能默默从 Inbox 消失。

### 6.2 queue

现有 `next-turn` 是 FIFO，但当前 SSE 只对原始请求拥有 owner。新增 queue 状态事件后，前端可把 queued 消息渲染在对应用户气泡下；当前 stream 完成后，Driver 继续处理下一条，且新 turn 必须产生新的 `TURN_START`。

### 6.3 interrupt

新增 Driver 方法：

```python
async def request_interrupt(self, dispatch_id: str) -> InterruptReceipt:
    # 标记 active turn；调用 dispatcher.request_cancel()
    # 等待当前 actor 进入 turn 终态；不取消 actor 外层等待者
```

关键点：

- `request_cancel()` 现在会直接 `task.cancel()`；interrupt 不能复用这个入口作为唯一实现。
- 建议拆成 `request_cooperative_cancel()` 和 `force_stop()`。
- `ToolDispatcher` 首先设置 cancellation token，等待工具释放事务/子进程；只有超时才 force stop，并产生 `non_cooperative` 诊断。
- `turn()` 捕获 `CancelledError` 后必须把当前 dispatch 状态结算为 `interrupted`，并保留 `staged_inputs`。
- 新 interrupt item 在旧 turn 终态后才从 `next-turn` claim，避免同时存在两个 active turn。

## 7. SSE 事件协议

新增事件，全部进入 `SessionStreamState` replay buffer：

```text
dispatch_accepted
dispatch_queued
dispatch_applying
dispatch_applied
dispatch_interrupted
dispatch_cancelled
dispatch_failed
turn_interrupt_requested
```

事件公共字段：

```json
{
  "dispatch_id": "dsp_...",
  "client_message_id": "cli_...",
  "mode": "steer",
  "status": "queued",
  "turn_id": "t4",
  "step_id": "s7",
  "seq": 42
}
```

`heartbeat` 仍然只是传输保活，不能作为 dispatch 已生效或模型有进展的证据。

前端 `sse-event-handler` 必须做到：

- 用 `dispatch_id` 更新对应用户消息，不按“最后一条用户消息”猜测。
- 重放同一事件幂等。
- `dispatch_applied` 早于用户消息历史刷新时，仍能通过 dispatch store 回填。
- `resume_failed` 后从 session detail + dispatch list 回源，不自动重复发送。

## 8. 前端交互设计

### 8.1 配置入口

在现有“设置 -> 系统/运行时”增加“助手执行中的消息”分组，使用 segmented control：

- `调整方向`：当前任务继续，下一步采用
- `立即打断`：结束当前执行后立即处理
- `排队`：当前任务完成后处理

配置文案应解释行为和代价，不暴露内部 `next-step` / `next-turn` 术语。

### 8.2 发送按钮菜单

附图中的输入框菜单可以作为临时覆盖入口：

- 主发送按钮保持当前默认策略。
- 菜单中提供三项“一次发送”动作，点击后只影响本条消息。
- 当前 stream 运行时，发送按钮不应被简单替换为 stop；应同时允许输入消息并显示策略图标。
- stop 仍是独立动作，不能与 interrupt 混为一谈。

### 8.3 消息状态

用户气泡下显示紧凑状态：

```text
调整方向 · 等待下一步
立即打断 · 正在停止当前任务
排队 · 前面还有 2 条
已生效 / 已取消 / 处理失败
```

状态必须来自 SSE 或 session detail，不能只根据前端 `isStreaming` 推断。

### 8.4 前端 store

新增独立 `dispatchesById`，不要把 dispatch 生命周期塞进 `Message` 联合类型：

```ts
type DispatchMode = "steer" | "interrupt" | "queue";
type DispatchStatus =
  | "accepted" | "queued" | "applying" | "applied"
  | "interrupt_requested" | "interrupted"
  | "cancelled" | "rejected" | "failed";
```

用户消息只保存 `dispatchId` 和 `clientMessageId`，助手块仍由现有 stream handler 维护。

## 9. 持久化与恢复

### 正常发送

1. 生成 `client_message_id`。
2. 后端事务写 dispatch accepted。
3. 成功写 Inbox 后写 queued/applying。
4. Driver claim 后写 applied。
5. turn 终态和消息 snapshot 同步 flush。

### 浏览器断线

- 当前 chat task 继续运行，原有 `SessionStreamState` 继续缓冲。
- dispatch 事件进入 replay buffer。
- 重连使用 `stream_id + after_seq`，缺口时走 session detail/dispatch list 回源。

### 服务重启

- `Driver.restore_runtime_state()` 恢复未认领 next-turn/next-step 和 active dispatch。
- active `steer` 若已认领但未进入模型，标为 `failed(recovery_required)`，不自动重放，避免重复改变方向。
- active `queue` 可恢复为 queued，并在下次 session actor kick 时继续。
- active `interrupt_requested` 恢复为 interrupted，原 turn 标为 interrupted，用户可显式 retry/resume。

## 10. 并发、幂等和边界规则

- 所有 session dispatch 操作必须在 `SessionManager._lock` 或 session actor 单线程上下文中完成，不能先查 busy 再异步 push。
- `client_message_id` 去重范围是 `(session_id, client_message_id)`。
- 同一个 dispatch 的 cancel/retry 使用 CAS 式状态检查；重复调用返回当前状态。
- `steer` 在 idle 时默认降级为 queue；不调用 `consume_next_step()` 创建空 turn。
- `interrupt` 在 idle 时等价于 queue 或普通 send，建议返回 `mode_effective: "queue"`，让前端可解释。
- 带图片、工作簿上下文、上传文件的消息必须在 dispatch admission 前完成 attachment admission；否则 dispatch rejected，不进 Inbox。
- workbook context 的版本校验仍由后端权威检查；steer/queue 不得绕过 `expected_version`。
- 高风险审批中的消息默认 queue；interrupt 只能中断主 turn，不能删除待审批记录。

## 11. 实施分期

### Phase 0：协议与观测

- 增加枚举、配置字段、dispatch event schema、幂等键。
- 先让当前默认行为映射为 `queue`，不改变现有 UI。
- 补状态日志和 focused tests。

### Phase 1：queue + steer

- 将现有 `next-turn` / `next-step` 接上 dispatch 状态。
- SSE 发送 queued/applied 事件。
- 前端 store 和消息状态落地。
- 设置页配置默认策略和单条覆盖。

### Phase 2：interrupt

- 引入 cooperative cancellation token。
- ToolDispatcher、子代理、工作区事务、Responses 请求逐层接入取消。
- 增加 interrupt timeout 和 non-cooperative 诊断。
- 做真实运行中的工具、审批、文件写入测试。

### Phase 3：恢复与兼容收口

- runtime snapshot/event log 全量覆盖 dispatch。
- 删除非流式入口的隐形 busy queue。
- 迁移旧 `inbox` 快照：无 dispatch id 的旧 item 生成 `legacy-{seq}`，默认按 queue 恢复。

## 12. 验收标准

### 后端

- 同一 `client_message_id` 重试只产生一个 dispatch 和一个用户 turn。
- 运行中发送 steer 不会启动第二个 engine task，并在下一个 step 被消费。
- 运行中发送 queue 能在当前 turn 结束后 FIFO 执行，且 SSE 可见。
- interrupt 不会产生未结算的 tool call；文件写入要么有提交回执，要么有明确 aborted/failed_partial。
- 断线重连、buffer overflow、服务重启后 dispatch 状态可回源且无重复执行。

### 前端

- 默认策略从 `/config/runtime` 正确加载和保存。
- 单条策略覆盖不会污染下一条消息。
- 三种状态在刷新后仍与服务端一致。
- 旧消息历史和新 dispatch 卡片不会重复。

### 测试建议

- Python：`tests/test_agent_inbox.py`、`tests/test_driver.py`、新增 `tests/test_message_dispatch.py`、API 幂等与恢复测试。
- TypeScript：新增 `chat-dispatch-lifecycle.test.ts`、配置读写测试、SSE replay/idempotency 测试。
- 浏览器：真实流式执行中分别发送三种策略，验证消息顺序、按钮状态、刷新恢复和工作簿写入结果。

## 13. 推荐默认值

首发版本建议默认 `steer`，理由是它最贴近“对正在执行的任务补充方向”，不会像 interrupt 一样放大取消风险，也不会让用户误以为消息丢失。对于当前已有审批或问答阻塞、以及 idle 会话，运行时应自动显示实际生效策略：`queue` 或 `send`，而不是假装仍然是 steer。
