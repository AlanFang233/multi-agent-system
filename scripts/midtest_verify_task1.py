#!/usr/bin/env python3
"""Operator-side INDEPENDENT verification of the Task 1 deliverable (A-G).

Runs the team's delivered software in a copied workspace with its own database
(the team's original artifacts are untouched), captures every stdout, and
asserts each acceptance point from the Mid_Test charter:
  A availability view, B create booking, C conflict blocked (and touching
  intervals allowed), D cancel, E availability updates after cancel,
  F persistence across process restart, G understandable usage docs.
Also re-runs the team's own demo.py / checks.py / boundary_test.py.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "mcp_workspace" / "sandboxes" / "258c8916685642f3a261281a8ffde149"
VER = ROOT / "artifacts" / "mid_test_local" / "task1_verification"
DATE = "2026-11-23"  # Monday (future workday)

RESULTS = []
OUTPUT = []


def run(*args, db=None, cwd=None, expect_rc=None):
    env = {"ROOMBOOK_DB": str(db)} if db else {}
    import os
    full_env = {**os.environ, **env, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, *args], cwd=str(cwd or VER),
                       capture_output=True, text=True, env=full_env, timeout=120)
    cmd = f"$ ROOMBOOK_DB={db or '(default)'} python {' '.join(args)}"
    OUTPUT.append(f"\n{cmd}\n[rc={p.returncode}]\n--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}")
    print(cmd, f"-> rc={p.returncode}", flush=True)
    return p


def check(cid, name, ok, evidence=""):
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok), "evidence": str(evidence)[:400]})
    print(f"[VERIFY] {'PASS' if ok else 'FAIL'} {cid}: {name} | {evidence}", flush=True)


def main():
    if VER.exists():
        shutil.rmtree(VER)
    shutil.copytree(SRC, VER)
    VER.mkdir(parents=True, exist_ok=True)
    db = VER / "verify_bookings.db"
    print(f"workspace copied to {VER}; db={db}", flush=True)

    # A — availability view
    p = run("main.py", "show", "--date", DATE, db=db)
    check("A", "Availability view lists rooms and slots",
          p.returncode == 0 and "Boardroom" in p.stdout and "free" in p.stdout
          and "18 of 18 slots free" in p.stdout,
          "Boardroom 18/18 free on fresh DB" if p.returncode == 0 else p.stderr[:200])

    # B — create a booking
    p = run("main.py", "book", "--date", DATE, "--room", "Boardroom",
            "--start", "10:00", "--end", "11:00",
            "--title", "Operator verification booking", "--organiser", "Operator", db=db)
    b_ok = p.returncode == 0 and ("confirmed" in p.stdout.lower() or "success" in p.stdout.lower())
    check("B", "Create booking succeeds", b_ok, p.stdout.strip().splitlines()[-1][:200] if p.stdout else p.stderr[:200])

    # C — overlapping booking must be blocked
    p = run("main.py", "book", "--date", DATE, "--room", "Boardroom",
            "--start", "10:30", "--end", "11:30",
            "--title", "Conflicting booking", "--organiser", "Mallory", db=db)
    c_blocked = p.returncode != 0 or "overlap" in (p.stdout + p.stderr).lower() \
        or "conflict" in (p.stdout + p.stderr).lower() or "error" in (p.stdout + p.stderr).lower()
    c_no_success = "confirmed" not in p.stdout.lower() and "success" not in p.stdout.lower()
    check("C", "Double booking (overlap) blocked", c_blocked and c_no_success,
          ((p.stdout + p.stderr).strip().splitlines() or [""])[-1][:200])

    # C2 — back-to-back (touching) booking must be allowed (half-open intervals)
    p = run("main.py", "book", "--date", DATE, "--room", "Boardroom",
            "--start", "11:00", "--end", "11:30",
            "--title", "Back-to-back booking", "--organiser", "Operator", db=db)
    check("C2", "Touching interval (11:00 after 10:00-11:00) allowed",
          p.returncode == 0 and "confirmed" in p.stdout.lower(),
          p.stdout.strip().splitlines()[-1][:200] if p.stdout else "")

    # D — cancel the first booking (id 1)
    p = run("main.py", "cancel", "--id", "1", db=db)
    check("D", "Cancel booking succeeds",
          p.returncode == 0 and "cancel" in p.stdout.lower(),
          p.stdout.strip().splitlines()[-1][:200] if p.stdout else p.stderr[:200])

    # E — slot freed after cancellation
    p = run("main.py", "show", "--date", DATE, "--room", "Boardroom", db=db)
    e_free = "10:00-10:30" in p.stdout
    freed = False
    for line in p.stdout.splitlines():
        if "10:00-10:30" in line and "free" in line:
            freed = True
    p2 = run("main.py", "book", "--date", DATE, "--room", "Boardroom",
             "--start", "10:00", "--end", "10:30",
             "--title", "Rebook after cancel", "--organiser", "Operator", db=db)
    check("E", "Cancelled slot immediately re-bookable",
          freed and p2.returncode == 0 and "confirmed" in p2.stdout.lower(),
          f"slot_free={freed}; rebook rc={p2.returncode}")

    # F — persistence across process restart (fresh process reads same DB)
    p = run("main.py", "list", "--date", DATE, db=db)
    n_confirmed = p.stdout.count("confirmed")
    check("F", "Data persists across process restarts (SQLite)",
          p.returncode == 0 and n_confirmed >= 2 and "cancel" in p.stdout.lower(),
          f"list shows {n_confirmed} confirmed + cancelled rows in fresh process")

    # G — usage docs
    readme = (VER / "README.md")
    txt = readme.read_text(encoding="utf-8") if readme.exists() else ""
    check("G", "Understandable usage documentation",
          readme.exists() and "python3 main.py" in txt and "book" in txt and "cancel" in txt,
          f"README.md {len(txt)} chars with start/book/cancel instructions")

    # Team's own test scripts, re-run by the operator
    p = run("demo.py", db=VER / "demo_rerun.db")
    check("T1", "Team demo.py re-run by operator exits 0",
          p.returncode == 0, f"rc={p.returncode}")
    p = run("checks.py", db=VER / "checks_rerun.db")
    check("T2", "Team checks.py (restart persistence) re-run passes",
          p.returncode == 0 and "ALL CHECKS PASSED" in p.stdout, f"rc={p.returncode}")
    p = run("boundary_test.py", db=VER / "boundary_rerun.db")
    check("T3", "Team boundary_test.py re-run passes",
          p.returncode == 0, f"rc={p.returncode}, tail={p.stdout.strip().splitlines()[-1][:120] if p.stdout else ''}")

    (VER / "verification_output.txt").write_text("\n".join(OUTPUT), encoding="utf-8")
    (VER / "verification_summary.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in RESULTS if r["ok"])
    print(f"\n[VERIFY] ==== {n_ok}/{len(RESULTS)} PASS ====", flush=True)
    for r in RESULTS:
        if not r["ok"]:
            print(f"[VERIFY] FAILED: {r['id']} {r['name']} :: {r['evidence']}", flush=True)
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
