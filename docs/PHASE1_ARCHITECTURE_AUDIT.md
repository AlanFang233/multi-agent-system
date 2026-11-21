# Phase 1 架构审计报告（PHASE1_ARCHITECTURE_AUDIT）

- 日期：2026-11-16
- 分支：`feature/agentforge-capabilities`（主项目 multi-agent-system）
- 审计对象：
  - 主项目 `AlanFang233/multi-agent-system` @ `a95995d`（WSL 工作副本 `~/projects/multi-agent-system`，含 Phase 0 的 4 个修复 commit）
  - 参考项目 `Xzh1844963039/AgentForge` @ `7cee900`（独立只读克隆 `D:\AgentForge-audit`，未做任何修改）
- 审计方法：全部结论来自**逐文件源码阅读**（6 组并行深读 + 关键结论人工抽查复核），不依赖 README 或历史记忆；主项目侧结论同时有 Phase 0 实测证据（46 pytest + Mock E2E 全链路 PASS）支撑。

---

## 1. 主项目 multi-agent-system：实际实现了什么

规模：`src/agent_ecosystem` 22 个 py 文件约 2,500 行 + 测试 11 个文件约 1,100 行（Phase 0 另新增 `test_a2a_metadata.py`）+ 5 个 SKILL.md + 4 个 YAML 配置。**定位是"薄编排、厚协议"的分布式 Agent  starter**，全部治理逻辑在注册表层，Agent 进程无状态。

| 模块 | 文件（行数） | 实际实现 | Phase 0 实测 |
|---|---|---|---|
| A2A Server | `a2a_server.py` (220) | a2a-sdk 1.3.0 JSON-RPC server；`/health`、AgentCard（`/.well-known/agent-card.json`）；`RoleExecutor.execute` 解析 `x-agent-delegation` metadata（route/trace_id）；无 key 时降级 DEMO 回答；`InMemoryTaskStore`（**任务不持久化**）；`cancel()` 未实现 | ✅ 三进程健康、卡片可解析、跨进程委托成功（修复 Struct bug 后） |
| 工具循环 | `tool_agent.py` (393) | Chat Completions 原生 function-calling 循环；`SYSTEM_PROMPTS`=角色 prompt+动态技能策略（~2.6k 字符）；工具名消毒 `mcp_{i}_{safe_id}`；输出 8000 字符截断；`force_tool` 测试钩子 | ✅ Mock E2E 全链路 |
| MCP 注册表 | `tool_registry.py` (122) | YAML 声明 7 server（http/stdio）；`refresh()` 逐 server 发现并分页拉取工具，失败 server 整体剔除；`list_for_agent` 角色白名单；`call()` 权限校验+jsonschema 入参校验；**每次调用重连**（docstring 自认待改持久会话）；Phase 0 已加 `_expand`（$HOME/~ 展开） | ✅ 56 工具/7 server；越权拒绝验证 |
| 动态委托 | `dynamic_delegation.py` (180) | 能力驱动 A2A 委托：loopback-only URL 校验、route 防环（同 agent 不重入）、`max_hops=2`/`max_delegations_per_task=3`/`timeout=120s`、trace_id、`a2a:discover_agents`+`a2a:delegate_task` 两个虚拟工具、委托目标 enum 动态生成 | ✅ E2E 中 research→coding→analysis 两跳委托 |
| 静态委托（旧） | `agent_delegation.py` (163) | `delegation:` 白名单 DAG + 环检测；**未接入运行路径**（tool_agent 只用 dynamic），仅 2 个测试文件引用 | ⚠️ 死代码候选（见 §5.4） |
| Skills | `agent_skill_registry.py` (193) | 按角色叠加 `skills:load`（SKILL.md frontmatter 校验、路径越权防护、10KB 上限、同名防重复加载）；analysis 专属确定性工具 `skills:loss_metrics`；技能说明**按需加载**而非常驻 prompt | ✅ E2E 中 analysis 加载 loss-analysis 并执行 loss_metrics |
| 记忆存储 | `memory/store.py` (247) | 单文件 SQLite（WAL，0600 权限）5 表：`tasks`/`task_events`/`checkpoints`/`tool_results`/`long_term_memories`；密钥 redact；长期记忆 kind∈{episodic,semantic,procedural}、status∈{pending,verified,rejected}、内容 sha256 去重（evidence_count 累加）；**词法检索**（词+中文 bigram 重叠打分），只召回 verified | ✅ 全生命周期 PASS（写/审/检/去重/隔离/checkpoint） |
| 记忆运行器 | `memory/runner.py` (261) | `run_tool_loop_with_memory`：任务幂等（completed 直接返回缓存答案）、断点 checkpoint（批后落盘）、长期记忆检索注入（低信任标注）、`memory:read_result` 分页回读原始工具结果、上下文分组压缩、工具结果外置存储（1MB 截断）+预览 | ✅ E2E 二次运行 `[LongMemory 检索] 命中 1 条` |
| 上下文压缩 | `memory/context.py` (119) | assistant tool_calls+tool 响应原子分组、字符预算、超限抛 `ContextLimitError`（不静默裁剪目标/工具声明） | ✅ 测试覆盖 |
| 证据反思 | `memory/reflection.py` (59) | 规则式、证据优先：只有**命中已知成功路由**（git_status/loss_metrics/delegate_task）或特定错误模式才产 verified procedural 记忆；重复调用≥3 次产 pending 提示 | ✅ E2E 生成 3 条 verified 记忆 |
| 沙箱 | `sandbox_mcp_server.py` (210) | 3 工具（create/run_python/run_pytest）；`docker run --rm` 一次性容器：`--network none --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 --memory 512m --cpus 1 --user 65534:65534 --pull=never` + 只读 bind + tmpfs；路径穿越/symlink/非 .py 拒绝；输出 16KB/流；≤90s；**Docker 不可用时报错不回退宿主机** | ✅ 真实执行 PASS（uid=65534、NETWORK_BLOCKED、容器内 pytest 通过） |
| Coordinator | `coordinator.py` (43) | FastAPI：`/health`、`/agents`、`/agents/discover`（拉三 agent 卡片）。**仅发现，无任何编排/规划** | ✅ |
| A2A 客户端 | `a2a_delegate.py` (90) | 裸 JSON-RPC `SendMessage`（blocking），metadata 透传，artifact 文本提取，`TASK_STATE_COMPLETED` 校验；httpx timeout=120 | ✅ |
| 配置 | `config/*.yaml`、`settings.py` | mcp_registry（7 server+3 角色权限）、agent_registry（capabilities+policy；Phase 0 清理了重复 `agents:` 键）、agent_skills、memory（预算 128k/96k） | ✅ |

