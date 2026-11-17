# Phase 2 架构文档（PHASE2_ARCHITECTURE）

- 日期：2026-11-17
- 分支：`feature/agentforge-capabilities`；Commits：`cf33bb2` `8af3cfb` `e9f86a0`
- 本文档描述 Phase 2 新增/修改模块的架构、关键设计决策、与现有系统的共存关系。

---

## 1. 模块全景

```
Coordinator 进程 (8000)
 ┌──────────────────────────────────────────────────────┐
 │ coordinator.py  (FastAPI app factory + REST)          │
 │  WorkflowService  (orchestration/service.py)          │
 │   ├─ Planner  (orchestration/planner.py)              │
 │   │   ├─ validate_plan  (Kahn 拓扑 + 环/限/深)       │
 │   │   ├─ CapabilityAssigner  (orchestration/assignment)│
 │   │   └─ LLM w/ StructuredOutputAdapter               │
 │   ├─ DagScheduler  (orchestration/scheduler.py)       │
 │   │   ├─ SINGLE-WRITER main loop (state transitions)  │
 │   │   ├─ asyncio.gather workers (dispatch I/O only)   │
 │   │   └─ Budgets: retry / reassign / deadline / count │
 │   ├─ Evidence Collector  (read shared SQLite)         │
 │   └─ WorkflowStore  (orchestration/store.py, IF)      │
 └──────┬──────────────────────────────┬────────────────┘
        │ A2A JSON-RPC                 │
        ▼ x-workflow metadata          ▼
 research:8001  coding:8002  analysis:8003  ← agent 进程不变
 registry 链：Skill → Delegation → Memory → (Governance P5) → Tool → MCP
```

---

## 2. Planner — Dynamic DAG Generation

**输入**: 自然语言 goal + agent capability catalog (从 `config/agent_registry.yaml` 构建)。

**LLM 阶段**:
1. `build_planner_messages(goal, catalog)` → `[system: PLANNER_PROTOCOL, user: goal(catalog/limits)]`
2. `Planner.plan_goal()` →
   - 调用 `client.chat.completions.create`（支持 3 种输出模式：function/json/text，automatic mode 从 function 开始，若 provider 拒绝则降级）
   - `extract_json_payload` 三级降级（Fence/Full/Strip）
   - `ExecutionPlan.model_validate` (Pydantic v2, extra=forbid)
   - 有界修复循环 ≤ `structured_output_repairs` (default 1)：
     * `finish_reason=length` → ` [RETRY]` (prompt from base_messages rebuild, truncated content NOT echoed back to keep the prompt small – fact learned from AgentForge's HKGAI incidents)
     * 其他错误 → ` [REPAIR]` (echo back the assistant's output before rephrasing)
   - 能力覆盖失败 → 立即拒绝 (`TaskAssignmentError`)，不消耗修复预算

**验证阶段**（`validate_plan`, ported as-is from AgentForge `orchestration/planner.py`):
- 空计划 / 规模限制 (`max_subtasks`, default 8)
- 自依赖 / 未知键
- **Kahn 拓扑排序 + 环检测**（稳定 tiebreak：按原序）
- 依赖深度限制 (`max_depth`, default 6)

**能力分配**（`CapabilityAssigner`, ported as-is from AgentForge `orchestration/assignment.py`):
- 对于每项必需的能力，必须 **100% 满足**（full coverage）
- 确定性 tiebreak：分数 (-score, len(capabilities), agent_id 字典序)
- 分配发生在物化之前（能力缺口→零残留节点）

**关键设计决策**: 能力分配依赖的是 `config/agent_registry.yaml`（不复刻第二套 Agent 注册表）。Planner 不决定 Agent（模型明确被要求不分配 Agent），由平台代码完成后分配。

---

## 3. Scheduler — True Parallel asyncio DAG

### Single-Writer 并发安全性

仅**主循环** (scheduler 单个 asyncio 任务) 修改共享状态 (节点/工作流/事件)。每个节点的工作协程 (worked coroutine) **只执行 I/O**（A2A 派发 / 等待），返回 `DispatchOutcome`，**完全不触碰 store**。

```
main loop:
  while True:
    check 控制标志 / 截止时间 / 预算
    ready = {PENDING + 所有 deps 已完成}
    对每个 ready 节点（最多 max_concurrency 个）→ transition RUNNING → create_task(_worker)
    await FIRST_COMPLETED → apply outcomes (mutate store HERE) → cascade fail / retry / reassign
    if no running & no non-terminal nodes → return
    if no running & stuck non-terminal nodes → deadlock error
```

### worker 协程

```python
async def _worker(workflow, node, agent, task_id):
    try:
        outcome = await asyncio.wait_for(dispatcher(node, workflow, agent, task_id), node_timeout)
    except TimeoutError:
        outcome = DispatchOutcome(ok=False, error="timeout", error_kind="timeout")
    return outcome
```

### 重试/改派策略

- **Time out / downstream fail** → bounded retry, **同样的 agent 和同样的 task_id**（idempotency key unchanged → agent 的 checkpoint 直接返回缓存结果，不会重复执行工具的副作用）
- **Agent unreachable (connect error)** →  bounded reassignment（切换到能胜任的 agent，`attempt++`）

