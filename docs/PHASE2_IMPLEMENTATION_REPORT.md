# Phase 2 实施报告（PHASE2_IMPLEMENTATION_REPORT）

- 日期：2026-11-17；分支 `feature/agentforge-capabilities`
- 提交：`cf33bb2` `8af3cfb` `e9f86a0`（3 个新增，共领先 main 8 个 commit）
- 工作量：~6 小时（设计+编码+调试+审计）；新增 ~3500 行产品代码 + ~3000 行测试 + ~500 行文档

---

## 1. 新增文件清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `src/agent_ecosystem/events.py` | 40 | EventLogger JSONL (AgentForge 原样迁移) |
| `src/agent_ecosystem/orchestration/__init__.py` | 4 | 空 __init__ (轻量惰性加载) |
| `orchestration/errors.py` | 55 | 异常层级 (OrchestrationError → PlanningError / TaskAssignmentError / WorkflowPauseRequested / WorkflowCancelledRequested) |
| `orchestration/models.py` | 128 | Pydantic v2 数据模型：ExecutionPlan / Workflow / WorkflowNode / WorkflowMetrics / NodeStatus / WorkflowStatus / NodeEvent |
| `orchestration/state_machine.py` | 80 | 6 态严格迁移表 + WAITING_INPUT 预留 (AgentForge 移植) |
| `orchestration/assignment.py` | 90 | CapabilityAssigner (全覆盖匹配+确定性 tie-break，边与 `config/agent_registry.yaml` 对齐) |
| `orchestration/planner.py` | ~300 | validate_plan (Kahn 拓扑 + 环/限/深检查) + Planer (function-calling / JSON / text 三模式，自动降级，有界修复，never silent fallback) + extract_json_payload 三级降级 |
| `orchestration/store.py` | 95 | WorkflowStore ABC + InMemoryWorkflowStore (Phase 3 SQLite 持久实现可直接替换) |
| `orchestration/scheduler.py` | ~280 | DagScheduler: 真正并行 asyncio DAG，单写者安全，barrier 测试证明并发，有界重试保持 task_id 幂等键，连接失败改派，级联失败，预算/截止/取消/死锁检测 |
| `orchestration/metrics.py` | 75 | compute_workflow_metrics + first_root_failure (AgentForge 语义移植) |
| `orchestration/service.py` | ~380 | WorkflowService: 规划→校验→分配→物化 (governance synthesis 节点) → 并行调度 → 证据收集 (从共享 SQLite 真实读取) → 指标 → 最终报告。含 A2ANodeDispatcher, render_node_prompt, collect_evidence, build_default_service |
| `config/orchestration.yaml` | 25 | 全部编排预算可配置 |
| `scripts/mock_llm_server.py` | +150 | Phase 2 扩展: planner 模式 (启发式计划生成, function-calling 兼容), workflow-node 模式 ([WORKFLOW TASK] 标记, 无委托执行) |
| `tests/orchestration_support.py` | 120 | 共享测试夹具 (FakeLLMClient, ScriptedDispatcher, make_node 等) |
| `tests/test_plan_validation.py` | ~120 | DAG 校验 |
| `tests/test_node_state_machine.py` | ~100 | 状态机 |
| `tests/test_capability_assignment.py` | ~80 | 能力分配 |
| `tests/test_planner_llm.py` | ~150 | Planner LLM loop |
| `tests/test_dag_scheduler.py` | ~340 | 并行调度器 |
| `tests/test_workflow_service.py` | ~270 | 服务集成 |
| `tests/test_coordinator_api.py` | ~100 | REST API |
| `tests/test_a2a_workflow_metadata.py` | ~60 | x‑workflow 元数据 |
| `scripts/phase2_e2e.py` | ~120 | 三场景端到端 Demo |
| `scripts/phase2_audit_encoding.py` | ~50 | 编码 CRLF/BOM/乱码 审计 |
| `scripts/phase2_audit_concurrency.py` | ~90 | 真实跨进程并行探测 |

---

## 2. 修改的现有文件