**主项目没有的**：全局任务规划（DAG）、工作流状态机与持久化、并行调度、审批/澄清/暂停恢复、记忆演化（晋升/置信度/衰减/embedding/反馈闭环）、工具调用治理（超时在 runner 层有，但无审计 digest/次数预算/重复守卫/审批钩子）、LLM 重试与错误分类、任何评测框架。

---

## 2. AgentForge：实际实现了什么（逐模块）

规模：98 个源文件 25,620 行 + 42 个测试文件 9,602 行（审计中实测复跑：312 passed + 4 环境性 skip，零失败）。**定位是"胖编排、薄 Agent"的单进程治理平台**：LLM 只产出结构化决策，全部路由/状态/预算/持久化由平台代码强制。

### 2.1 Coordinator / DAG Planner

- `orchestration/planner.py`（65 行）：唯一函数 `validate_plan(plan: ExecutionPlan, *, max_subtasks: int) -> list[SubtaskSpec]`。校验顺序：空计划→规模上限→自依赖→未知依赖→**Kahn 拓扑排序+环检测**（同层按计划原始顺序稳定 tie-break）。纯函数零 IO，**可原样复用**。
- `agents/coordinator.py`（221 行）：`CoordinatorAgent.plan_goal(goal, *, agent_cards, max_subtasks) -> PlanningResult`；PLANNER_PROTOCOL 完整/compact 双协议（compact <1100 字符，HKGAI 特化）；解析失败注入 `[REPAIR]`（回显错误）或 `[RETRY]`（finish_reason=length 时不回显截断内容）重试，`max_attempts = 1 + max_output_repairs`；全败抛 `PlanningError(message, attempts=[...])`（每次尝试落库审计）。
- `models/task.py`：`ExecutionPlan{goal_summary, subtasks[min 1]}`（model_validator 拒重复 key）；`SubtaskSpec{key(≤80), description, required_capabilities[min 1], depends_on, kind="generic"}`。
- 能力覆盖检查**不在 planner**，在物化计划时由 CapabilityAssigner 完成（先全量分配再建任务，能力缺口→零残留任务+工作流失败）。

### 2.2 Orchestrator（`orchestration/orchestrator.py`，1750 行）

