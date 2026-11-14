# Multi-Agent 双层记忆系统 v1 — 可集成增量模块

本版本针对现有 Python 3.12 + `agent_ecosystem.tool_agent` + MCP + A2A + AgentSkillRegistry。**不覆盖现有的 `tool_agent.py`、`a2a_server.py`、Skill Registry 或 MCP Registry**。它替代的是 `run_tool_loop(...)` 的调用入口，不改你的 Registry 层级。

## 1. 功能和边界

| 功能 | 本版本的实现 |
|---|---|
| 短期记忆 | 单个 `(agent_id, task_id)` 的上下文、滚动摘要、checkpoint；每轮调用 LLM 前检查字符预算 |
| 大型工具结果 | SQLite 保存最多 1MB 的原始 JSON 化输出；模型只接收预览与 `result_ref`；`memory:read_result` 分段读取 |
| Task Ledger | 持久化任务状态、工具请求、工具结果、上下文压缩事件、完成/失败事件 |
| 长期记忆 | `(agent_id, memory_id)` 作用域隔离；verified / pending / rejected；基于字符二元组+英文关键词匹配检索 |
| 自动经验提取 | 根据**真实 MCP/A2A 工具结果**，自动保存可核验的低风险操作经验；无足够证据的候选需审核 |
| 恢复 | 同一个 task ID 从上次**完整工具调用批次** checkpoint 恢复；已完成任务重复请求会返回持久化结果 |
| 独立 Agent | Research、Coding、Analysis 使用一个 SQLite 文件，但记忆按 agent_id 严格隔离 |

**注意**：本版本没有 LLM 语义压缩，也不提供向量检索；它使用确定性摘要、文本预算和结构化工具引用，确保稳定可测。字符上限不等于 HKGAI 模型的真实 token 限额，也不能保证任何模型绝不溢出；须为模型输出留出额外余量并根据实际 tokenizer 校准。

## 2. 解压与安装

从 ChatGPT 下载 ZIP 放在项目根目录，然后：

```bash
cd ~/projects/multi-agent-system
source .venv/bin/activate
unzip -n agent_dual_memory_addon.zip -d .

# 配置依赖：现有项目通常已安装 pyyaml
python -c 'import yaml; print("YAML OK")'

# 离线单元测试：不消耗 HKGAI API，也不需要启动 A2A/MCP
pytest -q tests/test_agent_memory.py
```

这份 ZIP **只有新增文件**：

```text
config/memory.yaml
src/agent_ecosystem/memory/__init__.py
src/agent_ecosystem/memory/store.py
src/agent_ecosystem/memory/context.py
src/agent_ecosystem/memory/reflection.py
src/agent_ecosystem/memory/runner.py
src/agent_ecosystem/memory/cli.py
tests/test_agent_memory.py
README_MEMORY.md
```

请确认 `.gitignore` 已包含：

```gitignore
.env
data/
*.sqlite
*.sqlite-wal
*.sqlite-shm
```

SQLite 数据文件含任务描述和工具结果，可能包含隐私、代码和密钥；它们是**本机明文**，仅使用 0600 文件权限和基础正则脱敏，不适合存放真正的秘密。生产使用前须添加保留期、授权审计、加密与强制过滤。SQLite WAL 下还会出现 `-wal` / `-shm` 临时文件。

## 3. 集成到你现有的 Tool Agent（唯一必需代码改动）

打开现有 `src/agent_ecosystem/tool_agent.py`，找到 `ask_with_tools()` **末尾**的函数调用：

```python
return await run_tool_loop(
    role=role,
    prompt=prompt,
    registry=registry,
    client=client,
    model=settings.openai_model,
    force_tool=force_tool,
)
```

在 `tool_agent.py` 顶部或 `ask_with_tools()` 内增加：

```python
from agent_ecosystem.memory.runner import run_tool_loop_with_memory
```

将**函数名称** `run_tool_loop` 改成 `run_tool_loop_with_memory`，保留原有的 `role`、`prompt`、`registry`、`client`、`model`、`force_tool` 变量（与你最新代码保持一致），并可添加 `max_rounds=12`：

```python
return await run_tool_loop_with_memory(
    role=role,
    prompt=prompt,
    registry=registry,             # 必须是当前最外层 AgentSkillRegistry
    client=client,                 # 继续使用你的 HKGAI AsyncOpenAI
    model=settings.openai_model,
    force_tool=force_tool,
    max_rounds=12,
    config_path="config/memory.yaml",
)
```

