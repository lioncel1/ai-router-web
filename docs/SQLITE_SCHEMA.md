# AI Router SQLite Schema设计方案

**版本：v0.8 Phase 1**

## 1. 目标

将 AI Router 从当前 JSON / JSONL 记录方式，逐步升级为 SQLite
持久化架构。

原则：

-   不破坏 v0.7 功能。
-   保留现有 Conversation 数据。
-   SQLite 作为新增统一数据层。
-   后续可迁移 PostgreSQL。

------------------------------------------------------------------------

## 2. 数据库位置

``` text
data/ai_router.db
```

------------------------------------------------------------------------

## 3. v0.8 第一阶段数据表

``` text
tasks
messages
task_events
provider_calls
```

未来扩展：

``` text
conversations
providers
transports
workflows
workflow_runs
tools
audit_events
cost_records
```

------------------------------------------------------------------------

# 4. tasks表

任务核心表。

``` sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL,
    title TEXT,
    original_input TEXT NOT NULL,
    result_summary TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT,
    metadata_json TEXT
);
```

task_type：

``` text
chat
auto
dialogue
workflow
background
scheduled
```

status：

``` text
queued
running
waiting_user
paused
done
failed
stopped
```

------------------------------------------------------------------------

# 5. messages表

统一保存用户和模型消息。

``` sql
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    task_id TEXT,
    conversation_id TEXT,
    role TEXT NOT NULL,
    provider TEXT,
    transport TEXT,
    content TEXT,
    sequence_no INTEGER,
    created_at TEXT,
    metadata_json TEXT
);
```

role：

``` text
user
assistant
tool
event
```

------------------------------------------------------------------------

# 6. task_events表

记录任务生命周期。

``` sql
CREATE TABLE task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data_json TEXT,
    created_at TEXT NOT NULL
);
```

事件示例：

``` text
task_created
task_started
provider_call_started
provider_call_finished
task_completed
task_failed
stop_requested
task_stopped
```

------------------------------------------------------------------------

# 7. provider_calls表

记录模型调用。

``` sql
CREATE TABLE provider_calls (
    call_id TEXT PRIMARY KEY,
    task_id TEXT,
    provider TEXT,
    transport TEXT,
    action TEXT,
    status TEXT,
    duration_ms INTEGER,
    input_length INTEGER,
    output_length INTEGER,
    quality_ok INTEGER,
    quality_reason TEXT,
    conversation_url TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    metadata_json TEXT
);
```

用于统计：

-   模型调用次数。
-   Web/API成功率。
-   延迟。
-   错误原因。

------------------------------------------------------------------------

# 8. 索引

``` sql
CREATE INDEX idx_tasks_status
ON tasks(status);

CREATE INDEX idx_tasks_created
ON tasks(created_at);

CREATE INDEX idx_messages_task
ON messages(task_id);

CREATE INDEX idx_provider_calls_task
ON provider_calls(task_id);
```

------------------------------------------------------------------------

# 9. 数据迁移策略

现有：

``` text
data/
├── gemini_conversations.json
└── chatgpt_conversations.json

logs/
└── router_calls.jsonl
```

不删除。

新增：

``` text
data/
└── ai_router.db
```

迁移阶段：

## 阶段1

新功能写入 SQLite，同时保留旧日志。

## 阶段2

验证：

-   Task数量。
-   Provider调用数量。
-   成功率。

## 阶段3

逐步降低旧日志依赖。

------------------------------------------------------------------------

# 10. 数据访问层

业务代码禁止直接操作数据库。

建议结构：

``` text
database/

├── connection.py
├── models.py
├── repository.py
└── migrations/
```

调用链：

``` text
Gateway

↓

Task Manager

↓

Repository

↓

SQLite
```

------------------------------------------------------------------------

# 11. Phase 1验收标准

完成后：

-   SQLite数据库创建成功。
-   Chat产生Task记录。
-   Auto产生Task和ProviderCall记录。
-   Dialogue产生Task和Message记录。
-   服务重启后历史数据存在。
-   不影响v0.7已有功能。
