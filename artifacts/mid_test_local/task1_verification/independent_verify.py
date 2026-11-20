#!/usr/bin/env python3
"""INDEPENDENT verification of analytics_api dashboard metrics.

Written from scratch by the test engineer. Does NOT import or reuse
analytics_api.aggregate_analytics to compute expected values. Ground truth is
derived directly from the raw SQLite booking rows with plain Python.

It DOES import analytics_api only to *call the endpoint under test* (the SUT
output), which is legitimate: we compare SUT output vs our own oracle.
"""
import datetime, json, os, sqlite3, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "bookings.db")
sys.path.insert(0, HERE)

CAP_PER_DAY = 540
WORK_START, WORK_END, SLOT = 540, 1080, 30
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# --------------------------------------------------------------------------
# Ground-truth oracle (independent implementation)
# --------------------------------------------------------------------------
def q(conn, sql, args=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args)]


def oracle(rooms, bookings, start, end, room_id=None):
    """Independent recomputation of every metric required by the spec."""
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    span = [d0 + datetime.timedelta(days=i) for i in range((d1 - d0).days + 1)]
    workdays = sum(1 for d in span if d.weekday() < 5)
    denom = workdays * CAP_PER_DAY

    def inrange(b):
        return start <= str(b["date"]) <= end and (room_id is None or b["room_id"] == room_id)

    rows = [b for b in bookings if inrange(b)]
    conf = [b for b in rows if b["status"] == "confirmed"]
    canc = [b for b in rows if b["status"] == "cancelled"]

    per_room = {}
    for r in rooms:
        if room_id is not None and r["id"] != room_id:
            continue
        per_room[r["id"]] = {
            "room_name": r["name"],
            "confirmed": sum(1 for b in conf if b["room_id"] == r["id"]),
            "cancelled": sum(1 for b in canc if b["room_id"] == r["id"]),
            "reserved_min": sum((b["end_min"] - b["start_min"]) for b in conf if b["room_id"] == r["id"]),
        }

    by_weekday = {i: {"confirmed": 0, "reserved_min": 0} for i in range(5)}
    for b in conf:
        w = datetime.date.fromisoformat(str(b["date"])).weekday()
        if 0 <= w < 5:
            by_weekday[w]["confirmed"] += 1
            by_weekday[w]["reserved_min"] += b["end_min"] - b["start_min"]

    by_day = {}
    for b in rows:
        dd = str(b["date"])
        e = by_day.setdefault(dd, {"confirmed": 0, "cancelled": 0, "reserved_min": 0})
        if b["status"] == "confirmed":
            e["confirmed"] += 1
            e["reserved_min"] += b["end_min"] - b["start_min"]
        elif b["status"] == "cancelled":
            e["cancelled"] += 1

    slots = {m: 0 for m in range(WORK_START, WORK_END, SLOT)}
    for b in conf:
        m = b["start_min"]
        while m < b["end_min"]:
            if m in slots:
                slots[m] += 1
            m += SLOT

    tot_conf = len(conf)
    tot_canc = len(canc)
    tot_min = sum(b["end_min"] - b["start_min"] for b in conf)
    return {
        "workdays": workdays, "denom": denom,
        "per_room": per_room,
        "by_weekday": by_weekday,
        "by_day": by_day,
        "slots": slots,
        "totals": {
            "confirmed": tot_conf, "cancelled": tot_canc, "reserved_min": tot_min,
            "reserved_hours": round(tot_min / 60, 2),
            "cancellation_rate": (tot_canc / (tot_conf + tot_canc)) if (tot_conf + tot_canc) else 0.0,
        },
    }


# --------------------------------------------------------------------------
# SUT endpoint caller (in-process WSGI, network-free)
# --------------------------------------------------------------------------
def call_endpoint(app, query):
    from urllib.parse import urlparse
    p = urlparse("/api/dashboard?" + query)
    cap = {}
    def sr(status, headers):
        cap["status"] = status
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": p.path, "QUERY_STRING": p.query}
    body = b"".join(app(env, sr))
    return cap["status"], json.loads(body.decode("utf-8"))


FAILS = []
def check(name, expected, actual, tol=1e-9):
    if isinstance(expected, float) or isinstance(actual, float):
        ok = abs(expected - actual) <= tol
    else:
        ok = expected == actual
    tag = "PASS" if ok else "FAIL"
    if not ok:
        FAILS.append((name, expected, actual))
    print("  [%s] %-48s exp=%r act=%r" % (tag, name, expected, actual))
    return ok


