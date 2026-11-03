# AI Router Workflow 模型设计

**文档版本：** v1.0 Draft  
**对应分支：** v0.8-planning  
**上位文档：** `SRS.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`TASK_MODEL.md`

## 1. Workflow 定义

Workflow 描述 AI Router 如何把一个 Task 分解为若干执行节点，并规定节点之间的顺序、条件、循环、并行和人工确认关系。

Workflow 是“定义”，WorkflowRun 是“某次实际执行”。

## 2. 设计目标

1. Auto 不再永久写死在 `orchestrator.py`。
2. Dialogue 不再永久写死为特殊循环。
3. 新增流程时优先组合节点，而不是复制一套新 Engine。
4. Workflow 与具体 Provider 解耦。
5. Workflow 可以引用路由策略，而不是强制固定模型。
6. 每个节点均可审计、可重试、可停止。
7. Workflow Definition 必须版本化。

## 3. 节点类型

### Model Node

调用一个模型。

参数示例：

```text
provider_policy
transport_policy
prompt_template
conversation_mode
retry_policy
timeout
```

### Tool Node

调用外部工具。

### Condition Node

根据结构化条件选择分支。

### Parallel Node

并行执行多个子节点。

### Human Approval Node

暂停 Task，进入 `waiting_user`。

### Delay Node

等待指定时间后继续。

### Notification Node

发送通知。

### Sub-workflow Node

调用另一个 Workflow。

### Loop Node

按次数或条件循环执行一组节点。

## 4. Workflow Definition

建议使用 JSON 结构，初期不设计复杂 DSL。

示例：

```json
{
  "workflow_id": "auto-v1",
  "version": 1,
  "name": "Auto Analysis Review Synthesis",
  "start": "analysis",
  "nodes": {
    "analysis": {
      "type": "model",
      "next": "review"
    },
    "review": {
      "type": "model",
      "next": "synthesis"
    },
    "synthesis": {
      "type": "model",
      "next": null
    }
  }
}
```

## 5. Auto 映射

当前 Auto：

```text
Gemini Analysis
 ↓
ChatGPT Review
 ↓
ChatGPT Synthesis
```

Workflow 化后：

```text
ModelNode(analysis)
 ↓
ModelNode(review)
 ↓
ModelNode(synthesis)
```

当前固定 Provider 映射可以作为 v1 Workflow 配置保留。

未来可改为：

```text
role=analysis
routing_policy=best_available
```

而无需修改 Workflow 结构。

## 6. Dialogue 映射

当前 Dialogue：

```text
用户 Prompt
 ↓
A
 ↓
B
 ↓
A
 ↓
B
```

Workflow 化后建议：

```text
Loop Node
 ├─ ModelNode A
 └─ ModelNode B
```

循环控制变量：

```text
max_messages
completed_messages
stop_requested
```

消息计数继续采用：

- 每个模型输出 = 1 条。
- A + B = 2 条。

Router 不注入隐藏 Prompt。

## 7. Context

WorkflowRun 维护结构化 context。

示例：

```json
{
  "original_input": "...",
  "last_message_id": "...",
  "completed_messages": 4,
  "selected_provider": "...",
  "selected_transport": "...",
  "variables": {}
}
```

Context 不应无限复制全部历史正文。

正文保存在 Message Store。

## 8. Prompt 规则

Workflow 可以定义 Prompt Template，但必须区分：

- 用户输入
- Workflow 系统提示
- 上一步输出
- 工具结果

任何隐藏 Prompt 都必须属于明确的 Workflow 定义，并可查看、可审计。

Dialogue 默认 Workflow 不增加隐藏 Prompt。

## 9. 路由策略

Model Node 支持三类路由。

### 固定 Provider + Transport

```text
provider=gemini
transport=web
```

### 固定 Provider，自动 Transport

```text
provider=chatgpt
transport=auto
```

### 全动态

```text
provider=auto
transport=auto
```

Routing Engine 返回实际执行 Provider + Transport，并记录原因。

## 10. Retry Policy

节点级重试策略示例：

```text
max_attempts = 2
retry_on = transport_failed, timeout
```

Workflow 层不得无限重试。

所有重试结果写入 ProviderCall。

## 11. Stop Policy

Workflow 每个节点结束后检查 `stop_requested`。

默认 graceful stop：

```text
当前节点完成
 ↓
检查 stop_requested
 ↓
停止，不启动下一节点
```

与 v0.7 Dialogue 行为一致。

## 12. Human Approval

典型流程：

```text
Model Node
 ↓
Human Approval Node
 ↓ approved
Tool Node
```

拒绝：

```text
Human Approval Node
 ↓ rejected
Task stopped / failed according to policy
```

## 13. Parallel

未来支持：

```text
         ┌→ Gemini
Input ───┤
         └→ ChatGPT
              ↓
           Synthesis
```

Parallel 节点应定义：

- 最大并发
- 任一失败是否终止
- 是否允许部分成功
- 汇总节点

v0.8 暂不实现。

## 14. Workflow Versioning

Workflow 行为变化必须产生新版本。

例如：

```text
auto-v1
auto-v2
```

已运行 Task 始终保存执行时的 `workflow_id + version`。

禁止用新版定义覆盖历史运行语义。

## 15. WorkflowRun

一次实际运行至少保存：

```text
workflow_run_id
workflow_id
workflow_version
task_id
status
current_step_id
started_at
finished_at
context_json
```

## 16. WorkflowStepRun

每个节点运行保存：

```text
step_run_id
workflow_run_id
step_id
step_type
status
input_json
output_json
started_at
finished_at
error_message
```

Provider 的详细调用仍进入 ProviderCall，不重复塞进 StepRun。

## 17. v0.8 与 Workflow 的关系

v0.8 不建议马上重写 Auto / Dialogue 为完整 Workflow Engine。

v0.8 先完成：

```text
Task
Message
TaskEvent
ProviderCall
History
```

同时在新数据模型中预留：

```text
workflow_id
workflow_run_id
workflow_step_run_id
```

这样后续引入 Workflow 时不需要重构历史数据。

## 18. Workflow Engine 第一版建议范围

未来第一版仅支持：

```text
Model Node
Condition Node
Loop Node
Human Approval Node
```

之后再增加：

```text
Tool
Parallel
Delay
Notification
Sub-workflow
```

避免一次实现过多。

## 19. 验收目标

Workflow Engine 上线后应满足：

- Auto 可由 Workflow Definition 表达。
- Dialogue 可由 Loop Workflow 表达。
- Workflow 定义与实际运行记录分离。
- 每个节点可查看输入、输出、耗时和状态。
- 可在节点边界 graceful stop。
- Workflow 版本可追踪。
- 修改 Workflow 不影响历史 Task 的解释。
