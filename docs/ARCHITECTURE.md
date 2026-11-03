# AI Router 总体架构设计方案（HLD）

**项目名称：** AI Router  
**文档版本：** v1.0 Draft  
**当前实现基线：** AI Router v0.7  
**文档日期：** 2026-09-23  
**文档状态：** 项目总体架构基线，后续随实现演进持续维护

---

## 1. 架构目标

AI Router 的总体架构必须围绕以下解耦目标设计：

1. 入口与核心解耦。
2. 任务与模型解耦。
3. Provider 与 Transport 解耦。
4. AI 决策与实际执行解耦。
5. Conversation、Task、Workflow、Message 各自独立建模。
6. 所有长期能力尽量采用 API First 设计。

目标是确保任何一个入口、模型、执行通道或工具发生变化时，都不会迫使整个系统重构。

---

## 2. 长期总体架构

```text
                         用户
                          │
          ┌───────────────┼────────────────┐
          │               │                │
         Web         Desktop/Mobile     第三方IM
          │               │           微信/飞书等
          └───────────────┼────────────────┘
                          │
                  Interaction Gateway
                          │
                    Authentication
                          │
                    AI Router Core
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
    Chat Service      Task Engine      Workflow Engine
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │
                    Routing Engine
                          │
               Provider Abstraction Layer
                          │
      ┌───────────────────┼────────────────────┐
      │                   │                    │
 Web Runtime         API Runtime          Local Runtime
      │                   │                    │
ChatGPT/Gemini   OpenAI/Gemini/...      Local Models
      │                   │                    │
      └───────────────────┼────────────────────┘
                          │
                     Tool Runtime
                          │
       Files / Browser / DB / Mail / Search /
       Python / Enterprise API / Messaging
                          │
                    Persistence Layer
                          │
 Conversations / Messages / Tasks / Workflows
 Knowledge / Memory / Metrics / Audit / Cost
```

---

## 3. 当前 v0.7 架构基线

当前系统主要由以下组件构成：

```text
Nginx
 ↓
FastAPI Gateway
 ↓
Chat / Auto / Dialogue
 ↓
Provider Runtime
 ↓
Gemini Web / ChatGPT Web
 ↓
Conversation Store / Logs
```

当前实现特点：

- Gateway 监听 `127.0.0.1:8765`
- Nginx 提供公网入口
- Basic Auth + Fail2Ban
- Gemini / ChatGPT 通过独立 Chrome Profile 运行
- Provider Runtime 已具备统一调用入口
- Auto 使用固定编排流程
- Dialogue 使用异步后台 Job + 前端轮询
- Conversation Store 当前主要采用 JSON
- Analytics / operational logs 主要采用 JSONL
- Gateway 生命周期由 user systemd 管理

现有架构作为稳定运行基线保留，后续逐步演进，不一次性推倒重写。

---

## 4. Interaction Gateway

Interaction Gateway 负责所有用户入口适配。

长期入口包括：

```text
Web Adapter
Desktop App Adapter
Mobile App Adapter
Feishu Adapter
WeChat Adapter
REST API
```

所有入口最终转换为统一 Message。

示例：

```json
{
  "source": "feishu",
  "user_id": "xxx",
  "conversation_id": "xxx",
  "type": "text",
  "content": "分析这个方案",
  "attachments": []
}
```

AI Router Core 不应关心消息来自 Web、App 还是第三方 IM。

---

## 5. AI Router Core

Core 只处理业务语义，不直接控制 Chrome 或具体模型 API。

建议逐步形成以下服务：

```text
Chat Service
Conversation Service
Message Service
Task Service
Workflow Service
Routing Service
Policy Service
Notification Service
```

---

## 6. Task Engine

Task 是长期架构中的核心实体。

Task Engine 负责：

```text
创建
排队
执行
暂停
等待用户
停止
恢复
完成
失败
```

Auto、Dialogue、未来 Agent Workflow 均应产生 Task。

长期任务不得只保存在类似以下内存对象中：

```python
DIALOGUE_JOBS = {}
```

必须持久化。

---

## 7. Workflow Engine

Workflow 用于描述多个执行节点之间的关系。

建议支持：

```text
Model Node
Tool Node
Condition Node
Parallel Node
Human Approval Node
Delay Node
Notification Node
Sub-workflow Node
```

当前 Auto：

```text
Analysis
 ↓
Review
 ↓
Synthesis
```

长期应由 Workflow 定义，而不是永久写死在 Orchestrator 中。

当前 Dialogue：

```text
A
 ↓
B
 ↓
A
 ↓
B
```

