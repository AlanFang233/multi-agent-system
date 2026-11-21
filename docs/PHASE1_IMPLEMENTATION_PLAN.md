# Phase 1 分阶段实施计划（PHASE1_IMPLEMENTATION_PLAN）

- 日期：2026-11-16；分支：`feature/agentforge-capabilities`；前置：Phase 0 已验收（46 tests，Mock E2E PASS）
- 排序依据：依赖关系（证据列/状态机是 HITL 与评测的地基；Provider 韧性在 live 评测前就位）。每个 Phase 独立可验收、独立 commit，**不做一次性大重构**；每阶段结束跑全量回归+Mock E2E。
- 工作量单位：人日（1 人全职当量），含测试与文档。总计约 24~34 人日。
- 通用完成标准（每阶段都适用）：① 新增代码有类型注解+docstring；② 新测试全绿且全量 pytest 无回归；③ Mock E2E（scripts/phase0_e2e.py）保持 PASS；④ 无真实 LLM 调用（除非该阶段显式授权 live）；⑤ 文档/README 更新；⑥ 独立清晰 commit（不推 main，不 force push）。

---

## Phase 2：Coordinator + DAG Planner + 并行调度器（预计 5~7 人日）★★★

**要解决的问题**：系统无全局任务规划；coordinator.py 仅发现/健康；复杂任务只能靠 Agent 逐跳自主委托，无法预规划依赖与并行。

**新增模块**：
- `src/agent_ecosystem/orchestration/__init__.py`
- `orchestration/models.py` —— ExecutionPlan/SubtaskSpec/Workflow/WorkflowNode/NodeStatus/WorkflowStatus（pydantic，字段见 API_AND_DATA_DESIGN §3）
- `orchestration/planner.py` —— 迁移 `validate_plan`（Kahn 拓扑+环检测+规模上限）+ `plan_goal`（LLM 规划：JSON 产出→schema 校验→[REPAIR]/[RETRY] 修复循环≤2 次→attempts 落库）
- `orchestration/assignment.py` —— 迁移 `CapabilityAssigner`（全覆盖+确定性 tie-break）+ `normalize_capability`
- `orchestration/state_machine.py` —— 迁移 6 状态迁移表+`transition_node`
- `orchestration/errors.py` —— 迁移异常层级（OrchestrationError/PlanningError/PlanValidationError/TaskAssignmentError/WorkflowPauseRequested/WorkflowCancelledRequested）
- `orchestration/store.py` —— WorkflowStore（stdlib sqlite3+WAL，新库 `data/orchestration.sqlite`：workflows/workflow_nodes/node_events/workflow_events 四表；schema_version 读写+迁移函数）
- `orchestration/scheduler.py` —— **新写**真并行调度器：ready 集合 `asyncio.gather`+Semaphore(并发上限默认 3)；依赖等待/级联失败/死锁检测/有界重试（默认 1 次）/改派（candidates score≥1.0，≤2 次）；预算：max_nodes=8、max_a2a_calls=24、墙钟 deadline；每轮循环头查 control 状态（协作式暂停/取消）
- `orchestration/service.py` —— WorkflowService：submit（规划→物化→治理节点 synthesis/finalize→调度→收尾 metrics+根因）/resume/pause/cancel/status；节点派发=A2A `send_a2a_text` 扩展 metadata `x-workflow`（见 API 文档 §4）；结果解析（文本+evidence refs）
- `orchestration/metrics.py` —— compute_workflow_metrics（迁移 32 字段公式，数据源改新库）
- `src/agent_ecosystem/events.py` —— EventLogger JSONL 原样迁移（39 行）
- `config/orchestration.yaml` —— 预算/并发/重试/治理链开关

**修改现有文件**：
- `coordinator.py` —— 挂载 REST：POST /workflows、GET /workflows/{id}、POST /workflows/{id}/pause|resume|cancel（HITL 端点 P3 加）；保留原 /health /agents /agents/discover 不变
- `a2a_server.py` —— RoleExecutor 解析 `x-workflow` metadata（workflow_id/node_id/attempt/replay_mode/approval_tokens/budget），透传给 ask_with_tools；节点任务 task_id=`{workflow_id}:{node_id}:{attempt}`（幂等键）
- `tool_agent.py` / `memory/runner.py` —— ask_with_tools/run_tool_loop_with_memory 增加 task_id 透传参数（顺带修 Phase 0 发现的 checkpoint 脱钩 bug）；节点级工具预算（max_tool_calls）从 metadata 注入
- `settings.py` —— 编排预算字段（默认值取 AgentForge 安全限）
- `agent_delegation.py` —— docstring 标注 deprecated（不删除）

