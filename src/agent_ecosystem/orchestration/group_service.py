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
    mcp_tools: list[str] | None = None
    skills: list[dict] = field(default_factory=list)

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
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
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
        """Persist a group with its agent definitions (does NOT start processes).

        Idempotent: re-creating an existing group stops any live agents, clears
        stale rows and replaces the definition, so a subsequent activate()
        spawns fresh processes on free ports instead of crashing on the
        groups.group_id UNIQUE constraint or returning dead URLs."""
        from datetime import datetime, timezone
        from .team_builder import runtime_dir
        runtime_dir(group_id)
        # Best-effort cleanup of any prior incarnation (kills live procs + rows).
        try:
            self.stop_agents(group_id)
        except Exception:
            pass
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO groups(group_id, group_name, agents_json, "
                "created_at) VALUES(?,?,?,?)",
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
        import socket

        repo_root = Path(__file__).resolve().parents[3]
        venv_python = str(repo_root / ".venv" / "bin" / "python")
        if not agents or len(agents) > 8:
            raise ValueError("A group must contain 1-8 agents")
        # Check the entire block; counting active groups alone can reuse occupied ports.
        for candidate in range(base_port, 9000 - len(agents), 10):
            sockets = []
            try:
                for offset in range(len(agents)):
                    sock = socket.socket()
                    sockets.append(sock)
                    sock.bind(("127.0.0.1", candidate + offset))
                base_port = candidate
                break
            except OSError:
                continue
            finally:
                for sock in sockets:
                    sock.close()
        else:
            raise RuntimeError("No available Agent port block")
        from .team_builder import write_bundle
        runtime = write_bundle(group_id, agents, base_port)
        started = []
        for i, agent in enumerate(agents):
            port = base_port + i
            ns_id = f"{group_id}:{agent.agent_id.split(':')[-1]}"
            agent.port = port
            agent.agent_id = ns_id
            started.append(agent)

        env = dict(os.environ)
        env["MAS_MCP_REGISTRY"] = str(runtime / "mcp_registry.yaml")
        env["MAS_AGENT_REGISTRY"] = str(runtime / "agent_registry.yaml")
        env["MAS_AGENT_SKILLS"] = str(runtime / "agent_skills.yaml")
        env["MAS_MEMORY_CONFIG"] = str(runtime / "memory.yaml")
        env["MAS_ORCHESTRATION_DATABASE"] = str(Path(self.db_path).resolve())

        try:
            return self._launch(group_id, started, env, runtime, repo_root, venv_python)
        except Exception:
            self.stop_agents(group_id)
            raise

    def _launch(self, group_id, started, env, runtime, repo_root, venv_python):
        import httpx
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
            log_dir = runtime / "logs"
            log_dir.mkdir(exist_ok=True)
            with open(log_dir / f"{role_name}.log", "ab") as log_file:
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
        deadline = time.monotonic() + 20
        pending = list(started)
        with httpx.Client(timeout=1, trust_env=False) as client:
            while pending and time.monotonic() < deadline:
                for agent in pending[:]:
                    try:
                        response = client.get(f"http://127.0.0.1:{agent.port}/health")
                        if response.status_code == 200 and response.json().get("agent") == agent.agent_id:
                            pending.remove(agent)
                    except (httpx.HTTPError, ValueError):
                        pass
                if pending:
                    time.sleep(0.1)
        if pending:
            raise RuntimeError("Agent startup failed; inspect runtime logs")
        return started

    def stop_agents(self, group_id: str) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pid FROM group_agents WHERE group_id=?", (group_id,)
            ).fetchall()
            for r in rows:
                try:
                    if r["pid"] > 0:
                        command = Path(f"/proc/{r['pid']}/cmdline").read_bytes().split(b"\0")
                        marker = command.index(b"--group-id")
                        if command[marker + 1].decode() == group_id:
                            os.kill(r["pid"], signal.SIGTERM)
                except (OSError, ValueError, IndexError):
                    pass
            conn.execute(
                "DELETE FROM group_agents WHERE group_id=?", (group_id,)
            )
            conn.commit()

    def get_agent_urls(self, group_id: str) -> dict[str, str]:
        """Return {agent_id: url} for a running group."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id, port, pid FROM group_agents WHERE group_id=?", (group_id,)
            ).fetchall()
        urls = {}
        for row in rows:
            try:
                command = Path(f"/proc/{row['pid']}/cmdline").read_bytes().split(b"\0")
                marker = command.index(b"--group-id")
                if command[marker + 1].decode() == group_id:
                    urls[row["agent_id"]] = f"http://127.0.0.1:{row['port']}"
            except (OSError, ValueError, IndexError):
                pass
        return urls


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
