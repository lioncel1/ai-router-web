# AI Router · Web Edition

**v1.1.0 · Dialogue 安全续聊与断点恢复**

个人自托管的 Gemini Web / ChatGPT Web 多模型协作工具，基于浏览器自动化（**非官方 API 客户端**）。发布的是源码快照，不承诺可以跨环境一键部署；使用前请核对平台服务条款、浏览器兼容性和认证安全。

## 已实现

- **Chat**：Gemini / ChatGPT 单模型新建、继续会话、会话管理和官方 URL 导入。
- **Auto**：Gemini 分析 → ChatGPT 审查 → ChatGPT 综合输出；异步任务及阶段结果。
- **Dialogue**：Gemini / ChatGPT 多轮原样转发，配置发问顺序和消息条数；当前模型回答结束后安全停止。
- **Dialogue 续聊**（v1.1）：从 `done` / `stopped` 任务继续，自动带入上条回答，**发送前可修改文本**；原任务不被改写，新任务保留关联。
- **异常核对和恢复**（v1.1）：支持天气卡片等无复制按钮的富文本回答；记录发送意图，任务结果不确定时先从原网页**只读检查**，明确人工确认后保存恢复检查点；不自动重发。
- **历史任务中心**：SQLite 保留 Chat、Auto、Dialogue 的任务、回答、调用及事件，重启后可查阅；已经恢复的任务可导航到最新续聊。
- **预留 Web/API 分发接口**：`provider_transport.py` 提供显式通道分发，**API 适配器尚未接入**；当前业务仍只使用 Web。

## 运行环境

- 已验证的服务器环境：Ubuntu 24.04、Python 3.12、Chrome 及桌面会话。
- 本项目 `*.sh` 和 `deploy/ai-router-gateway.service` 目前仍含 `/home/ubuntu/ai-router` 固定路径；若你使用其他用户/位置，请先检查并调整脚本路径。
- 安装依赖不等于安装 Chrome；请自行安装 Chrome 并在图形桌面登录 Gemini 和 ChatGPT 官方网站。

### 最小部署示例（Ubuntu，默认路径）

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

在图形桌面中完成浏览器人工登录，之后按实际用户环境启动 `./start_gemini.sh` 与 `./start_chatgpt.sh`（Chrome CDP 默认仅本机 `9222`、`9223`）。在终端启动 Gateway：

```bash
.venv/bin/uvicorn gateway_app:app --host 127.0.0.1 --port 8765
```

在服务器本机打开 `http://127.0.0.1:8765`。**不能直接把 Gateway/CDP 端口暴露公网**；远程访问应通过 HTTPS + 身份验证 + 限制访问范围的反向代理。参考 `deploy/`，但该服务单元需按实际用户设置。

## Dialogue：停止、继续、失败恢复

- **正常继续**：网页选 Dialogue → 从最近任务继续，或任务中心选 `done` / `stopped` → 继续；默认填入上一条模型回答，修改后明确点击确认才发送。
- **安全停止**：正在执行的模型允许完成当前回答，然后不再转发到另一模型；状态为 `stopped`。
- **恢复失败对话**：任务中心选 `failed` → 核对原网页回答 → 逐字确认候选回答归属 → 建立 `stopped` 恢复检查点 → 从检查点继续。**预览及核对不会发送模型请求**；未知的网页请求结果不得重试。
- **已恢复任务**：选中原失败任务会显示最新后续任务入口，避免重复建检查点。

详细操作和错误边界见 [`docs/DIALOGUE_RECOVERY.md`](docs/DIALOGUE_RECOVERY.md)。

## 自动化回归测试

```bash
.venv/bin/python -m pytest -q
```

本版本基线在用户 Ubuntu 实际环境验证：**124 passed、5 subtests passed**（v1.1 RC1）；公开包经过源码精简和离线复核，实际发布前应再次运行完整测试。普通 `pytest` 只发现 `tests/`；`tests/smoke_*.py` 属于主动联网用例，**不要随意执行**。`install_phase3.py`、`install_v09_phase2.py` 和 `phase2_assets/` 为历史安装器兼容性测试保留，**不是新安装步骤，不要在 v1.1 上执行 `--apply`**。

## 数据安全和授权

请勿提交 `.env`、`data/`、`profiles/`、`logs/`、`debug/`、备份及会话链接。请求和模型回答可能含有敏感数据，备份也应限制访问。完整说明见 [`SECURITY.md`](SECURITY.md)。

**目前未选择开源许可证。** 仓库公开可见并不等于授予第三方复制/改造/商用权限。如果计划开源发布，请自行决定 `LICENSE`。

变更信息见 [`CHANGELOG.md`](CHANGELOG.md)，发布说明见 [`RELEASE_NOTES_v1.1.0.md`](RELEASE_NOTES_v1.1.0.md)。部分 `docs/` 属于早期设计文档，非全部功能的已实现声明。
