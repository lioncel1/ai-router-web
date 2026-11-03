# AI Router 统一数据模型设计

**文档版本：** v1.0 Draft  
**对应分支：** v0.8-planning  
**上位文档：** `SRS.md`、`ARCHITECTURE.md`

## 1. 设计原则

1. Task 是核心执行对象。
2. Conversation 表示连续对话上下文，不等同于 Task。
3. Message 是最小通信记录，可同时关联 Conversation 与 Task。
4. Provider 表示模型/服务提供方，Transport 表示实际调用通道。
5. Workflow 是流程定义，WorkflowRun 是一次实际运行。
6. 外部执行行为必须可审计。
7. 业务数据、运行日志、审计日志和统计数据分离。
8. 首期采用 SQLite，结构允许后续迁移 PostgreSQL。
9. 所有主实体使用稳定内部 ID，不使用外部 URL、网页标题或显示名称作为主键。
10. 时间统一保存为带时区的 ISO 8601。

## 2. 核心实体关系

```text
User
 ├─ Conversation
 │   └─ Message
 │
 └─ Task
     ├─ Message
     ├─ WorkflowRun
     │   └─ WorkflowStepRun
     ├─ ProviderCall
     ├─ ToolCall
     ├─ TaskEvent
     └─ AuditEvent

Provider
 └─ Transport
     └─ ProviderCall

Workflow
 └─ WorkflowRun

Tool
 └─ ToolCall
```

核心关系：

- 一个 Conversation 可以产生多个 Task。
- 一个 Task 可以属于 Conversation，也可以是独立后台任务。
- Message 可以同时关联 Conversation 与 Task。
- 一个 Task 可以产生多个 ProviderCall。
- 一个 WorkflowRun 属于一个 Task。
- ProviderCall 必须明确 Provider 与 Transport。
- ToolCall 必须关联 Task，并接受 Policy / Approval 控制。

## 3. User

建议字段：

```text
user_id
display_name
status
created_at
updated_at
metadata_json
```

当前单用户环境可使用固定内部用户，例如 `local-admin`。未来 Web、App、飞书、微信等外部身份统一映射到内部 `user_id`。

## 4. Conversation

建议字段：

```text
conversation_id
user_id
display_name
provider_id
transport_id
external_conversation_id
external_url
status
created_at
updated_at
last_message_at
metadata_json
```

状态建议：

```text
active
pending
archived
invalid
deleted
```

规则：

- `conversation_id` 为内部稳定 ID。
- `external_url` 仅用于定位外部 Web 会话，不作为主键。
- 导入 ChatGPT / Gemini 会话时创建内部 Conversation。
- Web、API、Local 会话统一使用该模型。

## 5. Message

建议字段：

```text
message_id
conversation_id nullable
task_id nullable
parent_message_id nullable
role
provider_id nullable
transport_id nullable
content_type
content
attachments_json
sequence_no
created_at
metadata_json
```

role：

```text
user
assistant
system
tool
event
```

content_type：

```text
text
image
file
audio
video
structured_data
```

规则：

- 普通 Chat 的 Message 关联 Conversation 与 Task。
- Auto 阶段输入输出关联同一 Task。
- Dialogue 每次模型发言保存为一条 Message。
- Tool 结果可使用 `role=tool`。
- Provider 网页历史不能作为唯一消息来源。

## 6. Task

建议字段：

```text
task_id
user_id
conversation_id nullable
task_type
title
source
original_input
status
priority
workflow_id nullable
workflow_version nullable
provider_policy_json
transport_policy_json
cost_limit nullable
created_at
started_at nullable
finished_at nullable
updated_at
result_summary nullable
error_code nullable
error_message nullable
metadata_json
```

task_type：

```text
chat
auto
dialogue
workflow
background
scheduled
monitor
tool
```

status：

```text
queued
running
waiting_user
paused
done
failed
stopped
```

source：

```text
web
desktop
mobile
feishu
wechat
api
system
```

Task 必须持久化，服务重启后仍可查询。

## 7. TaskEvent

建议字段：

```text
event_id
task_id
event_type
event_data_json
created_at
```

典型事件：

```text
task_created
task_started
message_created
provider_call_started
provider_call_completed
retry_started
stop_requested
task_stopped
task_failed
task_completed
waiting_user
resumed
```

## 8. Provider

建议字段：

```text
provider_id
provider_name
provider_type
status
capabilities_json
created_at
updated_at
metadata_json
```