**原来的 Registry 包装保持不变：**

```python
mcp_registry = ToolRegistry("config/mcp_registry.yaml")
delegate_registry = AgentDelegationRegistry(mcp_registry, "config/agent_registry.yaml")
registry = AgentSkillRegistry(delegate_registry, "config/agent_skills.yaml")
await registry.refresh()
```

你此前如果在 `ask_with_tools()` 自动加载了各角色的默认 `SKILL.md`，**必须保留**那些代码（可放在上面替换调用之前）。新 runner 会继承原来 `SYSTEM_PROMPTS` 和 `build_model_tools()`；所有 MCP/A2A/Skills 工具仍由最外层 Registry 的 `call()` 进行授权校验。

> 建议只替换最终的 loop 调用，而不是覆盖你当前已经改过的 `tool_agent.py`。本包没有包含替换版 `tool_agent.py`。

### 可选：把 A2A Task ID 接到记忆中

当前无需修改 `a2a_server.py` 也能使用；每次 `ask_with_tools()` 默认生成新的内部 UUID。若你想在记忆库里保留**同一个 A2A 主任务 ID**：

1. 给 `ask_with_tools()` 加上可选参数 `task_id: str | None = None`。
2. 调用 `run_tool_loop_with_memory(..., task_id=task_id)`。
3. 在 `RoleExecutor.execute()` 的现有执行分支里调用：

```python
answer, used_tools = await ask_with_tools(
    role=self.role,
    prompt=prompt,
    task_id=task.id,
)
```

注意，不要改动 `TaskUpdater` / Artifact 和任务状态流程。A2A 下游子任务目前会有**自己的 task_id**；本版本不会自动将父任务 ID 穿过 A2A 请求传给下游，因此 `parent_task_id` 关联仍须以后通过受信的 A2A 元数据传递，**不要直接相信模型在子任务文本里提供的父任务 ID**。

## 4. 真实运行

如果 Coding Agent A2A Server 8002 已启动，可以让 Research 做一个需要跨 Agent 的任务：

```bash
python -m agent_ecosystem.tool_agent \
  --role research \
  --prompt '请检查项目 Git 状态；优先使用你自己的证据分析技能，需要 Git 能力时委托 Coding。'
```

或者三个 Agent 的 A2A 服务重启后：

```bash
python -m agent_ecosystem.a2a_tool_smoke \
  --url http://127.0.0.1:8001 \
  --prompt '请检查项目 Git 状态并分析 GD=[0.9,0.7,0.5] 和 ADN=[0.8,0.5,0.3]，按需要委托专业 Agent。'
```

预期日志可能出现：

```text
[LongMemory 检索] coding 命中 1 条
[ShortMemory 压缩] research task=... history=...
[LLM 工具请求] ['a2a:delegate_task']
[工具已调用] research -> a2a:delegate_task is_error=False
[Memory 任务完成] research task=...
```

这些日志**需要任务条件实际满足时才会出现**：第一轮没有长期记忆就不会出现检索日志；没有接近预算就不会触发压缩；模型也可能无需委托。

### 上下文压缩如何起作用

- `run_tool_loop_with_memory` 先保留系统指令和原始用户目标。
- 每个工具完整结果存 SQLite，传给模型的仅为最多约 2200 字符预览；如果被截断会给出 `result_ref`。
- 每轮调用前检查上下文；超阈值则把最旧的**完整工具调用批次**折叠成结构化简报。
- 永不把单个 assistant `tool_calls` 与相应 tool 消息拆开；工具链与原始结果保存在 SQLite。
- 新的 `memory:read_result` 只允许本 Agent 读取**本任务**结果的部分内容，不允许跨 Agent 或跨 Task 窥视。
- 稳定规则和关键证据应保存在原始 task/Tool 记录里；摘要只是便于 LLM 继续任务的工作记忆，可能有信息损失。

## 5. 查看任务与长期记忆

