"""Phase 0: real Docker sandbox execution + isolation checks (no MCP transport)."""
import asyncio
import sys
from pathlib import Path

from agent_ecosystem import sandbox_mcp_server as sb


async def main() -> int:
    print("PROJECT_ROOT:", sb.PROJECT_ROOT)
    print("DOCKER_IMAGE:", sb.DOCKER_IMAGE)
    created = sb.create()
    sid = created["sandbox_id"]
    ws = Path(created["workspace"])
    print("sandbox_id:", sid)
    print("workspace:", ws)
    (ws / "main.py").write_text(
        "import sys, socket, os\n"
        "print('HELLO_FROM_SANDBOX', sys.version.split()[0], 'uid=' + str(os.getuid()))\n"
        "print('sum_demo', sum([2.5, 2.0, 1.6, 1.2, 0.9]))\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3); print('NETWORK_UP')\n"
        "except Exception as e:\n"
        "    print('NETWORK_BLOCKED', type(e).__name__)\n",
        encoding="utf-8",
    )
    res = await sb.run_python(sid, "main.py", 30)
    print("--- run_python ---")
    print("success=%s exit_code=%s timed_out=%s" % (res["success"], res["exit_code"], res["timed_out"]))
    print("STDOUT:\n" + res["stdout"].strip())
    if res["stderr"].strip():
        print("STDERR:\n" + res["stderr"].strip()[:500])

    ok = res["success"] and "HELLO_FROM_SANDBOX" in res["stdout"] and "NETWORK_BLOCKED" in res["stdout"]

    # Path traversal must be rejected.
    try:
        sb._resolve_target(ws, "../escape.py")
        print("TRAVERSAL: FAIL (not rejected)")
        ok = False
    except ValueError:
        print("TRAVERSAL_BLOCKED: ok")

    # Non-Python file must be rejected.
    (ws / "notes.txt").write_text("x", encoding="utf-8")
    try:
        sb._resolve_target(ws, "notes.txt")
        print("NONPYTHON: FAIL (not rejected)")
        ok = False
    except ValueError:
        print("NONPYTHON_BLOCKED: ok")

    # run_pytest inside sandbox.
    (ws / "test_demo.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    pt = await sb.run_pytest(sid, "test_demo.py", 60)
    print("--- run_pytest --- success=%s exit=%s" % (pt["success"], pt["exit_code"]))
    print(pt["stdout"].strip()[-300:])
    ok = ok and pt["success"]

    print("SANDBOX_CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
