# Phase 1 API 与数据设计（PHASE1_API_AND_DATA_DESIGN）

- 日期：2026-11-16；分支：`feature/agentforge-capabilities`
- 本文档定义融合架构的目标形态：数据流、数据结构、协议扩展、DDL、API、管线规格。所有设计以**保留主项目 A2A+MCP+Skills 架构**为前提，AgentForge 资产按 MIGRATION_MATRIX 分类迁入。

---

## 1. 目标架构总览

```
                        用户 / CLI / Benchmark Runner
                                  │ REST (JSON)
                                  ▼
              ┌───────────────────────────────────────────┐
              │ Coordinator 进程 (coordinator.py 扩展)      │
              │  WorkflowService                          │
              │   ├─ DAG Planner (LLM+修复循环, attempts审计)│
              │   ├─ validate_plan (Kahn 拓扑/环/规模)      │
              │   ├─ CapabilityAssigner (全覆盖, 确定tie-break)│
              │   ├─ DAG Scheduler (asyncio 并行+依赖等待    │
              │   │    +级联失败+有界重试+改派+预算)          │
              │   ├─ WorkflowControl (节点级+工作流级 P/R/C) │
              │   └─ Metrics/根因分析/治理链 synthesis→finalize│
              │  HITL: ApprovalService + ClarificationService│
              │  WorkflowStore → data/orchestration.sqlite   │
              │  EventLogger  → data/events.jsonl           │
              └───────┬───────────┬───────────┬────────────┘
        A2A JSON-RPC  │           │           │  metadata: x-agent-delegation
        (x-workflow)  ▼           ▼           ▼             + x-workflow
              research:8001  coding:8002  analysis:8003     （Agent 自主委托保留，
              ┌────────────────────────────────────────┐     route/hop 预算不变）
              │ 每个 Agent 进程（a2a_server 不变式扩展）    │
              │  RoleExecutor → ask_with_tools           │
              │  registry 链（唯一工具通路）：              │
              │   AgentSkillRegistry (skills:load)        │
              │    → AgentDelegationRegistry (a2a:*)      │
              │     → MemoryToolRegistry (memory:read)    │
              │      → ToolGovernance (P5: digest/审批/   │
              │         重放/对账/预算/审计富化)            │
              │       → ToolRegistry (MCP 发现+权限+调用)  │
              │  memory runner (checkpoint/检索/演化/反馈) │
              │  SQLiteTaskStore (P3, A2A 任务持久化)      │
              └───────┬──────────────────┬───────────────┘
                      ▼                  ▼
              MCP servers ×7      data/agent_memory.sqlite（共享，WAL 多进程）
              (sandbox=Docker)    data/orchestration.sqlite（共享，Coordinator 主写）
```

**共存规则（DAG 与 A2A 动态委托）**：
1. Coordinator 负责**全局**：规划、节点分派、依赖、并行、预算、HITL、恢复。
2. Agent 执行节点期间**保留现有自主委托**（dynamic_delegation 的 route/max_hops/max_calls 原样生效）；委托子链挂在该节点的 A2A 任务内部，Coordinator 只观察节点终态与回传证据，不感知子链细节（子链审计仍在 memory 库按 task_id 可查）。
3. 防环三道闸：节点 DAG 环检测（validate_plan）＋ A2A route 防重入（现有）＋ workflow 级预算（max_nodes/max_a2a_calls/deadline）。
4. 节点幂等键：`task_id = {workflow_id}:{node_id}:{attempt}`，Agent 侧 checkpoint 以此为准——重派/重试命中 completed 直接返回缓存（零副作用重复）。

## 2. 核心数据流（一次完整工作流）

