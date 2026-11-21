#!/usr/bin/env python3
"""End-to-end run: booking system -> metrics engine -> dashboard UI.

1. Create the booking DB from the real booking records.
2. Start the dashboard service (HTTP) in-process.
3. Load the dashboard, switch date ranges, confirm all four metric groups
   render and update.
4. Print a full run log. Falls back to in-process rendering if the sandbox
   forbids binding a socket.
"""
import os
import tempfile
import threading
import time
import traceback
import urllib.request

import booking_store as bs
import metrics_engine as me
import dashboard_ui as ui

GROUPS = ["1) Bookings per room", "2) Total time reserved",
          "3) Busiest days and times", "4) Cancellations"]


def log(*a):
    print(*a, flush=True)


def main():
    tmp = tempfile.mkdtemp(prefix="booking_e2e_")
    db = os.path.join(tmp, "bookings.db")
    log("[1] Seeding booking database from real booking records ->", db)
    conn = bs.init_db(db)
    n = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    log("    booking records rows:", n)
    log("    sample via booking system 'list':")
    for b in bs.list_bookings(conn)[:3]:
        log("      #%d %s %s %s" % (b["id"], b["room"], b["date"], b["status"]))

    log("\n[2] Starting dashboard service (HTTP on 127.0.0.1:8765) ...")
    httpd = None
    try:
        httpd = ui.serve(db, port=8765)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)
        log("    service up: http://127.0.0.1:8765/")
    except Exception as e:
        log("    socket bind unavailable (%s); using in-process render" % e)

    log("\n[3] Load dashboard and switch date ranges (verify 4 groups each):")
    all_ok = True
    for i, (s, e, label) in enumerate(ui.NAV):
        if httpd is not None:
            url = "http://127.0.0.1:8765/?start=%s&end=%s" % (s, e)
            try:
                html = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            except Exception as ex:
                log("    HTTP fetch failed (%s); in-process fallback" % ex)
                html = ui.render_html(me.metrics_for_db(db, s, e))
        else:
            html = ui.render_html(me.metrics_for_db(db, s, e))
        present = [g for g in GROUPS if g.replace(")", ")") in html]
        ok = len(present) == 4
        all_ok = all_ok and ok
        m = me.metrics_for_db(db, s, e)
        log("    %-22s %s..%s  groups=%d/4  confirmed=%d reserved=%dmin cancel=%.0f%%"
            % (label, s, e, len(present), m["confirmed_count"],
               m["total_reserved_minutes"], m["cancel_rate"] * 100))

    log("\n[4] Text dashboard (whole week) — rendered from the same service data:")
    log(ui.render_text(me.metrics_for_db(db, "2026-11-23", "2026-11-27"), active=1))

    if httpd is not None:
        httpd.shutdown()
    log("\nRESULT: four metric groups rendered across all ranges: %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
