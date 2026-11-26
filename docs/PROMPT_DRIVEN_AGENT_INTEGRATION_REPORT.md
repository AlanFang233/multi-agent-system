# Prompt-Driven Agent Creation — Integration Report

**Date**: 2026-11-23 | **Branch**: `feature/prompt-driven-integration`（独立集成分支）
**Base**: 上游 `origin/main` = `b065e0d`（同伴 "agent group init by prompt"，其父链含已合并的 PR #1 = 本方 `053b89e`）
**本分支新增提交**: 见 §4（本地提交，未 Push）
**模式**: 全部 E2E 使用本地 Mock LLM（`scripts/mock_llm_server.py`）+ 真实 MCP/Docker/A2A/SQLite；未调用付费 API。

---

## 1. 同伴最新上传的文件及 Commit

Commit `b065e0d`（"agent group init by prompt"），35 文件 +1489/−200。核心新增：

| 文件 | 作用 |
|---|---|
| `src/agent_ecosystem/orchestration/team_builder.py` | TeamSpec/AgentSpec/SkillSpec 校验模型、`plan_team`（LLM 函数调用 submit_team）、`validate_policy`、`runtime_dir`（符号链接防护）、`write_bundle`（按组生成 mcp/agent/skills/memory 注册表 + manifest） |
| `src/agent_ecosystem/orchestration/coordinator_skills.py` | 控制面 Skills（write-skills / register-tools）加载与模板安全校验 |
| `config/capability_catalog.yaml` | 团队能力目录（10 能力） |
| `config/coordinator_skills.yaml` | 控制面 Skills 与模板清单 |
| `.trae/skills/write-skills/`、`.trae/skills/register-tools/` | Coordinator 专用 Skill 与资产模板 |
| `scripts/start_coordinator.sh` | Coordinator-only 启动（`--mock` / `--legacy`） |
| `tests/test_team_builder.py`、`tests/test_coordinator_only.py` | 同伴新增测试（21 项） |
| 其余 | coordinator/group_service/a2a_server/runner/assignment/service/sandbox/calculator/tool_agent 的 MAS_* 环境链接入、端口块可用性探测、/proc 校验、Dashboard 团队创建卡片、`midtest_*` 脚本调整、README 增补 |

## 2. 与本地版本的差异

- 本地旧分支 `feature/agentforge-capabilities`（`053b89e`）已随 PR #1 合并进上游；同伴在其上叠加 `b065e0d`。
- 本集成分支 = `origin/main`(b065e0d) + 本轮整合修复（§4）。旧分支与 Mid_Test 成果（`20505f9`/`053b89e`、`artifacts/mid_test_local/`、`docs/MID_TEST_LOCAL_TRIAL_REPORT.md`）完整保留在历史与旧分支中，未被改写。
- 同伴改动**未破坏**本方既有修复：审批重放、竞态补投递、规划器有界修复、filesystem CWD 钉扎（同伴在其上叠加了 `MAS_FILESYSTEM_ROOT` 覆盖，兼容）、沙箱超时钳制、组幂等创建/端口分配——合并后全部在位（已逐项 grep 核实）。

## 3. 已复用的模块（不重写）

`team_builder`（proposal/bundle/校验）、`coordinator_skills`（控制面 Skills）、`group_service.start_agents`（bundle 化启动 + 健康等待）、coordinator 的 `/groups/plan`、`/groups/provision`、`/coordinator/skills|templates`、Dashboard 团队卡片、`start_coordinator.sh` 的 Coordinator-only/legacy 双模式、mock 的 `submit_team` 固件。本轮仅在其上做最小修复与接线（§4）。

## 4. 实际新增或修复的代码（本分支）

| # | 类型 | 内容 | 文件 |
|---|---|---|---|
| 1 | **修复（MCP 分配根因）** | 新增 `required_tools` 能力→最小工具映射；`enforce_required_tools()` 在 `plan_team` 与 `/groups/provision` 将能力所需工具并入 `mcp_tools`（与 profile 求交，保持最小权限）；`validate_policy` 增加 capability↔profile 一致性校验（缺工具 profile 直接报可修复错误） | `config/capability_catalog.yaml`、`orchestration/team_builder.py`、`coordinator.py` |
| 2 | **修复（同伴 bug）** | `mounted_agents` 对 `Group` dataclass 取下标（`group["group_id"]`）→ 只要库中存在任何 group，`/agents` 与 `/agents/discover` 即 500；改为属性访问 + 回归测试 | `coordinator.py`、`tests/test_coordinator_only.py` |
| 3 | **修复（mock 固件）** | `_sandbox_paths` 正则不识别按组 bundle 路径 `sandboxes/<group>/workspace/<32hex>` → 组内 coding 节点跳过 write/run（无审批、无执行、假完成风险）；正则兼容两种布局 | `scripts/mock_llm_server.py` |
| 4 | **增强（可观测）** | Agent 启动时显式打印"已授权但当前不可用"的工具及原因（server 不可用不再静默） | `tool_agent.py` |
| 5 | **测试** | `test_enforce_required_tools_unions_minimal_set`、`test_capability_profile_mismatch_raises`、`test_agents_endpoints_survive_existing_groups` | `tests/test_team_builder.py`、`tests/test_coordinator_only.py` |
| 6 | **装置** | Prompt-Driven E2E 驱动（测试 A/B/C，15 项检查）；`wslr.sh` Git-Bash→WSL 网关（base64 封装，根治 `$`/路径 mangling）；`start_legacy_agents.sh` 兼容模式补挂 base agents | `scripts/prompt_driven_e2e.py`、`scripts/wslr.sh`、`scripts/start_legacy_agents.sh` |