- `TaskOrchestrator.run_goal(goal, conversation_id) -> Workflow` / `resume_goal(workflow_id)` / `pause_workflow` / `cancel_workflow` / `get_workflow_status`。
- 主流程：建 Workflow+root Task → coordinator.plan_goal → `_materialize_plan`（预算预检 `1+len(ordered)+policy_count ≤ max_tasks_per_workflow`；能力分配；key→task_id 依赖翻译；**平台强制追加治理链 synthesis→(review)→finalize**，LLM 被明令禁止自加 review）→ `_run_graph` 循环调度。
- **调度是串行的**（人工抽查确认：orchestration+runtime 无任何 `asyncio.gather/create_task/TaskGroup`）：每轮扫描 BLOCKED（子任务全终态则恢复/失败）→ PENDING（依赖全 COMPLETED 则执行；依赖 FAILED 级联失败）；全终态退出；一轮零进展抛死锁错误。"并行"只存在于数据模型，**这是迁移时必须重写的部分**。
- `_execute_task`：步数预算（per-run）+ LLM 调用预算（跨 resume，DB 全表计数 O(n²) 倾向）→ RUNNING → bus 投递 TASK_REQUEST + inbox 握手（**纯进程内假设**）→ `agent.execute_task(...)` 注入依赖结果对、委托结果对、**工具证据**（`_collect_tool_evidence`：BFS 依赖+委托闭包，从 SQLite `tool_calls` 读，"never reconstructed from LLM prose"）、审批令牌、澄清答案、`control_check` 闭包、replay_mode → 按 action 分派 COMPLETE/FAIL/DELEGATE/REJECT_ASSIGNMENT。
- 委托 `_handle_delegation`：三重预算（depth≤3、每任务 rounds≤2、总任务数≤60）+ 能力全覆盖 → 建子任务（parent/blocked_by/delegated_task_ids 元数据）→ 父任务 BLOCKED；子任务全部完成后父任务带 `delegated_results` 恢复执行。
- 失败与重分配：执行失败**无自动重试**直接 FAILED+级联；REJECT_ASSIGNMENT 时从 `assigner.candidates()` 选 score≥1.0 的其他 agent 新建替换任务（≤2 次），原子替换父任务引用，旧任务 CANCELLED。
- 崩溃恢复 `resume_goal`：终态幂等返回；过期清理；**RUNNING 中断任务→PENDING+replay_mode**（成功副作用由重放缓存供给，不二次执行）；审批/澄清必须已决策否则拒绝恢复；从 DB 重建上下文续跑。
- 收尾 `_finish_workflow`：DB 状态优先（防暂停/完成竞态）；finalize 任务完成才算 COMPLETED；`_first_failure` 根因分析（排除有失败依赖/子任务的失败项）；`compute_workflow_metrics`（32 字段）落库。

### 2.3 Task State Machine（`orchestration/state_machine.py`，82 行）

6 状态严格迁移表（**可原样复用**）：PENDING→{RUNNING,BLOCKED,FAILED,CANCELLED}；RUNNING→{COMPLETED,FAILED,BLOCKED,PENDING,CANCELLED}（RUNNING→PENDING 专为 HITL 挂起与崩溃恢复）；BLOCKED→{RUNNING,FAILED,CANCELLED}；三个终态不可迁出。PENDING→COMPLETED 非法（必须经过 RUNNING）。副作用：started_at 首次设置、终态盖 finished_at、COMPLETED 清 error。

### 2.4 MessageBus（`messaging/bus.py` 193 行 + `conversation.py` 74 行）

8 种 MessageType（direct/group/task_request/task_result/delegation/plan/system/error）；每条消息**同步落 SQLite `messages` 表** + JSONL 事件；投递是进程内 `agent.deliver(message)` 同步调用（AgentLike 协议），at-most-once、无 ack/outbox/重试；接收方按 message_id 内存去重。直聊会话 id 确定性推导（`dm_{a}__{b}` 排序拼接）。**结论：总线本身不可搬到跨进程架构（主项目已有 A2A 承担传输），但"消息即审计记录"的落库模式和会话 id 推导值得保留。**

### 2.5 ToolExecutor（`tools/executor.py`，693 行）

16 阶段管线（人工抽查确认重放缓存门禁 `replay_mode`）：查找→任务级白名单（benchmark 用）→角色权限 default-deny（`ToolPermissionPolicy`：10 种 ToolPermission，工具声明的每个权限都必须持有）→jsonschema Draft2020-12 入参预校验（前 5 条错误）→`arguments_digest`（canonical JSON SHA-256，全系统幂等连接件）→**重放缓存**（同 task 内 tool_name+SUCCESS+同 digest → 直接返回持久化输出，`replayed=True`）→**未决对账拦截**（STARTED/UNRESOLVED 行存在→NEEDS_RECONCILIATION 拒绝重放）→审批（token 消费或新建请求；PENDING→`raise ApprovalRequired`）→**高危 intent 预写**（write-ahead：先落 STARTED 对账行再执行；**写失败拒绝执行 fail-closed**）→`asyncio.wait_for(spec.timeout_seconds)`→出参 schema 校验→双层输出截断（min(executor 64KB, tool 级)）→metadata 注入（untrusted_output 等）→审计落库（`tool_calls` 17 字段+JSONL 双写）→对账收尾（审计写失败→强制 NEEDS_RECONCILIATION）。
**无自动重试、无调用次数预算**（重试只在 LLM 层；次数预算在 agent 层 `(name,digest)` 重复签名计数 ≤3）。
`ToolCallRecord`/`ToolEvidence`（`from_record`/`compact(max_text=8000)`）构成"证据只来自持久层"原则的载体。