**迁移的 AgentForge 文件**：planner.py（原样）、state_machine.py（原样）、assignment.py（原样）、errors.py（原样）、persistence/events.py（原样）、models/task.py+workflow.py（裁剪适配）、orchestrator.py（仅骨架蓝本：物化/级联/根因/治理链/预算检查点）、agents/coordinator.py（plan_goal 修复框架，协议重写）。

**数据库变化**：新库 `data/orchestration.sqlite`（4 表 DDL 见 API 文档 §5）；memory 库不变。

**新增测试**（~35 个）：
- 搬 test_planner(5)/test_state_machine(6)/test_assignment(10) 适配导入路径
- scheduler 单测（fake A2A sender）：并行重叠断言、依赖等待、级联失败、死锁、重试、改派、三类预算、暂停/取消轮询点
- service 集成（Mock）：RAG 示例 6 节点 DAG 全链路、治理节点产出最终报告、metrics 正确
- planner 修复循环：坏 JSON/截断/多余键
- SYSTEM_PROMPTS 尺寸守卫（Phase 0 遗留 #1）

**回归**：全量 pytest + phase0_e2e.py PASS + registry_smoke。

**完成标准**：Mock provider 下，用户任务书示例（"研究 RAG→设计→实现→测试→分析→整合报告"）自动拆解为 ≥5 节点 DAG，含依赖与并行，全部节点经真实 A2A 派发到三个 agent 进程执行并整合出最终报告；非法计划（环/超规模/能力缺口）100% 拒绝且审计留痕；全量测试绿。

---

## Phase 3：持久化任务状态 + Workflow Recovery + HITL（预计 5~7 人日）★★★

**要解决的问题**：进程崩溃/重启后工作流与 A2A 任务全丢；无审批/澄清/暂停恢复；副作用可能重复执行。

**新增模块**：
- `src/agent_ecosystem/a2a_task_store.py` —— SQLiteTaskStore（实现 a2a-sdk TaskStore 四方法；序列化 protobuf Task→JSON 行）
- `src/agent_ecosystem/hitl/__init__.py`
- `hitl/digest.py` —— `arguments_digest`（canonical SHA-256）原样迁移
- `hitl/repository.py` —— InteractionRepository（approvals/clarifications/workflow_events/tool_reconciliations 四表 SQL；UPSERT 不可变绑定列；CAS 消费）
- `hitl/approval_service.py` —— 迁移五重校验/TTL 惰性过期/幂等决策/metrics
- `hitl/clarification_service.py` —— 迁移 OPEN/ANSWERED/EXPIRED/CANCELLED+答案回注（修 cancel 复用 answer 瑕疵→独立 reason 列）
- `hitl/reconciliation.py` —— intent 预写（fail-closed）/未决拦截/重放缓存查询（键=agent,task_id,tool_id,digest）
- `orchestration/control.py` —— WorkflowControlService 迁移+**节点级** pause/cancel 扩展

**修改现有文件**：
- `a2a_server.py` —— task_store 换 SQLiteTaskStore；审批/澄清挂起→`TASK_STATE_INPUT_REQUIRED`+结构化 payload；恢复请求（带 approval_tokens/clarification_answer/replay_mode metadata）解析；每工具调用前 control 轮询（共享 SQLite 直读）
- `memory/runner.py` —— 工具调用路径接入治理钩子最小集：digest 计算+重放缓存命中直接返回+高危工具审批挂起（抛 ToolApprovalPending→executor 转 INPUT_REQUIRED）+tool_results 加列写入
- `memory/store.py` —— **tool_results ALTER 加列**：arguments_digest/is_error/duration_ms/execution_kind/provider_kind/approval_status（全带默认值，在线迁移函数）；新增 tool_reconciliations 表（或放 orchestration 库，见 API 文档 §5.4）
- `coordinator.py` REST —— /approvals /clarifications 端点+节点级暂停恢复；scheduler 支持 WAITING_INPUT 节点（**挂起仅冻结该节点及其下游，兄弟分支继续**）
- `scripts/` —— 审批/澄清 CLI 助手（list/approve/reject/answer）

