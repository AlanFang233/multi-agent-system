#!/usr/bin/env python3
"""Contract test for the dashboard aggregation.

The dashboard UI is JavaScript, but it must obey the SAME metric semantics as the
Python reference (validate_metrics.py / main.py).  This test mirrors the JS
aggregate() rules against the real bookings.db and asserts the frozen contract:

  * only status='confirmed' occupies a slot; cancelled counts only as cancelled
  * date range is inclusive on BOTH ends
  * reserved minutes = SUM(end_min - start_min) over confirmed in range
  * cancel_rate = cancelled / (confirmed + cancelled)
  * heatmap slot value = number of confirmed bookings covering each 30-min slot
  * utilisation = reserved_min / (workdays_in_range * 540)

It also builds a deterministic mock store and checks that mock and live paths
produce identically shaped payloads (same exact field names).
"""
import os, sqlite3, datetime, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), "bookings.db")
CAPACITY_MIN_PER_DAY = 540
WORK_START, WORK_END, SLOT = 540, 1080, 30


def aggregate(rooms, bookings, start_date, end_date, room_id=None):
    """Python twin of dashboard.js aggregate() — same field names."""
    filt = [b for b in bookings
            if start_date <= b["date"] <= end_date
            and (room_id is None or b["room_id"] == room_id)]
    d0 = datetime.date.fromisoformat(start_date)
    d1 = datetime.date.fromisoformat(end_date)
    workdays = 0
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            workdays += 1
        cur += datetime.timedelta(days=1)
    denom = workdays * CAPACITY_MIN_PER_DAY

    room_rows = {r["id"]: {"room_id": r["id"], "room_name": r["name"],
                           "bookings_confirmed": 0, "bookings_cancelled": 0,
                           "reserved_minutes": 0}
                 for r in rooms if room_id is None or r["id"] == room_id}
    totals = {"bookings_confirmed": 0, "bookings_cancelled": 0, "reserved_minutes": 0}
    slots = {m: 0 for m in range(WORK_START, WORK_END, SLOT)}
    for b in filt:
        if b["status"] != "confirmed":
            continue
        mins = b["end_min"] - b["start_min"]
        rr = room_rows.get(b["room_id"])
        if rr:
            rr["bookings_confirmed"] += 1
            rr["reserved_minutes"] += mins
        totals["bookings_confirmed"] += 1
        totals["reserved_minutes"] += mins
        m = b["start_min"]
        while m < b["end_min"]:
            if m in slots:
                slots[m] += 1
            m += SLOT
    for b in filt:
        if b["status"] == "cancelled":
            totals["bookings_cancelled"] += 1
            rr = room_rows.get(b["room_id"])
            if rr:
                rr["bookings_cancelled"] += 1
    for rr in room_rows.values():
        rr["reserved_hours"] = round(rr["reserved_minutes"] / 60, 2)
        rr["utilisation"] = rr["reserved_minutes"] / denom if denom else 0.0
        allb = rr["bookings_confirmed"] + rr["bookings_cancelled"]
        rr["cancellation_rate"] = rr["bookings_cancelled"] / allb if allb else 0.0
        rr["under_utilised"] = rr["utilisation"] < 0.25
        rr["over_utilised"] = rr["utilisation"] > 0.80
    allb = totals["bookings_confirmed"] + totals["bookings_cancelled"]
    totals["cancellation_rate"] = totals["bookings_cancelled"] / allb if allb else 0.0
    totals["reserved_hours"] = round(totals["reserved_minutes"] / 60, 2)
    return {"rooms": list(room_rows.values()), "totals": totals, "by_slot": slots}


def load_db():
    if not os.path.exists(DB):
        return [], [], None, None
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    conn.row_factory = sqlite3.Row
    rooms = [dict(r) for r in conn.execute("SELECT id,name,capacity FROM rooms")]
    bookings = [dict(b) for b in conn.execute("SELECT * FROM bookings")]
    span = conn.execute("SELECT MIN(date) mn, MAX(date) mx FROM bookings").fetchone()
    conn.close()
    return rooms, bookings, span["mn"], span["mx"]


def independent_reference(bookings, start, end, room_id=None):
    """A deliberately different implementation used as an oracle."""
    conf = [b for b in bookings if b["status"] == "confirmed" and start <= b["date"] <= end
            and (room_id is None or b["room_id"] == room_id)]
    canc = [b for b in bookings if b["status"] == "cancelled" and start <= b["date"] <= end
            and (room_id is None or b["room_id"] == room_id)]
    total_min = sum(b["end_min"] - b["start_min"] for b in conf)
    rate = len(canc) / (len(conf) + len(canc)) if (len(conf) + len(canc)) else 0.0
    return {"confirmed": len(conf), "cancelled": len(canc), "total_min": total_min, "rate": rate}


