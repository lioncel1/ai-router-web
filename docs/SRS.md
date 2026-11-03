# AI Router 软件需求规格说明书（SRS）

**项目名称：** AI Router  
**文档版本：** v1.0 Draft  
**对应软件基线：** AI Router v0.7  
**文档日期：** 2026-09-23  
**文档状态：** 项目顶层需求基线，后续随需求演进持续维护

---

## 1. 项目概述

### 1.1 项目背景

当前主流 AI 能力分散在不同平台、不同模型和不同使用入口中，例如 ChatGPT、Gemini，以及未来可能接入的 Claude、本地模型和其他 AI 服务。

直接使用这些平台存在以下问题：

- 不同模型之间相互独立，难以协同。
- 用户需要分别维护多个网页、账号和会话。
- Web 模型依赖浏览器登录状态、网页结构和账号状态，存在不稳定性。
- 复杂任务通常需要多个模型反复分析、审查、讨论，而现有聊天软件通常仅提供单模型会话。
- AI 输出主要停留在“回答问题”，缺少任务管理、工作流、工具调用、历史追踪、审批和执行能力。
- 用户入口目前主要依赖浏览器，未来需要扩展到桌面客户端、移动端、飞书、微信或其他通信工具。

因此建设 AI Router，用于在用户和各种 AI 能力之间建立统一的交互、调度、协作与执行中枢。

### 1.2 项目定位

AI Router 不定义为单纯的“多模型聊天网页”。

长期定位为：

> **一个模型无关、入口无关、任务驱动的 AI 智能交互与执行平台。**

系统负责连接不同用户入口、不同模型运行通道和各种工具，并通过路由、工作流、任务管理、多模型协作和人工确认机制，将用户自然语言需求转换为可追踪、可控制、可复用的 AI 任务。

### 1.3 核心原则

1. 模型不是产品入口。
2. Web 不是唯一执行通道。
3. Task 是核心业务对象。
4. Conversation 与 Task 分离。
5. Provider 与 Transport 分离。
6. Workflow 负责流程，Provider 只负责执行模型请求。
7. Router 不应暗中改变用户任务语义。
8. 自动 fallback 必须可配置、可见、可审计。
9. 具有真实副作用的 Tool 必须经过 Policy 控制。
10. 正式版本必须可以回滚、恢复和审计。

---

## 2. 总体建设目标

### 2.1 统一 AI 入口

系统长期应支持多个交互入口：

- Web
- Windows / macOS 客户端
- Android / iOS 客户端
- 飞书
- 微信或其他即时通信工具
- REST / Open API

如果第三方通信软件存在接口、合规或账号限制，应优先采用 AI Router 自有客户端，而不是让核心系统依赖某一个第三方平台。

长期形态应为：

```text
一个 AI Router Backend
+
多个 Client / Adapter
```

### 2.2 统一模型接入

AI Router 应同时支持三类模型执行通道。

#### Web Runtime

当前主要实现方式，例如 ChatGPT Web、Gemini Web。

主要风险包括页面结构变化、登录失效、验证码、账号风控、DOM 变化、浏览器故障和响应提取失败，因此 Web Runtime 不应成为系统唯一运行方式。

#### API Runtime

后续接入 OpenAI API、Gemini API、Anthropic API 及其他兼容 API，主要承担后台任务、稳定自动化、高可靠工作流、高并发、工具调用和无人值守任务。

系统应支持 Token、费用、预算和 API Key 安全管理。

#### Local Runtime

长期预留 Qwen、Llama、DeepSeek 及其他本地或企业私有模型，用于隐私数据、本地处理、低成本任务或企业私有部署。

### 2.3 Provider 与 Transport 分离

系统必须区分 Provider 与 Transport。

例如：

```text
ChatGPT
├── Web Transport
└── API Transport
```

上层任务系统不得依赖具体网页自动化代码。

### 2.4 统一任务入口

AI Router 的核心目标不是只提供“聊天”，而是处理“任务”。

任务类型包括普通问答、复杂分析、多模型讨论、文件处理、定时任务、条件监控、工具执行和人工审批任务，并统一进入 Task Engine 管理。