**迁移的 AgentForge 文件**：interaction/ 三文件（逻辑近原样，存储指向改）、tools/approval.py（digest+Gate 抽象）、orchestration/control.py、orchestrator.resume_goal 恢复协议、executor 的重放/对账三阶段、persistence 四表 DDL。

**数据库变化**：orchestration.sqlite 加 approvals/clarifications/tool_reconciliations 三表+a2a_tasks 表；memory 库 tool_results 加 6 列（迁移函数幂等）。

**新增测试**（~30 个）：
- 搬 test_phase4_hitl 11 例适配（绑定/一次性/过期/篡改/越权/拒绝零副作用/权限先于审批/澄清跨重启/暂停中断恢复/取消幂等/崩溃恢复无重复）
- 搬 test_phase5_reliability 5 例（审计写失败→对账阻断、无 intent 不执行、重启恢复、暂停恢复零重复副作用）
- 节点级挂起：审批挂起时并行兄弟节点继续推进（AgentForge 没有的新测试）
- SQLiteTaskStore 四方法+roundtrip+agent 重启后任务可见
- task_id 幂等：同节点重派命中 completed checkpoint 零工具重执行

**回归**：全量 pytest + phase0_e2e + **新增 phase3_e2e**（含审批节点与澄清节点的 Mock 全链路，含 kill -9 Coordinator 后 resume）。

**完成标准**：用户任务书 HITL 场景全过：高危操作暂停申请（持久化/参数快照/风险级/过期/参数一致性/防重放）；澄清四态+答案回注；PAUSE/RESUME/CANCEL/RESTART RECOVERY；kill -9 任意进程后恢复且已完成副作用零重复；无关并行分支不受挂起影响。

---

## Phase 4：Memory Evolution + Embedding Retrieval（预计 4~6 人日）★★★（用户标注重点）

**要解决的问题**：记忆只有规则式 verified 门槛，无四类型体系/证据分级/晋升/置信度/衰减/向量检索/反馈闭环；Mock 与真实经验不区分。

**新增模块**：
- `memory/embeddings.py` —— HashingEmbeddingProvider 原样 vendor（71 行零依赖）+EmbeddingProvider 接口+cosine
- `memory/evolution.py` —— MemoryEvolutionEngine 适配版：证据源=task_events+tool_results（新列）；_verification 三级分级（provider_kind/execution_kind/is_error）；EPISODIC/SEMANTIC/WORKING 生成；PROCEDURAL 候选（signature=sha256(agent,工具序列,参数提示白名单)）；晋升（support=独立 workflow 数≥2 ∧ REAL ∧ confidence=min(0.95,0.75+0.05n)≥0.8）；**MOCK 编排证据→PROVISIONAL 永不晋升+警示前缀**；衰减 effective_importance 公式+prune 规则（PROMOTED/REAL 永不删）
- `memory/feedback.py` —— retrieval_events 三级 scope 记录+任务完成回看+_procedure_matches（**放宽为工具子序列匹配+参数提示相似**）+confidence ±0.03/−0.10（clamp [0.05,0.99]）+memory_feedback 幂等落库

**修改现有文件**：
- `memory/store.py` —— long_term_memories **ALTER 加 12 列**（namespace/verification_level/importance/confidence/source_json/metadata_json/embedding_json/access_count/last_accessed_at/expires_at/promotion_state/dedupe_key，默认值保守回填旧行=UNVERIFIED）；新增 memory_candidates/retrieval_events/memory_feedback 三表（DDL 逐字迁移）；kind 枚举加 working；`search()` 升级 hybrid 打分（0.45cos+0.25jaccard+0.20imp+0.10verif+type_boost；默认只召回 REAL；前缀标签+字符预算）；**保留原词法 search 为 fallback 开关**（配置）；put_memory/set_memory_status API 向后兼容（status 映射 promotion_state 投影）
- `memory/runner.py` —— 检索时写 retrieval_events（workflow/task/execution scope）；任务完成触发 evolution（COMPLETED 才 learn=True）；工具结果记录 execution_kind/provider_kind
- `memory/reflection.py` —— 保留为快速规则通道，输出改为写 evolution 候选（不再直接 verified）
- `memory/cli.py` —— 新增 candidates/feedback/evolve 子命令
- `config/memory.yaml` —— 演化参数（promotion_threshold=2/promotion_confidence=0.8/decay_half_life=30d/forget=0.05/working_ttl=3600/embedding_dims=256/allow_mock_promotion=false/hybrid=on）