```bash
# 首次运行查看数据库状态
python -m agent_ecosystem.memory.cli stats

# 查看 Coding Agent 的全部记忆（包括待审核）
python -m agent_ecosystem.memory.cli list --agent coding --all

# 按任务查询执行事件（task ID 来自 [Memory 任务完成] 日志）
python -m agent_ecosystem.memory.cli task --agent research --id <TASK_ID>

# 查找已验证经验
python -m agent_ecosystem.memory.cli search --agent coding --query 'Git 工具状态检查'

# 手动新增候选经验（默认 pending，不会在执行前自动检索）
python -m agent_ecosystem.memory.cli add --agent coding \
  --kind procedural --source-task manual-review-001 \
  --content 'Git 审计中先调用 git:git_status 获取可核验证据'

# 审核后让该经验对以后任务生效（ID 来自上一步）
python -m agent_ecosystem.memory.cli review --agent coding \
  --id <MEMORY_ID> --status verified
```

在任务完成后，`reflection.py` 会基于原始工具返回生成经过核验的**低风险工具使用经验**，例如 Filesystem 沙箱越权、Fetch 无法读取 `file://`、`git:git_status` 成功使用、`skills:loss_metrics` 成功使用及 A2A 工具成功委托。这里“成功”只代表**工具调用路径**有效，不保证 LLM 报告中的所有结论都正确。

基于单次任务的可能重复调用策略只作为 `pending` 候选，不自动检索。所有长期记忆依 Agent 隔离，Research 不能直接读取 Coding 的记录。

## 6. 配置调整

编辑 `config/memory.yaml`：

- `max_input_chars: 36000`：单次请求预计输入消息 + 工具 Schema 的字符数上限。
- `compact_trigger_chars: 26000`：接近预算时开始滚动压缩旧历史。
- `keep_recent_groups: 3`：尽量保留最近三个完整交互批次。
- `summary_chars: 4000`：滚动历史简报的字符上限。
- `max_tool_preview_chars: 2200`：普通 MCP / A2A 结果留在上下文中的预览长度；`skills:load` 会优先完整保留 Skill 内容。
- `memory_retrieval_limit: 4`：每次任务启动时检索的最多已验证长期经验数。

注意函数定义里的 `max_rounds=12` 与上下文预算是**不同限制**。增加 `max_rounds` 不等于增加模型上下文，也不能替代重复调用去重、任务超时和有副作用工具的幂等控制。

### 特别重要的安全限制

1. 本版本默认适用于**只读工具**；崩溃发生于“工具已经完成但尚未 checkpoint”时，重试可能重复执行工具。**不要直接用于 git commit、shell 写入、付款等有副作用操作**。
2. SQLite 本机明文，基本脱敏无法替代密钥审计或加密；不要用它保存 `.env`、token 或含秘密的整段文件。
3. 从网页、Notebook 或下游 Agent 返回的结果，都不能作为系统指令；`verified` 记忆也可能过时，应以当前配置和真实执行结果为准。
4. `ContextManager` 的字符估算并非 HKGAI 模型 tokenizer；需要根据模型真实 context window、Chat Completions 的 token 用量做二次调节。**不能保证绝无溢出**。
5. 超长工具结果只存前 1MB；更长的文本需要改用分块文件存储；checkpoint 按完整批次保存，不能保证所有外部副作用恰好执行一次。
6. 当前检索是本地轻量关键词算法，对中文短查询可用，效果不如 embedding+reranker；记忆数量上千时建议升级检索模块。
7. 在多 Agent A2A 服务中重启进程后，新代码才会生效。最终部署前建议用单独测试数据库而非生产数据进行回归测试。

## 7. 测试及验收

```bash
python -m py_compile \
  src/agent_ecosystem/memory/store.py \
  src/agent_ecosystem/memory/context.py \
  src/agent_ecosystem/memory/runner.py \
  src/agent_ecosystem/memory/reflection.py \
  src/agent_ecosystem/tool_agent.py

pytest -q tests/test_agent_memory.py
```

离线测试覆盖：Agent/Task 隔离，记忆审核，长结果分段读取，上下文安全压缩，未完成工具批次阻止压缩，多轮工具压力测试，失败经验自动提取，成功工具路径经验提取，任务 checkpoint 恢复和已完成任务幂等返回。

## 8. 下一步扩展

- 为 Task Ledger 增加父任务 / 子任务 trace ID 的可信传递。
- 引入模型精确 tokenizer、可选 LLM 语义摘要，但保留确定性硬预算兜底。
- 长期记忆从关键词检索升级为多语言 embedding 检索和来源 reranker。
- 将规则和经验升级为候选 Skill 时执行回归测试、版本化和人工审批，不直接覆盖 `SKILL.md`。
