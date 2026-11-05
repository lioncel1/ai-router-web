# AI Router · Web/API Edition

**v1.2.0 · Gemini Web、ChatGPT Web、DeepSeek API 混合协作**

个人自托管的多模型对话与工作流工具：通过官方网页（浏览器自动化）访问 Gemini、ChatGPT，并通过独立 API Key 调用 DeepSeek。**不是上述平台的官方产品**；请遵守对应网站/API 的适用条款。当前发布的是在特定 Ubuntu 环境中验证过的**源码快照**，不是跨平台一键部署产品。

## 功能

| 模式 | 能力 |
| --- | --- |
| **Chat** | Gemini / ChatGPT 网页会话新建、继续、导入；DeepSeek API 单轮及 SQLite 持久化多轮 Chat |
| **Dialogue** | 从 Gemini Web、ChatGPT Web、DeepSeek API 中任选两个交替对话，设置先后、消息条数与 DeepSeek 模型 |
| **Dialogue 续聊** | 已完成或安全停止后从上条回答继续，**发送前可编辑**；结果不明时不自动重试或猜测轮次 |
| **Dialogue 异常恢复** | Gemini / ChatGPT 网页已生成但本地未登记的回答可以人工核对、确认后建立恢复检查点；不自动补发 |
| **Auto** | 分析、审查、综合三阶段分别选 Gemini / ChatGPT / DeepSeek；默认仍为 Gemini → ChatGPT → ChatGPT |
| **Task Center** | SQLite 记录任务、模型调用、消息、Web/API 通道及 DeepSeek 实际 Token 用量 |

DeepSeek 代码目前允许 `deepseek-flash`、`deepseek-v4-pro`；实际接口支持及模型可用性取决于账户和服务商。**已进行真人 API 实测的是 `deepseek-flash`**。Token 数为 API 返回用量，**没有按价格计算或保证计费金额**。

## 运行环境与部署限制

- 已验证的原服务器：Ubuntu 24.04、Python 3.12、Chrome、可用的图形桌面及持久登录态。
- `start_*.sh`、`router_call.sh` 及 `deploy/ai-router-gateway.service` 当前有 **`/home/ubuntu/ai-router` 固定路径**；变更用户或安装目录前必须逐个核对并修改。
- Gemini、ChatGPT Web 仍依赖官网 DOM，网站更新可能导致失效；此项目未提供官方 Gemini/OpenAI API 适配器。
- **Gateway 和 Chrome CDP 只应绑定 `127.0.0.1`**。远程访问应自行部署 HTTPS、强认证和访问限制。单纯通过公网 HTTP + Basic Auth 不能视为生产安全。

### 最小环境准备（Ubuntu，默认路径）

```bash
cd /home/ubuntu
git clone https://github.com/lioncel1/ai-router-web.git ai-router
cd ai-router
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x ./*.sh
mkdir -p data logs profiles/gemini profiles/chatgpt
.venv/bin/python -m database.connection
```

浏览器本身、图形桌面和账号人工登录需要另行准备。检查脚本中的 Chrome 路径后启动 `./start_gemini.sh` 和 `./start_chatgpt.sh`（CDP 通常为本机 `9222` 和 `9223`）。本机调试 Gateway：

```bash
.venv/bin/uvicorn gateway_app:app --host 127.0.0.1 --port 8765
```

打开服务器本机 `http://127.0.0.1:8765`。如要长期运行，可参考 `deploy/ai-router-gateway.service`，但需要自行完成 systemd 用户服务安装、Nginx/TLS/认证加固。

### 配置 DeepSeek API Key

**不要将密钥写入代码、命令参数、Git、问题单或聊天记录。** 在服务器上以受限文件保存后，由用户级 systemd Gateway 加载。例如（以下为示意路径，不包含真实密钥）：

```bash
mkdir -p -m 700 ~/.config/ai-router ~/.config/systemd/user/ai-router-gateway.service.d
chmod 700 ~/.config/ai-router
# 使用你信任的本地编辑方式创建 ~/.config/ai-router/deepseek.env
# 文件内容格式：DEEPSEEK_API_KEY=<你的密钥>（不要提交此文件）
chmod 600 ~/.config/ai-router/deepseek.env
printf '[Service]\nEnvironmentFile=%%h/.config/ai-router/deepseek.env\n' > ~/.config/systemd/user/ai-router-gateway.service.d/deepseek.conf
systemctl --user daemon-reload
systemctl --user restart ai-router-gateway
```

本机使用普通终端前台运行 Gateway 时，也必须在**仅当前服务进程可访问**的环境中提供 `DEEPSEEK_API_KEY`；不要在共享机器命令历史里输入密钥。未配置密钥时 DeepSeek API 请求会拒绝，不影响 Web 模式。

`GET /api/deepseek/status` 用于检查配置是否加载（`configured`），**不验证密钥有效性**。单轮 `/api/deepseek/chat`、多轮 `/api/deepseek/session/chat` 会进行真实调用，可能产生费用。详见 [`docs/DEEPSEEK_API.md`](docs/DEEPSEEK_API.md)。

## 续聊、安全停止与中断处理

- Dialogue 停止请求在当前模型调用自然结束后生效，并不会撤回已经送出的网页/API 消息。
- 常规续聊根据**已确认保存的最后一条回复**选择下一个模型，允许编辑待发送内容；原历史任务不改写。
- **请求结果不确定时不能自动重发**。Web 回答可在原网页人工核对；API 多轮会话变为 `uncertain` 后禁止继续，当前没有自动解锁/人工确认 API 结果的功能。
- Auto 自定义的 API 阶段不会重试；Web 请求结果不明时按安全边界终止。当前有重启/持久化保护，但不提供任意阶段自动断点续跑。

详见 [`docs/DIALOGUE_RECOVERY.md`](docs/DIALOGUE_RECOVERY.md) 和 [`docs/DEEPSEEK_API.md`](docs/DEEPSEEK_API.md)。

## 测试与发布范围

```bash
.venv/bin/python -m pytest -q
```

原开发服务器 v1.2 RC1（提交 `eeadfe8`）**173 passed、5 subtests passed**；已实测 DeepSeek Flash 的单轮、多轮 Chat，Gemini → DeepSeek Dialogue，以及 Gemini → DeepSeek → ChatGPT Auto。上述是该服务器环境的记录，不意味着每台机器都可无配置运行。浏览器 UI 自动化测试需要 Chrome/Chromium；公开包的浏览器回归测试优先寻找 PATH 中的 Chrome/Chromium；在隔离容器的模拟网页访问被管理策略阻断时，个别用例会标记为跳过。

`tests/smoke_*.py` 是手工联机测试脚本，不属于常规 pytest 测试；避免在未确认状态时重新发消息。`install_phase3.py`、`install_v09_phase2.py`、`install_v12_deepseek.py`、`install_v12_phase2.py` 与 `phase2_assets/` 仅为**旧版本测试依赖/兼容资料**，不要在已运行的 v1.2 上执行这些安装器的 `--apply`。

## 安全与授权

- **绝不上传** `data/`、`profiles/`、`logs/`、`.env`、密钥、Cookie、个人会话 URL、数据库及备份。请查看 [`SECURITY.md`](SECURITY.md)。
- 本仓库当前**没有 LICENSE**。源码公开可阅读不等于允许第三方自行再分发、改造或商用；若决定开源需明确选择许可证。
- `docs/` 中有早期系统设计记录，不是全部功能已经实现的证明。

发布历史见 [`CHANGELOG.md`](CHANGELOG.md) 和 [`RELEASE_NOTES_v1.2.0.md`](RELEASE_NOTES_v1.2.0.md)。