### 2.6 ApprovalService（`interaction/approval_service.py`，362 行）

实现工具层 `ApprovalGate` 抽象（依赖倒置）。`request_approval`（补 TTL 默认 3600s、落库、发事件、**立即返回 PENDING 不阻塞**）；`consume_approval` 五重校验：未知 id→拒；`consumed_at` 非空→拒（一次性）；**tool_name+argument_digest 不匹配→拒**（参数快照绑定，防批准后偷换参数）；workflow/task/agent 三元组不匹配→拒（防跨任务挪用）；过期→EXPIRED。消费用 SQL CAS（`UPDATE ... WHERE consumed_at IS NULL AND status='approved'` + rowcount==1）防并发双花。`approve/reject` 幂等、非 PENDING 抛 `ApprovalStateError`。过期是**惰性**的（`expire_overdue()` 在读写入口触发，无后台任务）。存储层 UPSERT 的 `DO UPDATE` 不含绑定列（digest/arguments/tool_name 入库后不可改）。
`tools/approval.py`：`arguments_digest`（sort_keys+紧凑分隔符+default=str 的 SHA-256）、`ApprovalRequired`、`PendingApprovalGate`/`AutoApprovalGate`。

### 2.7 ClarificationService（`interaction/clarification_service.py`，216 行）

`create`（TTL 默认 86400s）/`answer`（空答案拒绝、ANSWERED 幂等、非 OPEN 抛错）/`cancel`/`expire_overdue`/`metrics`。状态机 OPEN→{ANSWERED,EXPIRED,CANCELLED}。答案回注：resume 时写 `task.metadata["clarification_answer"]` → agent prompt `[CLARIFICATION_ANSWER]` 段（截断 400 字符），任务以 replay_mode 整体重跑。瑕疵：cancel 把原因写进 `answer` 字段（语义复用）。

### 2.8 WorkflowControlService（`orchestration/control.py`，120 行）

**粒度=整个 workflow**（无节点级暂停）。pause/resume/cancel 只改 DB 状态+计数器+双写事件，**不中断执行**；实际中断是协作式轮询：orchestrator 每轮循环头 `_control_state()` 读 DB，agent 每推理轮/每工具调用前 `control_check()` → `AgentSuspension("pause"/"cancel")`。终态禁 pause/resume，幂等重复调用安全。cancel 级联：取消未决审批/澄清→全部非终态任务 CANCELLED→停各 agent 沙箱。

### 2.9 MemoryEvolutionEngine（`memory/engine.py` 654 行 + `evolution_store.py` 1202 行）

- 触发：workflow COMPLETED 后 `process_workflow(workflow_id, learn=True)`；输入=审计库 tool_calls/tasks/workflows；跳过条件明确（workflow_not_found/no_tool_audit）。
- **证据分级**（`_verification`，逐字）：任一工具调用非 SUCCESS→`UNVERIFIED`；任一 `record.metadata["mock"]`→`MOCK_SIMULATION`；否则 `REAL_EXECUTION`。
- 记忆创建：每个有工具审计的 task 产 EPISODIC（反思文本=工具序列+关键输出摘录+证据 id）+SEMANTIC（首条有输出的记录事实）+WORKING（TTL 3600s）；**PROCEDURAL 候选三重门槛**：非 UNVERIFIED ∧ workflow COMPLETED ∧ 全部工具 SUCCESS。
- **晋升规则**（人工抽查确认 engine.py:308-311）：候选 signature=sha256(agent+capabilities+kind+steps+terms)；`support_count`=**独立 workflow 数**（同一 workflow 重复证据不累计）；REAL ∧ support≥`memory_promotion_threshold`(默认 2) → `confidence=min(0.95, 0.75+0.05*support)` ≥ `memory_promotion_confidence`(0.8) → 创建 PROCEDURAL/REAL/PROMOTED 记忆（importance 0.9）。**MOCK 证据永不 PROMOTED**：只产 PROVISIONAL、confidence 0.3、内容强制前缀 `[MOCK SIMULATION - NOT REAL-WORLD VERIFIED]`，默认检索不可见。
- 衰减/遗忘：`effective_importance = importance × 0.5^(age_days/30) × weight{REAL 1.0/MOCK 0.55/UNVERIFIED 0.25} × (1+min(0.25, 0.02×access_count))`；prune 规则：过期 working 删；**PROMOTED 与 REAL_EXECUTION 永不删**；太新不删；effective<0.05 删。无降级机制（promotion 单调不减，confidence clamp [0.05,0.99]）。
- 检索：混合打分 `0.45×cosine + 0.25×Jaccard + 0.20×importance + 0.10×verification{1.0/0.45/0.15} + type_boost{procedural +0.1, semantic +0.04, working −0.05}`；默认**只召回 REAL_EXECUTION**；召回内容带 `[type|verification|confidence|support]` 前缀标签；总预算 3000 字符；召回即写 `retrieval_events`（workflow/task/execution 三级 scope）+access 计数。
- 反馈闭环：`process_workflow(learn=False)` 按三级 scope 精确匹配检索事件→仅对 PROCEDURAL+PROMOTED+REAL 判定 `_procedure_matches`（工具序列全等+白名单参数相等——**极脆，迁移需放宽为相似度**）→`confidence += 0.03(成功)/−0.10(失败)`，写 `memory_feedback`（UNIQUE(retrieval_id,memory_id) 幂等）。
- Embedding：`HashingEmbeddingProvider`（256 维 blake2b 符号特征哈希，token+字符 3-gram，L2 归一化；**非语义模型**，作者自标 NOT VERIFIED）；向量存 `embedding_json` TEXT 列，检索时全量载入暴力 cosine（万条内可用，无 ANN）。
- 独立库：`data/memory_evolution.db`（5 表：schema_meta/evolved_memories/memory_candidates/retrieval_events/memory_feedback），与审计库分离；旧库 ALTER 迁移有先例（`_migrate_scope_columns`）。
- 消融实验 `memory/experiment.py`：学习期→OFF/ON 对照，`pipeline_verified` 与 `real_llm_learning_verified=False` **分离标注**（诚实性设计典范）。

