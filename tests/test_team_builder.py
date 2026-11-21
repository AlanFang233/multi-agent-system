import json
import shutil
from types import SimpleNamespace

import httpx
import pytest
import yaml

from agent_ecosystem.agent_skill_registry import AgentSkillRegistry
from agent_ecosystem.coordinator import create_app
from agent_ecosystem.orchestration import team_builder as teams
from agent_ecosystem.orchestration.group_service import GroupAgent, GroupService
from orchestration_support import FakeLLMClient


def proposal():
    return {
        "group_name": "Demo", "agent_count": 2, "agents": [
            {
                "agent_id": "lead", "role_name": "Lead",
                "responsibilities": ["Research sources and summarize evidence"],
                "capabilities": ["evidence_synthesis", "web_search"],
                "tool_profile": "research", "mcp_tools": ["fetch:fetch"],
                "skills": [{"name": "source-review", "description": "Review evidence",
                            "instructions": "Check sources, cite evidence, report limitations."}],
            },
            {
                "agent_id": "dev", "role_name": "Developer",
                "responsibilities": ["Implement and test"],
                "capabilities": ["file_editing", "sandbox_execution"],
                "tool_profile": "coding",
                "mcp_tools": ["filesystem_code:write_file", "sandbox:run_pytest"],
                "skills": [{"name": "test-workflow", "description": "Verify implementation",
                            "instructions": "Write tests, run them, report actual results."}],
            },
        ],
    }


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    for name in ("mcp_registry.yaml", "agent_registry.yaml", "capability_catalog.yaml",
                 "coordinator_skills.yaml"):
        shutil.copyfile(teams.ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copytree(teams.ROOT / ".trae/skills", tmp_path / ".trae/skills")
    return tmp_path


def test_spec_count_ids_and_synthesis():
    for mutate in (
        lambda p: p.update(agent_count=3),
        lambda p: p["agents"][1].update(agent_id="lead"),
        lambda p: p["agents"][0].update(agent_id="../escape"),
        lambda p: p["agents"][0].update(capabilities=["web_search"]),
    ):
        p = proposal()
        mutate(p)
        with pytest.raises(ValueError):
            teams.TeamSpec.model_validate(p)


def test_unknown_capabilities_and_tool_escalation(root):
    for change in ({"mcp_tools": ["filesystem_code:write_file"]},
                   {"capabilities": ["root_access"]}):
        p = proposal()
        p["agents"][0].update(change)
        with pytest.raises(ValueError):
            teams.validate_policy(teams.TeamSpec.model_validate(p), root)


def test_bundle_and_namespaced_skills(root):
    spec = teams.TeamSpec.model_validate(proposal())
    agents = [GroupAgent(**a) for a in teams.agent_definitions(spec)]
    path = teams.write_bundle("demo", agents, 8100, root)
    assert path == root / "mcp_workspace/sandboxes/demo"
    mcp = yaml.safe_load((path / "mcp_registry.yaml").read_text())
    assert mcp["permissions"]["demo:lead"] == ["fetch:fetch"]
    assert set(mcp["servers"]) == {"fetch", "filesystem_code", "sandbox"}
    assert mcp["servers"]["filesystem_code"]["env"]["MAS_FILESYSTEM_ROOT"] == str(path / "workspace")
    registry = AgentSkillRegistry(SimpleNamespace(list_for_agent=lambda _: []),
                                  path / "agent_skills.yaml")
    skill = registry.list_for_agent("demo:lead")[0]
    assert skill["input_schema"]["properties"]["name"]["enum"] == ["source-review"]
    assert not registry.list_for_agent("other:lead")
    assert not (root / "config/mcp_registry_demo.yaml").exists()
    assert (path / "workspace/templates/write-skills/agent-skill-template.md").exists()
    assert "Reference role template" in agents[0].role_prompt


def test_runtime_rejects_paths_and_symlinks(root):
    with pytest.raises(ValueError):
        teams.runtime_dir("../escape", root)
    path = teams.runtime_dir("demo", root)
    (path / "manifest.json").symlink_to(root / "outside")
    with pytest.raises(ValueError):
        teams.runtime_dir("demo", root)


async def test_planner_repairs_and_persists(root):
    client = FakeLLMClient([
        {"arguments": '{"agents":[]}'}, {"arguments": json.dumps(proposal())},
    ])
    result = await teams.plan_team("Create two agents: lead and developer", client, "fake", root)
    assert result["status"] == "awaiting_confirmation"
    assert len(client.calls) == 2
    assert (root / "mcp_workspace/sandboxes" / result["group_id"] / "proposal.json").exists()


async def test_planner_failure_is_bounded(root):
    client = FakeLLMClient([{"arguments": "{}"}, {"arguments": "{}"}])
    with pytest.raises(ValueError):
        await teams.plan_team("Create two agents", client, "fake", root)
    assert len(client.calls) == 2
    assert not (root / "mcp_workspace/sandboxes").exists()


async def test_api_requires_confirmation_and_creates_team(root, monkeypatch):
    original_runtime, original_catalog = teams.runtime_dir, teams.catalog
    monkeypatch.setattr(teams, "runtime_dir", lambda gid, *args: original_runtime(gid, root))
    monkeypatch.setattr(teams, "catalog", lambda *args: original_catalog(root))
    llm = FakeLLMClient([{"arguments": json.dumps(proposal())}])
    svc = SimpleNamespace(store=SimpleNamespace(path=root / "groups.sqlite"),
                          planner=SimpleNamespace(client=llm, model="fake"))
    launches = []

    def fake_start(self, gid, agents, base_port):
        launches.append(agents)
        teams.write_bundle(gid, agents, base_port, root)
        with self._connect() as conn:
            for i, a in enumerate(agents):
                a.agent_id = f"{gid}:{a.agent_id}"
                a.port = base_port + i
                conn.execute(
                    "INSERT INTO group_agents VALUES(?,?,?,?,?,?,?)",
                    (a.agent_id, gid, a.role_name, a.role_prompt,
                     json.dumps(a.capabilities), a.port, 0),
                )
        return agents

    monkeypatch.setattr(GroupService, "start_agents", fake_start)
    def fake_urls(self, gid):
        with self._connect() as conn:
            return {r["agent_id"]: f"http://127.0.0.1:{r['port']}" for r in conn.execute(
                "SELECT agent_id,port FROM group_agents WHERE group_id=?", (gid,)
            )}
    monkeypatch.setattr(GroupService, "get_agent_urls", fake_urls)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(svc)), base_url="http://test"
    ) as client:
        result = (await client.post("/groups/plan", json={"prompt": "Create two agents"})).json()
        gid = result["group_id"]
        r = await client.post("/groups/provision", json={"group_id": gid})
        assert r.status_code == 409
        assert not launches
        r = await client.post("/groups/provision", json={"group_id": gid, "confirmed": True})
        assert r.status_code == 200, r.text
        assert len(r.json()["agents"]) == 2
        assert launches[0][0].skills[0]["name"] == "source-review"
        assert launches[0][0].mcp_tools == ["fetch:fetch"]
        r = await client.post("/groups/provision", json={"group_id": gid, "confirmed": True})
        assert r.status_code == 200
        assert len(launches) == 1
        page = (await client.get("/dashboard")).text
        assert "Preview Team" in page and "Confirm &amp; Start Agents" in page