---

## 3. 当前软件基线（v0.7）

### 3.1 Gemini Web

支持 new、ask、list，以及继续真实 Gemini Web 会话。

### 3.2 ChatGPT Web

支持 new、ask、list，以及普通 `/c/...` 和 `/g/.../c/...` 类型会话。

### 3.3 会话管理

当前支持：

- 查看已有 Conversation
- 选择并继续 Conversation
- 导入已有 Web 会话 URL
- 同一 Provider 内防止会话名称静默覆盖
- pending 会话保护

### 3.4 Auto 模式

```text
原始任务
 ↓
Gemini Analysis
 ↓
ChatGPT Review
 ↓
ChatGPT Synthesis
 ↓
最终答案
```

Auto 是未来 Workflow Engine 的早期实现。

### 3.5 Dialogue 模式

```text
用户 Prompt
 ↓
模型 A
 ↓
模型 B
 ↓
模型 A
 ↓
模型 B
...
```

当前规则：

- 发问模型和对话模型由用户选择。
- 默认 6 条模型消息。
- 用户可自定义消息数量。
- A 输出一次计 1 条，B 输出一次计 1 条。
- A→B 完成一轮交替等于 2 条消息。
- 点击停止后，当前模型正常完成输出，再停止后续传递。
- Router 不注入隐藏角色、身份、轮数或任务 Prompt。
- 第一条为用户原始 Prompt。
- 后续只原样传递上一模型回答。

### 3.6 网关与安全

当前具备 FastAPI Gateway、Nginx 反向代理、Basic Auth、Fail2Ban、Provider Runtime 与公网入口隔离、Gateway 仅监听 `127.0.0.1:8765`、用户级 systemd 生命周期管理。

---

## 4. 功能需求

### FR-001 单模型对话

系统应支持新建、继续、导入和管理模型会话；长期支持重命名、搜索、归档、删除和历史查看。

### FR-002 Conversation 管理

长期至少应包含：

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
```

### FR-003 Message 历史

系统应保存并展示用户输入、模型输出、系统事件、工具事件和人工审批事件。

### FR-004 Task 模型

Task 至少包含：

```text
task_id
user_id
source
task_type
original_input
workflow_id
provider_policy
transport_policy
status
started_at
finished_at
result
error
cost
audit_metadata
```

统一状态建议：

```text
queued
running
waiting_user
paused
done
failed
stopped
```

### FR-005 Task Center

统一管理普通 Chat、Auto、Dialogue、Workflow、后台任务、定时任务和条件监控任务，并支持查看、搜索、筛选、停止、继续、归档、重新执行和查看执行过程。

### FR-006 Workflow Engine

未来应将 Auto、Dialogue 等流程抽象为 Workflow，支持：

- Model Node
- Tool Node
- Condition Node
- Parallel Node
- Human Approval Node
- Delay Node
- Notification Node
- Sub-workflow Node

### FR-007 Dynamic Routing

系统应根据任务特征、模型最近成功率、响应时间、历史质量、费用、通道健康状态、上下文需求、工具能力和用户策略动态选择 Provider / Transport，并保存路由原因。

### FR-008 Transport 策略

用户应可配置 Web 优先、API 优先、自动选择或指定通道。

Web 失败时应允许停止、询问用户或自动切换 API。发生切换时必须记录原计划通道、实际执行通道、切换原因、失败原因、费用和耗时，不得静默 fallback。

### FR-009 多模型协作

支持单模型、顺序编排、双模型对话、多模型评审、并行分析、投票、综合和专家角色协作。模型角色和提示词应由用户或 Workflow 明确控制。

### FR-010 Tool Runtime

未来支持文件、浏览器、搜索、邮件、日历、Excel、数据库、Python、企业内部系统、HTTP API、消息发送和文档生成。所有具有外部副作用的工具必须经过权限与 Policy 控制。

### FR-011 Human-in-the-loop

重要操作应支持：

```text
AI制定计划
 ↓
等待用户批准
 ↓