长期应演变为 Loop Workflow。

---

## 8. Dialogue Engine

v0.7 继续保留独立实现。

其核心规则为：

```text
current_message = 用户 Prompt

while message_count < limit:
    answer = provider(current_message)
    save(answer)
    current_message = answer
    switch_provider()
```

设计约束：

- Router 不增加隐藏角色 Prompt。
- 第一条为用户原始输入。
- 后续只原样传递上一模型输出。
- 停止请求在当前模型完成后生效。
- 消息数量按模型输出次数计数。

---

## 9. Routing Engine

Routing Engine 负责两个层面的选择。

### 9.1 Provider Selection

例如：

```text
Gemini
ChatGPT
Claude
Local Model
```

### 9.2 Transport Selection

例如：

```text
Web
API
Local
```

因此最终执行目标应表达为：

```text
Provider + Transport
```

例如：

```text
ChatGPT + Web
ChatGPT + API
Gemini + Web
Gemini + API
```

未来动态路由不应固定模型角色，而应依据任务特征、历史表现、健康状态、成本和能力进行选择。

---

## 10. Provider Abstraction Layer

上层统一调用标准接口，不直接依赖：

```text
chatgpt_chat.py
gemini_chat.py
```

建议长期形成类似：

```python
ProviderRequest(
    provider="chatgpt",
    transport="web",
    conversation_id="...",
    message="..."
)
```

统一返回：

```python
ProviderResponse(
    success=True,
    content="...",
    conversation_id="...",
    external_url="...",
    latency_ms=1234,
    usage={},
    error=None
)
```

---

## 11. Web Runtime

现有 v0.7 的 Web 能力逐步归入该层。

主要模块包括：

```text
Chrome Profile Manager
Browser Session
DOM Interaction
Response Detection
Conversation URL
Quality Validation
Provider Lock
```

Web Runtime 独立于 API Runtime。

必须继续保持：

- Web Provider 失败不等于整个 Router 失败。
- 网页动态标题、Thinking 状态等不得误判为最终回答。
- Provider Success 需要同时满足执行、回答、URL / 会话证据等条件。
- 不使用静默 fallback。

---

## 12. API Runtime

后续新增独立模块。

负责：

```text
API认证
请求执行
流式输出
Token统计
费用统计
Rate Limit
重试
错误映射
```

API Key 不得进入普通日志。

API Runtime 与 Web Runtime 共享统一 ProviderResponse。

---

## 13. Local Runtime

作为长期预留接口，可连接：

```text
Ollama
vLLM
OpenAI-compatible endpoint
其他本地 Inference Server
```

Local Runtime 应与 Web / API Runtime 使用相同上层抽象。

---

## 14. Transport Manager

负责执行通道管理。

示例状态：

```text
ChatGPT Web
health = healthy
latency = 28s
recent_failure_rate = 5%

OpenAI API
health = healthy
latency = 3s
cost = ...
```

职责包括：

- 健康状态
- 可用性
- 限流
- 预算
- 重试
- Transport 切换策略
- 费用保护

---

## 15. Policy Engine

Policy Engine 负责系统级约束，避免把关键控制交给模型。

例如：

```text
允许自动 fallback 吗？
单任务费用上限是多少？
哪些 Tool 必须审批？
是否允许使用 Web？
是否允许使用 API？
是否允许联网？
最大 Dialogue 消息数是多少？
```

---

## 16. Conversation Service

长期 Conversation 不再只依赖名称和 URL。

建议数据结构：

```text
conversation_id
display_name
provider
transport
external_id
external_url
created_at
updated_at
status
metadata
```

Conversation 负责“连续对话上下文”，不等同于 Task。

---

## 17. Message Store

用于保存实际消息。

统一角色建议：

```text
user
assistant
system
tool
event
```

每条消息至少应包含：

```text
message_id
conversation_id
task_id
role
provider
transport
content_type
content
attachments
created_at
metadata
```

这是实现历史聊天页面和任务回放的基础。

---

## 18. Storage Architecture

当前继续使用：

```text
JSON
JSONL
```

作为过渡方案。

进入 Task Center 阶段后，建议优先采用：

```text
SQLite
```

理由：

- 当前服务器规模较小
- 当前主要为单用户
- 部署简单
- 事务能力优于 JSON
- 后续可迁移 PostgreSQL

未来多用户、多实例时迁移：

```text
PostgreSQL
```

建议逻辑表：

```text
users
conversations
messages
tasks
task_events
workflow_runs
provider_calls
providers
transports
tool_calls
audit_events
cost_records
```

---

## 19. Knowledge Layer