```
用户任务 → POST /workflows
→ Planner LLM 产出 ExecutionPlan(JSON) → validate_plan → CapabilityAssigner 全量分配
→ 物化 workflow_nodes（含治理节点 synthesis/finalize）→ 落库 → events.jsonl
→ Scheduler 循环:
   ready = {PENDING ∧ deps 全 COMPLETED} → asyncio.gather(≤并发上限) 派发
     派发 = send_a2a_text(agent_url, 节点 prompt, metadata={x-agent-delegation, x-workflow})
     Agent 侧: RoleExecutor 解析 metadata → run_tool_loop_with_memory(task_id=幂等键)
       工具调用 → ToolGovernance(digest→重放?→对账?→审批?→超时→截断→审计) → MCP
       高危需审批 → A2A 任务态 INPUT_REQUIRED + payload{approval_id,...}
     Coordinator 收 INPUT_REQUIRED → 节点 WAITING_INPUT（兄弟分支继续）
     人审批 → POST /approvals/{id}/decide → 重派节点(metadata 带 approval_tokens, replay_mode=true)
       Agent 重放: 已成功工具从审计缓存出，审批工具凭 token 五元组校验后消费执行
   节点 COMPLETED → 结果+evidence_refs 落库 → 解锁下游
   节点 FAILED → 有界重试(attempt+1) → 改派(candidates) → 仍败则级联失败依赖者
   控制轮询: 每轮循环头读 control 状态（paused/cancelled → 协作式收敛）
→ 全部终态 → synthesis 节点（依赖闭包结果对+工具证据）→ finalize 节点 → workflow COMPLETED
→ metrics 计算落库 → GET /workflows/{id} 返回最终报告
→ (COMPLETED 时) MemoryEvolutionEngine.process_workflow(learn=true)
```

## 3. DAG 节点与工作流数据结构（`orchestration/models.py`）

```python
class NodeStatus(str, Enum):          # 迁移自 AgentForge TaskStatus + WAITING_INPUT
    PENDING="pending"; RUNNING="running"; WAITING_INPUT="waiting_input"
    BLOCKED="blocked"; COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"
TERMINAL_NODE_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})

class WorkflowStatus(str, Enum):
    PENDING="pending"; RUNNING="running"; PAUSED="paused"
    COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"

class SubtaskSpec(BaseModel):          # 迁移，字段不变
    key: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    kind: str = Field(default="generic")   # generic|synthesis|finalize

class ExecutionPlan(BaseModel):        # 迁移，validator 拒重复 key
    goal_summary: str = Field(default="", max_length=2000)
    subtasks: list[SubtaskSpec] = Field(min_length=1)

class WorkflowNode(BaseModel):
    node_id: str = Field(default_factory=lambda: new_id("node"))
    workflow_id: str
    plan_key: str                      # SubtaskSpec.key
    description: str
    required_capabilities: list[str]
    assigned_agent: str                # agent_registry.yaml 的键（research/coding/analysis）
    dependencies: list[str] = []       # node_id 列表
    status: NodeStatus = NodeStatus.PENDING
    attempt: int = 0
    result: str | None = None
    error: str | None = None
    evidence_refs: list[str] = []      # memory 库 tool_results.result_id 列表
    depth: int = 1
    created_at/started_at/finished_at
    metadata: dict = {}                # reassigned_from / waiting_reason / approval_id / replay 等

class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: new_id("wf"))
    goal: str; status: WorkflowStatus
    final_result: str | None; error: str | None
    final_node_id: str | None
    created_at/finished_at
    metrics: WorkflowMetrics           # 迁移 32 字段+A2A 扩展（§10）
    metadata: dict                     # plan_summary/provider_kind/live 等
```

状态迁移表：在 AgentForge 6 态迁移表基础上增加 `RUNNING→WAITING_INPUT`、`WAITING_INPUT→{PENDING(重派),CANCELLED}`；其余原样（终态不可迁出、PENDING 不可直达 COMPLETED）。

