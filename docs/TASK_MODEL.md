# AI Router Task 模型设计

**文档版本：** v1.0 Draft  
**对应分支：** v0.8-planning  
**上位文档：** `SRS.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`

## 1. Task 定义

Task 表示 AI Router 对一个用户意图或系统工作单元的完整执行实例。

Task 不等同于一次 HTTP 请求，也不等同于 Conversation。

示例：

- 用户发送一次普通 Chat：一个 Task。
- Auto 三阶段执行：一个 Task。
- Dialogue 六条模型消息：一个 Task。
- 每天 17:00 自动汇总新闻：每次实际运行产生一个 Task。
- 长期监控规则本身不是 Task，每次触发执行产生新的 Task。

## 2. 生命周期

```text
queued
  ↓
running
  ├────────────→ waiting_user
  │                   ↓
  │                 running
  │
  ├────────────→ paused
  │                   ↓
  │                 running
  │
  ├────────────→ done
  ├────────────→ failed
  └────────────→ stopped
```

## 3. 状态定义

### queued
任务已经创建，尚未开始执行。

### running
任务正在执行。

### waiting_user
需要人工确认、补充信息或批准。

### paused
系统主动暂停，可恢复。

### done
任务正常完成。

### failed
任务因不可恢复错误失败。

### stopped
用户或策略要求停止。

终止状态：

```text
done
failed
stopped
```

进入终止状态后默认不继续写入正常执行结果。如需重新执行，创建新 Task，并通过 `parent_task_id` 或 metadata 关联原 Task。

## 4. Stop 语义

当前 Dialogue 已确定采用 graceful stop：

> 点击停止后，不强制终止当前 Provider 调用；当前模型完成输出后，不再执行下一步。

建议 Task 增加：

```text
stop_requested_at
stop_mode
```

stop_mode：

```text
graceful
immediate
```

初期只实现 `graceful`。

`immediate` 未来仅用于明确支持安全取消的 Transport / Tool，不允许用简单杀进程代替。

## 5. Retry 语义

区分两类重试。

### Provider Call Retry
单个模型调用失败后的有限重试。

### Task Retry
整个 Task 从头重新执行。

规则：

- Provider Retry 不创建新 Task。
- 整体重新执行创建新 Task。
- 所有 Retry 写 TaskEvent / ProviderCall。
- 不允许无限重试。
- 默认重试次数必须有上限。

## 6. Resume 语义

`waiting_user` / `paused` 可以恢复。

恢复时保留：

- 原 Task ID
- 已完成步骤
- 当前 Workflow 状态
- 用户补充内容
- 恢复时间
- 恢复操作者

`failed` 默认不原地恢复，而是新建 Task。

## 7. Task 与 Conversation

```text
Conversation
 ├─ Task 1
 ├─ Task 2
 └─ Task 3
```

Conversation 是长期上下文容器；Task 是一次执行。

普通 Chat：

- Conversation 可长期存在。
- 每次用户发送一条消息形成一个 Task。

Dialogue：

- 一次 Dialogue 运行是一个 Task。
- 多条模型输出属于同一 Task。

Auto：

- 一次 Auto 是一个 Task。
- Analysis / Review / Synthesis 是同一 Task 的多个阶段。

## 8. Task 与 Workflow

普通 Chat 可视为隐式简单 Workflow：

```text
Input
 ↓
Model
 ↓
Output
```

Auto：

```text
Analysis
 ↓
Review
 ↓
Synthesis
```

Dialogue：

```text
Provider A
 ↓
Provider B
 ↓
Loop until message limit / stop
```

Task 保存实际运行状态，Workflow 保存流程定义。

## 9. 优先级

初期建议：

```text
normal
high
low
```

暂不实现复杂队列调度。

交互式用户任务默认 `normal`，后台定时任务默认 `low`。

## 10. 超时

区分：

- Provider Call timeout
- Tool Call timeout
- 整体 Task timeout

长 Dialogue / Workflow 不应使用普通 HTTP 长连接维持整个 Task 生命周期。

前端采用 start + status/poll，后续可引入 SSE。

## 11. 输入与输出

Task 原始输入保存为：

```text
original_input
```

Workflow 派生 Prompt 不覆盖原始输入，而进入 Message / StepRun。

Task 输出可保存：

```text
result_summary
final_message_id
result_artifacts
```

完整过程不复制到 `result_summary`。

## 12. 标准错误码

建议：

```text
provider_unavailable
provider_timeout
provider_invalid_response
transport_failed
workflow_failed
tool_failed
approval_rejected
budget_exceeded
user_stopped
internal_error
```

前端可显示用户友好文本，数据库保留标准 error_code。

## 13. 成本

Task 可累计：

```text
api_cost
tool_cost
total_cost
token_input
token_output
```

Web Transport 即使没有直接 API 费用，也记录调用次数和耗时。

## 14. 审计

至少记录：

```text
task_created
task_started
provider_selected
transport_selected
stop_requested
waiting_user
resumed
task_completed
task_failed
task_stopped
```

## 15. v0.8 最小实现

第一版只要求：

- Task SQLite 持久化。
- Chat / Auto / Dialogue 统一创建 Task。
- 状态正确更新。
- 保存开始、结束时间。
- 保存原始输入。
- 保存错误与最终结果摘要。
- 支持历史列表。
- 支持任务详情。
- 支持 graceful stop。
- Gateway 重启后历史仍存在。

暂不要求：

- 分布式队列。
- 多 Worker。
- 任务跨服务器恢复。
- failed 原地恢复。
- 复杂优先级调度。

## 16. 验收案例

### 普通 Chat

```text
queued → running → done
```

### Provider 失败

```text
queued → running → failed
```

### Dialogue 手动停止

```text
queued → running
用户 stop_requested
当前 Provider 完成
→ stopped
```

### Auto 完成

```text
queued → running
analysis
review
synthesis
→ done
```

### Gateway 重启

已完成 Task 不丢失，可从 Task Center 查询。
