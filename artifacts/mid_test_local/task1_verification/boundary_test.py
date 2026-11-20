#!/usr/bin/env python3
"""Boundary / edge-case suite for the no-overlap + cancellation rules.

Targets exactly the cases the repair task calls out:
  * touching intervals (back-to-back) must NOT be treated as overlapping
  * full containment (one booking wholly inside another) MUST be rejected
  * exact duplicates must be rejected
  * cancelled bookings must never block a slot; the slot must be
    immediately re-bookable and immediately shown free.
"""
import datetime, io, os, sys, tempfile, contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main

tmp = tempfile.mkdtemp(prefix="roombook_boundary_")
os.environ["ROOMBOOK_DB"] = os.path.join(tmp, "bookings.db")
main.DB_PATH = os.environ["ROOMBOOK_DB"]

d = datetime.date.today() + datetime.timedelta(days=1)
while d.weekday() not in main.WORKDAYS:
    d += datetime.timedelta(days=1)
DAY = d.isoformat()
ROOM = "Boardroom"

results = []
def run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main.main(args)
    return code, buf.getvalue()

def expect_ok(label, args):
    code, out = run(args)
    ok = (code == 0 and "SUCCESS" in out)
    results.append((ok, label, out.strip().splitlines()[-1] if out.strip() else out.strip()))
    return ok

def expect_reject(label, args, needle="Overlap"):
    code, out = run(args)
    ok = (code == 1 and needle in out)
    results.append((ok, label, out.strip().splitlines()[-1] if out.strip() else out.strip()))
    return ok

def slot_free(out, s):
    for line in out.splitlines():
        st = line.strip()
        if st.startswith("[") and s in st:
            return "free" in line
    return None

if os.path.exists(main.DB_PATH):
    os.remove(main.DB_PATH)
main.get_connection().close()

print("=== Boundary tests on %s, room %s ===" % (DAY, ROOM))

expect_ok("T1 baseline 10:00-11:00 booked",
          ["book", "--date", DAY, "--room", ROOM, "--start", "10:00", "--end", "11:00",
           "--title", "base", "--organiser", "A"])
expect_reject("T2 exact duplicate 10:00-11:00 rejected",
              ["book", "--date", DAY, "--room", ROOM, "--start", "10:00", "--end", "11:00"])

expect_ok("T3 touching after  11:00-12:00 accepted (back-to-back)",
          ["book", "--date", DAY, "--room", ROOM, "--start", "11:00", "--end", "12:00",
           "--title", "touch-after", "--organiser", "B"])
expect_ok("T4 touching before 09:30-10:00 accepted (back-to-back)",
          ["book", "--date", DAY, "--room", ROOM, "--start", "09:30", "--end", "10:00",
           "--title", "touch-before", "--organiser", "C"])

expect_reject("T5 partial overlap left 09:30-10:30 rejected",
              ["book", "--date", DAY, "--room", ROOM, "--start", "09:30", "--end", "10:30"])
expect_reject("T6 partial overlap right 10:30-11:30 rejected",
              ["book", "--date", DAY, "--room", ROOM, "--start", "10:30", "--end", "11:30"])

expect_ok("T7 large block 13:00-15:00 booked",
          ["book", "--date", DAY, "--room", ROOM, "--start", "13:00", "--end", "15:00",
           "--title", "big", "--organiser", "D"])
expect_reject("T8 contained inside (13:30-14:00) rejected",
              ["book", "--date", DAY, "--room", ROOM, "--start", "13:30", "--end", "14:00"])
expect_reject("T9 containing (12:30-15:30) rejected",
              ["book", "--date", DAY, "--room", ROOM, "--start", "12:30", "--end", "15:30"])

code, out = run(["show", "--date", DAY, "--room", ROOM])
t10 = (slot_free(out, "09:30-10:00") is False and slot_free(out, "10:00-10:30") is False
       and slot_free(out, "10:30-11:00") is False and slot_free(out, "11:00-11:30") is False
       and slot_free(out, "11:30-12:00") is False and slot_free(out, "12:00-12:30") is True
       and slot_free(out, "13:00-13:30") is False and slot_free(out, "14:30-15:00") is False
       and slot_free(out, "15:00-15:30") is True)
results.append((t10, "T10 availability marks exact booked slots taken, boundaries free", "checked"))

expect_ok("T11 cancel the 10:00-11:00 block", ["cancel", "--id", "1"])
code, out = run(["show", "--date", DAY, "--room", ROOM])
free_after = (slot_free(out, "10:00-10:30") is True and slot_free(out, "10:30-11:00") is True)
results.append((free_after, "T12 cancelled slot shown free immediately", "checked"))
expect_ok("T13 re-book the freed slot 10:00-11:00 after cancellation",
          ["book", "--date", DAY, "--room", ROOM, "--start", "10:00", "--end", "11:00",
           "--title", "rebook", "--organiser", "E"])
expect_reject("T14 overlap against the re-booked slot rejected",
              ["book", "--date", DAY, "--room", ROOM, "--start", "10:30", "--end", "11:30"])

for bid in ("1", "2", "3", "4", "5", "6", "7"):
    run(["cancel", "--id", bid])
code, out = run(["show", "--date", DAY, "--room", ROOM])
allfree = "18 of 18 slots free" in out
results.append((allfree, "T15 after cancelling all, 18 of 18 slots free", "checked"))

print()
fails = 0
for ok, label, detail in results:
    print("%s  %s" % ("PASS" if ok else "FAIL", label))
    if not ok:
        fails += 1
        print("        last: %s" % detail)
print()
print("RESULT: %d/%d checks passed" % (len(results) - fails, len(results)))
print("BOUNDARY SUITE: " + ("ALL PASSED" if fails == 0 else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
