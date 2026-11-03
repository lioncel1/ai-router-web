# AI Router 代码结构规范

**版本：v0.8 Phase 1**

## 1. 目标

规范 v0.8 后续代码改造方向，避免功能持续增加导致：

-   Gateway 文件过大。
-   数据库逻辑分散。
-   Provider调用与业务流程耦合。
-   Task状态无法统一管理。

原则：

> 入口轻量化，业务模块化，数据统一化。

------------------------------------------------------------------------

# 2. 推荐目录结构

``` text
ai-router/

├── gateway_app.py

├── task_manager.py

├── database/
│   ├── connection.py
│   ├── models.py
│   ├── repository.py
│   └── migrations/

├── providers/
│   ├── chatgpt/
│   │   ├── web.py
│   │   └── api.py
│   │
│   ├── gemini/
│   │   ├── web.py
│   │   └── api.py
│   │
│   └── router.py

├── workflows/
│   ├── auto.py
│   └── dialogue.py

├── services/
│   ├── conversation_service.py
│   └── history_service.py

└── legacy/
    └── json compatibility
```

------------------------------------------------------------------------

# 3. 模块职责

## gateway_app.py

职责：

-   HTTP接口。
-   参数校验。
-   返回结果。

不负责：

-   数据库操作。
-   Provider细节。
-   Workflow逻辑。

调用：

``` text
Gateway
 ↓
Service / Task Manager
```

------------------------------------------------------------------------

## task_manager.py

核心模块。

负责：

-   创建Task。
-   更新状态。
-   Stop请求。
-   Resume。
-   生命周期管理。

示例：

``` python
create_task()

update_status()

complete_task()

request_stop()
```

------------------------------------------------------------------------

# 4. database模块

## connection.py

负责：

-   SQLite连接。
-   初始化。

------------------------------------------------------------------------

## models.py

定义：

-   Task
-   Message
-   ProviderCall
-   Event

------------------------------------------------------------------------

## repository.py

统一数据访问。

例如：

``` python
save_task()

get_task()

save_message()

save_provider_call()
```

业务代码禁止直接SQL。

------------------------------------------------------------------------

# 5. Provider结构

目标：

统一不同模型调用。

结构：

``` text
Provider

 ├ ChatGPT
 │   ├ Web
 │   └ API
 │
 └ Gemini
     ├ Web
     └ API
```

Provider只负责：

-   调用模型。
-   返回结果。

不负责：

-   保存历史。
-   管理Task。

------------------------------------------------------------------------

# 6. Workflow模块

负责流程。

例如：

Auto：

``` text
Analysis
 ↓
Review
 ↓
Synthesis
```

Dialogue：

``` text
A
 ↓
B
 ↓
Loop
```

Workflow不直接管理数据库。

通过：

``` text
Task Manager
```

记录执行状态。

------------------------------------------------------------------------

# 7. Service层

处理业务能力。

例如：

Conversation Service：

负责：

-   会话导入。
-   会话查询。
-   会话绑定。

History Service：

负责：

-   历史查询。
-   展示数据整理。

------------------------------------------------------------------------

# 8. Legacy兼容

现有：

``` text
gemini_conversations.json

chatgpt_conversations.json

router_calls.jsonl
```

暂时保留。

用途：

-   回滚。
-   数据迁移。
-   问题排查。

禁止继续增加新的业务依赖。

------------------------------------------------------------------------

# 9. 数据流规范

标准流程：

``` text
User

↓

Gateway

↓

Task Manager

↓

Workflow

↓

Provider Router

↓

Provider

↓

Repository

↓

SQLite
```

------------------------------------------------------------------------

# 10. 禁止事项

禁止：

## Gateway直接调用Provider

错误：

``` text
gateway
 |
chatgpt_chat.py
```

------------------------------------------------------------------------

正确：

``` text
gateway

↓

task_manager

↓

provider_router

↓

provider
```

------------------------------------------------------------------------

禁止：

多个模块各自维护状态。

例如：

``` python
ORCHESTRATION_JOBS={}
DIALOGUE_JOBS={}
```

后续统一：

``` text
tasks表
```

------------------------------------------------------------------------

# 11. v0.8实施范围

第一阶段只实现：

-   database模块。
-   Task Manager。
-   Repository。
-   SQLite初始化。
-   Chat接入。

Auto和Dialogue：

保持现状。

待基础稳定后迁移。

------------------------------------------------------------------------

# 12. 验收标准

完成后：

-   Gateway保持简洁。
-   数据库访问统一。
-   Task生命周期统一。
-   Provider可扩展Web/API。
-   后续Workflow扩展无需大规模重构。
