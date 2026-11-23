import json
import shutil
import subprocess

import httpx
import pytest
import yaml

from agent_ecosystem.coordinator import create_app
from agent_ecosystem.orchestration import team_builder as teams
from agent_ecosystem.orchestration.assignment import CapabilityAssigner
from agent_ecosystem.orchestration.coordinator_skills import CoordinatorSkills
from agent_ecosystem.orchestration.errors import TaskAssignmentError
from agent_ecosystem.orchestration.group_service import GroupAgent
from agent_ecosystem.orchestration.service import WorkflowService, build_default_service
from agent_ecosystem.tool_registry import ToolRegistry
from orchestration_support import FakeLLMClient, FakePlanner


@pytest.fixture
def coordinator_service(tmp_path):
    return WorkflowService(planner=FakePlanner([]),
                           config={"coordinator_only": True,
                                   "memory_database": str(tmp_path / "memory.sqlite")})


def test_empty_control_plane_does_not_load_worker_registry(tmp_path):
    svc = WorkflowService(
        planner=FakePlanner([]),
        config={"coordinator_only": True, "agent_registry": str(tmp_path / "missing.yaml")},
    )
    assert svc.agent_urls == {}
    assert svc.assigner.cards == []
    with pytest.raises(TaskAssignmentError, match="create and activate"):
        svc.assigner.assign(["evidence_synthesis"])
    with pytest.raises(ValueError):
        CapabilityAssigner([])


async def test_coordinator_api_reports_only_control_plane(coordinator_service):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(coordinator_service)),
        base_url="http://test",
    ) as client:
        assert (await client.get("/agents")).json() == {"coordinator": "http://test"}
        discovery = (await client.get("/agents/discover")).json()
        assert set(discovery) == {"coordinator"}
        assert discovery["coordinator"]["protocol"] == "REST"
        r = await client.post("/workflows", json={"goal": "Do work"})
        assert r.status_code == 409
        assert not coordinator_service.store.list_workflows()
        skills = (await client.get("/coordinator/skills")).json()
        assert {s["display_name"] for s in skills["skills"]} == {"Write_Skills", "Register Tools"}
        templates = (await client.get("/coordinator/templates")).json()
        assert "register-tools/agent-role-template.md" in templates


async def test_agents_endpoints_survive_existing_groups(tmp_path):
    """Regression: mounted_agents subscripted Group dataclasses (group["group_id"])
    and returned 500 on /agents and /agents/discover whenever any group existed
    in the SQLite store."""
    from agent_ecosystem.orchestration.group_service import GroupService
    from agent_ecosystem.orchestration.sqlite_store import SQLiteWorkflowStore
    store = SQLiteWorkflowStore(tmp_path / "orch.sqlite")
    svc = WorkflowService(
        planner=FakePlanner([]), store=store,
        config={"coordinator_only": True,
                "memory_database": str(tmp_path / "memory.sqlite")},
    )
    gs = GroupService(str(store.path))
    gs.create_group("team-dead", "Dead Team", [
        {"agent_id": "a", "role_name": "A", "role_prompt": "p",
         "capabilities": ["web_search"], "tool_profile": "research"}])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(svc)), base_url="http://test",
    ) as client:
        r = await client.get("/agents")
        assert r.status_code == 200, r.text
        assert r.json() == {"coordinator": "http://test"}  # group never activated
        r2 = await client.get("/agents/discover")
        assert r2.status_code == 200, r2.text


