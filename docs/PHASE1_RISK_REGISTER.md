# Phase 1 风险登记册（PHASE1_RISK_REGISTER）

- 日期：2026-11-16；分支：`feature/agentforge-capabilities`
- 评级：可能性/影响 各取 高/中/低；状态：Open（默认）/Mitigated-by-design（设计已内建缓解）/Watch
- 来源：AgentForge 源码审计发现的固有限制、Phase 0 实测踩坑、两架构融合的设计缝隙、协作与进度因素。

## 一、Top 5 风险（需每次阶段评审首先过目）

| ID | 风险 | 为什么最关键 |
|---|---|---|
| R1 | 并行调度竞态 | AgentForge 调度器是串行的（审计确认无 gather/TaskGroup），其状态容器 `_RunContext` 完全无并发保护；我们新写真并行调度器等于进入上游没有测试覆盖过的无人区 |
| R2 | 跨进程证据链断裂 | AgentForge 的证据/重放/对账全部假设"编排器与工具审计同库同进程"；本架构工具审计在各 Agent 进程的共享 SQLite，一旦未来分布式部署（异机）整套幂等机制失效 |
| R8 | 组员协作冲突 | 主仓库是组员项目；任何"清理死代码/改配置语义"的动作都可能与其未推送的本地工作冲突 |
| R13 | 记忆库在线迁移破坏数据 | ALTER 加列+旧行回填发生在有真实数据的库上；回填策略错误会污染证据分级（如把历史词法 verified 记忆误标为 REAL） |
| R3 | HKGAI 网关不稳定 | 历史实测：~2,290 字符以上 prompt 出现无响应断连（11/12 请求失败）；本计划的工作流 prompt（依赖结果+证据注入）天然更长 |

## 二、完整登记表

### A. 架构与并发

| ID | 风险 | 可能性 | 影响 | 缓解 | 预警信号 | 涉及阶段 |
|---|---|---|---|---|---|---|
| R1 | 并行调度竞态：节点状态双写、ready 集合重复派发、metrics 计数丢失 | 高 | 高 | 单写者原则（仅 Scheduler 协程改节点状态，asyncio 单线程模型内天然串行化状态变更；gather 的 worker 只返回结果不直接改库）；派发前 CAS（UPDATE … WHERE status='pending'）；节点级锁按 node_id；并行测试显式断言"两独立节点时间重叠且状态无双写" | 同一节点出现两条 dispatched 事件；node_events 乱序 | P2 |
| R2 | 跨进程证据链断裂：重放缓存/对账/审批消费依赖 Agent 与 Coordinator 共读同库 | 中 | 高 | 当前部署形态（同机 WSL）下共享 SQLite WAL 是既有事实（memory 库已多进程写）；表所有权划分明确（API 文档 §5.1）；所有跨进程读走"先落库后引用"（evidence_refs 传 result_id 不传内容）；**文档明示边界：异机部署需先把审批/对账服务化为 Coordinator API**，列入 P7 部署文档限制章节 | 出现第二个部署主机需求；WAL busy 错误率上升 | P3/P5 |
| R12 | 治理层演化成第二套工具系统（违反融合原则） | 中 | 高 | 架构约束入文档与 code review 清单：ToolGovernance 禁止实现任何工具调用/发现逻辑，只做策略与审计；MCP ToolRegistry 保持唯一后端；P5 验收含"架构审查项：grep 治理层无 call_tool/Client 引用" | 治理层出现 MCP Client import | P5 |
| R18 | 节点级 HITL 挂起超出上游已验证设计（AgentForge 只有 workflow 级暂停） | 中 | 中 | 状态机只加两条迁移（RUNNING→WAITING_INPUT、WAITING_INPUT→PENDING/CANCELLED）；调度器把 WAITING_INPUT 视为"非 ready 非终态"（与 BLOCKED 同型处理，复用已验证路径）；专项测试：挂起节点不阻塞兄弟分支、恢复后精确一次 | 恢复后兄弟节点状态异常；挂起期间死锁误报 | P3 |
| R14 | MCP 持久会话引入状态性 bug（连接泄漏、工具目录过期、stdio 进程僵死） | 中 | 中 | 会话池带健康检查+失败重建+断路器（N=3 败熔断 30s）；保留 per-call 降级开关（配置一键回退 Phase 0 行为）；泄漏测试（100 调用后进程数断言） | refresh 后工具数与 per-call 模式不一致 | P5 |
| R16 | Docker 资源竞争：并行节点×一次性容器×WSL 内存上限 | 中 | 中 | 调度并发上限默认 3 且可配；沙箱已有 512m/1cpu/pids 64 限额；benchmark 批次串行（沿用 AgentForge）；压测记录 WSL 内存水位入部署文档 | 容器 OOM/启动失败率上升 | P2/P6 |

