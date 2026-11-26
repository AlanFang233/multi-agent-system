# Full-System Re-Verification Report（整合后全系统复验）

**Date**: 2026-11-23 | **Branch**: `feature/prompt-driven-integration`
**Commits**: `b065e0d`（上游/同伴）→ `e61c3f1`（整合修复）→ 本复验提交（harness 修正 + 本报告）
**Mode**: 全部 E2E 使用 Mock LLM + 真实 MCP/Docker/A2A/SQLite；未调用付费 API。
**两种启动模式均验证**：Coordinator-only（新默认）与 `--legacy`（挂载 base agents 的兼容模式）。

---

## 1. 复验矩阵

| # | 套件 / 场景 | 栈模式 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | `pytest tests/`（205 项，含同伴 21 + 本方 6 新回归） | 离线 | **205 passed, 1 skipped** | 终端输出 |
| 2 | `ruff --select F,E9` | 离线 | **clean**（仅存量无害 F841） | 终端输出 |
| 3 | 全系统验收 `scripts/acceptance_e2e.py`（33 项：Task A/B/拒绝/澄清/Group/重启恢复/僵尸/隔离/清理） | legacy | **33/33 PASS** | `data/acceptance/summary.json`、`logs/acceptance.log` |
| 4 | Prompt-Driven E2E `scripts/prompt_driven_e2e.py`（15 项：测试 A/B/C） | coordinator-only | **15/15 PASS** | `artifacts/prompt_driven/summary.json` |
| 5 | Phase 0 原始 A2A E2E（research→coding(Docker)→analysis(Skills)→delegate） | legacy | **PASS**（`E2E_RESULT: PASS`） | 终端输出 |
| 6 | Phase 2 Coordinator E2E（A/B/C 三场景，含并行 overlap 证明） | legacy | **3/3 PASSED** | 终端输出 |
| 7 | Dynamic Groups E2E（phase5：dev-squad 创建/激活/消息） | coordinator-only | group workflow **completed**（3/3 节点） | `wf_27d66552…` |
| 8 | HITL E2E（审批消费/拒绝零执行/假 COMPLETED 回归） | 两模式 | acceptance B1-B4、R1-R4 + PD B3、C1 全 PASS | 同 #3/#4 |
| 9 | Memory 工作流（phase4_verify） | legacy | workflow **completed**（3 节点） | `wf_1cc926e8…` |
| 10 | Memory 单元/集成（test_phase4_memory 等） | 离线 | 含于 #1 | — |

## 2. 本轮复验发现并修正的问题（均为测试装置/环境，非产品缺陷）

1. **按组 memory DB 查询目标**：同伴 b065e0d 起 Group 记忆写入 bundle 的
   `mcp_workspace/sandboxes/<gid>/agent_memory.sqlite`（MAS_MEMORY_CONFIG），验收脚本仍查默认
   DB → C4/IS5/IS6 假失败。修正 `acceptance_e2e.py`：`group_mem_db(gid)` + 回退逻辑；复跑 33/33。
   （隔离实际上更强：组间物理分库。）
2. **acceptance RS 重启语义**：仓库默认改为 coordinator-only 后，崩溃恢复步骤重启的协调器不再
   挂载 base workers → 后续 base 步骤 409。修正：RS 重启显式 `MAS_COORDINATOR_ONLY=0`。
3. **`start_coordinator.sh` 端口守卫静默跳过**：端口 8000 被占时脚本 exit 1，调用方（phase5）
   误以为已切换模式 → 出现"coordinator-only + 手挂 agents"的混合栈（Phase 2 409 的根因）。
   处置：切换模式前先 `pkill` 协调器；已在本报告记录，建议后续在脚本中打印更显眼的告警（P2）。
4. **外层 `timeout` 杀调用链会连带 graceful 终止 nohup 服务**：复验改为分步调用并留余量；
   另 WSL VM 空闲回收仍需 keep-alive（`scripts/wslr.sh` 说明已含此注意事项）。

## 3. 架构行为复核（对照同伴设计）

- Coordinator-only 默认：`/agents` 仅控制面；`/workflows` 无 worker 时 409 并指引建 Group ✓
- `--legacy` 兼容：base 三 Agent 挂载后 Phase 0/2 全绿 ✓（原架构未被破坏）
- Prompt→Preview→Confirm→Provision→Activate：未 confirm 的 provision=409；proposal 落盘
  `awaiting_confirmation`；确认后 4 类产物（mcp/agent/skills/memory 注册表 + manifest）生成 ✓
- 动态 Agent 真实参与 DAG：PD B3 节点派发至命名空间 Agent 且 completed；审批 4/4 消费 ✓
- Group 上下文/产物继承：按组 memory DB + runtime workspace 跨任务保留（Mid_Test Task 2 已证） ✓
- MCP 最小权限：enforce_required_tools 后提案工具集 = 能力必需 ∪ LLM 提案（⊆ profile）；越权
  调用 PermissionError；不可用 server 显式告警 ✓

## 4. 结论

整合分支在**两种启动模式**下全系统复验通过：33/33 全系统验收 + 15/15 Prompt-Driven +
Phase 0/2/4/5 回归 + 205 单元/集成测试。同伴的 Prompt-Driven 架构与本方 Coordinator/DAG/HITL/
Memory/MCP 能力已真正整合（复用其 team_builder/coordinator_skills/bundle 启动链，本方仅做
最小修复：能力→工具充分性、Group 下标 500、mock 组路径、不可用工具告警）。

剩余缺口（不阻塞）：Dashboard live 字段映射（Mid_Test P0）、free_search 离线不可用（显式告警
已就位）、runner 不持久化 Token usage、A2A TaskStore 仍 InMemory、phase5 端口守卫告警体验（P2）。

**建议**：本分支可推送 fork 并开/更新 PR 供同伴审阅（本轮按指示仅本地提交，未 Push）。
