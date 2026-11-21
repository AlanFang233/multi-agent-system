#!/usr/bin/env python3
"""End-to-end manual run of the three workflows + error handling.

Drives main.py exactly like a normal user would (separate CLI invocations),
against a throwaway demo database, and prints a transcript.
"""
import datetime
import io
import os
import shutil
import sys
import tempfile
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402


def next_weekday():
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() not in main.WORKDAYS:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def run(args):
    print("\n$ python main.py %s" % " ".join(args))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main.main(args)
    out = buf.getvalue().rstrip("\n")
    print(out)
    if code:
        print("[exit code %d]" % code)
    return code, out


def main_demo():
    global DEMO_DATE
    DEMO_DATE = next_weekday()
    tmp = tempfile.mkdtemp(prefix="roombook_demo_")
    os.environ["ROOMBOOK_DB"] = os.path.join(tmp, "bookings.db")
    main.DB_PATH = os.environ["ROOMBOOK_DB"]

    print("Demo date (next workday): %s" % DEMO_DATE)
    print("Database: %s" % main.DB_PATH)

    print("\n\n===== WORKFLOW 1: CHECK AVAILABILITY (all rooms) =====")
    run(["show", "--date", DEMO_DATE])

    print("\n\n===== WORKFLOW 2: BOOK ROOM =====")
    run(["book", "--date", DEMO_DATE, "--room", "Boardroom",
         "--start", "10:00", "--end", "11:00",
         "--title", "Sprint review", "--organiser", "Alice"])

    print("\n-- Second, non-overlapping booking for the same room succeeds:")
    run(["book", "--date", DEMO_DATE, "--room", "Boardroom",
         "--start", "11:30", "--end", "12:00",
         "--title", "1:1", "--organiser", "Bob"])

    print("\n-- Overlapping booking is REJECTED with a clear error:")
    run(["book", "--date", DEMO_DATE, "--room", "Boardroom",
         "--start", "10:30", "--end", "11:30",
         "--title", "Should fail", "--organiser", "Carol"])

    print("\n-- Invalid input cases:")
    run(["book", "--date", DEMO_DATE, "--room", "Ghost Room",
         "--start", "10:00", "--end", "11:00"])
    run(["book", "--date", DEMO_DATE, "--room", "Boardroom",
         "--start", "11:00", "--end", "10:00"])
    run(["book", "--date", "2020-01-06", "--room", "Boardroom",
         "--start", "10:00", "--end", "11:00"])
    run(["book", "--date", DEMO_DATE, "--room", "Boardroom",
         "--start", "08:00", "--end", "09:30"])
    run(["book", "--date", DEMO_DATE, "--room", "Boardroom",
         "--start", "09:00", "--end", "16:00"])

    print("\n\n-- Availability now shows the taken slots:")
    run(["show", "--date", DEMO_DATE, "--room", "Boardroom"])

    print("\n\n===== WORKFLOW 3: CANCEL BOOKING =====")
    code, out = run(["cancel", "--id", "1"])
    # booking #1 is the 10:00-11:00 Sprint review

    print("\n-- The cancelled slot is INSTANTLY free again (must not show as taken):")
    code, out = run(["show", "--date", DEMO_DATE, "--room", "Boardroom"])
    assert "10:00-10:30" in out and "TAKEN" not in out.split("10:00-10:30")[1].split("\n")[0], \
        "cancelled slot still shown as taken!"
    assert "10:00-10:30  free" in out and "10:30-11:00  free" in out

    print("\n-- Cancelling again reports it is already cancelled:")
    run(["cancel", "--id", "1"])

    print("\n-- Cancel by room+date (lookup by alternative key):")
    run(["cancel", "--room", "Boardroom", "--date", DEMO_DATE])

    print("\n-- Cancel an unknown booking id:")
    run(["cancel", "--id", "999"])

    print("\n\n-- Final booking list (cancelled records are kept, not taken):")
    run(["list"])

    print("\n\nDEMO COMPLETE")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main_demo()
