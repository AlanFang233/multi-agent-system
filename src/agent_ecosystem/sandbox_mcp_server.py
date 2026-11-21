"""Local Docker-backed sandbox MCP for a trusted, single-user Coding Agent.

Exposes only three tools: create, run_python and run_pytest.
No arbitrary shell, Docker flags, images or host paths are accepted from the agent.

Run: python -m agent_ecosystem.sandbox_mcp_server
Requires: MCP SDK v2, Docker Engine, prebuilt agent-sandbox-py:3.12-v1 image.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid

from mcp.server import MCPServer

mcp = MCPServer("Coding Docker Sandbox")

# Source file: <project>/src/agent_ecosystem/sandbox_mcp_server.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT / "mcp_workspace" / "sandboxes"
DOCKER_IMAGE = "agent-sandbox-py:3.12-v1"
SANDBOX_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
MAX_SECONDS = 90
MAX_OUTPUT_BYTES = 16_384  # Per output stream. Prevent context/memory flooding.


def _root() -> Path:
    """Return the permitted workspace root, rejecting relocated/symlinked roots."""
    configured = os.environ.get("MAS_SANDBOX_ROOT")
    if configured:
        path = Path(configured)
        base = PROJECT_ROOT / "mcp_workspace" / "sandboxes"
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("Group sandbox root must be absolute and not symlinked")
        if not path.is_relative_to(base) or path == base:
            raise ValueError("Group sandbox root must be inside sandboxes")
        path.mkdir(parents=True, exist_ok=True)
        return path
    workspace = PROJECT_ROOT / "mcp_workspace"
    if workspace.is_symlink() or WORKSPACE_ROOT.is_symlink():
        raise ValueError("Workspace roots must not be symlinks")
    workspace.mkdir(parents=True, exist_ok=True)
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    root = WORKSPACE_ROOT.resolve(strict=True)
    if root.parent != workspace.resolve(strict=True):
        raise ValueError("Sandbox root escapes mcp_workspace")
    return root


def _sandbox_dir(sandbox_id: str) -> Path:
    if not SANDBOX_ID_RE.fullmatch(sandbox_id):
        raise ValueError("sandbox_id must be a 32-character ID from sandbox:create")
    base = _root()
    candidate = base / sandbox_id
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("Sandbox does not exist or is a symlink")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != base:
        raise ValueError("Sandbox escapes permitted root")
    return resolved


def _resolve_target(workdir: Path, path: str, *, allow_directory: bool = False) -> str:
    """Validate a file/directory and return its unambiguous absolute container path."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise ValueError("Invalid relative path")
    rel = PurePosixPath(path)
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError("Absolute paths and traversal are forbidden")
    target = workdir / rel.as_posix()
    if target.is_symlink():
        raise ValueError("Symlinks are not allowed")
    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Requested path does not exist") from exc
    if not resolved.is_relative_to(workdir):
        raise ValueError("Requested path escapes the sandbox")
    if resolved.is_file():
        if resolved.suffix != ".py":
            raise ValueError("Only Python .py files may be executed")
    elif not (allow_directory and resolved.is_dir()):
        raise ValueError("Target must be a Python file" + (" or directory" if allow_directory else ""))
    return "/work/" + resolved.relative_to(workdir).as_posix()


def _docker_args(name: str, workdir: Path, program: list[str]) -> list[str]:
    """Construct a fixed, non-agent-configurable Docker invocation."""
    return [
        "docker", "run", "--rm", "--pull=never", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges=true", "--pids-limit", "64",
        "--memory", "512m", "--cpus", "1", "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        "--mount", f"type=bind,src={workdir},dst=/work,readonly",
        "--workdir", "/work", "-e", "HOME=/tmp",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        DOCKER_IMAGE, *program,
    ]


async def _limited_read(stream: asyncio.StreamReader) -> tuple[str, bool]:
    fragments: list[bytes] = []
    remaining = MAX_OUTPUT_BYTES
    truncated = False
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        if len(chunk) > remaining:
            truncated = True
        if remaining > 0:
            accepted = chunk[:remaining]
            fragments.append(accepted)
            remaining -= len(accepted)
    return b"".join(fragments).decode("utf-8", errors="replace"), truncated


async def _force_remove(name: str) -> None:
    """Best-effort cleanup if the Docker client times out or is interrupted."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        pass


async def _execute_docker(workdir: Path, program: list[str], timeout_seconds: int) -> dict:
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError(
            f"timeout_seconds must be a positive integer (got {timeout_seconds!r})")
    clamped_from = None
    if timeout_seconds > MAX_SECONDS:
        # LLMs routinely request more than the safety cap; the old opaque
        # hard-fail ("Error executing tool ...") sent agents into long
        # misdiagnosis loops (Mid_Test bug #10). Clamp to the cap and surface
        # the adjustment in the result instead of failing the call.
        clamped_from = timeout_seconds
        timeout_seconds = MAX_SECONDS
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI unavailable; start Docker Desktop and enable WSL integration")

    name = "ag-sandbox-" + uuid.uuid4().hex
    cmd = _docker_args(name, workdir, program)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    stdout_task = asyncio.create_task(_limited_read(proc.stdout))
    stderr_task = asyncio.create_task(_limited_read(proc.stderr))
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        await proc.wait()
        await _force_remove(name)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await _force_remove(name)
        raise

    (stdout, out_truncated), (stderr, err_truncated) = await asyncio.gather(
        stdout_task, stderr_task
    )
    result = {
        "success": not timed_out and proc.returncode == 0,
        "exit_code": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": out_truncated,
        "stderr_truncated": err_truncated,
    }
    if clamped_from is not None:
        result["timeout_note"] = (
            f"timeout_seconds={clamped_from} exceeded the sandbox cap; "
            f"clamped to {MAX_SECONDS}s")
    return result


@mcp.tool()
def create() -> dict:
    """Create an isolated task workspace under mcp_workspace/sandboxes/."""
    base = _root()
    sandbox_id = uuid.uuid4().hex
    workdir = base / sandbox_id
    workdir.mkdir(mode=0o755, exist_ok=False)
    return {
        "sandbox_id": sandbox_id,
        "workspace": str(workdir),
        "instructions": "Use filesystem_code:write_file to create .py files in this workspace.",
    }


@mcp.tool()
async def run_python(sandbox_id: str, script: str = "main.py", timeout_seconds: int = 30) -> dict:
    """Run one existing Python file in a network-disabled, read-only Docker sandbox."""
    workdir = _sandbox_dir(sandbox_id)
    target = _resolve_target(workdir, script)
    outcome = await _execute_docker(workdir, ["python", "-B", target], timeout_seconds)
    return {"sandbox_id": sandbox_id, "script": script, **outcome}


@mcp.tool()
async def run_pytest(sandbox_id: str, target: str = ".", timeout_seconds: int = 60) -> dict:
    """Run pytest inside one sandbox, without network or writes to the workspace."""
    workdir = _sandbox_dir(sandbox_id)
    checked = _resolve_target(workdir, target, allow_directory=True)
    program = [
        "python", "-B", "-m", "pytest", "-q", "--maxfail=1", "--tb=short",
        "-p", "no:cacheprovider", "--basetemp=/tmp/pytest", checked,
    ]
    outcome = await _execute_docker(workdir, program, timeout_seconds)
    return {"sandbox_id": sandbox_id, "target": target, **outcome}


if __name__ == "__main__":
    # Stdout is reserved for JSON-RPC; never print a startup banner here.
    mcp.run(transport="stdio")
