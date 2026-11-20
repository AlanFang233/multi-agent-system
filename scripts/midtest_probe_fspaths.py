#!/usr/bin/env python3
"""Deterministic probe of filesystem_code MCP relative-path resolution.

Writes via relative paths and reports where the bytes actually landed.
No LLM involved. Probe files are removed afterwards.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="/bin/bash",
        args=[str(ROOT / "scripts" / "start_code_filesystem_mcp.sh")],
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for rel in ("sandboxes/_probe_b.txt",):
                try:
                    res = await s.call_tool(
                        "write_file", {"path": rel, "content": f"probe {rel}\n"})
                    err = bool(getattr(res, "isError", False))
                    text = ""
                    for c in (res.content or []):
                        text += getattr(c, "text", "")
                    print(f"write {rel!r}: isError={err} msg={text[:120]!r}")
                except Exception as exc:
                    print(f"write {rel!r}: EXC {type(exc).__name__}: {str(exc)[:150]}")
    landed = []
    for cand in (ROOT / "mcp_workspace" / "sandboxes" / "_probe_b.txt",
                 ROOT / "src" / "sandboxes" / "_probe_b.txt",
                 ROOT / "sandboxes" / "_probe_b.txt"):
        if cand.exists():
            landed.append(str(cand.relative_to(ROOT)))
            cand.unlink()
    print("landed:", landed)
    # After the start-script fix, relative "sandboxes/..." resolves under the
    # FIRST confine root (mcp_workspace) — the location sandbox tooling and
    # agents expect. Before the fix it silently landed in src/sandboxes.
    ok = landed == ["mcp_workspace/sandboxes/_probe_b.txt"]
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
