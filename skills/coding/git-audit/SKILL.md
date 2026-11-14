---
name: git-audit
description: 只读审查 Python 项目的 Git 工作区、未提交修改、分支及提交历史。适用于仓库状态检查、开发进度审计，不执行提交或推送。
metadata:
  version: "0.1.0"
---
# Git audit — Coding 专用

## 环境

Git 项目根目录：`/home/alan_fang/projects/multi-agent-system`。
Filesystem MCP 根目录：`/home/alan_fang/projects/multi-agent-system/mcp_workspace`，并非 Git 根目录。

## 工作流

1. 优先调用 `git:git_status`，`repo_path` 必须使用绝对项目根目录，不用 `.` 代替。
2. 如果可用，继续调用 `git:git_diff_unstaged` 和其他只读 Git 工具。
3. 先报告分支、staged、unstaged、untracked；再报告最近提交、远程分支、冲突或未知项目。
4. 如果 `No commits yet`，将最近提交列为“无历史”，而不是工具故障。
5. 尚未执行的检查必须写“未验证”，不要假装 `git log` 或 `git branch` 已运行。
6. 禁止修改文件、add、commit、push、reset 或打开 `.env`。A2A 子任务中的仓库路径必须匹配已授权项目。

## 输出格式

| 检查项 | 结果 | 证据来源 |
|---|---|---|

最后分别列出：已验证结果、权限或工具限制、推荐的下一步。不要将 Git 没有提交等同于源码不存在。
