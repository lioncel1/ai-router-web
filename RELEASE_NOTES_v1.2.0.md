# AI Router Web/API Edition · v1.2.0

## 主要更新

- **DeepSeek API**：独立单轮接口、SQLite 持久化多轮 Chat、网页 Chat Provider 选择；支持按代码配置的 `deepseek-flash` / `deepseek-v4-pro`，其中 Flash 已实际验证。
- **混合 Dialogue**：Gemini Web、ChatGPT Web 与 DeepSeek API 任意选择两个交替对话；保留 v1.1 安全停止、续聊前编辑、失败任务人工核对和恢复导航。
- **可配置 Auto**：分析、审查、综合阶段分别指定模型，默认流程兼容 v1.1。
- **调用审计**：SQLite 区分 Web/API 通道，保存 DeepSeek 返回的 Token 用量和调用状态；不推算费用。
- **异常防重**：API 结果不确定则隔离多轮会话；Auto 的 API 阶段不自动重试，重启后不自动补发不确定的请求。

## 验证记录

- 原 Ubuntu 服务器 v1.2 RC1：`173 passed, 5 subtests passed`；开发分支最新提交为 `eeadfe8`。
- 已真人实测 DeepSeek Flash 单轮、多轮，Gemini → DeepSeek 双模型 Dialogue，以及 Gemini → DeepSeek → ChatGPT Auto；V4 Pro 尚无实际调用验证。
- 本发布包为公开源码精简版，排除内部运维记录、终端误存内容和重复安装快照。公开包仍需在目标机器验证浏览器、系统路径和网络策略。

## 已知限制与安全

- Gemini、ChatGPT 使用网页自动化；项目对官网 DOM 变化敏感。
- 脚本含 Ubuntu 默认路径 `/home/ubuntu/ai-router`，不支持无需配置的跨机器安装。
- DeepSeek `uncertain` 多轮会话当前不支持自动解除；请勿自动重发可能已送达的请求。
- API Key 必须保存在服务器的受限环境配置中，不得进入源码或 GitHub。
- 本项目未进行公开互联网生产级安全审计；禁止公网裸露 Gateway/CDP，远程访问需 HTTPS 与强认证。
- 目前没有选择开源许可证；仅提供公开可查看的源码快照。