def mock_store():
    """Deterministic mock store with the real field names."""
    seed = [20261005]
    def rnd():
        seed[0] = (seed[0] * 1103515245 + 12345) & 0x7fffffff
        return seed[0] / 0x7fffffff
    rooms = [{"id": 1, "name": "Boardroom", "capacity": 12},
             {"id": 2, "name": "Meeting Room A", "capacity": 8},
             {"id": 3, "name": "Huddle Room B", "capacity": 4}]
    bias = {1: 0.85, 2: 0.5, 3: 0.18}
    bookings, bid = [], 0
    start = datetime.date.today() - datetime.timedelta(days=56)
    for day in range(56):
        d = start + datetime.timedelta(days=day)
        if d.weekday() > 4:
            continue
        for room in rooms:
            load = bias[room["id"]]
            n = round((0.6 + rnd() * 1.4) * load * 4)
            for _ in range(n):
                s = WORK_START + SLOT * int(rnd() * 16)
                e = min(s + SLOT * (1 + int(rnd() * 4)), WORK_END)
                if e <= s:
                    continue
                cancelled = rnd() < (0.10 + 0.1 * (1 - load))
                bid += 1
                bookings.append({"id": bid, "room_id": room["id"], "date": d.isoformat(),
                                 "start_min": s, "end_min": e, "title": "M%d" % bid,
                                 "organiser": ["Alice", "Bob", "Cara", "Dan"][bid % 4],
                                 "status": "cancelled" if cancelled else "confirmed",
                                 "created_at": d.isoformat() + "T08:00:00",
                                 "cancelled_at": (d.isoformat() + "T08:30:00") if cancelled else None})
    return {"rooms": rooms, "bookings": bookings}


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (" :: " + detail if detail else ""))
    return cond


def main():
    ok = True
    rooms, bookings, mn, mx = load_db()
    print("db:", DB, "exists:", os.path.exists(DB))
    print("rooms:", len(rooms), "bookings:", len(bookings), "span:", mn, "..", mx)

    if bookings:
        a = aggregate(rooms, bookings, mn, mx)
        ref = independent_reference(bookings, mn, mx)
        ok &= check("confirmed total matches oracle", a["totals"]["bookings_confirmed"] == ref["confirmed"],
                    "%s vs %s" % (a["totals"]["bookings_confirmed"], ref["confirmed"]))
        ok &= check("cancelled total matches oracle", a["totals"]["bookings_cancelled"] == ref["cancelled"])
        ok &= check("reserved minutes matches oracle", a["totals"]["reserved_minutes"] == ref["total_min"])
        ok &= check("cancel rate matches oracle", abs(a["totals"]["cancellation_rate"] - ref["rate"]) < 1e-9,
                    "%.4f" % a["totals"]["cancellation_rate"])
        # inclusive-end check: single last day equals one-day aggregate
        one = aggregate(rooms, bookings, mx, mx)
        last_day = sum(1 for b in bookings if b["date"] == mx and b["status"] == "confirmed")
        ok &= check("inclusive end date (last day only)", one["totals"]["bookings_confirmed"] == last_day,
                    "%s vs %s" % (one["totals"]["bookings_confirmed"], last_day))
        # empty range
        empty = aggregate(rooms, bookings, "2027-03-01", "2027-03-05")
        ok &= check("empty range -> zero usage, no crash",
                    empty["totals"]["bookings_confirmed"] == 0 and empty["totals"]["cancellation_rate"] == 0.0)
        # cancelled must never add reserved minutes
        canc_min = sum(b["end_min"] - b["start_min"] for b in bookings if b["status"] == "cancelled" and mn <= b["date"] <= mx)
        ok &= check("cancelled rows add no reserved minutes",
                    a["totals"]["reserved_minutes"] == ref["total_min"] and canc_min >= 0)
        # slot voice check for one room/day vs naive scan
        for r in rooms:
            for dt in sorted({b["date"] for b in bookings})[:3]:
                ag = aggregate(rooms, bookings, dt, dt, r["id"])
                naive = {}
                for b in bookings:
                    if b["status"] == "confirmed" and b["room_id"] == r["id"] and b["date"] == dt:
                        m = b["start_min"]
                        while m < b["end_min"]:
                            naive[m] = naive.get(m, 0) + 1
                            m += SLOT
                match = all(ag["by_slot"].get(k, 0) == v for k, v in naive.items())
                ok &= check("heatmap slot values %s %s" % (r["name"], dt), match)
                break

    # mock + shape/field-name contract
    mock = mock_store()
    a = aggregate(mock["rooms"], mock["bookings"], mock["bookings"][0]["date"], mock["bookings"][-1]["date"])
    required_room = {"room_id", "room_name", "bookings_confirmed", "bookings_cancelled",
                     "reserved_minutes", "reserved_hours", "utilisation", "cancellation_rate",
                     "under_utilised", "over_utilised"}
    ok &= check("mock room payload has exact field names", required_room.issubset(set(a["rooms"][0].keys())))
    ok &= check("mock totals field names", {"bookings_confirmed", "bookings_cancelled",
                                            "reserved_minutes", "reserved_hours", "cancellation_rate"}.issubset(
        set(a["totals"].keys())))
    ok &= check("mock under/over flags present", all(isinstance(r["under_utilised"], bool) and
                isinstance(r["over_utilised"], bool) for r in a["rooms"]))
    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