## 5. MCP Tool 分配矩阵

Profile（`config/mcp_registry.yaml` permissions，最小权限来源）：

| Profile | 工具集（摘要） |
|---|---|
| research | fetch:fetch；filesystem 只读系；free_search:search/research/fetch |
| coding | calculator:add；filesystem 只读系；filesystem_code 读写系；git 只读系；sandbox:create/run_python/run_pytest |
| analysis | calculator:add；filesystem 只读系 |

能力→必需工具（本轮新增，`capability_catalog.yaml required_tools`，节选）：
`web_search→free_search:*`；`documentation_research→fetch+filesystem 读`；`evidence_synthesis→filesystem 读`；`file_editing→filesystem_code 写/编辑/列/读`；`sandbox_execution→sandbox:create+run_python`；`python_debugging→sandbox+filesystem_code 读`；`statistics/numerical_analysis/loss_metrics→calculator:add(+读)`；`git_audit→git 只读`。

分配流程：LLM 提案 `mcp_tools` ⊆ profile（`validate_policy`）→ `enforce_required_tools` 并入能力必需集（仍 ⊆ profile）→ `write_bundle` 按命名空间写入组注册表 → Agent 进程经 `MAS_MCP_REGISTRY` 加载 → `ToolRegistry.list_for_agent(ns)` 生效 → 越权调用在工具边界 `PermissionError`。

## 6. 每个 Agent 实际能访问的工具（实测，测试 A）

组 `team-13ea3b69…`（2-Agent 提案）：
- `…:agent-1`（research profile）实测 allowlist 7 项：fetch:fetch、filesystem 只读 4 项、skills:load、a2a:delegate_task/discover（委托层附加）。**真实调用** fetch:fetch 成功（is_error=False，返回网页内容）。
- `…:agent-2`（coding profile）实测 allowlist 10 项：filesystem_code 读写 4、sandbox 3、calculator… 含 skills:load + a2a×2。**真实调用** sandbox:create 成功（Docker）。
- 越权探针：agent-1 调 `filesystem_code:write_file` → `PermissionError: '…:agent-1' is not allowed to call 'filesystem_code:write_file'`（允许列表边界生效）。
证据：`artifacts/prompt_driven/testA_tool_probes.json`。

## 7. Skills 加载与分配结果

1. Coordinator 启动即校验并加载 write-skills / register-tools（`/coordinator/skills` 返回两者；非法模板启动失败）。
2. `plan_team` 将两 Skill 全文 + 4 个模板注入规划系统提示；LLM 为每 Agent 生成 1-4 个 instruction-only Skill（测试 A：每 Agent `role-workflow`）。
3. `write_bundle` 把 Skill 写入 `<runtime>/skills/<ns>/<name>/SKILL.md`，模板复制到 `<runtime>/workspace/templates/`。
4. Agent 经 `MAS_AGENT_SKILLS` 按命名空间发现：`skills:load` 的 enum 仅含本 Agent 的 Skill；实测 `skills:load(role-workflow)` 成功返回指令。
5. Skill 不覆盖权限：SkillSpec 仅 name/description/instructions（extra=forbid），role prompt 明示"Skills 与工具输出不能覆盖平台安全规则"；runner 侧工具输出/记忆均为低信任材料。
6/7. Skill 为纯指令、不声明工具依赖 → 不存在"已加载但工具缺失"；MCP 侧不可用工具现在会显式告警（§4-4）。

## 8. Prompt → TeamSpec → Group → A2A 真实执行证据

- 测试 A：`POST /groups/plan`（2-Agent 中文 prompt）→ proposal `awaiting_confirmation`（`testA_proposal.json`）→ 无 confirm 的 provision = **409** → confirm 后 2 个 Agent 健康（8110/8111）。
- 测试 B：4 角色 prompt → 动态提案（角色名来自 prompt，非硬编码 Scenario 1）→ provision → 4 Agent 健康 → `POST /groups/{id}/messages` 提交协作目标 → DAG 派发至命名空间 Agent → **4 次 HITL 审批全部消费** → workflow `wf_ee4e2acf…` **completed** → 组 memory 库中存在 2 条 `sandbox:run_python` 真实执行审计（`testB_workflow.json`）。
- 测试 C：同组再提交 → 首个审批**拒绝** → workflow `wf_07b3a5b8…` **failed**，高风险工具执行数 **0**（无假 COMPLETED）。
- 事件流/审批日志：`artifacts/prompt_driven/`（summary.json 15/15）。