### 2.10 BenchmarkRunner / Verifier / Ablation / 可靠性（`benchmark/`，19 文件 6,479 行）

- Runner：批次目录+manifest+**JSONL 逐 run 落盘断点续跑**（`(task_id,repeat_index)` 去重）；git commit 溯源；每 run 独立 data_dir+独立 runtime；时间预算=`asyncio.wait_for` 包裹每次 runtime 调用只计活跃秒；预算超标**后置否决**（verifier PASSED 也强制 FAILED）；HITL 由 `human_script` 脚本模拟（approve/reject/wait_expire/leave_pending，全程记 `HumanInteractionRecord(simulated=True)`）。
- Verifier 8 类独立检查：文件（含逐字节 exact、路径逃逸防护）、工具审计（status/permission/approval/reason 过滤计数）、workflow 状态+暂停恢复事件计数、审批记录（含 consumed）、澄清记录、任务图（完成数/深度/改派数）、**重复副作用**（SUCCESS 调用按 `(tool_name,arguments_digest)` 计数 ≤max）、**沙箱重跑**（把独立测试脚本放进 Docker 重执行 Agent 产物，如 `from solution import add; assert add(2,3)==5`；Docker 不可用→诚实 SKIPPED 不假判）。**成功判据只有 verifier，绝不采信 `workflow.status==completed` 或 LLM 自述**。
- 任务集：34 个 dev 任务由 `benchmarks/dev/build_tasks.py`（2310 行 DSL 生成器）确定性产出 JSONL，生成时即 `model_validate`；10 类别（research/code_generation/debugging/file_editing/planning/delegation/code_execution/approval/clarification/failure_recovery）每类 basic+hard；开放式任务显式 `manual_review_reason`→NEEDS_REVIEW 不冒充自动通过。
- 指标：RunMetrics 31 字段全公式化（goal_success=verifier PASSED 唯一判据；retry=相同 digest 签名重复计数；invalid_json_rate、plan_validation_failure_rate、recovery_success 仅 FAILURE_RECOVERY 类…）；聚合含 p95、按类别分桶、明示"needs_review/skipped 不算成功、无统计显著性声明"。
- 消融：`DEFAULT_ABLATION_CONFIGS` A_single_agent（**solo agent 汇总全部能力+全部权限，但工具/权限/沙箱/持久化/治理走完全相同代码路径**——变量控制干净）/B_multi_agent/C_multi_agent_memory；C 组先跑 learning 任务写 `shared_memory.db`（学习集不计入评测聚合）；同 dataset/同 repeats/同 provider/同任务预算。
- 故障注入 20 种（全景表见子审计）：暂停/取消注入（ProviderBehavior）、工具失败、schema 非法参数、非法路径、沙箱超时（真实 Docker `while True` + 2s）、失败测试解读、时间/token 预算、权限拒绝、委托深度、依赖失败传播、不可能任务诚实失败、审批拒绝/真实过期、invalid JSON、重复动作死循环、框架级工具超时、**容器被杀自动重建**、**审计写失败→对账阻断重放**、**无 intent 审计不执行高危**、记忆故障非致命、**跨进程重启恢复**、live 传输断连硬停。
- Live 门禁：`live_api_allowed=False` 默认；CLI `--allow-live-api` 显式开关；`LiveBudgetProvider` 请求+token 双预算（stage 默认 120/250k）；六级阶梯逐级通过（连通性→结构化输出→工具→多 agent→HITL+记忆→全量）；无 key/未授权→全部 `NOT_VERIFIED` 只跑 fixture harness 自检；报告强制 MOCK/LIVE 横幅（"NOT real-LLM capability"）；密钥脱敏断言入测试。

