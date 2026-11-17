"""Phase 2 audit: encoding / mojibake / line-ending / path sanity checker."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "src/agent_ecosystem/events.py",
    "src/agent_ecosystem/coordinator.py",
    "src/agent_ecosystem/a2a_server.py",
    "src/agent_ecosystem/tool_agent.py",
    "src/agent_ecosystem/a2a_delegate.py",
    "config/orchestration.yaml",
    "tests/orchestration_support.py",
    "tests/test_plan_validation.py",
    "tests/test_node_state_machine.py",
    "tests/test_capability_assignment.py",
    "tests/test_planner_llm.py",
    "tests/test_dag_scheduler.py",
    "tests/test_workflow_service.py",
    "tests/test_coordinator_api.py",
    "tests/test_a2a_workflow_metadata.py",
]
TARGETS += [
    str(p.relative_to(ROOT))
    for p in (ROOT / "src/agent_ecosystem/orchestration").glob("*.py")
]

MOJIBAKE_MARKERS = ["锟", "纇", "鈥", "鈭", "鈩", "\ufffd", "Ã©", "â€"]
BAD_PATH_MARKERS = ["/home/alan_fang", "C:\\\\", "D:\\\\", "C:/", "D:/"]

problems = []
checked = 0
for rel in TARGETS:
    p = ROOT / rel
    if not p.exists():
        problems.append(f"MISSING: {rel}")
        continue
    checked += 1
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append(f"BOM: {rel}")
    if b"\x00" in raw:
        problems.append(f"NUL bytes: {rel}")
    if b"\r\n" in raw:
        problems.append(f"CRLF line endings: {rel}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"NOT valid UTF-8: {rel}: {exc}")
        continue
    for marker in MOJIBAKE_MARKERS:
        if marker in text:
            problems.append(f"MOJIBAKE {marker!r}: {rel}")
    if rel.endswith((".py", ".yaml")) and "test" not in rel:
        for marker in BAD_PATH_MARKERS:
            if marker in text:
                problems.append(f"BAD PATH {marker!r}: {rel}")

# Chinese content must decode to real terms (proves UTF-8 integrity end to end)
svc = (ROOT / "src/agent_ecosystem/orchestration/service.py").read_text(encoding="utf-8")
for term in ("综合节点", "工作流", "低信任证据", "工具审计记录"):
    if term not in svc:
        problems.append(f"UTF-8 CONTENT CHECK failed for {term!r} in service.py")

print(f"checked {checked} files")
if problems:
    print("PROBLEMS:")
    for x in problems:
        print(" -", x)
    sys.exit(1)
print("ENCODING_AUDIT: PASS (utf-8 valid, no BOM/CRLF/NUL/mojibake, no foreign paths)")