### B. 数据与迁移

| ID | 风险 | 可能性 | 影响 | 缓解 | 预警信号 | 涉及阶段 |
|---|---|---|---|---|---|---|
| R13 | 记忆库在线迁移破坏/污染数据 | 中 | 高 | 迁移函数幂等+迁移前自动备份文件（cp 到 data/backups/ 带时间戳）；旧行回填保守（verification=UNVERIFIED，绝不回填 REAL）；status→promotion_state 只做投影不改写原列；迁移测试用"旧 schema fixture 库"验证升级后旧测试全绿 | 迁移后旧 CLI 命令输出异常 | P3/P4 |
| R6 | SQLite 多进程写竞争加剧（新增 Coordinator/治理层/审批 CAS 多个写者） | 中 | 中 | WAL+busy_timeout=10s（现有）；写路径全部短事务；表所有权划分；CAS 单语句原子；竞争压测（双进程并发写 approvals）列入 P3 测试 | database is locked 错误出现 | P3+ |
| R19 | 哈希 embedding 非语义导致检索质量提升有限；_procedure_matches 全等匹配过脆 | 高 | 中 | 接受现状并诚实标注（AgentForge 自标 NOT VERIFIED）；EmbeddingProvider 接口预留真实模型；反馈匹配放宽为子序列+参数提示相似（迁移时已改）；benchmark C 组用检索/使用率指标客观衡量，若无提升则报告如实呈现 | C 组 memory_usage_count 恒 0 | P4/P6 |
| R5 | Prompt 膨胀：技能策略 2.6k 字符 + 工作流模板（依赖结果/证据段）叠加，推高 token 成本并触发 HKGAI 断连（R3 联动） | 高 | 中 | 尺寸守卫测试（SYSTEM_PROMPTS ≤ 显式上限）；技能策略按需注入（仅当角色有注册技能）；工作流模板各段截断预算入配置（依赖结果[:2000]、证据[:300]/条、总量受 ContextManager 128k 约束且 compact_trigger 提前压缩）；规划用 compact prompt 选项 | 单请求 prompt_chars 超过历史断连阈值（~2,290 字符经验值→live 时监控） | P2/P7 |

### C. 外部依赖与环境

| ID | 风险 | 可能性 | 影响 | 缓解 | 预警信号 | 涉及阶段 |
|---|---|---|---|---|---|---|
| R3 | HKGAI 网关不稳定（无响应断连/超时） | 高 | 高 | 全部开发与验收默认 Mock；live 走六级阶梯（连通性先行，1 次调用低成本探活）；compact 规划协议；ResilientLLMClient 断连分类+有界重试+断连硬停（吸取 Stage2BudgetProvider 经验）；预算封顶（120 req/250k token）；live 失败不阻塞阶段验收（标 NOT_VERIFIED） | stage1 连通性 FAILED | P7 |
| R4 | a2a-sdk 版本漂移（1.3.0 Struct 事故已发生一次；pyproject 允许 >=1,<2 全区间） | 中 | 高 | pyproject 锁定 `a2a-sdk~=1.3.0`（P2 提交）；契约测试常驻（test_a2a_metadata.py 已建；新增 TaskStore/TaskState 契约断言）；升级须过全量回归 | pip 解析出 1.4+ | P2 起持续 |
| R10 | Windows/WSL 工程陷阱（wsl.exe 参数 $ 剥离、MSYS 路径转换、CRLF、UTF-16 日志、GBK mojibake） | 高 | 低 | 已固化工作法：逻辑一律进脚本文件+`cd ~/… && bash scripts/x.sh`；Write/Edit 走 UNC 路径；日志 `iconv`；经验已写入记忆与 DEPLOYMENT 文档（P7） | 命令行为异常空输出 | 全程 |
| R17 | 端口/进程冲突（8001-8003/9001/9100 固定；测试与开发实例互踩） | 中 | 低 | phase0_start_all.sh 先 pkill 后启动；benchmark 每 run 独立数据目录+可选端口偏移配置；CI 用独立端口段 | health 检查串台（角色不符） | P2+ |