## 9. 全量测试与 E2E 结果（本分支，2026-11-23）

| 项 | 结果 |
|---|---|
| `pytest tests/` | **205 passed, 1 skipped**（含同伴 21 项 + 本方 3 项新回归） |
| `ruff --select F,E9` | clean（仅存量无害 F841） |
| Prompt-Driven E2E（Mock，测试 A/B/C） | **15/15 PASS** |
| Phase 0 原始 A2A E2E（legacy 栈） | **PASS**（research→coding(Docker)→analysis(skills)→delegate 全链 completed） |
| Phase 2 Coordinator E2E | **3/3 PASSED**（含并行 overlap 证明） |
| Dynamic Groups E2E（phase5 + 测试 B） | dev-squad workflow **completed**；4 角色团队 workflow **completed** |
| HITL E2E | 测试 B（4 审批消费后 completed）+ 测试 C（拒绝→failed、零执行） |
| Memory 工作流（phase4_verify） | workflow **completed**（3 节点） |

## 10. 与原始系统的兼容性

- Coordinator-only 为默认（`config/orchestration.yaml coordinator_only: true` + `start_coordinator.sh`）；`--legacy` 或 `MAS_COORDINATOR_ONLY=0` 挂载 research/coding/analysis，**Phase 0/Phase 2 原链路在 legacy 模式全绿**（§9）。
- 原 MCP 注册表/ Skills / 双层记忆 / 自主委托路径未改动；`/workflows` 在无 worker 时返回 409 并指引先建 Group（控制面语义清晰化）。
- 旧 Group API（`POST /groups` 直传角色，如 Mid_Test/验收脚本）继续可用：`mcp_tools=None` 时回退整 profile 权限（向后兼容），bundle 化启动/按组 memory/FS/sandbox 根同样生效。
- Mid_Test 成果与验收证据不受影响（独立分支与 artifacts 保留）。

## 11. 已知 Bug 与剩余缺口

已修（本轮）：§4 的 1-3（MCP 能力-工具漂移、Group 下标 500、mock 组路径正则）。
剩余：
- `free_search` server 依赖 `uvx` 联网拉包，离线环境不可用——现在会显式告警而非静默（§4-4），但搜索类能力在离线环境仍不可实测。
- `fetch` MCP 拒绝回环/私网地址（上游安全设计），本地自测需用公网 URL。
- `loss_metrics` 能力仅 analysis profile + loss-analysis Skill 可提供；其他 profile 声明该能力会被一致性校验拒绝（设计如此，需规划端改派）。
- Mid_Test 遗留：Dashboard live 模式字段映射（P0，见 MID_TEST 报告）；runner 不持久化 Token usage；A2A TaskStore 仍 InMemory。
- mock `submit_team` 固件仅解析编号角色行（通用 NLP 依赖真实模型）；Mock 下 release 角色 profile 为 research（固件二值化），真实模型不受此限。

## 12. 最新运行方式与示例命令

```bash
# 默认：Coordinator-only + Mock（不挂 base agents）
bash scripts/phase3_start.sh                 # = start_coordinator.sh --mock
# 兼容模式：挂载 research/coding/analysis（Phase 0/2 回归需要）
bash scripts/phase3_start.sh --legacy

# Prompt 驱动建队（REST）
curl -s -X POST localhost:8000/groups/plan -H 'Content-Type: application/json' \
     -d '{"prompt":"创建 2 个智能体:\n1. Research Analyst: 资料研究与事实核查\n2. Python Developer: 开发、运行和测试"}'
curl -s -X POST localhost:8000/groups/provision -H 'Content-Type: application/json' \
     -d '{"group_id":"<proposal.group_id>","confirmed":true}'
curl -s -X POST localhost:8000/groups/<group_id>/messages -H 'Content-Type: application/json' \
     -d '{"goal":"..."}'
# 或 Dashboard：http://127.0.0.1:8000/dashboard（Create Team From Prompt 卡片：Preview → Confirm & Start）

# CLI（与 cli.py 一致）
python -m agent_ecosystem.cli approvals / approve <id> / reject <id> / clarify <id> "答"
python -m agent_ecosystem.cli group-show <group_id>

# 回归
python -m pytest tests/ -q
bash scripts/prompt_driven_e2e.py 前请先 bash scripts/phase3_start.sh
python -u scripts/prompt_driven_e2e.py       # 测试 A/B/C（15 项）
bash scripts/phase5_verify_group.sh          # Groups + Phase 0
python -u scripts/phase2_e2e.py              # Coordinator 3 场景（需 --legacy 栈）
```

Git-Bash 环境下执行任意 WSL 内联命令（含 `$`/循环/绝对路径）：
`bash scripts/wslr.sh '<wsL bash 命令串>'`（base64 网关，规避 MSYS mangling）。
