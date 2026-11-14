# Multi-Agent System — 依赖安装与运行指南

适用环境：**WSL2 / Ubuntu、Python 3.12、Node.js 24**。项目包含 Research / Coding / Analysis 三个 A2A Agent、MCP 工具、Agent Skills 和 SQLite 双层记忆。

源码：[AlanFang233/multi-agent-system](https://github.com/AlanFang233/multi-agent-system)。本指南根据仓库当前 `pyproject.toml`、`config/mcp_registry.yaml`、`scripts/start_filesystem_mcp.sh` 和 `src/agent_ecosystem/tool_agent.py` 整理。后续版本更新时请以仓库文件为准。

## 1. 安装系统基础工具

在 Ubuntu/WSL2 终端运行：

```bash
sudo apt update
sudo apt install -y git curl ca-certificates build-essential unzip
```

检查 Git：

```bash
git --version
```

> Docker/Compose、PostgreSQL、Redis **不是当前 SQLite 记忆版 Agent 的启动前提**，只有使用仓库中的 `compose.yaml` 所定义的基础设施时才需要它们。

## 2. 安装 Python 3.12

项目 `pyproject.toml` 要求 `>=3.12,<3.13`。如果已有 Python 3.12，可跳过安装：

```bash
python3.12 --version
```

如果系统没有 3.12，推荐通过官方 `uv` 安装指定版本（不必更改系统默认 Python）：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
uv --version
```

参考：[uv 安装](https://docs.astral.sh/uv/getting-started/installation/) / [uv 管理 Python](https://docs.astral.sh/uv/guides/install-python/)。

## 3. 克隆仓库并创建 Python 虚拟环境

```bash
cd ~/projects
git clone https://github.com/AlanFang233/multi-agent-system.git
cd multi-agent-system

# 如已有本项目的 .venv 且 Python 为 3.12，不要重复创建
uv venv --python 3.12 --seed .venv
source .venv/bin/activate
python --version   # 应为 3.12.x
```

如果没有使用 `uv`，可使用已安装的 `python3.12 -m venv .venv`（需要系统提供相应的 venv 模块）。

**首次安装前检查 `README.md`：** 当前公开仓库的 `pyproject.toml` 使用 `readme = "README.md"`，但公开根目录尚无此文件。先创建一个简要 README，否则项目的 editable install 可能报错：

```bash
test -f README.md || printf '# Multi-Agent System\n\nA2A + MCP + Skills + Memory.\n' > README.md
```

之后应将正式的 `README.md` 提交到 GitHub。

## 4. 安装项目 Python 依赖

仓库已经定义正式依赖和 `dev` extra，无需手工逐一安装 FastAPI、A2A、MCP、OpenAI、LangGraph、Pydantic 等包：

```bash
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pip check
```

其中 `dev` 包含 `pytest`、`pytest-asyncio`、`pytest-cov`、`ruff`。

**额外重要依赖：** `config/mcp_registry.yaml` 启动 Git/Fetch MCP 使用 `.venv/bin/python -m mcp_server_git` 和 `-m mcp_server_fetch`，但这两个 MCP 服务**没有写在当前 `pyproject.toml` 主依赖中**，须单独安装：

```bash
python -m pip install 'mcp-server-git>=2026.11.11' 'mcp-server-fetch>=2026.11.11'
python -c 'import mcp_server_git, mcp_server_fetch; print("Git/Fetch MCP OK")'
```

2026-11-11 官方 MCP 服务更新修复了旧版本的若干安全问题，建议不使用更旧的 Git/Fetch MCP 版本。之后可将这两个依赖正式添加到 `pyproject.toml`，避免新用户遗漏。

## 5. 安装 Node.js 24 与 Filesystem MCP

Filesystem MCP 当前脚本固定使用 `nvm use 24`，而最新版相应服务要求 Node.js 24 及以上。建议使用官方 nvm：

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.41.0/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install 24
nvm alias default 24
node --version
npm --version
npx --version
```

脚本 `scripts/start_filesystem_mcp.sh` 已使用 `npx -y @modelcontextprotocol/server-filesystem@2.3.0 --read-only ...`，一般无须另行全局 `npm install`。首次执行会联网下载并缓存包。可以预先缓存：

```bash
npm exec --yes --package=@modelcontextprotocol/server-filesystem@2.3.0 -- node -v
```

> 上一行只验证 `npm exec` 的环境，并不等于 MCP 服务完成握手。实际验证请运行第 8 节中的 Agent 测试。

官方文档：[nvm](https://github.com/nvm-sh/nvm) / [Filesystem MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md)。

## 6. 配置本地路径与环境变量

**此步骤对从 GitHub 克隆到新电脑的用户必需。** 仓库的 `config/mcp_registry.yaml` 当前写有原作者机器上的绝对路径，例如 `/home/alan_fang/projects/multi-agent-system`。请检查并替换成自己的本地项目路径：

```bash
pwd
nano config/mcp_registry.yaml
nano scripts/start_filesystem_mcp.sh
```

需要核对 `filesystem.args` 的启动脚本路径、`git.command` / `fetch.command` 的 Python 解释器路径、`git.args` 中的 `--repository` 路径，以及 `scripts/start_filesystem_mcp.sh` 的 `mcp_workspace` 路径。

创建 Filesystem MCP 沙箱目录：

```bash
mkdir -p mcp_workspace data
```

准备 API 配置（**绝对不要提交包含真实 Key 的 `.env`**）：

```bash
cp -n .env.example .env
nano .env
```

确保 `.env` 至少具有这些字段（用你自己的密钥替换占位值）：

```dotenv
OPENAI_API_KEY=your_private_api_key_here
OPENAI_BASE_URL=https://api1.hkgai.net/v1
OPENAI_MODEL=t2_hkgai-v3_fp8_1m_e7
```

**注意：** 公开仓库的 `.env.example` 目前没有 `OPENAI_BASE_URL`，但 `tool_agent.ModelSettings` 将它作为必填字段。请在本地 `.env` 中补齐，并建议同步更新 `.env.example`（仅保留示例值）。如果使用其他 OpenAI-compatible 服务，则填写该服务的 Base URL 和可用模型 ID。

## 7. 可选：启动 PostgreSQL / Redis

当前 `config/memory.yaml` 使用 SQLite：`data/agent_memory.sqlite`，因此**不需要** PostgreSQL/Redis 来运行已验证的双层记忆功能。

如果之后要运行 `compose.yaml` 中的 PostgreSQL + pgvector、Redis，可另行安装 Docker Desktop（启用 WSL integration）或 Docker Engine + Compose 插件，然后运行：

```bash
docker compose up -d
docker compose ps
```

请先更改 `POSTGRES_PASSWORD` 等示例口令，不要在公开部署中使用默认值。

## 8. 运行测试与启动 Agent

首先运行离线单元测试：

```bash
python -m pytest -q
python -m pytest -q tests/test_agent_memory.py
```

可检查当前记忆数据库（首次启动前可能为空）：

```bash
python -m agent_ecosystem.memory.cli stats
```

在**三个不同的 WSL 终端**中分别运行（每个终端都要先激活 `.venv`）：

```bash
# 终端 1：Coding
cd ~/projects/multi-agent-system && source .venv/bin/activate
python -m agent_ecosystem.a2a_server --name coding --port 8002
```

```bash
# 终端 2：Analysis
cd ~/projects/multi-agent-system && source .venv/bin/activate
python -m agent_ecosystem.a2a_server --name analysis --port 8003
```

```bash
# 终端 3：Research
cd ~/projects/multi-agent-system && source .venv/bin/activate
python -m agent_ecosystem.a2a_server --name research --port 8001
```

在第四个终端验证（同样激活 `.venv`）：

```bash
python -m agent_ecosystem.tool_agent \
  --role research \
  --prompt '请检查 Git 仓库状态，必要时自主委托 Coding Agent。'
```

正常情况下应能看到 `[Skill 已加载] research -> evidence-synthesis`、`[A2A 自动委托] research -> coding`、`[Memory 任务完成]` 等日志。Coding 成功调用 Git MCP 时也会有对应工具日志。

> 配置中的 Calculator MCP 服务 URL 为 `http://127.0.0.1:9001/mcp`。如果需要计算器工具，需另外启动项目中的 Calculator MCP Server；不使用计算器的 Git 审计任务可先跳过。若 `registry.refresh()` 报告 Calculator 不可用，应确认是否需要该服务。

## 9. 常见错误

| 错误 | 检查方式 |
| --- | --- |
| `Package requires a different Python` | `python --version` 必须为 3.12.x。 |
| `README.md does not exist` / editable install 失败 | 创建根目录 `README.md`，再运行 `pip install -e '.[dev]'`。 |
| `No module named mcp_server_git` | 在**同一个 `.venv`** 里运行第 4 节的 Git/Fetch MCP 安装命令。 |
| `OPENAI_BASE_URL Field required` | 在 `.env` 中增加 `OPENAI_BASE_URL`。 |
| `nvm: command not found` / `nvm.sh` missing | 加载 `~/.nvm/nvm.sh`，检查 Node.js 24 是否已安装。 |
| Filesystem MCP `Access denied` | 检查 `mcp_workspace` 白名单；这是权限隔离，不应通过扩大沙箱权限绕过。 |
| `No such file or directory` 指向其他用户家目录 | 编辑 `config/mcp_registry.yaml` 的绝对路径。 |
| Research A2A 委托连接失败 | 确认 Coding `8002` / Analysis `8003` 服务已启动并使用当前代码。 |
| 工具 `Connection refused` 9001 | 计算器服务未启动；仅在需要该工具时启动它。 |
| 记忆没有历史记录 | GitHub 不包含私有 `data/` 数据库，新环境启动时为空属于正常情况。 |

## 10. 发布与安全检查

```bash
git status --short
python -m pip check
python -m pytest -q
```

确保 `.gitignore` 排除 `.env`、`.venv/`、`data/`、`mcp_workspace/`、`*.sqlite*` 等运行数据。不要提交 API Key、数据库里的历史任务内容或执行日志。

下一次提交建议包含 `README.md`、本安装指南、已补全占位字段的 `.env.example`；还可将 Git/Fetch MCP 的 Python 包加入项目依赖，减少部署步骤。
