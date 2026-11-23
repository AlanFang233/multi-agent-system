#!/usr/bin/env python3
"""Probe the sandbox timeout-clamp fix (Mid_Test bug #10) via direct MCP calls.

1. run_python with timeout_seconds=120 (previously an opaque hard failure)
   must execute and report the clamp in timeout_note.
2. run_pytest on the dev agent's real test file must return pytest output
   (not "Error executing tool").
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = "e0ffc0fea8474c4293c3ed1de0b0e724"


def _text(res) -> str:
    out = ""
    for c in (res.content or []):
        out += getattr(c, "text", "")
    return out


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "python"),
        args=["-m", "agent_ecosystem.sandbox_mcp_server"],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("run_python", {
                "sandbox_id": SANDBOX, "script": "_hello.py",
                "timeout_seconds": 120})
            txt = _text(res)
            print("run_python(120s) isError:", bool(getattr(res, "isError", False)))
            print("  body:", txt[:400])
            ok1 = (not getattr(res, "isError", False)
                   and "clamped to 90s" in txt and "HELLO_OK" in txt)
            res2 = await s.call_tool("run_pytest", {
                "sandbox_id": SANDBOX, "target": "test_dashboard_metrics.py",
                "timeout_seconds": 120})
            txt2 = _text(res2)
            print("run_pytest isError:", bool(getattr(res2, "isError", False)))
            print("  body:", txt2[:500])
            ok2 = ("Error executing tool" not in txt2
                   and ("passed" in txt2 or "failed" in txt2))
    print("PROBE:", "PASS" if (ok1 and ok2) else f"FAIL (run_python={ok1} run_pytest={ok2})")
    return 0 if (ok1 and ok2) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