def verify_range(app, rooms, bookings, label, start, end):
    print("\n=== RANGE %s : %s .. %s ===" % (label, start, end))
    status, payload = call_endpoint(app, "from=%s&to=%s" % (start, end))
    print("  HTTP status:", status)
    o = oracle(rooms, bookings, start, end)

    # meta
    check("meta.start_date", start, payload.get("meta", {}).get("start_date"))
    check("meta.end_date", end, payload.get("meta", {}).get("end_date"))
    check("meta.workdays", o["workdays"], payload.get("meta", {}).get("workdays"))
    check("meta.room_count", len(o["per_room"]), payload.get("meta", {}).get("room_count"))

    # totals
    t = payload.get("totals", {})
    check("totals.bookings_confirmed", o["totals"]["confirmed"], t.get("bookings_confirmed"))
    check("totals.bookings_cancelled", o["totals"]["cancelled"], t.get("bookings_cancelled"))
    check("totals.reserved_minutes", o["totals"]["reserved_min"], t.get("reserved_minutes"))
    check("totals.reserved_hours", o["totals"]["reserved_hours"], t.get("reserved_hours"))
    check("totals.cancellation_rate", o["totals"]["cancellation_rate"], t.get("cancellation_rate"))

    # per room
    pr = {r["room_id"]: r for r in payload.get("rooms", [])}
    for rid, exp in o["per_room"].items():
        a = pr.get(rid, {})
        check("room %d bookings_confirmed" % rid, exp["confirmed"], a.get("bookings_confirmed"))
        check("room %d bookings_cancelled" % rid, exp["cancelled"], a.get("bookings_cancelled"))
        check("room %d reserved_minutes" % rid, exp["reserved_min"], a.get("reserved_minutes"))
        exp_util = (exp["reserved_min"] / o["denom"]) if o["denom"] else 0.0
        check("room %d utilisation" % rid, exp_util, a.get("utilisation"))

    # by_weekday
    wd = payload.get("by_weekday", [])
    for i in range(5):
        check("by_weekday[%d].bookings_confirmed" % i, o["by_weekday"][i]["confirmed"],
              wd[i].get("bookings_confirmed") if i < len(wd) else None)
        check("by_weekday[%d].reserved_minutes" % i, o["by_weekday"][i]["reserved_min"],
              wd[i].get("reserved_minutes") if i < len(wd) else None)

    # by_slot
    bs = {s["start_min"]: s for s in payload.get("by_slot", [])}
    check("by_slot length", 18, len(payload.get("by_slot", [])))
    for m in range(WORK_START, WORK_END, SLOT):
        check("by_slot[%d].bookings_confirmed" % m, o["slots"][m],
              bs.get(m, {}).get("bookings_confirmed"))

    # busiest days ranking
    exp_rank = sorted(o["by_day"].items(),
                      key=lambda kv: (-kv[1]["confirmed"], kv[0]))
    exp_rank = [k for k, v in exp_rank]
    act_rank = [d.get("date") for d in payload.get("rankings", {}).get("busiest_days", [])]
    check("rankings.busiest_days order", exp_rank, act_rank)

    # busiest slots ranking
    exp_slots = sorted(range(WORK_START, WORK_END, SLOT),
                       key=lambda m: (-o["slots"][m], m))
    act_slots = [s.get("start_min") for s in payload.get("rankings", {}).get("busiest_slots", [])]
    check("rankings.busiest_slots order", exp_slots, act_slots)

    return payload


def shape_check(payload):
    print("\n=== API CONTRACT SHAPE ===")
    required = ["meta", "rooms", "totals", "by_day", "by_weekday", "by_slot", "summary", "rankings"]
    for k in required:
        check("payload has top-level %r" % k, True, k in payload)
    meta_keys = {"start_date", "end_date", "room_count", "workdays", "capacity_minutes_per_day"}
    check("meta keys", meta_keys, set(payload.get("meta", {}).keys()))
    tot_keys = {"bookings_confirmed", "bookings_cancelled", "reserved_minutes",
                "reserved_hours", "cancellation_rate"}
    check("totals keys", tot_keys, set(payload.get("totals", {}).keys()))
    room_keys = {"room_id", "room_name", "capacity", "bookings_confirmed",
                 "bookings_cancelled", "reserved_minutes", "reserved_hours",
                 "utilisation", "cancellation_rate", "under_utilised", "over_utilised"}
    if payload.get("rooms"):
        check("room row keys", room_keys, set(payload["rooms"][0].keys()))
    sum_keys = {"most_used_rooms", "least_used_rooms"}
    check("summary keys", sum_keys, set(payload.get("summary", {}).keys()))
    rank_keys = {"busiest_days", "busiest_slots"}
    check("rankings keys", rank_keys, set(payload.get("rankings", {}).keys()))