**迁移的 AgentForge 文件**：memory/embeddings.py（原样）、evolution_store.py（DDL/合并/衰减/prune/_rank 逻辑）、engine.py（_verification/晋升/反馈判定）、models.py（EvolvedMemory/MemorySource/MemoryCandidate/RetrievalEvent/MemoryFeedback 裁剪）。

**数据库变化**：memory 库 long_term_memories +12 列、+3 表、kind 枚举扩展；迁移函数幂等+旧行保守回填；**现有 5 表零破坏**（tasks/task_events/checkpoints/tool_results 原样）。

**新增测试**（~25 个）：
- 搬 test_memory_engine 5 例（2 workflow 晋升/mock 永不晋升+前缀/失败 UNVERIFIED/无审计跳过/反馈精确调整）
- 搬 test_memory_evolution_store 9 例（隔离/过滤前缀/embedding 排序/去重合并/独立 workflow 计数/working 过期/衰减遗忘/检索事件/预算截断）
- 搬 test_phase4_memory_scope 4 例（scope 精确归因/取消无成功反馈/并发不串污）
- 迁移兼容：旧库升级后旧测试全绿+旧行为（词法检索/verified 门槛）不变
- 中文查询 hybrid 召回用例

**回归**：全量 pytest（含旧 memory 测试零修改通过）+ phase0_e2e + 二次运行检索命中断言升级为"命中且带 verification 标签"。

**完成标准**：用户任务书记忆需求逐项落地：四类型/向量+混合检索/agent 与 workflow 隔离/来源追踪/置信度/重要性/去重/衰减/遗忘/反馈；**Evidence-first**：Mock E2E 产生的经验止步 PROVISIONAL 且带警示前缀，真实工具证据+2 独立工作流才晋升 PROCEDURAL；检索→使用→行为改变→反馈调整全链路落库可查。

---

## Phase 5：统一 Tool Governance + Skills 收尾 + Sandbox 硬化（预计 3~5 人日）★★

**要解决的问题**：工具调用无统一治理（digest/次数/重复/审批/风险分级散缺）；MCP 每调用重连拖慢链路；沙箱无属主隔离；技能漏注册与绝对路径残留。

**新增模块**：
- `src/agent_ecosystem/tool_governance.py` —— 治理 Overlay（唯一 choke point，包在 registry 链最外侧）：任务级白名单→风险分级（`config/tool_policy.yaml` 配置驱动，去 AgentForge 硬编码）→digest→重放缓存→对账拦截→审批挂起（INPUT_REQUIRED 语义复用 P3）→超时→双层截断→审计富化（17 字段语义对齐 tool_results 新列）
- `config/tool_policy.yaml` —— 高危规则表（tool_id+参数模式→risk/requires_approval；默认：filesystem_code:write_file 覆盖已有→high、edit_file→medium、sandbox:*→low、fetch/free_search→low+untrusted_output 标注）

**修改现有文件**：
- `tool_registry.py` —— 持久会话池（每 server 长连接+惰性建连+失败重建+断路器 N=3/M=30s）；refresh 语义改显式/定时；保留 per-call 降级开关
- `memory/runner.py` —— 工具调用统一走 governance；重复调用守卫（(tool_id,digest) ≤3）；任务级调用预算
- `sandbox_mcp_server.py` —— 硬化差异项：--memory-swap 等值、tmpfs noexec、--init、env 白名单（现 3 个已符合，固化断言）、root-user validator、_read_limited 防死锁；**create 记录 owner(agent/task)→run_python/run_pytest 属主校验**；sandboxes 按 agent 分子目录
- `skills/coding/git-audit/SKILL.md` —— 去 `/home/alan_fang` 硬编码（相对描述）
- `scripts/start_filesystem_mcp.sh` —— 改 ROOT 推导（照 start_code_filesystem_mcp.sh 样板）
- `config/agent_skills.yaml` —— （**需组员确认**）python-fact-grounded-coding 裁剪 Pylance 段后注册，或记录不注册原因

