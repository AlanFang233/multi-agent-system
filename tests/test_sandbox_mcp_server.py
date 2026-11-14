"""Offline regression tests for Sandbox MCP (no Docker daemon required)."""
import asyncio
from pathlib import Path

import pytest

from agent_ecosystem import sandbox_mcp_server as sandbox


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sandbox, "WORKSPACE_ROOT", tmp_path / "mcp_workspace" / "sandboxes")
    return tmp_path


def test_create_and_validate_sandbox(isolated_root):
    created = sandbox.create()
    assert len(created["sandbox_id"]) == 32
    root = Path(created["workspace"])
    assert root.is_dir()
    assert sandbox._sandbox_dir(created["sandbox_id"]) == root


def test_reject_unknown_sandbox(isolated_root):
    with pytest.raises(ValueError):
        sandbox._sandbox_dir("../../home")
    with pytest.raises(ValueError):
        sandbox._sandbox_dir("a" * 32)


def test_script_must_be_inside_sandbox(isolated_root):
    root = Path(sandbox.create()["workspace"])
    (root / "main.py").write_text("print('OK')", encoding="utf-8")
    assert sandbox._resolve_target(root, "main.py") == "/work/main.py"
    with pytest.raises(ValueError):
        sandbox._resolve_target(root, "../main.py")
    with pytest.raises(ValueError):
        sandbox._resolve_target(root, "/etc/passwd")
    with pytest.raises(ValueError):
        sandbox._resolve_target(root, "not_created.py")


def test_symlink_escape_rejected(isolated_root):
    root = Path(sandbox.create()["workspace"])
    secret = isolated_root / "secret.py"
    secret.write_text("SECRET", encoding="utf-8")
    (root / "secret.py").symlink_to(secret)
    with pytest.raises(ValueError):
        sandbox._resolve_target(root, "secret.py")


def test_pytest_directory_and_non_python_rejection(isolated_root):
    root = Path(sandbox.create()["workspace"])
    (root / "test_demo.py").write_text("def test_true(): assert True", encoding="utf-8")
    (root / "notes.txt").write_text("no", encoding="utf-8")
    assert sandbox._resolve_target(root, ".", allow_directory=True) == "/work/."
    with pytest.raises(ValueError):
        sandbox._resolve_target(root, "notes.txt", allow_directory=True)


def test_docker_command_is_fixed_and_hardened(isolated_root):
    root = Path(sandbox.create()["workspace"])
    args = sandbox._docker_args("ag-sandbox-demo", root, ["python", "-B", "/work/main.py"])
    assert args[:2] == ["docker", "run"]
    assert "--network" in args and args[args.index("--network") + 1] == "none"
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert "--user" in args
    assert "--pull=never" in args
    assert "--privileged" not in args
    assert "--mount" in args and args[args.index("--mount") + 1].endswith(",readonly")
    assert "agent-sandbox-py:3.12-v1" in args


def test_bounded_output():
    async def check():
        stream = asyncio.StreamReader()
        stream.feed_data(b"x" * (sandbox.MAX_OUTPUT_BYTES + 20))
        stream.feed_eof()
        output, truncated = await sandbox._limited_read(stream)
        assert truncated is True
        assert len(output) == sandbox.MAX_OUTPUT_BYTES
    asyncio.run(check())