### D. 协作与流程

| ID | 风险 | 可能性 | 影响 | 缓解 | 预警信号 | 涉及阶段 |
|---|---|---|---|---|---|---|
| R8 | 组员协作冲突：死代码清理/配置语义变更与其本地工作冲突；合并回 main 时的争议 | 中 | 高 | 只在 feature 分支开发；不删组员代码（deprecation 仅 docstring 标注）；每阶段独立小 commit 便于 cherry-pick；合并 main 前需用户+组员双确认；涉及组员意图的改动（技能注册 #5、静态委托去留 #4）先问后动 | 远端 main 出现新 commit | 全程 |
| R9 | Mock 结果被误报为真实 LLM 能力 | 中 | 高 | 设计层强制分账（provider_kind 全链路列/横幅/枚举）；报告措辞规范（"Mock 编排，工具真实执行"）；live 未授权一律 NOT_VERIFIED；本人汇报纪律：每份报告首行标注证据等级 | 报告出现无出处的成功率数字 | P4/P6/P7 |
| R11 | 工作量超支（24~34 人日）与课程时间线冲突 | 中 | 中 | 阶段独立可验收可暂停（每 Phase 末为天然停止点）；优先级已排（P2/P3/P4 关键路径，P5-P7 可裁剪范围：如 P5 只做治理层不做持久会话、P6 任务集 30→18、P7 流式延后）；每阶段实际耗时记录并滚动修正估算 | 单阶段超估算 50% | 全程 |
| R20 | 审批覆盖面缺口：高危操作未入 tool_policy 规则（如 filesystem_code:edit_file 改写重要文件、未来新增 MCP server） | 中 | 中 | 默认拒绝思维：新增 server/tool 时治理层对"未知工具"记 WARN 审计；policy 表评审入 P5 验收；文件系统写操作全量列规则（write/edit/create_directory）；git 无写工具现状在文档标注（若未来放开 git push MCP 必须先入 policy） | 审计出现 policy 未覆盖的写操作 | P5 |
| R7 | HITL 无鉴权（loopback 信任模型，decided_by 自报） | 低 | 中 | 与主项目既有安全边界一致（委托 loopback-only）；文档明示单用户本地模型；审批消费五元组绑定+一次性 CAS 已防进程间误用；多用户场景列为范围外并写入 DEPLOYMENT 限制 | 部署需求出现多用户/远程访问 | P3/P7 |
| R15 | Benchmark 对 fixture 过拟合：Mock 剧本全绿但真实 LLM 行为差异大 | 高 | 中 | fixture 结果强制标注"harness validation, not LLM quality"（照搬 AgentForge 免责声明）；live 阶梯授权后小预算抽查（9 任务）对照 fixture 行为差异；任务验证全部走独立 verifier（文件/审计/沙箱重跑），不依赖 LLM 输出文本，天然降低过拟合影响 | live 抽查成功率与 fixture 差 >40pp | P6/P7 |

## 三、机会清单（正向风险）

| ID | 机会 | 说明 |
|---|---|---|
| O1 | Phase 0 修复的 A2A Struct bug 与 metadata 回归测试可直接作为 a2a-sdk 升级契约门 | 已落地 |
| O2 | AgentForge 314 个测试中约 60+ 例可适配复用，大幅摊薄 P2-P6 测试成本 | 各阶段测试清单已列明搬改用例 |
| O3 | 主项目一次性只读沙箱模型比 AgentForge 常驻可写容器更严格，可作为课程报告的差异化安全亮点 | P5 参数对比表已备 |
| O4 | 共享 SQLite 的同机部署让"跨进程证据链"以极低成本获得（无需消息队列） | 若课程演示需要分布式，再评估服务化 |

## 四、评审节奏

每个 Phase 验收时：① 过 Top 5 风险状态；② 新增风险入册；③ 已缓解风险降级为 Watch/Mitigated；④ 实际耗时 vs 估算滚动修正 R11。
