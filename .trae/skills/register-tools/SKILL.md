---
name: register-tools
description: Select least-privilege MCP tools for requested Agent roles. Use when registering tools for an agent group. Do not use for installing servers or bypassing approval.
---

# Register Tools

1. Map each requested responsibility to concrete tool operations. Distinguish reading, writing, network access and code execution.
2. Select `tool_profile` from `research`, `coding` or `analysis`. These are permission profiles, not pre-mounted Agent processes.
3. Select each `server:tool` identifier exactly from the supplied profile allowlist. Do not invent identifiers, wildcard grants, commands, URLs, credentials or service installations.
4. Respect explicit read-only and no-execution requirements. Request only tools needed by this role; omit unrelated tools even if the profile allows them.
5. Include prerequisite tools when needed. Running a Docker sandbox requires `sandbox:create` and an appropriate run tool. File editing normally requires reading or inspecting the target.
6. Follow [the registration template](assets/tool-registration-template.yaml). Return `mcp_tools` in `submit_team`; the Coordinator, not the model, renders trusted server definitions and namespaced permissions.
7. Describe each role using [the role template](assets/agent-role-template.md). Keep role authority separate from Skill procedure.
8. Present tools and risk boundaries for human confirmation. Never claim a server is reachable or a tool has run unless actual runtime evidence supports that claim.

After confirmation, the Coordinator copies only referenced trusted MCP server definitions,
binds Filesystem and Docker paths to the team's workspace, writes registries, and launches
A2A workers. Stop on unknown tools or permission failures; do not broaden permissions.