async def test_coordinator_skills_are_loaded_into_planning_context():
    guidance = await CoordinatorSkills(teams.ROOT).context()
    example = json.loads(guidance["templates"]["write-skills/team-spec-example.json"])
    teams.validate_policy(teams.TeamSpec.model_validate(example))
    client = FakeLLMClient([{"arguments": json.dumps(example)}])
    # No runtime artifact is needed for this context assertion.
    with pytest.MonkeyPatch.context() as monkeypatch:
        def stop_before_write(*args):
            raise RuntimeError("context checked")
        monkeypatch.setattr(teams, "runtime_dir", stop_before_write)
        with pytest.raises(RuntimeError, match="context checked"):
            await teams.plan_team("Create 1 agents: researcher", client, "mock")
    system = client.calls[0]["messages"][0]["content"]
    assert "# Write_Skills" in system
    assert "# Register Tools" in system
    assert "Agent Skill Template" in system
    assert "tool-registration-template.yaml" in system


def test_skill_frontmatter_and_project_location():
    for name in ("write-skills", "register-tools"):
        file = teams.ROOT / ".trae/skills" / name / "SKILL.md"
        raw = file.read_text()
        meta = yaml.safe_load(raw.split("---", 2)[1])
        assert meta["name"] == file.parent.name
        assert "Use when" in meta["description"]
        assert not any(s in meta["description"] for s in (": ", "<", ">", "TODO"))
        assert f"name: {name}\n" in raw
        assert "description: " + meta["description"] in raw
        assert len(raw.splitlines()) < 500


def test_template_paths_cannot_escape(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copytree(teams.ROOT / ".trae", tmp_path / ".trae")
    config = yaml.safe_load((teams.ROOT / "config/coordinator_skills.yaml").read_text())
    config["templates"]["write-skills"] = ["../../outside"]
    (tmp_path / "config/coordinator_skills.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="simple filenames"):
        CoordinatorSkills(tmp_path)


def test_capabilities_are_independent_of_base_agents(tmp_path):
    (tmp_path / "config").mkdir()
    for name in ("capability_catalog.yaml", "mcp_registry.yaml"):
        shutil.copyfile(teams.ROOT / "config" / name, tmp_path / "config" / name)
    assert not (tmp_path / "config/agent_registry.yaml").exists()
    assert "evidence_synthesis" in teams.catalog(tmp_path)["capabilities"]


def test_default_service_supports_zero_workers(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "coordinator_only": True, "agent_registry": str(tmp_path / "missing.yaml"),
        "events_file": str(tmp_path / "events.jsonl"),
        "orchestration_database": str(tmp_path / "orchestration.sqlite"),
    }))
    monkeypatch.delenv("MAS_COORDINATOR_ONLY", raising=False)
    svc = build_default_service(config_path=config, llm_client=FakeLLMClient([]), model="mock")
    assert not svc.agent_urls


async def test_calculator_is_lazy_in_team_bundle(tmp_path):
    (tmp_path / "config").mkdir()
    for name in ("capability_catalog.yaml", "mcp_registry.yaml", "agent_registry.yaml",
                 "coordinator_skills.yaml"):
        shutil.copyfile(teams.ROOT / "config" / name, tmp_path / "config" / name)
    shutil.copytree(teams.ROOT / ".trae", tmp_path / ".trae")
    agent = GroupAgent("analyst", "Analyst", "Calculate",
                       tool_profile="analysis", mcp_tools=["calculator:add"])
    folder = teams.write_bundle("lazy", [agent], 8100, tmp_path)
    config = yaml.safe_load((folder / "mcp_registry.yaml").read_text())
    assert config["servers"]["calculator"]["transport"] == "stdio"
    assert "--stdio" in config["servers"]["calculator"]["args"]
    assert "url" not in config["servers"]["calculator"]
    registry = ToolRegistry(folder / "mcp_registry.yaml")
    assert await registry.refresh() == {}
    result = await registry.call("lazy:analyst", "calculator:add", {"a": 1, "b": 2})
    assert result["is_error"] is False
    assert "3" in json.dumps(result)


def test_startup_entry_points_have_valid_shell_syntax():
    for name in ("start_coordinator.sh", "phase3_start.sh", "start_agents.sh",
                 "midtest_start_real.sh"):
        result = subprocess.run(["bash", "-n", str(teams.ROOT / "scripts" / name)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