**AgentCard/Capability 映射**：`config/agent_registry.yaml` 的 `agents.{name}.{url,description,capabilities}` 直接构造 `AgentCard(agent_id=name, capabilities=[normalize_capability(c) ...])`；A2A `/.well-known/agent-card.json` 的 skills 仅作展示与发现校验（Coordinator 启动时比对 registry 与线上卡片一致性，不一致告警不阻断）。Planner prompt 目录 = registry 的 name+description+capabilities（不注入 A2A 卡片全文，控制 prompt 尺寸）。

## 4. A2A 协议扩展（metadata，向后兼容）

现有 `x-agent-delegation: {trace_id, route}` 不变。新增独立命名空间键（旧 Agent 忽略未知键，渐进部署安全）：

```json
"x-workflow": {
  "workflow_id": "wf_…",
  "node_id": "node_…",
  "attempt": 1,
  "task_id": "wf_…:node_…:1",          // Agent 侧 checkpoint 幂等键
  "replay_mode": false,
  "provider_kind": "mock|live",
  "approval_tokens": [{"approval_id":"approval_…","tool_id":"filesystem_code:write_file","argument_digest":"<sha256>"}],
  "clarification_answer": "…",          // 恢复时回注
  "budget": {"max_tool_calls": 12, "deadline_ts": 1763…},
  "evidence_refs": ["<result_id>", …]   // 上游节点证据（可选，供 prompt 注入）
}
```

**节点任务 prompt 模板**（Coordinator 组装，纯文本走现有 A2A message）：
```
[WORKFLOW TASK] {node.description}
[DEPENDENCY RESULTS] {上游 (description, result[:2000]) 对，低信任标注}
[EVIDENCE SUMMARY] {上游 evidence 摘要（tool_id/status/关键输出[:300]），标注"来自审计记录"}
[CONSTRAINTS] 工具预算 {max_tool_calls}；截止 {deadline}；完成后给出结论与证据引用。
```
（各段沿用 memory runner 的"低信任材料"标注惯例；总尺寸受 ContextManager 预算约束。）

**挂起回传**：Agent 侧审批/澄清挂起时，A2A 任务态置 `TASK_STATE_INPUT_REQUIRED`，artifact 携带 JSON：
`{"suspend":"approval|clarification","approval_id":"…","tool_id":"…","argument_digest":"…","risk_level":"high","reason":"…","question":"…","clarification_id":"…"}`。
Coordinator 解析后置节点 WAITING_INPUT。

## 5. 数据库设计

### 5.1 新库 `data/orchestration.sqlite`（Coordinator 主写；Agent 只读 control/approval 相关表）

