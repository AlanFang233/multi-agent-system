---
name: evidence-synthesis
description: 对研究、仓库状态和多 Agent 报告进行证据核验，分清实际工具结果、下游 Agent 陈述与无法验证的推断。适用于形成研究报告和整合 A2A 子任务。
metadata:
  version: "0.1.0"
---
# Evidence synthesis — Research 专用

1. 将问题拆分为可以查证的子任务，先检查已有证据，不重复执行同一检查。
2. Git / 代码检查委托 Coding；定量计算和数据分析委托 Analysis。优先委托专业 Agent，不在不适合的 MCP 沙箱内反复尝试。
3. 证据层级：A. 本 Agent 工具原始结果；B. 下游 Agent 报告（标注来源及 A2A task id）；C. 未验证推测。不可把 B 写成 A。
4. 对文件读取、访问拒绝、无匹配内容明确说明，不要猜测。
5. Filesystem MCP 根目录是 `mcp_workspace`，不等于 Git 仓库根。Fetch 仅用于 HTTP/HTTPS；不要用 `file://` 读取本地文件。
6. 当关键结论可证时立即汇总；连续两次路径或工具调用无效后停止重复试探。

## 输出格式

- **目标**：用户要解决的问题
- **事实与证据**：工具、Agent、路径、验证状态
- **分析与不确定性**：仅根据现有证据推导
- **建议与限制**：缺少证据的部分如实说明

## 工作区约定

项目 Git 仓库根目录：
/home/alan_fang/projects/multi-agent-system

Filesystem MCP 沙箱：
/home/alan_fang/projects/multi-agent-system/mcp_workspace

两个目录用途不同。

- Git 相关检查优先委托 Coding Agent。
- 不要为了检查 Git 而反复探索 Filesystem 沙箱。
- Filesystem MCP 的 "." 代表 mcp_workspace。
- 无法访问项目根目录不代表 Git 仓库不存在。
- 子目录没有 .git 不代表它不属于父级 Git 仓库。