### 2.11 LLM Provider 与 Structured Output（`llm/` 6 文件 ~1,700 行 + `agents/base.py` 协议层）

- `llm/base.py`：`ChatMessage/TokenUsage/LLMResponse`；**7 类错误分类**（no_response_disconnect/timeout/transport/http_client_error/http_server_error/parse_error/provider_error）+`classify_transport_error`；`LLMProviderError(error_class, status_code)`。
- `openai_compatible.py`（529 行，纯 httpx 不用 SDK）：`RETRYABLE_STATUS_CODES={408,409,429,500,502,503,504}`（429/5xx 重试，401/403 立即抛）；有界重试 `max_retries+1` 次，退避 `min(0.5×2^attempt, 4.0)`（**无 jitter、不读 Retry-After**）；错误文本 `[:500]`+**API key `.replace(key,"[REDACTED]")` 脱敏**；SSE 流式：`reasoning_content` 只计数**绝不混入 content**、**部分内容永不作为成功返回**（断流即 raise 不重试，防半截 JSON 被当有效动作）、终止事件三态判定+13 项 stream_metrics（headers/first_byte/first_event/first_content 毫秒级诊断）；client 惰性重建+keep-alive（真 TCP 测试）。
- `factory.py`：缺 key **显式抛 `ProviderConfigurationError`，绝不静默降级 mock**（"real-API runs can never be mistaken for mock runs"）。
- **Structured output 不在 provider 层**：全仓无 `response_format`/原生 function-calling；靠 `ACTION_PROTOCOL` JSON 动作信封（complete/tool_call/delegate/clarify/reject_assignment/fail 六动作+逐类型 payload 校验）+`extract_json_payload`（```json 围栏→全文→首尾花括号三级降级）+`parse_agent_action`（纯文本降级 complete+warning；有 JSON 无 action 字段同样降级；model_validate 失败硬错误）+修复循环（`[REPAIR]` 回显错误/`[RETRY]` length 专用不回显，预算 `structured_output_repairs=1`）。**注意：主项目用的是原生 function-calling，此协议不迁移，只迁移"解析-修复-预算"思想用于 Planner。**
- Agent 层预算：`max_turns=max_tool_calls+3+max_output_repairs`；任务 token ≤120k（事后检查）；工具调用 ≤6；同 `(name,digest)` 重复 ≤3。

### 2.12 Docker Sandbox（`sandbox/`，661 行）

**哲学与主项目相反**：AgentForge 是**每 agent 常驻容器**（`sleep infinity`+`docker exec`），workspace 可写 bind mount（宿主写、容器读写同目录），靠 `WorkspaceManager` 的 12 项路径防护（NUL/绝对/盘符/`..`/深度12/长度240/非法字符/Windows 保留名/尾空格点/扩展名白名单/逐段 symlink/resolve 复核）+原子写（mkstemp+fsync+replace）保证边界；主项目是**一次性容器+只读挂载**。
参数对比（AgentForge 独有值得吸收项）：`--memory-swap` 与 `--memory` 等值（封死 swap）、tmpfs 带 `noexec`、`--init`（僵尸回收）、env 白名单仅 3 变量不透传宿主、容器内 `timeout --signal=TERM --kill-after=2s` + 外层 wait_for 双保险、exit 124 判超时并**销毁容器**、`_read_limited` 8192 chunk 超限继续 drain 防管道死锁、`SandboxConfig` model_validator 拒绝 6 种 root user 写法、preflight `docker info`(8s) 失败抛 `SandboxUnavailableError`。
**不回退宿主机三层保证**：preflight 异常+工具层捕获结构化失败（`sandbox_unavailable` metadata）+宿主哨兵文件测试锁定（`test_no_host_fallback_when_docker_missing`）。
已知缺口：`--mount source=` 路径未转义逗号/空格；无 seccomp/apparmor 显式 profile；`check_available` 同步 subprocess。

### 2.13 其余支撑模块

- `runtime.py`（586 行）：20 步组合根装配（顺序=依赖图），`submit_goal`/`resume_workflow`/HITL 门面/`chat_with_agent`；单进程单模型（全 agent 共享一个 provider 实例）。
- `agents/base.py`（982 行）：见 2.11；另有 `_inbox` 内存收件箱（message_id 去重）、任务状态镜像、`remember_task_outcome`（UNVERIFIED 级，靠衰减遗忘）。角色子类 15 行纯 prompt 差异。
- `persistence/database.py`（984 行）：13 表 schema（含 `tool_calls` 17 列、`tool_reconciliations` UNIQUE(workflow,task,tool,digest)、`approvals` 21 列、`clarifications`、`workflow_events`）；同步写+WAL+RLock（自认设计取舍）；schema_version 只写不读**无迁移机制**；无外键。
- `evaluation/metrics.py`（154 行）：`compute_workflow_metrics` 32 字段（完成率/委托/token/审批等待/暂停恢复计数…）。
- `config/settings.py`（181 行）：pydantic-settings，env 前缀 `AGENTFORGE_`，SecretStr，**编排安全限全表**（max_plan_subtasks=8[1,30]、max_delegation_depth=3、max_delegation_rounds=2、max_reassignments=2、max_execution_steps=30、max_tasks_per_workflow=60、max_llm_calls_per_workflow=200、max_task_tokens=120k、max_repeated_tool_actions=3、structured_output_repairs=1）。

### 2.14 测试资产

42 文件/9,602 行；本次审计中在只读克隆上实测复跑：**312 passed + 4 环境性 skip**（Windows 侧 Docker daemon 探活超时 ×3、Windows symlink 权限 ×1），144s，零失败——迁移蓝本处于全绿状态。与迁移最相关：`test_orchestration.py`（10 个端到端：全链路依赖边断言、委托深度、预算中止、级联失败、能力缺口零残留、跨 runtime 重启存活）、`test_phase4_hitl.py`（11 个：绑定消费一次性、参数篡改/越权/过期拒绝、崩溃恢复无重复副作用、权限先于审批）、`test_phase5_reliability.py`（10 个故障注入）、`test_phase5_benchmark.py`（数据集覆盖度、批次导出、resume 幂等、A/B/C、live 门禁）、`test_memory_engine.py`/`test_memory_evolution_store.py`（晋升/mock 区分/反馈/scope 隔离/并发不串污）、`test_phase6a/b_*`（provider 传输、流式、compact 协议、密钥不落盘）。**测试用例本身就是迁移验收标准的最佳来源。**

---

## 3. 两套架构根本差异（融合决策依据）

| 维度 | multi-agent-system | AgentForge | 融合决策 |
|---|---|---|---|
| 进程模型 | 多进程，A2A HTTP JSON-RPC | 单进程，对象注入+内存 inbox | **保留多进程 A2A**；AgentForge 编排逻辑改造为 Coordinator 进程内调度器 |
| 工具协议 | MCP（外部 server，stdio/http） | 进程内 Python BaseTool | **保留 MCP**；ToolExecutor 的治理管线降维成 MCP 调用外的策略/审计包装层，**不建第二套工具系统** |
| LLM 接口 | AsyncOpenAI 原生 function-calling | 自研 httpx+content 内嵌 JSON 动作协议 | **保留 function-calling**；移植重试/分类/脱敏/预算/修复思想，不移植动作信封协议 |
| 调度 | 无（Agent 自主委托） | 串行 DAG+治理链 | 移植 DAG 校验/分配/状态机/预算，**新写真并行调度器**（asyncio） |
| 状态持久化 | 记忆库 checkpoint（任务级）；A2A 任务内存态 | 13 表全量持久化+对账 | 移植表设计到新的 orchestration 库；补 A2A TaskStore |
| 记忆 | 词法检索+规则反思（verified 门槛严） | 四类型+证据分级+晋升+embedding 混合+反馈闭环 | **在现有 schema 上加列/加表升级**，不替换（§4.7 兼容方案） |
| HITL | 无 | 完整（审批/澄清/暂停恢复/对账） | 整体迁移，挂起粒度从 workflow 级**增强为节点级** |
| 评测 | 无 | 完整（独立验证/消融/故障注入/live 门禁） | 整体迁移，驱动层从进程内 API 改为 Coordinator REST |

---

## 4. Phase 0 遗留问题复核（8 项）

1. **Memory 测试预算修复是否掩盖 prompt 膨胀**——结论：修复正当，但需加守卫。生产预算（memory.yaml `max_input_chars=128000`）远大于核心 prompt 3,571 字符，测试的 3000 预算在 `1195041` 合入 2.6k 字符 DYNAMIC_SKILL_POLICY 后已脱离现实；提高预算后压缩/外置存储/恢复等原意图全部保留（trigger 4000<core+2 组仍触发压缩）。**遗留动作**：P2 增加尺寸守卫测试（`len(SYSTEM_PROMPTS[role]) ≤ 显式上限`）防再次静默膨胀；中期把技能策略改为"角色有注册技能才注入"。不放宽任何安全断言、不删测试。
2. **A2A InMemoryTaskStore**——确认 a2a-sdk 1.3.0 提供 `TaskStore` 接口（save/get/delete/list 四方法）与 `DatabaseTaskStore`（需 SQLAlchemy AsyncEngine+aiosqlite，项目未装 aiosqlite）。**方案**：P3 自实现 ~100 行 stdlib `SQLiteTaskStore`（与 memory/store.py 同风格，避免新增依赖）。**连带 bug**：`RoleExecutor.execute` 未把 `task.id` 传给 `run_tool_loop_with_memory`（runner 自生成 uuid4），导致 A2A 重试与记忆 checkpoint 脱钩、幂等失效——P3 一并修复（一行改动+回归测试）。
3. **MCP 每调用重连**——确认（`_client()` 每次 `async with` 新起 stdio 进程；E2E 中每请求 refresh 拉起 7 个 server）。AgentForge 无此问题（进程内工具）但其 spec 元数据映射思想适用。**方案**：P5 加持久会话池（每 server 一个长连接+失败重建+断路器），`refresh()` 改为可手动/定时，保留 per-call 模式为降级路径。
4. **两套委托注册表**——确认 `tool_agent.ask_with_tools` 只用 `dynamic_delegation`；`agent_delegation.py`（静态白名单+环检测）仅被 `test_agent_delegation.py`/`test_agent_delegation_tool_loop.py` 引用。**方案**：不删除（组员功能），P2 在模块 docstring+README 标注 superseded/deprecated，环检测测试思想并入 DAG 校验测试；删除需组员/用户同意后单独 commit。
5. **技能漏注册**——`skills/coding/python-fact-grounded-coding/SKILL.md` 存在但未列入 `config/agent_skills.yaml`。**注意**：该技能正文引用 Pylance MCP 工具（本项目注册表不存在），注册后可能诱导 LLM 调用不存在的工具。**方案**：P5 与组员确认——注册前先裁剪 Pylance 段，或保持不注册并记录原因。
6. **绝对路径残留**——Phase 0 已修 mcp_registry（$HOME 展开）。残留：`skills/coding/git-audit/SKILL.md` 正文硬编码 `/home/alan_fang/...`（会进 LLM prompt 误导路径）；`scripts/start_filesystem_mcp.sh` 假设 `~/projects/multi-agent-system` 布局。**方案**：P5 技能文本改相对描述；脚本改用 `ROOT=$(dirname ...)` 推导（`start_code_filesystem_mcp.sh` 已是正确样板）。
7. **SQLite 与未来向量库兼容**——AgentForge 模式=`embedding_json` TEXT 列+内存暴力 cosine（万条内可行）；本项目 compose.yaml 已预留 pgvector/pg17 但记忆路径未用。**方案**：P4 在 `long_term_memories` 加 `embedding_json` 列+`EmbeddingProvider` 接口（默认 vendor HashingEmbeddingProvider，可换真实模型）；检索接口抽象出 `VectorIndex` 缝，pgvector 作为可选后端不引入硬依赖。
8. **沙箱多 Agent 隔离边界**——现状：`sandbox_id`（32hex）即能力令牌，**无属主记录**；任何持 `sandbox:*` 权限的 agent 若知道 id 可在他人沙箱执行（当前仅 coding 有权限，实际风险低；E2E 中 write_file 走 filesystem_code 也限 mcp_workspace 根）。AgentForge 对照：每 agent 独立 workspace+容器名含 agent_id+跨 agent 文件不可见测试。**方案**：P5 在 create 返回中记录 owner（agent/task，写入审计），`run_python/run_pytest/_sandbox_dir` 校验属主；sandboxes 目录按 agent 分子目录；补跨 agent 隔离测试。

---

## 5. 审计总结论

1. **两个项目真正实现了什么**：主项目=可运行的 A2A+MCP+Skills 分布式底座（协议与权限层扎实，治理/编排/评测空白）；AgentForge=单进程治理平台（编排/HITL/记忆演化/评测完整且测试严格，但无网络协议层、调度串行、工具非 MCP）。
2. **值得迁移**（详见 MIGRATION_MATRIX）：planner 校验、状态机、能力分配、预算体系、审批/澄清/控制三服务+对账/重放幂等三件套、记忆演化全套（分级/晋升/衰减/混合检索/反馈）、ToolExecutor 治理管线（降维为包装层）、Provider 韧性层、Benchmark 全套（含 live 门禁与诚实性机制）。
3. **保留原实现**：A2A 传输与 AgentCard、MCP ToolRegistry、Skills 机制、一次性只读沙箱模型、原生 function-calling、memory 5 表 schema（增量升级）。
4. **必须重新设计**：真并行 DAG 调度器（AgentForge 无）、跨进程证据/审计链（AgentForge 假设同库同进程）、节点级 HITL 挂起（AgentForge 只有 workflow 级）、Planner 的结构化输出走 function-calling/JSON-schema 而非动作信封协议。