### Concurrency proof

- Unit: `test_two_independent_nodes_really_overlap_barrier` — 两个 asyncio.Event 互相等待，硬断言两个 worker 必须在同一时刻在 dispatch 内部（不可能被串行执行）。
- Cross‑process: `scripts/phase2_audit_concurrency.py` — three nodes dispatched to three real agent processes; 12.5s monotonic overlap, 77.9s wall‑clock vs 117.7s total execution (proves speedup).

---

## 4. A2A Integration — minimal backward-compatible extension

**新增的 metadata 字段模式**（独立于现有的 `x-agent-delegation` 之下）：

```json
"x-workflow": {
  "workflow_id": "wf_…",
  "node_id": "node_…",
  "attempt": 0,
  "task_id": "wf_…:node_…:0",
  "idempotency_key": "wf_…:node_…:0",
  "parent_task_id": "wf_…",
  "budget": { "max_tool_calls": 12, "deadline_ts": 1763… },
  "replay_mode": false,
  "approval_tokens": []
}
```

**Agent‑side parsing** (`a2a_server.py:_workflow_context`):  
将 `x-workflow` 映射到 `task_id`/`parent_task_id`/`max_tool_calls`，向后兼容——如果无 `x-workflow`，使用现有的自主委托模式（Phase 0 E2E 仍然 PASSES）。同时处理 protobuf Struct 浮点数→整数的问题（a2a‑sdk 1.3.0 的已知问题）。

**Delegation 权利**：agent 在调度期间保留了自主委托的能力（`x-agent-delegation` 路由仍然保留，hop 预算不变）。

---

## 5. Evidence Collection

```python
def collect_evidence(agent_id, task_id, memory_db_path) -> dict
```

直接读取**与 agent 共享的** `data/agent_memory.sqlite`。对于每个节点的 `task_id`：读取 `tasks`/`task_events`（tool_result 行）/`tool_results` 表 → 返回 `executed_tools`/`tool_events`/`result_ids`。协调器的合成提示包含这些引用（"来自工具审计记录……"），无论是 Agent 自述还是 LLM 的胡言乱语均不包容。

---

## 6. Phase 3 HITL 接口预留

| 预留内容 | 当前状态 |
|---|---|
| `NodeStatus.WAITING_INPUT` | State machine fully defined (RUNNING→WAITING_INPUT, WAITING_INPUT→{PENDING,CANCELLED})；tested in unit. 不用于调度（schedule still treats it as non‑terminal)。 |
| `x-workflow.approval_tokens` | Field stubbed as `[]`；coordinator always sends an empty list. |
| `x-workflow.replay_mode` | Field always `false`. |
| `WorkflowStore` interface | Abstract class designed with all required CRUD methods, ready for SQLite implementation (Phase 3). |

---

## 7. Config 与默认值

全部编排预算从 `config/orchestration.yaml` 加载（默认值取自 AgentForge 的安全限制）：

```yaml
max_subtasks: 8            planner_output_mode: auto
max_nodes: 12              structured_output_repairs: 1
max_depth: 6               max_concurrency: 3
node_timeout_seconds: 300  deadline_seconds: 900
max_node_retries: 1        max_reassignments: 1
max_a2a_calls: 24          node_tool_budget: 12
synthesis_enabled: true    synthesis_capability: evidence_synthesis
```

---

## 8. 与 AgentForge 迁移对应表

| AgentForge asset | Phase 2 porting |
|---|---|
| `orchestration/planner.py` (validate_plan) | As‑is port → `orchestration/planner.py` |
| `orchestration/state_machine.py` (transition) | As‑is port + `WAITING_INPUT` → `orchestration/state_machine.py` |
| `orchestration/assignment.py` (CapabilityAssigner) | As‑is port → `orchestration/assignment.py` |
| `orchestration/errors.py` | As‑is port → `orchestration/errors.py` |
| `persistence/events.py` (EventLogger) | As‑is port → `src/agent_ecosystem/events.py` |
| `orchestrator.py` (_run_graph / budgets / cascade) | Blueprint → `scheduler.py` (rewritten: serial → parallel, in‑process→A2A) |
| `orchestrator.py` (_materialize / governance chain) | Blueprint → `service.py` (more lightweight: synthesis only, no review, capability‑aware) |
| `agents/coordinator.py` (plan_goal) | Repair loop + protocol patterns → `planner.py:Planner` (adapted: native function‑calling, not content-embedded JSON envelope) |
| `agents/base.py` (extract_json_payload / _recall cache) | As‑is port of `extract_json_payload` → `planner.py` |
| `config/settings.py` (budget defaults) | → `config/orchestration.yaml` + `settings.py` extension |
| `persistence/database.py` (DDL) | → reserved for P3 in `orchestration/store.py` |

**明确不迁移**（AgentForge 单进程假设）：MessageBus / BaseAgent 循环 / Runtime / 内置工具 / 嵌入式 JSON 动作协议。