执行
```

典型场景包括发送邮件、修改正式文件、执行交易、删除数据、调用付费服务和修改生产系统。

### FR-012 Knowledge

Knowledge 与聊天历史分离，用于保存 PDF、Word、项目资料、企业文档、数据库和外部知识源等材料。

### FR-013 Memory

Memory 与 Conversation、Knowledge 分离，用于保存长期偏好、项目状态、历史决定、重要事实和工作习惯，并具备来源、更新时间、权限和删除能力。

### FR-014 多模态

统一消息模型预留：

```text
text
image
file
audio
video
structured_data
```

Message 至少包含 `type`、`content`、`attachments`、`metadata`。

### FR-015 通知与后台任务

未来支持定时任务、条件监控、长时间后台任务、完成通知、失败通知和等待审批通知。

通知出口可包括 AI Router Client、Web、飞书、微信适配器、Email 和其他消息服务。

### FR-016 AI Router Client

自有客户端不应只做网页套壳，长期至少包含：

```text
Chat
Tasks
History
Workflows
Files
Notifications
Providers
Settings
```

Web、Desktop、Mobile 共用同一 Backend API。

---

## 5. 非功能需求

### NFR-001 稳定性

单个 Provider、Transport、Task 失败不得导致 Gateway 整体崩溃。

### NFR-002 可恢复性

长期任务和任务状态必须持久化，服务重启后不得完全丢失。

### NFR-003 可扩展性

新增模型、Transport、入口、Tool、Workflow 节点时，不应要求大规模修改核心代码。

### NFR-004 可观测性

记录成功率、失败率、延迟、调用次数、错误类型、通道健康状态、Token、API 费用和 Workflow 成功率。

### NFR-005 安全性

系统应保证：

- Provider 登录凭据不进入普通日志。
- API Key 采用安全凭据管理。
- Gateway 不直接暴露 Provider Runtime。
- 所有外部入口具有认证。
- 支持 HTTPS。
- 支持登录失败限制。
- 支持权限控制和审计日志。
- 敏感操作需要额外确认。
- 任务日志与业务内容日志分离。

### NFR-006 成本管理

API 与 Agent 自动化任务支持单任务、每日、每月和 Provider 预算，支持 Token 数量、费用预估、费用告警和超预算停止。

### NFR-007 可审计性

系统应能够回答：

```text
谁发起？
从哪个入口发起？
什么时候？
执行了什么 Workflow？
用了哪个 Provider？
走 Web、API 还是 Local？
调用了哪些 Tool？
是否发生重试？
是否发生 Transport 切换？
谁批准了关键动作？
结果是什么？
费用是多少？
```

### NFR-008 可维护性

正式版本必须保留 CHANGELOG、Git commit、annotated tag 和 bundle / archive。

---

## 6. 项目非目标

当前和中期不以以下事项为主要目标：

- 自研基础大语言模型
- 替代微信、飞书等通信软件本身
- 自己实现浏览器引擎
- 取消所有人工控制
- 强制所有任务使用 Agent
- 在没有必要时引入复杂分布式架构

核心始终是：

> **连接、路由、编排、执行和管理 AI 能力。**

---

## 7. 版本路线建议

### v0.7 — 已完成

Web Provider 基础、ChatGPT / Gemini、Auto、Conversation Management、Dialogue、版本管理规范。

### v0.8 — Task & History

建议目标：统一 Task 数据模型、任务持久化、Message 历史、Task Center、Dialogue 历史、Auto 历史、任务详情页。

### v0.9 — Provider Architecture

建议目标：Provider / Transport 正式解耦、Web Runtime 模块化、API Runtime、Transport Health、Token / 费用统计、Transport 策略。

### v0.10 — Workflow

建议目标：Workflow Engine、Model Node、Condition Node、Human Approval、Tool 基础框架。

### 后续阶段

逐步推进 Dynamic Routing、Knowledge / Memory、Tools、Notifications、Third-party Messaging、AI Router Client、Local Runtime 和多用户平台化。

具体版本号可根据实现复杂度调整，不将长期路线写死。