def main():
    import analytics_api as api
    app = api.create_app(DB)

    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rooms = q(conn, "SELECT id,name,capacity FROM rooms")
    bookings = q(conn, "SELECT * FROM bookings")
    print("DB:", DB)
    print("rooms:", rooms)
    print("bookings:", len(bookings))
    print("statuses:", sorted({b["status"] for b in bookings}))
    dates = sorted({str(b["date"]) for b in bookings})
    print("date span:", dates[:1], "..", dates[-1:], "n_days_with_data:", len(dates))
    conn.close()

    # pick ranges from data
    all_dates = sorted({str(b["date"]) for b in bookings})
    d0, d1 = all_dates[0], all_dates[-1]
    normal_start, normal_end = d0, d0  # single busy day
    # a normal multi-day range: first 7 calendar days of data span
    ns = datetime.date.fromisoformat(d0)
    ne = ns + datetime.timedelta(days=6)
    normal = (ns.isoformat(), ne.isoformat())

    # range containing cancellations: full span
    canc_range = (d0, d1)

    # empty range: pick a week with no bookings
    booked = set(all_dates)
    probe = datetime.date.fromisoformat(d1)
    empty = None
    for _ in range(4000):
        probe += datetime.timedelta(days=1)
        if probe.weekday() < 5 and probe.isoformat() not in booked:
            # ensure whole week empty
            wk = [probe + datetime.timedelta(days=i) for i in range(5)]
            if all(w.isoformat() not in booked for w in wk):
                empty = (wk[0].isoformat(), wk[4].isoformat())
                break
    if empty is None:
        empty = ("2090-01-02", "2090-01-06")

    verify_range(app, rooms, bookings, "NORMAL(7d)", *normal)
    verify_range(app, rooms, bookings, "WITH-CANCELLATIONS(full)", *canc_range)
    verify_range(app, rooms, bookings, "EMPTY", *empty)

    status, payload = call_endpoint(app, "from=%s&to=%s" % canc_range)
    shape_check(payload)

    # ---- edge cases ----
    print("\n=== EDGE: reversed params ===")
    s, p = call_endpoint(app, "from=%s&to=%s" % (d1, d0))
    print("  status:", s, "body:", p)
    check("reversed -> 400", True, s.startswith("400"))
    check("reversed -> error key", True, "error" in p)

    print("\n=== EDGE: invalid date params ===")
    for bad in ["not-a-date", "2026-13-40", ""]:
        s, p = call_endpoint(app, "from=%s&to=%s" % (bad, d1))
        print("  from=%r -> status=%s body=%s" % (bad, s, p))
        check("invalid from=%r -> 400" % bad, True, s.startswith("400"))

    print("\n=== EDGE: synthetic boundary / zero-length / midnight ===")
    tmp = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(tmp)
    c.execute("CREATE TABLE rooms(id INTEGER PRIMARY KEY, name TEXT, capacity INT)")
    c.execute("CREATE TABLE bookings(id INTEGER PRIMARY KEY, room_id INT, date TEXT, "
              "start_min INT, end_min INT, title TEXT, organiser TEXT, status TEXT, "
              "created_at TEXT, cancelled_at TEXT)")
    c.execute("INSERT INTO rooms VALUES (1,'Boardroom',12)")
    # date range we will test: 2026-10-05 .. 2026-10-09 (Mon..Fri)
    recs = [
        # inside range, normal
        (1, "2026-10-05", 540, 600, "confirmed"),
        # starts before range start? range start is 2026-10-05 so use 2026-10-04 (Sun)
        (1, "2026-10-04", 540, 600, "confirmed"),          # day before range -> excluded
        (1, "2026-10-10", 540, 600, "confirmed"),          # day after range -> excluded
        # exactly midnight boundaries
        (1, "2026-10-06", 0, 60, "confirmed"),             # midnight-01:00
        (1, "2026-10-06", 0, 0, "confirmed"),              # zero-length at midnight
        # zero-length inside working hours
        (1, "2026-10-07", 600, 600, "confirmed"),          # 10:00-10:00 zero length
        # boundary last day
        (1, "2026-10-09", 1050, 1080, "confirmed"),        # 17:30-18:00
    ]
    for i, (rid, d, s0, e0, st) in enumerate(recs, 1):
        c.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (i, rid, d, s0, e0, "t", "o", st, "2026-01-01T00:00:00", None))
    c.commit(); c.close()

    app2 = api.create_app(tmp)
    bs, be = "2026-10-05", "2026-10-09"
    s, p = call_endpoint(app2, "from=%s&to=%s" % (bs, be))
    print("  status:", s)
    conn = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
    trooms = q(conn, "SELECT id,name,capacity FROM rooms")
    tbook = q(conn, "SELECT * FROM bookings")
    conn.close()
    o = oracle(trooms, tbook, bs, be)
    print("  oracle totals:", o["totals"])
    print("  sut    totals:", p.get("totals"))
    check("boundary: totals.confirmed", o["totals"]["confirmed"], p["totals"]["bookings_confirmed"])
    check("boundary: totals.reserved_minutes", o["totals"]["reserved_min"], p["totals"]["reserved_minutes"])
    check("boundary: by_slot[540]", o["slots"][540], {x["start_min"]: x for x in p["by_slot"]}[540]["bookings_confirmed"])
    os.remove(tmp)

    print("\n================ SUMMARY ================")
    if FAILS:
        print("FAILURES: %d" % len(FAILS))
        for n, e, a in FAILS:
            print("  - %s : expected=%r actual=%r" % (n, e, a))
    else:
        print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
