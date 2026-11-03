# AI Router Web Edition

个人自托管的 Gemini Web / ChatGPT Web 多模型协作工具。**v1.0.0 Web Edition** 基于经过验证的 v0.9 Web 功能快照，不包含后续 v0.10 API 通道开发成果。

> **适用范围：** 技术预览 / 个人自托管项目。依赖官方网页结构、登录状态和浏览器自动化，不是 Gemini、OpenAI 或 DeepSeek 的官方产品，也不保证网页更新后的持续可用性。请遵守相关服务的使用规则。

## 功能

- **Chat**：Gemini / ChatGPT 单模型新建与继续会话、会话列表及官方会话 URL 导入。
- **Auto**：Gemini 分析 → ChatGPT 审查 → ChatGPT 综合输出（支持后台任务与状态轮询）。
- **Dialogue**：双模型逐轮转发，可配置先后顺序及消息数，支持“当前模型回复完成后停止”。
- **Task Center**：SQLite 保存 Chat / Auto / Dialogue 任务、模型调用、消息及事件；进程重启后可查历史。
- **保守异常恢复**：结果不确定的 Web 请求不会被自动重新发送。

## 环境要求

- Ubuntu Linux（已验证的部署环境是 Ubuntu 24.04、Python 3.12）。
- Google Chrome、图形桌面（XFCE/XRDP 等）及可正常登录的 Gemini / ChatGPT 官方网页。
- Python 虚拟环境、Playwright、FastAPI、Uvicorn。
- 当前 shell 脚本和 systemd 服务单元**包含 `/home/ubuntu/ai-router` 绝对路径**，因此此版本不是任意路径下开箱即用的安装包；如果部署在其他账户或路径，请先修改脚本和服务配置。

## 最小安装流程（Ubuntu，示例路径）

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

先在图形桌面分别完成 Gemini 和 ChatGPT 的**人工登录**。在本地服务器终端使用 `./start_gemini.sh` 和 `./start_chatgpt.sh` 启动专用 Chrome 节点，检查仅本机可访问的 CDP 端口 `9222`、`9223`。不要将 CDP 端口开放到公网。

启动 Gateway（仅本机）：

```bash
.venv/bin/uvicorn gateway_app:app --host 127.0.0.1 --port 8765
```

随后可在服务器本机访问 `http://127.0.0.1:8765`。如需远程访问，应在前面配置 HTTPS、强身份验证与反向代理，不要直接对公网开放 Gateway 或 CDP 端口。仓库提供的 `deploy/ai-router-gateway.service` 是路径固定的参考配置；更改前请核对本机用户和目录。

## 回归测试

```bash
.venv/bin/python -m pytest -q
```

测试套件使用隔离数据库；部分 UI 测试需要已安装的 Chrome/Chromium。`tests/smoke_*.py` 是需要**主动执行**的联网冒烟脚本，普通 `pytest` 不运行它们。不要在不理解影响时执行或运行手工浏览器登录脚本。

## 安全及数据

- 官方网站登录态保存在本机浏览器 Profile 中，**不在 GitHub 仓库中**；不要上传 `profiles/`、`data/`、`logs/`、`debug/`、`.env` 或 API 密钥。
- 任务内容与会话信息可能涉及隐私，应保护数据目录及数据库备份。
- 通过网页发送的请求可能已抵达远端而本地无法确认；**不要擅自自动重试**。
- 目前仓库没有官方 API 通道；DeepSeek API 及 Gemini/ChatGPT API 属于后续研发范围。
- 部署与漏洞报告建议见 [SECURITY.md](SECURITY.md)。

## 版本与授权

- **v1.0.0 Web Edition**：公开源码的初始 Web 版（基于 v0.9 已验证功能）。
- **许可状态：尚未选择开源许可证**。公开可查看不等于允许复制、修改或商用；在添加明确许可证前，适用默认版权规则。未来如计划正式开源，应由仓库所有者确定 LICENSE。

更多实现与历史信息见 [`docs/`](docs/) 和 [`CHANGELOG.md`](CHANGELOG.md)。
