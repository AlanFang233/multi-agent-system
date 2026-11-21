#!/usr/bin/env python3
"""Seed realistic usage data into the team workspace's default bookings.db,
using ONLY the delivered CLI (python main.py book/cancel). Task 1's sandbox
runs were ephemeral by design (read-only /work, tmpfs /tmp), so the workspace
has no booking records yet; Task 2's dashboard must analyze real records.
Every row here is produced by the delivered software itself — nothing is
hardcoded into the system. The full CLI transcript is archived.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WS = ROOT / "mcp_workspace" / "sandboxes" / "258c8916685642f3a261281a8ffde149"
ART = ROOT / "artifacts" / "mid_test_local"
DB = WS / "bookings.db"

BOOKINGS = [
    # date, room, start, end, title, organiser
    ("2026-11-23", "Boardroom", "09:00", "10:00", "Sprint planning", "Alice"),
    ("2026-11-23", "Boardroom", "14:00", "16:00", "Design review", "Bob"),
    ("2026-11-23", "Meeting Room A", "10:00", "11:00", "1:1 Carol/Alice", "Carol"),
    ("2026-11-23", "Huddle Room B", "09:30", "10:00", "Team standup", "Dana"),
    ("2026-11-24", "Boardroom", "10:00", "12:00", "Roadmap workshop", "Alice"),
    ("2026-11-24", "Meeting Room A", "13:00", "14:00", "Client call", "Eve"),
    ("2026-11-24", "Meeting Room A", "15:00", "16:30", "Interview panel", "Frank"),
    ("2026-11-24", "Huddle Room B", "09:30", "10:00", "Team standup", "Dana"),
    ("2026-11-25", "Boardroom", "09:00", "09:30", "Quick sync", "Bob"),
    ("2026-11-25", "Meeting Room A", "11:00", "12:00", "Demo prep", "Carol"),
    ("2026-11-25", "Huddle Room B", "14:00", "15:00", "Vendor demo", "Eve"),
    ("2026-11-26", "Boardroom", "13:00", "15:00", "Architecture review", "Frank"),
    ("2026-11-26", "Meeting Room A", "09:00", "10:30", "Onboarding training", "Alice"),
    ("2026-11-26", "Huddle Room B", "16:00", "17:00", "Retro", "Dana"),
    ("2026-11-27", "Boardroom", "10:00", "11:00", "All-hands prep", "Bob"),
    ("2026-11-27", "Meeting Room A", "14:00", "15:00", "Release review", "Carol"),
    ("2026-11-27", "Huddle Room B", "09:30", "10:00", "Team standup", "Dana"),
]
# cancel after booking: (date, room) -> cancels that day's booking for the room
CANCELS = [
    ("2026-11-24", "Meeting Room A", "13:00"),   # client call cancelled
    ("2026-11-25", "Huddle Room B", "14:00"),    # vendor demo cancelled
    ("2026-11-26", "Meeting Room A", "09:00"),   # training cancelled
]

lines = []


def run(*args):
    env = {**os.environ, "ROOMBOOK_DB": str(DB), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, "main.py", *args], cwd=str(WS),
                       capture_output=True, text=True, env=env, timeout=60)
    line = f"$ main.py {' '.join(args)}\n  rc={p.returncode} out={p.stdout.strip()[:180]}"
    lines.append(line)
    print(line, flush=True)
    return p


def main():
    if DB.exists():
        print(f"ERROR: {DB} already exists; refusing to overwrite", flush=True)
        return 1
    import re
    ok = 0
    ids = {}
    for d, room, s, e, title, org in BOOKINGS:
        p = run("book", "--date", d, "--room", room, "--start", s, "--end", e,
                "--title", title, "--organiser", org)
        ok += p.returncode == 0
        m = re.search(r"booking #(\d+) confirmed", p.stdout)
        if m:
            ids[(d, room, s)] = m.group(1)
    # cancel by id (room+date cancel is ambiguous when a room has 2+ bookings
    # that day — the CLI itself instructs to use --id then)
    for d, room, s in CANCELS:
        bid = ids.get((d, room, s))
        if bid is None:
            lines.append(f"!! no id captured for {d} {room} {s}")
            continue
        p = run("cancel", "--id", bid)
        ok += p.returncode == 0
    p = run("list")
    lines.append(f"\nDB: {DB} exists={DB.exists()} size={DB.stat().st_size if DB.exists() else 0}")
    (ART / "task1_seed_log.txt").write_text("\n".join(lines), encoding="utf-8")
    total = len(BOOKINGS) + len(CANCELS)
    print(f"\nSEED: {ok}/{total} CLI calls rc=0; db={DB}", flush=True)
    return 0 if ok == total else 2


if __name__ == "__main__":
    sys.exit(main())