```sql
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- schema_version='1'，读写双向

CREATE TABLE workflows (
  workflow_id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
  final_result TEXT, error TEXT, final_node_id TEXT,
  created_at TEXT NOT NULL, finished_at TEXT,
  metrics_json TEXT NOT NULL DEFAULT '{}', metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_workflows_created ON workflows(created_at);

CREATE TABLE workflow_nodes (
  node_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, plan_key TEXT NOT NULL,
  description TEXT NOT NULL, required_capabilities TEXT NOT NULL,  -- JSON list
  assigned_agent TEXT, dependencies TEXT NOT NULL DEFAULT '[]',    -- JSON list
  status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
  result TEXT, error TEXT, evidence_refs TEXT NOT NULL DEFAULT '[]',
  depth INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_nodes_workflow ON workflow_nodes(workflow_id, created_at);
CREATE INDEX idx_nodes_status ON workflow_nodes(workflow_id, status);

CREATE TABLE node_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, node_id TEXT,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);  -- dispatched/completed/failed/retry/reassigned/waiting_input/resumed/cancelled…
CREATE INDEX idx_node_events ON node_events(workflow_id, id);

CREATE TABLE workflow_events (        -- DDL 迁移自 AgentForge
  event_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, event_type TEXT NOT NULL,
  created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_workflow_events ON workflow_events(workflow_id, created_at);

CREATE TABLE approvals ( … );         -- 21 列 DDL 逐字迁移（见 AgentForge persistence/database.py:151-176）
CREATE TABLE clarifications ( … );    -- 14 列 DDL 逐字迁移（+1 列 cancel_reason，修语义复用瑕疵）
CREATE TABLE tool_reconciliations ( … ); -- DDL 逐字迁移，UNIQUE(workflow_id, task_id, tool_name, argument_digest)

CREATE TABLE a2a_tasks (              -- SQLiteTaskStore（P3）
  task_id TEXT PRIMARY KEY, context_id TEXT, agent TEXT NOT NULL,
  state TEXT NOT NULL, payload_json TEXT NOT NULL,   -- Task proto 的 JSON 序列化
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

写入方约定（规避 SQLite 多写者竞争）：workflows/workflow_nodes/node_events/workflow_events 仅 Coordinator 写；approvals/clarifications 由 Coordinator（创建/决策）与 Agent（consume 的 CAS UPDATE）写——CAS 单语句原子，WAL+busy_timeout=10s 下安全；tool_reconciliations 由 Agent 治理层写；a2a_tasks 由各 Agent 自己写（每 agent 只写自己进程的任务，可按 agent 列分库如竞争成为问题）。

### 5.2 `data/agent_memory.sqlite` 扩展（P3 加列 + P4 加列/加表；全部在线迁移、幂等、旧行为零破坏）

```sql
-- P3: tool_results 加列（证据链/重放/对账地基）
ALTER TABLE tool_results ADD COLUMN arguments_digest TEXT;      -- canonical SHA-256
ALTER TABLE tool_results ADD COLUMN is_error INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tool_results ADD COLUMN duration_ms REAL;
ALTER TABLE tool_results ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'real';  -- real|mock
ALTER TABLE tool_results ADD COLUMN provider_kind TEXT NOT NULL DEFAULT 'unknown'; -- mock|live|unknown（LLM 编排来源）
ALTER TABLE tool_results ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'not_required';
CREATE INDEX idx_results_replay ON tool_results(agent_id, task_id, tool_id, arguments_digest);

