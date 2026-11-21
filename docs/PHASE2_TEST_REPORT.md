# Phase 2 测试报告（PHASE2_TEST_REPORT）

- 日期：2026-11-17；分支 `feature/agentforge-capabilities`；Commit: `cf33bb2`..`e9f86a0`
- 全量 pytest：**124 passed**（46 基线 + 78 新增），0 失败，执行时间 3.84s
- 端到端 Mock Demo：**3/3 场景 PASSED**（A: 36.3s, B: 79.9s, C: 24.1s）
- 并行证据：barrier 单元测试 + 真实跨进程 OVERLAP 12.5s
- Ruff（F,E9）：新文件 0 问题；存量文件 3 F401（未修改）

---

## 一、新增测试清单（78 个，覆盖用户要求全部 22 类）

| 测试文件 | 数量 | 覆盖项 |
|---|---|---|
| `test_plan_validation.py` | 12 | 拓扑排序，环/自依赖/未知依赖/深度/规模拒绝，extra forbid，确定性tie-break，priority范围 |
| `test_node_state_machine.py` | 7 | 严格迁移表，运行态→已完成/待决/等待输入/阻塞，终态不可迁，WAITING_INPUT（P3预留） |
| `test_capability_assignment.py` | 8 | 三角色路由（真实配置），全覆盖拒绝，大小写/连字符归一化，确定性tie-break，偏好专家，部分分，空目录 |
| `test_planner_llm.py` | 13 | 有效计划一次通过，无效JSON→修复→成功，修复耗尽失败，schema违规，循环拒绝，超规模拒绝，能力缺口拒绝（不消耗修复次数），截断→[RETRY]不回显助手输出，provider能力降级（function→JSON），provider传输错误→显式失败无静默降级，extract_json_payload三级降级，拼装prompt含goal/catalog/limits |
| `test_dag_scheduler.py` | 14 | barrier重叠证明，并发上限1序列化，依赖等待，分支失败→兄弟继续+级联失败阻断下游派发，深度级联，节点超时，有界重试保持task_id幂等键，重试次数耗尽→失败，连接错误→改派，无替代agent→重试后失败，A2A调用预算，截止时间，取消标志，死锁检测，未分配agent→失败免派发，优先级排序，菱形压力一致性（单写者证明） |
| `test_workflow_service.py` | 10 | 合成+证据全链路，依赖结果在下游提示中传递，规划失败→工作流FAILED零节点，节点数超过上限→拒绝零残留，节点失败→根因分析，collect_evidence（读取真实SQLite审计而非LLM自述），A2A派发器构建x‑workflow元数据+收集证据，派发器失败分类（connect/failed_state/empty），render_node_prompt各段+合成标记，classify_provider（mock/live/unknown），status快照+取消标志 |
| `test_coordinator_api.py` | 6 | ASGI‑Transport全链路，404未知工作流，409冲突（未完成时查结果），422无效goal，取消端点，原有endpoint不变（/health,/agents） |
| `test_a2a_workflow_metadata.py` | 5 | 纯dict元数据，Protobuf Struct→MessageToDict（含Struct浮点数→整数兼容），缺省WF元数据→保持旧行为，无效WF元数据→容忍不崩溃 |

---

## 二、并行证明（并发 ≠ 仅创建 asyncio Task）

### Unit barrier 测试

```python
async def test_two_independent_nodes_really_overlap_barrier():
    a_in, b_in = asyncio.Event(), asyncio.Event()
    # Worker 在 dispatch 内按自身进入 → 互相等待
    # 若为串行则 wait_for timeout → 测试失败
    assert timeline["a_enter"] < timeline["b_exit"]
    assert timeline["b_enter"] < timeline["a_exit"]
```

### 真实跨进程探测

```
OVERLAP: max_enter=57833.96  min_exit=57846.48  →  12.5s 重叠
DEPENDENCY: synthesis enter=57871.88 = last plan exit → 依赖等待正确
EVIDENCE: all 3 plan nodes collected 2–4 tool audit rows per agent
wall_clock 77.9s vs total_execution 117.7s → 并行加速因子 1.5x
```

---

## 三、端到端 Mock 场景

| 场景 | 节点数 | 耗时 | 代理角色覆盖 | 特征 |
|---|---|---|---|---|
| A_simple | 3 (2 plan + synthesis) | 36.3s | research, analysis | 线性链 |
| B_complex | 6 (5 plan + synthesis) | 79.9s | research, coding, analysis | ML全流程含真实Docker沙箱执行 |
| C_parallel | 4 (3 plan + synthesis) | 24.1s | research（纯研究型） | 三节点并行重叠检测通过 |

---

## 四、回归完整性

- **Phase 0 基线**：46 个测试全部通过
- **Phase 0 E2E 兼容**：`scripts/phase0_e2e.py` 在 Phase 2 变更下重新运行，TASK_STATE_COMPLETED，8 工具调用全 is_error=False，3 verified procedural memory 生成 — 确认 A2A/MCP/Skills/Docker/Memory 未被破坏
- **Phase 1 doc 检查**：96/96 通过（基线锚定 commit `95d56ea`）