Knowledge 与 Conversation History 分离。

未来支持：

```text
Project Documents
User Files
External Knowledge Sources
Structured Database
Vector Index
```

模型通过统一 Retrieval 接口访问。

---

## 20. Memory Layer

Memory 与 Conversation、Knowledge 分离。

负责：

```text
长期偏好
项目状态
历史决定
重要事实
工作习惯
```

所有长期记忆必须具备：

```text
来源
更新时间
权限
删除能力
```

---

## 21. Tool Runtime

Tool Runtime 是实际执行能力的安全边界。

正确关系：

```text
Model
 ↓
请求 Tool
 ↓
Policy Engine
 ↓
必要时 Human Approval
 ↓
Tool Runtime
 ↓
真实系统
```

模型不得直接拥有无限制系统权限。

---

## 22. Human Approval

Human Approval 是 Workflow 的标准节点，而不是临时补丁。

适用于：

- 发送外部消息
- 修改正式文件
- 删除数据
- 执行交易
- 付费调用
- 生产系统变更

任务状态进入：

```text
waiting_user
```

获得批准后继续执行。

---

## 23. Notification Service

Notification Service 负责异步通知。

未来输出渠道：

```text
Web
AI Router Client
Feishu
WeChat Adapter
Email
其他消息服务
```

Task Engine 只产生标准事件，例如：

```text
Task Completed
Task Failed
Approval Required
```

不关心具体通知渠道。

---

## 24. Client Architecture

自研客户端采用 API First。

```text
Web
Desktop
Mobile
   ↓
AI Router API
   ↓
Backend
```

客户端不直接访问 ChatGPT / Gemini。

所有客户端共享：

```text
Chat
History
Tasks
Workflows
Files
Provider Status
Notifications
Settings
```

---

## 25. Security Architecture

长期结构：

```text
Internet
 ↓
HTTPS
 ↓
Authentication
 ↓
Interaction Gateway
 ↓
Authorization / Policy
 ↓
AI Router Core
 ↓
Runtime / Tools
```

Web Runtime 的以下组件不得直接暴露公网：

```text
Chrome
CDP
8765
Browser Profiles
Provider Locks
```

当前 Basic Auth + Fail2Ban 作为阶段性方案保留，后续补充 HTTPS 和更完善的身份认证体系。

---

## 26. Audit Architecture

建立独立审计事件流。

主要事件：

```text
task_created
provider_selected
transport_selected
provider_failed
retry_started
fallback_requested
fallback_applied
tool_requested
approval_required
approval_granted
tool_executed
task_completed
task_failed
task_stopped
```

业务内容日志与系统审计日志必须分离。

---

## 27. Observability

统一采集：

```text
Provider可用率
Transport可用率
平均响应时间
P95延迟
任务成功率
错误类型
重试率
Token
费用
Workflow成功率
```

这些数据未来既供运维使用，也供 Dynamic Router 使用。

---

## 28. Cost Control

API Runtime 和自动化 Workflow 必须受到预算约束。

至少支持：

```text
单任务预算
每日预算
每月预算
Provider预算
超预算通知
超预算停止
```

Dialogue、多模型评审和长 Workflow 需要特别关注调用次数和成本。

---

## 29. 版本与发布架构

正式版本统一采用：

```text
代码变更
 ↓
CHANGELOG
 ↓
Git commit
 ↓
annotated tag
 ↓
Git bundle / archive
```

当前已正式归档：

```text
v0.6
v0.7
```

后续正式版本延续同一规则。

---

## 30. 架构迁移路线

不为追求“最终架构”一次性重写现有系统。

推荐迁移顺序：

```text
v0.7
稳定 Web 系统基线

 ↓

统一 Task / Message 数据模型
任务持久化
历史中心

 ↓

Provider / Transport 解耦

 ↓

API Runtime

 ↓

Workflow 抽象

 ↓

Dynamic Routing

 ↓

Tools / Knowledge / Memory

 ↓

Client / IM Adapter

 ↓

多用户与平台化
```

每一步均要求：

- 保持上一版本可用
- 可回滚
- 可验收
- 有版本说明
- 不破坏稳定 Provider Runtime

---

## 31. 当前阶段建议

下一阶段不应继续直接在 `gateway_app.py` 上堆叠大量业务功能。

在继续开发前，优先完成以下详细设计：

```text
统一数据模型
Task 模型
Workflow 模型
```

其中 v0.8 建议首先解决：

```text
Task持久化
Message历史
Task Center
历史查看
```

然后再进入 Provider / Transport 的正式解耦与 API Runtime。
