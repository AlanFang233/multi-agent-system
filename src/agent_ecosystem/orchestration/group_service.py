"""Dynamic Group management — persistent groups with custom role agents.

Groups are stored in ``data/orchestration.sqlite`` alongside workflow data.
When a Group is activated, dynamic A2A agent processes are spawned on ephemeral
ports with the group's custom role prompts and capability configs.

Tables added: ``groups``, ``group_agents``
"""
from __future__ import annotations

import json, os, signal, sqlite3, subprocess, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GroupAgent:
    agent_id: str          # namespaced "{group}:{role}" after activation
    role_name: str         # "Technical Lead" | "Software Developer" | ...
    role_prompt: str       # Full system prompt (injected into A2A server)
    capabilities: list[str] = field(default_factory=list)
    tool_profile: str = "research"   # which base permission set to copy
    port: int = 0          # Dynamically assigned
    pid: int = 0           # OS process ID

@dataclass
class Group:
    group_id: str
    group_name: str
    agents: list[GroupAgent] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class GroupService:
    """Manages Groups and their dynamic agent lifecycles."""

    def __init__(self, db_path: str = "data/orchestration.sqlite") -> None:
        self.db_path = db_path
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id TEXT PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    agents_json TEXT NOT NULL DEFAULT '[]',
                    workflow_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS group_agents (
                    agent_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    role_name TEXT NOT NULL,
                    role_prompt TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    port INTEGER NOT NULL,
                    pid INTEGER NOT NULL,
                    PRIMARY KEY (group_id, agent_id)
                );
            """)
            conn.commit()

    def create_group(self, group_id: str, group_name: str,
                     agents: list[dict]) -> Group:
        """Persist a new group with its agent definitions (does NOT start processes)."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO groups(group_id, group_name, agents_json, created_at) "
                "VALUES(?,?,?,?)",
                (group_id, group_name, json.dumps(agents, ensure_ascii=False), now),
            )
            conn.commit()
        return Group(
            group_id=group_id, group_name=group_name,
            agents=[], created_at=now,
        )

    def get_group(self, group_id: str) -> Group | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if row is None:
                return None
            agents_data = json.loads(row["agents_json"])
            agent_rows = conn.execute(
                "SELECT * FROM group_agents WHERE group_id=?", (group_id,)
            ).fetchall()
            agents = [
                GroupAgent(
                    agent_id=r["agent_id"], role_name=r["role_name"],
                    role_prompt=r["role_prompt"],
                    capabilities=json.loads(r["capabilities_json"]),
                    port=r["port"], pid=r["pid"],
                ) for r in agent_rows
            ]
            return Group(
                group_id=row["group_id"], group_name=row["group_name"],
                agents=agents,
                workflow_ids=json.loads(row["workflow_ids_json"]),
                created_at=row["created_at"],
                metadata=json.loads(row["metadata_json"]),
            )

    def list_groups(self) -> list[Group]:
        with self._connect() as conn:
            rows = conn.execute("SELECT group_id FROM groups ORDER BY created_at DESC").fetchall()
        return [self.get_group(r["group_id"]) for r in rows]

    def add_workflow(self, group_id: str, workflow_id: str) -> None:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT workflow_ids_json FROM groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if current is None:
                return
            wf_ids = json.loads(current["workflow_ids_json"])
            if workflow_id not in wf_ids:
                wf_ids.append(workflow_id)
            conn.execute(
                "UPDATE groups SET workflow_ids_json=? WHERE group_id=?",
                (json.dumps(wf_ids), group_id),
            )
            conn.commit()

    # -- agent process management --------------------------------------------

    def start_agents(self, group_id: str, agents: list[GroupAgent],
                     base_port: int = 8100) -> list[GroupAgent]:
        """Spawn one A2A server process per agent on sequential ports.

        Agent ids are namespaced as ``{group_id}:{role}`` so memory rows, tool
        permissions and delegation routes never collide across groups. Each
        group gets its own generated MCP/agent registry files (permissions are
        copied from the role's ``tool_profile``), passed to the child process
        via MAS_* environment variables."""
        import yaml

        repo_root = Path(__file__).resolve().parents[3]
        venv_python = str(repo_root / ".venv" / "bin" / "python")
        config_dir = repo_root / "config"

        # Load base registries to copy servers/permissions/policy from
        base_mcp = yaml.safe_load((config_dir / "mcp_registry.yaml").read_text(encoding="utf-8"))
        base_agents = yaml.safe_load((config_dir / "agent_registry.yaml").read_text(encoding="utf-8"))
        base_permissions = base_mcp.get("permissions", {})

        # Generate group-scoped registries
        group_mcp = {"servers": base_mcp.get("servers", {}), "permissions": {}}
        group_registry = {"agents": {}, "policy": base_agents.get("policy", {})}
        started = []
        for i, agent in enumerate(agents):
            port = base_port + i
            ns_id = f"{group_id}:{agent.agent_id}" if ":" not in agent.agent_id else agent.agent_id
            url = f"http://127.0.0.1:{port}"
            profile = getattr(agent, "tool_profile", "research")
            group_mcp["permissions"][ns_id] = list(base_permissions.get(profile, []))
            group_registry["agents"][ns_id] = {
                "url": url,
                "description": agent.role_name,
                "capabilities": list(agent.capabilities),
            }
            agent.port = port
            agent.agent_id = ns_id
            started.append(agent)

        mcp_path = config_dir / f"mcp_registry_{group_id}.yaml"
        reg_path = config_dir / f"agent_registry_{group_id}.yaml"
        mcp_path.write_text(yaml.safe_dump(group_mcp, allow_unicode=True), encoding="utf-8")
        reg_path.write_text(yaml.safe_dump(group_registry, allow_unicode=True), encoding="utf-8")

        env = dict(os.environ)
        env["MAS_MCP_REGISTRY"] = str(mcp_path)
        env["MAS_AGENT_REGISTRY"] = str(reg_path)

        for agent in started:
            role_name = agent.agent_id.split(":", 1)[1]
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO group_agents(agent_id, group_id, role_name, "
                    "role_prompt, capabilities_json, port, pid) VALUES(?,?,?,?,?,?,?)",
                    (agent.agent_id, group_id, agent.role_name, agent.role_prompt,
                     json.dumps(agent.capabilities), agent.port, 0),
                )
                conn.commit()
            log_dir = repo_root / "logs"
            log_dir.mkdir(exist_ok=True)
            log_file = open(log_dir / f"group_{group_id}_{role_name}.log", "ab")
            proc = subprocess.Popen(
                [venv_python, "-m", "agent_ecosystem.a2a_server",
                 "--group-id", group_id, "--role-name", role_name,
                 "--port", str(agent.port), "--host", "127.0.0.1"],
                cwd=str(repo_root), env=env,
                stdout=log_file, stderr=log_file,
            )
            agent.pid = proc.pid
            with self._connect() as conn:
                conn.execute(
                    "UPDATE group_agents SET pid=? WHERE group_id=? AND agent_id=?",
                    (proc.pid, group_id, agent.agent_id),
                )
                conn.commit()
        # Wait for servers to bind
        time.sleep(3)
        return started

    def stop_agents(self, group_id: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pid FROM group_agents WHERE group_id=?", (group_id,)
            ).fetchall()
            for r in rows:
                try:
                    os.kill(r["pid"], signal.SIGTERM)
                except OSError:
                    pass
            conn.execute(
                "DELETE FROM group_agents WHERE group_id=?", (group_id,)
            )
            conn.commit()

    def get_agent_urls(self, group_id: str) -> dict[str, str]:
        """Return {agent_id: url} for a running group."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id, port FROM group_agents WHERE group_id=?", (group_id,)
            ).fetchall()
        return {r["agent_id"]: f"http://127.0.0.1:{r['port']}" for r in rows}


# ── scenario 1: 4-role software dev team ────────────────────────────────────

SCENARIO_1_ROLES = [
    {
        "agent_id": "lead",
        "role_name": "Technical Lead",
        "role_prompt": (
            "You are the Technical Lead of a software development team.\n"
            "Your responsibilities:\n"
            "- Understand user requirements and define the overall technical approach.\n"
            "- Break down the project into concrete subtasks and assign them to "
            "the appropriate team members (dev, qa, release).\n"
            "- Make final decisions on architecture, technology stack, and scope trade-offs.\n"
            "- Do NOT write all the code yourself — delegate implementation to the Developer.\n"
            "- Do NOT test everything yourself — delegate testing to the QA Engineer.\n"
            "- If information is missing, ask the user for clarification.\n"
            "- Use authorized MCP tools. Verify execution results before claiming success.\n"
            "- You may delegate subtasks to other agents via a2a:delegate_task."
        ),
        "capabilities": ["planning", "task_decomposition", "delegation", "synthesis",
                     "evidence_synthesis", "web_search"],
        "tool_profile": "research",
    },
    {
        "agent_id": "dev",
        "role_name": "Software Developer",
        "role_prompt": (
            "You are the Software Developer of the team.\n"
            "Your responsibilities:\n"
            "- Implement features and bug fixes according to the Lead's specifications.\n"
            "- Write clean, tested, maintainable code.\n"
            "- Use the Docker sandbox for writing and running code.\n"
            "- Report implementation progress and any blockers to the Lead.\n"
            "- Use authorized MCP tools. Verify execution results before claiming success.\n"
            "- You may delegate subtasks to other agents via a2a:delegate_task."
        ),
        "capabilities": ["coding", "sandbox_execution", "file_editing", "python_debugging"],
        "tool_profile": "coding",
    },
    {
        "agent_id": "qa",
        "role_name": "QA Engineer",
        "role_prompt": (
            "You are the QA (Quality Assurance) Engineer of the team.\n"
            "Your responsibilities:\n"
            "- Design and execute test plans for features delivered by the Developer.\n"
            "- Write automated tests runnable inside the Docker sandbox.\n"
            "- Report test results, bugs, and quality metrics to the Lead.\n"
            "- Do NOT modify production code — only write test code.\n"
            "- If a test fails, clearly describe the failure so the Developer can reproduce it.\n"
            "- Use authorized MCP tools. Verify execution results before claiming success."
        ),
        "capabilities": ["testing", "sandbox_execution", "statistics", "numerical_analysis"],
        "tool_profile": "coding",
    },
    {
        "agent_id": "release",
        "role_name": "Release Manager",
        "role_prompt": (
            "You are the Release Manager of the team.\n"
            "Your responsibilities:\n"
            "- Ensure all deliverables meet quality standards before release.\n"
            "- Coordinate with QA to confirm tests pass.\n"
            "- Prepare release notes, version tags, and deployment checklists.\n"
            "- Track the overall project timeline and report progress to the Lead.\n"
            "- Do NOT write code — focus on release readiness and documentation.\n"
            "- Use authorized MCP tools. Verify execution results before claiming success."
        ),
        "capabilities": ["review", "synthesis", "file_editing", "documentation_research"],
        "tool_profile": "analysis",
    },
]