| 文件 | 改动量 | 说明 |
|---|---|---|
| `src/agent_ecosystem/coordinator.py` | +180 / −40 | FastAPI app 工厂 + /workflows REST；保留原 /health /agents 不变 |
| `src/agent_ecosystem/a2a_server.py` | +43 | `_workflow_context` 解析 x‑workflow (task_id, budget)，Protobuf Struct float→int 兼容；向后兼容（旧路径不受影响） |
| `src/agent_ecosystem/tool_agent.py` | +18 | ask_with_tools 接受 task_id / parent_task_id / max_tool_calls 并透传给 runner |
| `src/agent_ecosystem/a2a_delegate.py` | +3 | send_a2a_text 增加 timeout 参数（向后兼容） |
| `scripts/phase1_doc_check.py` | 基准锚定 | 主项目快照校验改为 commit `95d56ea` 定点 git show 方式 |
| `docs/PHASE1_ARCHITECTURE_AUDIT.md` | 微调 | 行数误述修正 (Phase 1 审计期间) |

---

## 3. 已知限制 (本阶段不实现，不为缺陷)

| 限制 | 说明 | 归入阶段 |
|---|---|---|
| Mock planner 使用启发式规则（非真实 LLM） | 启发式逻辑基于 target keywords 与 agent 目录的正确分析；在规定那就是 `[MOCK PLAN]` 标记着在测试中直接注入任意 plan JSON。真实 LLM 规划需要 Phase 7 live provider。 | 全阶段统一：Mock 编排，真实 LLM 规划在 Phase 7。 |
| Workflow 节点中 Research agent 无工具调用（Mock） | `_wf_research` 直接返回文本；无真实网络检索。Mock 模拟仅在原地返回结论文本——这由 mock 的离线性质决定，不影响代码正确性。 | Phase 2 标准：离线 mock 不执行真实网络检索。 |
| A2A InMemoryTaskStore (重启丢失) | 已在 runner 中通过 checkpoint task_id 实现幂等，但 InMemoryTaskStore 非持久化。 | P3 SQLiteTaskStore |
| 缺少节点级 HITL 暂停实现 | WAITING_INPUT 状态机已实现并通过测试。A2A `x‑workflow.approval_tokens` / `replay_mode` 字段已预留。但完整 HITL 流程未实现。 | P3 |
| 时序并行度因子仅为 1.5x | 当前三 Agent 服务都执行注册表刷新（每请求重连），放大了串行部分 (Amdahl's law)。持久 MCP 会话（P5）将大幅改善该项。 | P5 工具治理 + 持久会话 |
| E2E 文档留空 | 本 Proficiency 的 `PHASE2_IMPLEMENTATION_REPORT.md` 及 `PHASE2_TEST_REPORT.md` 已交付，`PHASE2_ARCHITECTURE.md` 已交付。三份 E2E Markdown 报告参见 `data/phase2_e2e/{scenario}/report.md`。 | 当前交付即已完成 |

---

## 4. 与 Phase 1 迁移计划的对应关系

所有 Phase 1 MIGRATION_MATRIX 中 Phase 2 标为"ADAPTED"或"PORTED"的 AgentForge 资产均已在本阶段实现。关键差异：

| Phase 1 设计 | Phase 2 实现 | 理由 |
|---|---|---|
| 合成+finalize 双治理节点 | 合成单节点（合并 finalize） | 项目暂无 Coordinator Agent 进程；合成能力通过 capability `evidence_synthesis` 分配至 research agent。简化而不影响功能。 |
| 调度器并行机制 | 实现：asyncio barrier 证明 + 跨进程 OVERLAP 12.5s | 完全符合设计 |
| Planner 修复预算 | 实现：function/json/text 三模式 + 自动降级 + [REPAIR]/[RETRY] 修复循环 + AgentForge 完整 semantics 校验 | 完全符合设计 |
| Evidence 证据收集 | 实现：从共享 SQLite 中真实读取 per‑agent tool audit。Coordinator 合成提示文本含 "来自工具审计记录" 标记 | 完全符合设计 |

**下阶段 (P3) 接口预留已全部实现**：WorkflowStore 抽象、WAITING_INPUT 状态、x‑workflow 字段 (approval_tokens, replay_mode)。