-- P4: long_term_memories 加列（演化体系）
ALTER TABLE long_term_memories ADD COLUMN namespace TEXT NOT NULL DEFAULT 'experience';
ALTER TABLE long_term_memories ADD COLUMN verification_level TEXT NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE long_term_memories ADD COLUMN importance REAL NOT NULL DEFAULT 0.5;
ALTER TABLE long_term_memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.15;
ALTER TABLE long_term_memories ADD COLUMN source_json TEXT;        -- MemorySource: workflow/task/tool_call_ids/evidence_digest/provider_kind/tool_execution
ALTER TABLE long_term_memories ADD COLUMN metadata_json TEXT;
ALTER TABLE long_term_memories ADD COLUMN embedding_json TEXT;     -- list[float] JSON
ALTER TABLE long_term_memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE long_term_memories ADD COLUMN last_accessed_at TEXT;
ALTER TABLE long_term_memories ADD COLUMN expires_at TEXT;         -- WORKING TTL
ALTER TABLE long_term_memories ADD COLUMN promotion_state TEXT NOT NULL DEFAULT 'none'; -- none|candidate|provisional|promoted
ALTER TABLE long_term_memories ADD COLUMN dedupe_key TEXT;
CREATE UNIQUE INDEX idx_memories_dedupe ON long_term_memories(agent_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
-- kind 枚举扩展 'working'；现有 status 列保留为 promotion_state 粗投影（pending≈candidate, verified≈promoted）

-- P4: 三张新表 DDL 逐字迁移自 AgentForge evolution_store.py:37-124
CREATE TABLE memory_candidates ( candidate_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
  namespace TEXT NOT NULL, signature TEXT NOT NULL, verification_level TEXT NOT NULL,
  content TEXT NOT NULL, procedure_json TEXT NOT NULL, support_count INTEGER NOT NULL DEFAULT 1,
  evidence_workflow_ids_json TEXT NOT NULL DEFAULT '[]', evidence_tool_call_ids_json TEXT NOT NULL DEFAULT '[]',
  evidence_task_ids_json TEXT NOT NULL DEFAULT '[]', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  promoted_memory_id TEXT, status TEXT NOT NULL DEFAULT 'candidate' );
CREATE UNIQUE INDEX idx_candidate_signature ON memory_candidates(agent_id, signature);
CREATE TABLE retrieval_events ( retrieval_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
  namespace TEXT NOT NULL, query TEXT NOT NULL, memory_ids_json TEXT NOT NULL,
  workflow_id TEXT, task_id TEXT, execution_id TEXT, created_at TEXT NOT NULL );
CREATE INDEX idx_retrieval_scope ON retrieval_events(workflow_id, task_id, execution_id);
CREATE TABLE memory_feedback ( feedback_id TEXT PRIMARY KEY, retrieval_id TEXT NOT NULL,
  memory_id TEXT NOT NULL, workflow_id TEXT, task_id TEXT, execution_id TEXT,
  agent_id TEXT NOT NULL, used INTEGER NOT NULL, success INTEGER NOT NULL,
  confidence_delta REAL NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, UNIQUE(retrieval_id, memory_id) );
```

迁移执行：`MemoryDB.__init__` 内检测缺失列/表→逐条 ALTER/CREATE（幂等）→`schema_meta` 记版本。旧行回填：verification_level='UNVERIFIED'（保守），status='verified' 的旧行 promotion_state 置 'promoted' 但 verification 仍 UNVERIFIED（历史词法记忆不冒充证据记忆，检索默认过滤下自然淡出，可被反馈/重验证升级）。

### 5.3 证据分级与晋升（P4 规则，数值与 AgentForge 对齐）

- 分级：节点/任务的全部工具审计中任一 is_error → UNVERIFIED；任一 execution_kind='mock' → MOCK_SIMULATION；否则 REAL_EXECUTION。**LLM 编排来源另记 provider_kind**：provider_kind='mock' 的工作流，其 PROCEDURAL 候选默认不晋升（`allow_mock_promotion=false`），只产 PROVISIONAL+`[MOCK SIMULATION - NOT REAL-WORLD VERIFIED]` 前缀（工具真实执行但决策来自脚本编排的经验不足以证明"该方法有效"）。
- 晋升：signature=sha256(agent_id, 工具序列, 参数提示白名单{path,filename,url,query,remote,branch} 截 200)；support=独立 workflow 数；REAL ∧ support≥2 → confidence=min(0.95, 0.75+0.05·support) ≥0.8 → PROMOTED（importance 0.9）。
- 衰减：`effective = importance × 0.5^(age_days/30) × {REAL 1.0, MOCK 0.55, UNVERIFIED 0.25} × (1+min(0.25, 0.02·access))`；prune：过期 working 删、PROMOTED/REAL 永不删、龄<min(1d, 半衰期/2) 不删、effective<0.05 删。
- 检索：hybrid `score = 0.45·max(0,cos) + 0.25·jaccard + 0.20·importance + 0.10·{1.0/0.45/0.15} + type_boost{proc +0.1, sem +0.04, epi 0, work −0.05}`；默认只 REAL；召回写 retrieval_events（三级 scope）；反馈 ±0.03/−0.10。

## 6. Coordinator REST API（P2 基础 + P3 HITL）

```
POST   /workflows                    {goal, provider_kind?} → 202 {workflow_id}
GET    /workflows                    ?limit&status → 列表
GET    /workflows/{id}               → {workflow, nodes[], metrics, events?}
POST   /workflows/{id}/pause         {reason?, node_id?}  → 200 {status}   # node_id 缺省=整工作流
POST   /workflows/{id}/resume        → 200 {status}
POST   /workflows/{id}/cancel        {reason?} → 200 {status}
GET    /workflows/{id}/result        → 200 {final_result} | 409 {status}
GET    /approvals                    ?workflow_id&status → 列表
GET    /approvals/{id}
POST   /approvals/{id}/decide        {decision: approve|reject, decided_by, comment}
GET    /clarifications  /  GET /clarifications/{id}
POST   /clarifications/{id}/answer   {answer, answered_by}
GET    /health  /agents  /agents/discover     # 现有端点不变
```
鉴权边界：loopback-only（与现有委托策略一致）；decided_by 为自报值——文档明示"单用户本地信任模型"，多用户需另加鉴权层（范围外，记入 RISK）。

## 7. ToolGovernance 管线规格（P5；唯一 choke point，MCP ToolRegistry 仍是唯一执行后端）

```
call(agent_id, tool_id, arguments, ctx{workflow_id?, task_id, budget})
 1 任务级白名单（benchmark/节点 budget 注入；默认关闭）
 2 角色权限（沿用 ToolRegistry，不重复实现）
 3 入参 jsonschema（沿用 ToolRegistry）
 4 digest = arguments_digest(arguments)                      # canonical SHA-256，迁移
 5 重放缓存: replay_mode ∧ ∃(agent,task,tool,digest,status=success) → 返回持久化输出(replayed=true)
 6 对账拦截: ∃ STARTED/UNRESOLVED (workflow,task,tool,digest) → NEEDS_RECONCILIATION 拒绝
 7 审批判定: tool_policy.yaml 规则（tool_id+参数模式→risk/requires_approval；配置驱动，无硬编码工具名）
   7a 有 token: 五元组校验+CAS 消费（迁移 ApprovalService.consume 语义）
   7b 无 token 且需要: 创建 ApprovalRequest(PENDING, TTL) → 返回 suspend 信号（runner 转 A2A INPUT_REQUIRED）
 8 高危 intent 预写: begin_tool_reconciliation(STARTED)；写失败 → DENIED 不执行（fail-closed）
 9 执行: registry.call + asyncio.wait_for(工具级超时；a2a:delegate 240s 现有特例保留)
10 输出治理: 双层截断（min(governance 64KB, runner 预览 8KB)现有语义合并配置化）+ untrusted_output 标注
11 审计: tool_results 行富化（digest/duration/is_error/execution_kind/provider_kind/approval_status）+ node_events/tool 事件
12 对账收尾: persisted→RESOLVED_SUCCEEDED/FAILED；审计写失败→UNRESOLVED+结果强制 NEEDS_RECONCILIATION
守卫（runner 层现有位置强化）: 同 (tool_id,digest) 重复 ≤3；节点工具调用数 ≤budget.max_tool_calls
```

## 8. HITL 时序（审批为例）

```
Agent(节点执行中) Governance 判定高危 → approvals 表落 PENDING 行（参数快照+digest+风险+TTL）
→ A2A 任务态 INPUT_REQUIRED + payload{approval_id,...}（工具未执行，零副作用）
Coordinator 轮询/收到状态 → 节点 RUNNING→WAITING_INPUT（状态机特许迁移）→ node_events(waiting_input)
→ **兄弟并行节点继续调度**；仅该节点下游依赖等待
人: GET /approvals → POST /approvals/{id}/decide(approve)（CAS: 仅 PENDING 可决；过期自动 EXPIRED）
Coordinator: 节点 WAITING_INPUT→PENDING，attempt 不变，metadata.replay_mode=true
→ 重派 A2A（x-workflow.approval_tokens=[{approval_id, tool_id, digest}]）
Agent 重放: 已成功工具→步骤5 缓存命中零重复；审批工具→Governance 7a 五元组校验+消费（一次性 CAS）→ 执行 → intent/对账照常
→ 节点 COMPLETED → 解锁下游
拒绝路径: decide(reject) → 重派带 approval_decision=rejected → Agent 收到 DENIED 结果按失败处理（可改道/上报）
过期路径: TTL 到 → EXPIRED → 节点 FAILED(waiting_expired) 或按策略重发起新审批（默认 FAILED，可配置）
```

崩溃恢复（RESTART RECOVERY）：Coordinator 重启→resume：终态幂等返回；RUNNING 节点→PENDING+replay_mode（A2A 侧 task_id 幂等键保证 Agent 已完成任务直接回缓存）；WAITING_INPUT 节点校验审批/澄清已决（未决→保持等待并提示）；对账 STARTED/UNRESOLVED 行→禁止重放，节点 FAILED(needs_reconciliation) 等人工。

## 9. LLM 门面（P7；保留 AsyncOpenAI 与原生 function-calling）

```python
class LLMErrorClass(str, Enum):  # 迁移 AgentForge 7 类
    NO_RESPONSE_DISCONNECT="no_response_disconnect"; TIMEOUT="timeout"; TRANSPORT="transport"
    HTTP_CLIENT="http_client_error"; HTTP_SERVER="http_server_error"; PARSE="parse_error"; PROVIDER="provider_error"

class LLMCallError(RuntimeError):  # provider/status_code/error_class/details（key 已脱敏）

class ResilientLLMClient:
    """AsyncOpenAI 包装：接口面 = chat.completions.create(**kwargs) 兼容子集"""
    RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
    # 有界重试 max_retries+1；退避 min(0.5·2^n, 4.0)+jitter(0~0.3)；尊重 Retry-After（若返回）
    # 401/403/404 立即抛 HTTP_CLIENT；usage 累计→TokenAccounting（workflow 级预算数据源）
    # 所有错误文本 str.replace(api_key, "[REDACTED]")

class TokenBudgetGuard:  # 迁移 LiveBudgetProvider 语义
    def __init__(*, max_requests: int|None, max_tokens: int|None, live_allowed_env="MAS_LIVE_API_ALLOWED")
    # 未授权且 provider 指向非 mock base_url → 拒绝启动（显式配置错误，绝不静默降级）
```

Planner 结构化输出：首选 function-calling（tools=[plan_schema_function]，tool_choice 强制）取 ExecutionPlan JSON；解析失败→`extract_json_payload` 三级降级→仍败注入 [REPAIR]（回显错误）/[RETRY]（length 截断，不回显）重试 ≤ structured_output_repairs 次；attempts 全落 node_events。

## 10. 指标与报告（P6；覆盖用户任务书 11 项）

RunMetrics（迁移 31 字段+A2A 扩展）与用户指标映射：Task Success Rate=goal_success(verifier PASSED)；Task Correctness=verifier checks 通过比例；Completion Time=end_to_end/active_seconds；Token Consumption=prompt/completion/total（live=provider usage，mock=估算并分账）；Tool Calls/Delegation Count/Failure Rate/Recovery Success Rate=审计表直算；Memory Retrieval Rate=retrieval_events/任务数；Memory Usage Rate=feedback.used 占比；Memory-assisted Success Rate=有记忆命中任务的成功率 vs 无命中对照。聚合：p50/p95、按类别、A/B/C 对照（同任务/模型/预算/验证器）；报告四格式+MOCK/LIVE 横幅+“needs_review/skipped 不算成功、无显著性声明”。

## 11. Mock/Real 分账与授权门（全链路强制）

- 每条执行记录带 `provider_kind`（memory 库列 §5.2）与 `execution_kind`；benchmark 每条 run 带 provider_kind 枚举。
- 记忆晋升默认拒绝 mock 编排证据（§5.3）；检索结果强制 verification 前缀标签。
- 报告横幅与 CSV 列双通道防止混报；`benchmark live`/阶梯验收需 `--allow-live-api` ∧ key 已配置双条件，否则全 NOT_VERIFIED。
- 环境变量：`MAS_LIVE_API_ALLOWED=1`（进程级总闸）；预算默认 120 req/250k token，快照逐级落盘。
