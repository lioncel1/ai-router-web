# DeepSeek API：v1.2 使用边界

## 模型与通道

- 网页 Gemini / ChatGPT 使用 `web` 通道；DeepSeek 使用 `api` 通道，不能在同一会话中直接混用浏览器 JSON 与 API 历史。
- 代码允许的 DeepSeek 模型名为 `deepseek-flash` 和 `deepseek-v4-pro`。仅 `deepseek-flash` 已在原开发服务器上通过真实 API 调用验证。
- API Key 从服务进程的 `DEEPSEEK_API_KEY` 环境变量读取。`/api/deepseek/status` 的 `configured=true` **只表示环境变量存在，不保证密钥有效**。
- 禁止提交密钥及用户请求内容；API 使用量可写入 SQLite，不根据模型标价推算费用。

## Chat 会话

- `/api/deepseek/chat` 为单轮 API 调用：返回回答、`usage`、`task_id`，没有多轮上下文。
- `/api/deepseek/session/chat` 支持 `new` 和 `ask`，多轮会话的已确认消息存于 SQLite。
- `/api/deepseek/sessions` 与 `/api/deepseek/sessions/{name}` 可读取会话状态及已确认消息。
- 每次发送前将会话标为 `pending` 并持久化；仅收到完整回答且成功持久化时恢复 `ready`。超时、网络中断、进程重启、记录失败等情况将会话隔离为 `uncertain`，不得自动重试、跳过失败消息或在已有上下文继续发送。
- 当前没有 API 不确定结果的手工核对解锁流程；请保留数据以供人工排查。

## Dialogue 与 Auto

- Dialogue 中可以从 Gemini、ChatGPT、DeepSeek 任选两个模型；选择 DeepSeek 时需配置可用的 API Key。DeepSeek 多轮历史与其 API 会话隔离保存。
- Auto 三阶段可分别选择模型，默认 Gemini → ChatGPT → ChatGPT；API 阶段不自动重试；使用量仅累加 API 实际返回的 Token。
- 对任何结果不明的模型请求都不能推定成功或失败并自行重发。任务中心记录 `transport`，区分 Web 和 API。

## 安全

- API 调用会产生实际费用，建议先用短提示词与小轮数做确认。
- 仅绑定本机 `127.0.0.1:8765`，外部访问应有 HTTPS、认证和网络访问限制。
- `.env`、会话历史、数据库备份和模型回答均可能含有敏感信息；请按 [`../SECURITY.md`](../SECURITY.md) 妥善管理。