**迁移的 AgentForge 文件**：tools/executor.py（管线阶段逻辑，非类整体）、tools/policy.py（PermissionEvaluation 结构）、sandbox/docker.py（参数差异项+validator+_read_limited）、sandbox/base.py（SandboxConfig validator）。

**数据库变化**：无新表（复用 P3 的列与对账表）。

**新增测试**（~20 个）：治理管线各阶段单测（搬 test_tool_framework 12 例语义）；持久会话（10 调用 1 spawn、kill server 自愈、断路器）；沙箱参数断言+属主隔离+哨兵（Docker 缺失不回退）；重复守卫/预算。

**回归**：全量 pytest + phase0_e2e（应显著变快：refresh 不再每请求 spawn）+ phase3_e2e。

**完成标准**：所有工具调用 100% 经治理层且审计含 digest/duration/decision；高危配置化触发审批；跨 agent 沙箱访问被拒；MCP 调用 P95 延迟较 Phase 0 下降（记录基线对比）；无第二套工具执行系统（架构审查项）。

---

## Phase 6：Benchmark + Ablation + 故障注入（预计 5~7 人日）★★★

**要解决的问题**：无自动化评测；功能声明缺乏独立验证；Mock/真实结果无分账机制。

**新增模块**：
- `src/agent_ecosystem/benchmark/` —— models.py（BenchmarkTask/VerificationSpec 8 check/RunMetrics 31+ 字段/AggregateMetrics/BenchmarkRun/Batch，角色改三角色+**新增 analysis 类别与数值容差 check**）、dataset.py（JSONL/目录加载+强校验，原样）、runner.py（批次/resume/预算后置否决/git 溯源；驱动改 Coordinator REST+A2A 轮询）、verification.py（8 类 check 取数改 orchestration/memory 库+mcp_workspace+沙箱重跑）、metrics.py（公式照搬+A2A 新增指标：HTTP 调用数/延迟/委托深度/记忆检索率/使用率/记忆辅助成功率）、report.py（JSON/JSONL/CSV/MD+MOCK/LIVE 横幅，原样）、failures.py（12 类分类，错误文案规则重写）、ablation.py（A 单 agent=直发单 Agent 全能力白名单/B 多 agent 无记忆/C 多 agent+演化；学习评测分离+shared memory）、faults.py（故障注入器：暂停/取消/工具失败/stub MCP/Docker 失败/invalid JSON/重启/A2A 5xx 超时/委托环）
- `benchmarks/tasks/build_tasks.py` + `benchmarks/tasks/dev.jsonl` —— ~30 任务生成器（DSL+model_validate 即时校验模式照搬）：11 类（research/code_generation/debugging/planning/delegation/tool_execution/memory_retrieval/memory_evolution/approval/clarification/failure_recovery/docker_execution，含 analysis 统计任务）每类 basic+hard，开放式任务显式 manual_review
- `cli_benchmark.py`（或 scripts/）—— list/run/compare/report/failures/ablation 子命令+`--allow-live-api` 门禁

**修改现有文件**：Mock LLM server 升级为 fixture policy provider（条件动作匹配+故障注入钩子，兼容 Phase 0 脚本协议）。

**迁移的 AgentForge 文件**：benchmark/models|dataset|report|metrics|ablation|failures（近原样）、runner|verification|execution|provider（驱动/取数层重写）、test_phase5_* 用例语义。

**数据库变化**：无（评测只读现有库+独立批次目录）。

**新增测试**（~18 个）：harness 自检批次（fixture 5 任务全 PASSED+4 报告产物+横幅）、resume 幂等、预算后置否决、verifier 不采信自述、A/B/C 冒烟、每故障类型一用例（pytest -m reliability / docker 标记分离）、数据集覆盖度断言（≥28 任务/11 类/每类 basic+hard/全有客观 check）。

