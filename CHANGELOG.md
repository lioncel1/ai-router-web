# AI Router Changelog

## v0.9

### 新增
- Auto 三阶段任务接入 SQLite 持久化。
- Dialogue 双模型对话接入 SQLite 持久化。
- 记录各阶段模型调用、执行结果和停止请求。
- Gateway 重启后支持查询历史任务状态。

### 稳定性
- 未确认完成的任务不会在重启后自动重发。
- Dialogue 在当前模型回答结束后安全停止。
- 新增相关自动化回归测试。

### 已知限制
- 执行中的 Web 模型请求不支持断点续跑。
- Web Provider 仍依赖官方网页及浏览器登录状态。

## v0.8

### 新增
- 引入 SQLite 数据库、Schema Migration 和 Repository 数据访问层。
- 新增持久化 Task Manager，管理任务生命周期和停止请求。
- 普通 Chat 接入 Task 记录，保存消息、模型调用和执行结果。
- 新增 Task Center，支持历史列表、筛选、分页及任务详情。
- 新增任务历史查询 API。

### 修复与改进
- 修复 ChatGPT 新版网页回答提取兼容问题。
- 完善 Git 数据保护和自动化回归测试。

### 已知限制
- Auto 和 Dialogue 尚未统一接入 SQLite Task 历史。
- 旧版 JSON 会话记录继续保留，尚未全面迁移。
- Web Provider 仍依赖官方网页结构与浏览器登录状态。


## v0.7

### 新增
- 会话管理：网页端可查看、选择已有 Gemini / ChatGPT 会话。
- 支持导入已有 Gemini / ChatGPT 官方网页会话。
- 支持 ChatGPT 普通 /c/... 与 /g/.../c/... 会话地址。
- 同一 Provider 内禁止同名会话静默覆盖。
- pending 会话继续保持保护，避免误重发。
- 新增 Dialogue 双模型对话模式。
- Dialogue 可分别选择发问模型和对话模型。
- 对话次数按模型消息数计数；A→B 为 2 条消息，默认 6 条，可自定义。
- Dialogue 支持运行中请求停止：当前模型回答完成后停止，不再发送给下一模型。
- Dialogue 页面按顺序显示双方实时对话内容。

### 行为约定
- Dialogue 不注入隐藏角色、身份、轮数或任务提示词。
- 第一条发送用户原始 Prompt，之后只在两个模型之间原样转发上一条模型回答。
- 需要专家角色、互相质疑、最终收敛等要求时，由用户直接写入首次 Prompt。
- 现有 Gemini、ChatGPT 单模型模式及 Auto（三阶段编排）保持兼容。

### 已知限制
- Gateway 目前不会自动抓取导入会话在官方网页中的既往完整聊天记录；导入主要用于继续该真实会话。
- Dialogue 依赖 Web Provider 响应速度，多消息任务可能运行较长时间。