Provider 表示逻辑模型/服务，不直接表示 Web 或 API。

## 9. Transport

建议字段：

```text
transport_id
provider_id
transport_type
display_name
status
priority
config_ref
supports_streaming
supports_tools
supports_files
created_at
updated_at
metadata_json
```

transport_type：

```text
web
api
local
```

示例：

```text
chatgpt-web
chatgpt-api
gemini-web
gemini-api
```

## 10. ProviderCall

建议字段：

```text
provider_call_id
task_id
workflow_run_id nullable
workflow_step_run_id nullable
conversation_id nullable
provider_id
transport_id
action
request_message_id nullable
response_message_id nullable
status
attempt_no
started_at
finished_at
duration_ms
external_url nullable
input_length nullable
output_length nullable
token_input nullable
token_output nullable
cost_amount nullable
currency nullable
quality_ok nullable
quality_reason nullable
error_code nullable
error_message nullable
metadata_json
```

后续成功率、延迟、费用、重试和 Web/API 对比均从 ProviderCall 汇总。

## 11. Workflow / WorkflowRun / WorkflowStepRun

Workflow 建议字段：

```text
workflow_id
name
version
description
status
definition_json
created_at
updated_at
metadata_json
```

WorkflowRun：

```text
workflow_run_id
workflow_id
workflow_version
task_id
status
started_at
finished_at
current_step_id nullable
context_json
result_json
error_message nullable
```

WorkflowStepRun：

```text
step_run_id
workflow_run_id
step_id
step_type
sequence_no
status
started_at
finished_at
input_json
output_json
error_message nullable
metadata_json
```

step_type：

```text
model
tool
condition
parallel
approval
delay
notification
subworkflow
loop
```

## 12. Tool / ToolCall / Approval

Tool：

```text
tool_id
name
category
status
risk_level
requires_approval
capabilities_json
config_ref
created_at
updated_at
metadata_json
```

risk_level：

```text
read_only
low
medium
high
critical
```

ToolCall：

```text
tool_call_id
task_id
workflow_run_id nullable
workflow_step_run_id nullable
tool_id
status
request_json
response_json
started_at
finished_at
duration_ms
approval_id nullable
error_message nullable
metadata_json
```

Approval：

```text
approval_id
task_id
workflow_run_id nullable
tool_call_id nullable
approval_type
status
requested_at
decided_at nullable
requested_by
decided_by nullable
request_summary
decision_comment nullable
metadata_json
```

## 13. AuditEvent

建议字段：

```text
audit_id
user_id nullable
task_id nullable
event_type
actor_type
actor_id
object_type
object_id
summary
details_json
created_at
```

审计日志不复制全部聊天正文。

## 14. CostRecord 与 Attachment

CostRecord：

```text
cost_id
task_id
provider_call_id nullable
tool_call_id nullable
provider_id nullable
transport_id nullable
amount
currency
token_input nullable
token_output nullable
created_at
metadata_json
```

Attachment：

```text
attachment_id
user_id
task_id nullable
message_id nullable
file_name
content_type
storage_path
size_bytes
sha256
created_at
metadata_json
```

## 15. v0.8 最小实现范围

v0.8 第一阶段 SQLite 只实现：

```text
tasks
messages
task_events
provider_calls
```

目标：

1. Chat / Auto / Dialogue 都产生 Task。
2. 每次模型输出保存为 Message。
3. Task 生命周期保存为 TaskEvent。
4. Provider 调用保存为 ProviderCall。
5. Gateway 重启后历史 Task 仍可查看。
6. 现有 Conversation JSON 与 JSONL 日志继续兼容，不立即删除。

## 16. 迁移原则

1. 不直接修改或删除现有 Conversation JSON。
2. 新数据库上线初期采用兼容读取或双写。
3. 旧 JSON / JSONL 不自动删除。
4. 迁移前必须备份。
5. Schema 带 `schema_version`。
6. 迁移脚本必须可验证、可重复执行。
7. 迁移失败不得覆盖原数据。

## 17. v0.8 数据层验收

- 普通 Chat 创建 Task。
- Auto 创建一个 Task 和多个 ProviderCall / Message。
- Dialogue 创建一个 Task，并按模型消息数保存 Message。
- Task 正确进入 done / failed / stopped。
- Gateway 重启后历史 Task 可查询。
- Task Center 能读取历史。
- 不破坏 v0.7 Conversation 与 Web Provider 能力。