**回归**：全量 pytest + 三个 E2E 脚本。

**完成标准**：`benchmark run`（fixture）产出 JSON/JSONL/CSV/MD 四格式报告与用户要求的全部指标；A/B/C 同条件对比报告产出；成功判定 100% 来自独立验证器（文件/审计/沙箱重跑）；故障注入全场景行为符合预期；未授权时零付费调用（门禁测试锁定）。

---

## Phase 7：Provider 稳定性 + 文档 + 部署 + 最终集成（预计 3~5 人日）★★

**要解决的问题**：裸 AsyncOpenAI 无韧性；live 评测无阶梯门禁；部署文档缺失；无 CI。

**新增模块**：
- `src/agent_ecosystem/llm/` —— base.py（LLMProviderError+7 类错误分类+classify_transport_error，原样迁移）、resilient.py（AsyncOpenAI 包装：RETRYABLE{408,409,429,500,502,503,504} 有界重试+退避 min(0.5·2^n,4)+jitter+Retry-After、401/403 立即抛、key 脱敏、usage 累计落库、上下文长度守卫）、budgets.py（LiveBudgetProvider 语义：请求+token 双预算+snapshot+`MAS_LIVE_API_ALLOWED` 门）、structured.py（extract_json_payload 三级降级+修复提示，Planner 用）
- `benchmark/live.py` —— 六级阶梯验收（连通性→结构化规划→单工具→多 agent DAG→HITL+记忆→全量），双条件授权（key 配置∧显式开关），逐级 PASSED 才进，共享预算（默认 120 req/250k token），报告 NOT_VERIFIED 默认态
- `.github/workflows/ci.yml` —— pytest+ruff（Ubuntu+Windows 矩阵；Docker 标记 job 分离）
- `docs/DEPLOYMENT.md` —— WSL2/Docker/Windows 三环境部署文档、服务启动脚本说明、.env 指南、常见错误表（吸收 Phase 0 全部踩坑）

**修改现有文件**：`tool_agent.py`/`a2a_server.py`/`coordinator.py` 的 AsyncOpenAI 实例统一换 resilient 包装（行为兼容，配置驱动）；`compose.yaml` 可选扩展 coordinator+3 agent 容器化 profile（SQLite 卷挂载；不强制）。

**迁移的 AgentForge 文件**：llm/base.py（原样）、openai_compatible.py（重试/分类/脱敏逻辑适配包装形态）、benchmark/live*.py（门禁+阶梯模式）、provider 预算类。

**数据库变化**：无（usage 落 agent 执行审计既有表/新列）。

**新增测试**（~15 个）：500 重试/401 单次/超时分类/脱敏断言/退避序列/预算超限/阶梯无授权全 NOT_VERIFIED/harness 自检；流式（若启用）reasoning 隔离+部分内容永不成功。

**回归**：全量 pytest+三 E2E+fixture benchmark 批次。

**完成标准**：Mock 全链路在注入网络抖动（代理层模拟 429/断连）下仍稳定完成；所有错误输出无 key；CI 绿；部署文档在干净 WSL 环境按文档一次跑通（实测）；（若用户授权 live）阶梯 1~4 级在真实网关 PASSED 且预算快照落盘——**未授权则全部标 NOT_VERIFIED，不冒充**。

---

## 依赖关系与里程碑

```
P2 (DAG+调度) ──► P3 (持久化+HITL) ──► P5 (工具治理) ──► P6 (Benchmark) ──► P7 (Provider+文档+CI)
      │                 │                                     ▲
      └────────► P4 (记忆演化) ────────────────────────────────┘（P4 与 P3/P5 可部分并行；P6 的 C 组依赖 P4）
```

里程碑验收（每阶段末向用户汇报后继续）：
- M2：RAG 示例 DAG Mock 全链路 + 并行证据
- M3：kill -9 恢复零重复副作用 + HITL 全场景
- M4：mock/real 分账晋升演示 + 检索反馈闭环
- M5：治理层 100% 覆盖 + 延迟基线对比
- M6：30 任务 fixture 批次四格式报告 + A/B/C
- M7：CI 绿 + 部署文档实测 +（授权后）live 阶梯
