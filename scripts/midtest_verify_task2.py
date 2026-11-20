#!/usr/bin/env python3
"""Operator-side INDEPENDENT verification of Task 2 (dashboard).

1. Computes ground-truth metrics with MY OWN raw SQL over the real bookings.db
   (no team code involved), then compares against the team's
   analytics_api.aggregate_analytics() output.
2. Prepares artifacts/mid_test_local/dashboard_live/ — a copy of the team's
   dashboard with live-mode injection (team deliverables untouched).
3. Archives results to artifacts/mid_test_local/task2_verification.json.
"""
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WS = ROOT / "mcp_workspace" / "sandboxes" / "258c8916685642f3a261281a8ffde149"
ART = ROOT / "artifacts" / "mid_test_local"
LIVE = ART / "dashboard_live"
D0, D1 = "2026-11-23", "2026-11-27"

results = []


def check(cid, name, ok, evidence=""):
    results.append({"id": cid, "name": name, "ok": bool(ok), "evidence": str(evidence)[:400]})
    print(f"[VERIFY2] {'PASS' if ok else 'FAIL'} {cid}: {name} | {evidence}", flush=True)


def ground_truth():
    """My own SQL — deliberately not using any team helper."""
    conn = sqlite3.connect(str(WS / "bookings.db"))
    conn.row_factory = sqlite3.Row
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(bookings)")]
    print("bookings columns:", cols, flush=True)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM bookings WHERE date >= ? AND date <= ?", (D0, D1))]
    rooms = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM rooms")}
    conn.close()

    def minutes(r):
        return r["end_min"] - r["start_min"]

    conf = [r for r in rows if r["status"] == "confirmed"]
    canc = [r for r in rows if r["status"] == "cancelled"]
    truth = {
        "confirmed": len(conf),
        "cancelled": len(canc),
        "total_minutes": sum(minutes(r) for r in conf),
        "cancel_rate": len(canc) / len(rows) if rows else 0.0,
        "per_room": {},
        "per_day": {},
        "per_slot": {},
    }
    for r in conf:
        rn = rooms[r["room_id"]]
        pr = truth["per_room"].setdefault(rn, {"n": 0, "minutes": 0})
        pr["n"] += 1
        pr["minutes"] += minutes(r)
        d = truth["per_day"].setdefault(r["date"], {"n": 0, "minutes": 0})
        d["n"] += 1
        d["minutes"] += minutes(r)
        for slot in range(r["start_min"], r["end_min"], 30):
            truth["per_slot"][slot] = truth["per_slot"].get(slot, 0) + 1
    return truth


def main():
    os.chdir(str(WS))
    sys.path.insert(0, str(WS))
    import analytics_api as A

    truth = ground_truth()
    conn = A.open_store()
    rooms, bookings = A.load_rooms_and_bookings(conn)
    payload = A.aggregate_analytics(rooms, bookings, D0, D1)
    conn.close()

    t = payload["totals"]
    check("D1", "Confirmed count matches independent SQL",
          t["bookings_confirmed"] == truth["confirmed"],
          f"api={t['bookings_confirmed']} sql={truth['confirmed']}")
    check("D2", "Cancelled count matches independent SQL",
          t["bookings_cancelled"] == truth["cancelled"],
          f"api={t['bookings_cancelled']} sql={truth['cancelled']}")
    check("D3", "Total reserved minutes matches independent SQL",
          t["reserved_minutes"] == truth["total_minutes"],
          f"api={t['reserved_minutes']} sql={truth['total_minutes']}")
    check("D4", "Cancellation rate matches independent SQL",
          abs(t["cancellation_rate"] - truth["cancel_rate"]) < 1e-9,
          f"api={t['cancellation_rate']:.4f} sql={truth['cancel_rate']:.4f}")
    ok_rooms = all(
        r["bookings_confirmed"] == truth["per_room"][r["room_name"]]["n"]
        and r["reserved_minutes"] == truth["per_room"][r["room_name"]]["minutes"]
        for r in payload["rooms"] if r["room_name"] in truth["per_room"])
    check("D5", "Per-room bookings/hours match independent SQL", ok_rooms,
          json.dumps({r["room_name"]: [r["bookings_confirmed"], r["reserved_hours"]]
                      for r in payload["rooms"]}, ensure_ascii=False))
    api_top_day = payload["rankings"]["busiest_days"][0]
    sql_top_day = max(truth["per_day"].items(), key=lambda kv: (kv[1]["n"], kv[1]["minutes"]))
    check("D6", "Busiest day matches independent SQL",
          api_top_day["date"] == sql_top_day[0],
          f"api={api_top_day['date']} sql={sql_top_day[0]}")
    api_top_slot = payload["rankings"]["busiest_slots"][0]
    sql_top_slot = max(truth["per_slot"].items(), key=lambda kv: kv[1])
    check("D7", "Busiest slot matches independent SQL",
          api_top_slot["start_min"] == sql_top_slot[0],
          f"api={api_top_slot['slot_label']} sql_start_min={sql_top_slot[0]}")
    # date-range selectivity: a single-day range must return only that day
    conn = A.open_store()
    rooms2, b2 = A.load_rooms_and_bookings(conn)
    day = A.aggregate_analytics(rooms2, b2, "2026-11-25", "2026-11-25")
    conn.close()
    sql_25 = truth["per_day"].get("2026-11-25", {"n": 0})
    check("D8", "Date-range filter works (single day 11-25)",
          day["totals"]["bookings_confirmed"] == sql_25["n"],
          f"api={day['totals']['bookings_confirmed']} sql={sql_25['n']}")

    # prepare live-mode browser copy (team files untouched)
    if LIVE.exists():
        shutil.rmtree(LIVE)
    shutil.copytree(WS / "dashboard", LIVE)
    html = (LIVE / "index.html").read_text(encoding="utf-8")
    inject = ('<script>/* operator harness: switch to live API */\n'
              'BookingDashboard.CONFIG.mode="live";\n'
              'BookingDashboard.CONFIG.dataUrl="/api/dashboard";</script>\n</body>')
    assert "</body>" in html
    (LIVE / "index.html").write_text(html.replace("</body>", inject), encoding="utf-8")
    check("D9", "Live-mode dashboard copy prepared (team files untouched)",
          (LIVE / "index.html").exists() and (WS / "dashboard" / "index.html").read_text(
              encoding="utf-8") == html)

    (ART / "task2_verification.json").write_text(json.dumps(
        {"ground_truth_sql": {"confirmed": truth["confirmed"],
                              "cancelled": truth["cancelled"],
                              "total_minutes": truth["total_minutes"],
                              "cancel_rate": truth["cancel_rate"],
                              "per_room": truth["per_room"],
                              "per_day": truth["per_day"],
                              "busiest_slot_start_min": sql_top_slot[0]},
         "api_totals": t, "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\n[VERIFY2] ==== {n_ok}/{len(results)} PASS ====", flush=True)
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