def test_launch_failure_cleans_rows(root, monkeypatch):
    import subprocess
    original_runtime = teams.runtime_dir
    original_bundle = teams.write_bundle
    monkeypatch.setattr(teams, "runtime_dir", lambda gid, *args: original_runtime(gid, root))
    monkeypatch.setattr(teams, "write_bundle",
                        lambda gid, agents, port: original_bundle(gid, agents, port, root))
    gs = GroupService(str(root / "groups.sqlite"))
    gs.create_group("demo", "Demo", [])

    def fail(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(OSError):
        gs.start_agents("demo", [GroupAgent("lead", "Lead", "Research")])
    assert gs.get_agent_urls("demo") == {}


def test_group_sandbox_root_is_confined(root, monkeypatch):
    from agent_ecosystem import sandbox_mcp_server as sandbox
    monkeypatch.setattr(sandbox, "PROJECT_ROOT", root)
    monkeypatch.setenv("MAS_SANDBOX_ROOT", str(root / "outside"))
    with pytest.raises(ValueError):
        sandbox._root()
    target = root / "mcp_workspace/sandboxes/demo/workspace"
    monkeypatch.setenv("MAS_SANDBOX_ROOT", str(target))
    assert sandbox._root() == target


def test_requested_counts():
    assert teams.requested_count("创建三个人工智能 agents") is None
    assert teams.requested_count("创建三个智能体") == 3
    assert teams.requested_count("Create 2 agents") == 2
    for text in ("创建 9 个 agents", "创建3个agents，改为2个agents"):
        with pytest.raises(ValueError):
            teams.requested_count(text)


async def test_planner_enforces_user_count(root):
    client = FakeLLMClient([{"arguments": json.dumps(proposal())}] * 2)
    with pytest.raises(ValueError, match="exactly 3"):
        await teams.plan_team("Create 3 agents", client, "fake", root)


async def test_mock_provider_team_planning(root):
    from openai import AsyncOpenAI
    from scripts.mock_llm_server import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http_client:
        async with AsyncOpenAI(
            api_key="mock", base_url="http://test/v1", http_client=http_client
        ) as client:
            result = await teams.plan_team(
                "Create 2 agents:\n1. Lead: review evidence\n2. Developer: implement tests",
                client, "mock", root,
            )
    assert result["spec"]["agent_count"] == 2
    assert "MOCK-generated" in result["spec"]["agents"][0]["skills"][0]["instructions"]


def test_dashboard_javascript_is_parseable():
    import subprocess
    from agent_ecosystem.coordinator import DASHBOARD_HTML
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to check embedded JavaScript")
    script = DASHBOARD_HTML.split("<script>")[1].split("</script>")[0]
    assert "d.workflow.final_result.slice" not in script
    assert "escapeHtml(d.workflow.final_result)" in script
    assert "overflow:auto;max-height:70vh" in DASHBOARD_HTML
    result = subprocess.run([node, "--check"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
