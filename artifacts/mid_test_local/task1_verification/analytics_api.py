#!/usr/bin/env python3
"""Analytics / aggregation backend for the meeting-room booking dashboard.

This module implements the read-only metrics defined by the frozen dashboard
contract (dashboard/dashboard.js aggregate() + DATA_CONTRACT.md, the Python
twin living in dashboard/test_contract.py) on top of the EXISTING booking data
store.  Data access reuses the application's own configuration from main.py
(ROOMBOOK_DB / bookings.db) -- no new data source is invented.

Contract endpoint
-----------------
    GET /api/dashboard?from=YYYY-MM-DD&to=YYYY-MM-DD[&room_id=<int>]
        200 application/json  -> the summary object returned by
                                 ``aggregate_analytics`` (see below)
        400 application/json  {"error": "..."} for an invalid or reversed range

Design
------
* The aggregation logic is isolated in the PURE function
  ``aggregate_analytics(rooms, bookings, from_date, to_date, room_id=None)``
  which takes plain room/booking records plus a range and returns the summary
  dict.  It performs no I/O and is unit-testable in isolation.
* ``get_analytics`` is the thin I/O wrapper: it reads the existing SQLite
  store (read-only) and delegates to the pure function.
* ``create_app`` is a tiny WSGI app exposing the contract endpoint.

Edge cases handled (per the metric spec)
----------------------------------------
* empty result set for a range with no bookings -> 200 with empty arrays
* bookings on the boundary dates are included (range is INCLUSIVE both ends)
* cancelled bookings count only as cancelled, never as occupied
* invalid / reversed date range -> ValidationError -> HTTP 400
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Reuse the existing application: data-access path + domain constants.
# Importing main.py has no side effects (its menu runs under __main__).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:  # pragma: no cover - import guard
    import main as booking_app

    DB_PATH = booking_app.DB_PATH
    WORK_START_MIN = booking_app.WORK_START_MIN      # 540  (09:00)
    WORK_END_MIN = booking_app.WORK_END_MIN          # 1080 (18:00)
    SLOT_MINUTES = booking_app.SLOT_MINUTES          # 30
except Exception:  # allow the pure function to be imported without the app
    booking_app = None
    DB_PATH = os.environ.get(
        "ROOMBOOK_DB", os.path.join(_HERE, "bookings.db")
    )
    WORK_START_MIN, WORK_END_MIN, SLOT_MINUTES = 540, 1080, 30

CAPACITY_MIN_PER_DAY = WORK_END_MIN - WORK_START_MIN  # 540 (9h working day)
UTIL_LOW, UTIL_HIGH = 0.25, 0.80
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
CONFIRMED, CANCELLED = "confirmed", "cancelled"


class ValidationError(ValueError):
    """Raised on invalid input; maps to HTTP 400 at the API boundary."""


# ---------------------------------------------------------------------------
# Pure aggregation (no I/O) -- the unit-testable core.
# ---------------------------------------------------------------------------
def _parse_date(value, field):
    if value is None or str(value).strip() == "":
        raise ValidationError("Parameter '%s' is required (YYYY-MM-DD)." % field)
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError(
            "Parameter '%s'=%r is not a valid date (use YYYY-MM-DD)."
            % (field, value)
        )


def _each_date(d0, d1):
    out, cur = [], d0
    while cur <= d1:
        out.append(cur)
        cur += datetime.timedelta(days=1)
    return out


def aggregate_analytics(rooms, bookings, from_date, to_date, room_id=None):
    """Aggregate booking records over an inclusive date range.

    Parameters
    ----------
    rooms : iterable of mappings with id, name, capacity
    bookings : iterable of mappings with room_id, date ('YYYY-MM-DD'),
        start_min, end_min, status ('confirmed' | 'cancelled')
    from_date, to_date : str 'YYYY-MM-DD' (inclusive on both ends)
    room_id : optional int-like filter

    Returns
    -------
    dict -- the summary object (shape frozen by the dashboard contract).
    """
    start = _parse_date(from_date, "from")
    end = _parse_date(to_date, "to")
    if start > end:
        raise ValidationError(
            "Invalid range: 'from' (%s) is after 'to' (%s)."
            % (start.isoformat(), end.isoformat())
        )
    s_iso, e_iso = start.isoformat(), end.isoformat()

    if room_id in (None, "", "all"):
        room_id = None
    else:
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            raise ValidationError("Parameter 'room_id' must be an integer.")

    dates = _each_date(start, end)
    workdays = sum(1 for d in dates if d.weekday() < 5)
    denom = workdays * CAPACITY_MIN_PER_DAY

    room_rows = {}
    for r in rooms:
        if room_id is not None and r["id"] != room_id:
            continue
        room_rows[r["id"]] = {
            "room_id": r["id"],
            "room_name": r["name"],
            "capacity": r.get("capacity"),
            "bookings_confirmed": 0,
            "bookings_cancelled": 0,
            "reserved_minutes": 0,
        }

    day_rows = {}
    by_weekday = [
        {"weekday": WEEKDAYS[i], "weekday_index": i + 1,
         "bookings_confirmed": 0, "reserved_minutes": 0} for i in range(5)
    ]
    by_slot = {
        m: {"start_min": m, "slot_label": "%02d:%02d" % (m // 60, m % 60),
            "bookings_confirmed": 0}
        for m in range(WORK_START_MIN, WORK_END_MIN, SLOT_MINUTES)
    }
    totals = {"bookings_confirmed": 0, "bookings_cancelled": 0,
              "reserved_minutes": 0}

    for b in bookings:
        bdate = str(b["date"])
        if not (s_iso <= bdate <= e_iso):          # inclusive both ends
            continue
        if room_id is not None and b["room_id"] != room_id:
            continue

        if b["status"] == CONFIRMED:
            mins = b["end_min"] - b["start_min"]
            row = room_rows.get(b["room_id"])
            if row is not None:
                row["bookings_confirmed"] += 1
                row["reserved_minutes"] += mins
            totals["bookings_confirmed"] += 1
            totals["reserved_minutes"] += mins
            day = day_rows.setdefault(bdate, {"date": bdate,
                "bookings_confirmed": 0, "reserved_minutes": 0,
                "bookings_cancelled": 0})
            day["bookings_confirmed"] += 1
            day["reserved_minutes"] += mins
            w = datetime.date.fromisoformat(bdate).weekday()
            if 0 <= w < 5:
                by_weekday[w]["bookings_confirmed"] += 1
                by_weekday[w]["reserved_minutes"] += mins
            m = b["start_min"]
            while m < b["end_min"]:
                if m in by_slot:
                    by_slot[m]["bookings_confirmed"] += 1
                m += SLOT_MINUTES
        elif b["status"] == CANCELLED:
            totals["bookings_cancelled"] += 1
            row = room_rows.get(b["room_id"])
            if row is not None:
                row["bookings_cancelled"] += 1
            day = day_rows.setdefault(bdate, {"date": bdate,
                "bookings_confirmed": 0, "reserved_minutes": 0,
                "bookings_cancelled": 0})
            day["bookings_cancelled"] += 1
        # any other status is not representable in v1 -> excluded

    room_list = []
    for r in room_rows.values():
        r["reserved_hours"] = round(r["reserved_minutes"] / 60, 2)
        r["utilisation"] = (r["reserved_minutes"] / denom) if denom else 0.0
        allb = r["bookings_confirmed"] + r["bookings_cancelled"]
        r["cancellation_rate"] = (r["bookings_cancelled"] / allb) if allb else 0.0
        r["under_utilised"] = r["utilisation"] < UTIL_LOW
        r["over_utilised"] = r["utilisation"] > UTIL_HIGH
        room_list.append(r)

    totals["reserved_hours"] = round(totals["reserved_minutes"] / 60, 2)
    all_tot = totals["bookings_confirmed"] + totals["bookings_cancelled"]
    totals["cancellation_rate"] = (
        totals["bookings_cancelled"] / all_tot) if all_tot else 0.0

    ordered = sorted(room_list, key=lambda r: r["utilisation"], reverse=True)
    most = [{"room_id": r["room_id"], "room_name": r["room_name"],
             "utilisation": r["utilisation"]} for r in ordered[:3]]
    least = [{"room_id": r["room_id"], "room_name": r["room_name"],
              "utilisation": r["utilisation"]} for r in ordered[-3:][::-1]]

    by_day = [day_rows[k] for k in sorted(day_rows)]
    slots = [by_slot[k] for k in sorted(by_slot)]

    return {
        "meta": {
            "start_date": s_iso,
            "end_date": e_iso,
            "room_count": len(room_list),
            "workdays": workdays,
            "capacity_minutes_per_day": CAPACITY_MIN_PER_DAY,
        },
        "rooms": room_list,
        "totals": totals,
        "by_day": by_day,
        "by_weekday": by_weekday,
        "by_slot": slots,
        "summary": {"most_used_rooms": most, "least_used_rooms": least},
        # explicit rankings required by the task (busiest days / busiest times)
        "rankings": {
            "busiest_days": sorted(by_day, key=lambda d: (-d["bookings_confirmed"], d["date"])),
            "busiest_slots": sorted(slots, key=lambda s: (-s["bookings_confirmed"], s["start_min"])),
        },
    }


# ---------------------------------------------------------------------------
# I/O wrapper -- reuses the existing data store.
# ---------------------------------------------------------------------------
def open_store(db_path=None):
    """Open the existing bookings store read-only (reuses main.DB_PATH)."""
    path = db_path or DB_PATH
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_rooms_and_bookings(conn):
    rooms = [dict(r) for r in conn.execute("SELECT id, name, capacity FROM rooms")]
    bookings = [dict(b) for b in conn.execute("SELECT * FROM bookings")]
    return rooms, bookings


def data_span(conn):
    row = conn.execute("SELECT MIN(date) mn, MAX(date) mx FROM bookings").fetchone()
    return (row["mn"], row["mx"]) if row else (None, None)


def get_analytics(from_date=None, to_date=None, room_id=None, db_path=None):
    """Read the store and return the aggregated summary for the range.

    When from_date/to_date are omitted the full data span is used (spec Q1).
    """
    conn = open_store(db_path)
    try:
        rooms, bookings = load_rooms_and_bookings(conn)
        if from_date in (None, "") or to_date in (None, ""):
            mn, mx = data_span(conn)
            from_date = from_date or mn
            to_date = to_date or mx
    finally:
        conn.close()

    if from_date in (None, "") or to_date in (None, ""):
        today = datetime.date.today().isoformat()
        from_date, to_date = from_date or today, to_date or today

    return aggregate_analytics(rooms, bookings, from_date, to_date, room_id)


# ---------------------------------------------------------------------------
# WSGI endpoint: GET /api/dashboard
# ---------------------------------------------------------------------------
def _json_response(start_response, status, payload):
    body = json.dumps(payload).encode("utf-8")
    start_response(status, [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ])
    return [body]


def create_app(db_path=None):
    """Return a WSGI application implementing the contract endpoint."""
    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        if method != "GET":
            return _json_response(start_response, "405 Method Not Allowed",
                                  {"error": "Only GET is supported."})
        if environ.get("PATH_INFO") not in ("/api/dashboard", "/api/dashboard/"):
            return _json_response(start_response, "404 Not Found",
                                  {"error": "Unknown endpoint."})
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        from_date = (qs.get("from") or [None])[0]
        to_date = (qs.get("to") or [None])[0]
        room_id = (qs.get("room_id") or [None])[0]
        try:
            payload = get_analytics(from_date, to_date, room_id, db_path)
        except ValidationError as exc:
            return _json_response(start_response, "400 Bad Request",
                                  {"error": str(exc)})
        return _json_response(start_response, "200 OK", payload)
    return app


# ---------------------------------------------------------------------------
# Self-test / demo runner (executed via `python analytics_api.py`).
# ---------------------------------------------------------------------------
def _call_app(app, path_with_query):
    """Exercise the WSGI app in-process (network-free)."""
    parsed = urlparse(path_with_query)
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": parsed.path,
               "QUERY_STRING": parsed.query}
    body = b"".join(app(environ, start_response))
    return captured["status"], json.loads(body.decode("utf-8"))


def _self_tests():
    """Unit tests of the pure function on a tiny synthetic dataset."""
    rooms = [{"id": 1, "name": "Boardroom", "capacity": 12},
             {"id": 2, "name": "Meeting Room A", "capacity": 8}]
    bookings = [
        # Monday 2026-10-05
        {"room_id": 1, "date": "2026-10-05", "start_min": 540, "end_min": 660,
         "status": "confirmed"},
        {"room_id": 1, "date": "2026-10-05", "start_min": 600, "end_min": 630,
         "status": "cancelled"},
        # boundary day = end of range (inclusive)
        {"room_id": 2, "date": "2026-10-09", "start_min": 570, "end_min": 630,
         "status": "confirmed"},
        # outside the range -> must be ignored
        {"room_id": 2, "date": "2026-10-12", "start_min": 540, "end_min": 600,
         "status": "confirmed"},
    ]
    a = aggregate_analytics(rooms, bookings, "2026-10-05", "2026-10-09")
    assert a["totals"]["bookings_confirmed"] == 2, a["totals"]
    assert a["totals"]["bookings_cancelled"] == 1, a["totals"]
    assert a["totals"]["reserved_minutes"] == 120 + 60, a["totals"]
    assert abs(a["totals"]["cancellation_rate"] - 1 / 3) < 1e-9
    assert a["meta"]["workdays"] == 5
    assert a["by_slot"][0]["start_min"] == 540 and a["by_slot"][0]["bookings_confirmed"] == 1
    day0 = a["by_day"][0]
    assert day0["date"] == "2026-10-05" and day0["bookings_cancelled"] == 1
    # empty range -> well-formed, all zeros
    e = aggregate_analytics(rooms, bookings, "2026-11-02", "2026-11-06")
    assert e["totals"]["bookings_confirmed"] == 0 and e["by_day"] == []
    assert e["meta"]["workdays"] == 5 and len(e["by_slot"]) == 18
    # reversed range -> validation error
    try:
        aggregate_analytics(rooms, bookings, "2026-10-09", "2026-10-05")
        raise AssertionError("expected ValidationError for reversed range")
    except ValidationError:
        pass
    # invalid date -> validation error
    try:
        aggregate_analytics(rooms, bookings, "nope", "2026-10-05")
        raise AssertionError("expected ValidationError for bad date")
    except ValidationError:
        pass
    print("SELF-TESTS: all passed")


def main():
    _self_tests()
    app = create_app()

    conn = open_store()
    try:
        rooms, bookings = load_rooms_and_bookings(conn)
        mn, mx = data_span(conn)
    finally:
        conn.close()
    print("store span: %s .. %s | rooms: %d | bookings: %d"
          % (mn, mx, len(rooms), len(bookings)))

    # ---- range 1: BUSY (the full data span) --------------------------------
    status, payload = _call_app(app, "/api/dashboard?from=%s&to=%s" % (mn, mx))
    print("\n=== [1] BUSY range %s..%s -> %s ===" % (mn, mx, status))
    print("totals:", json.dumps(payload["totals"]))
    for r in payload["rooms"]:
        print("  room %-16s confirmed=%3d cancelled=%2d reserved_h=%6.1f util=%.3f"
              % (r["room_name"], r["bookings_confirmed"], r["bookings_cancelled"],
                 r["reserved_hours"], r["utilisation"]))
    print("busiest days  :", json.dumps(payload["rankings"]["busiest_days"][:3]))
    print("busiest slots :", json.dumps(payload["rankings"]["busiest_slots"][:3]))
    print("payload shape :", sorted(payload.keys()))

    # ---- range 2: QUIET / EMPTY -------------------------------------------
    booked_days = {str(b["date"]) for b in bookings}
    quiet = None
    if mn and mx:
        d, last = datetime.date.fromisoformat(mn), datetime.date.fromisoformat(mx)
        while d <= last:
            if d.weekday() < 5 and d.isoformat() not in booked_days:
                quiet = (d.isoformat(), d.isoformat())
                break
            d += datetime.timedelta(days=1)
    if quiet is None:
        quiet = ("1990-01-01", "1990-01-05")  # guaranteed no bookings
    status, payload2 = _call_app(app, "/api/dashboard?from=%s&to=%s" % quiet)
    print("\n=== [2] QUIET/EMPTY range %s..%s -> %s ===" % (quiet[0], quiet[1], status))
    print("totals:", json.dumps(payload2["totals"]))
    print("by_day:", json.dumps(payload2["by_day"]))
    print("rooms:", json.dumps(payload2["rooms"]))

    # ---- edge cases: reversed + invalid -----------------------------------
    s, p = _call_app(app, "/api/dashboard?from=%s&to=%s" % (mx, mn))
    print("\n=== [3] reversed range %s..%s -> %s :: %s"
          % (mx, mn, s, p.get("error")))
    s, p = _call_app(app, "/api/dashboard?from=not-a-date&to=%s" % mx)
    print("=== [4] invalid date -> %s :: %s" % (s, p.get("error")))


if __name__ == "__main__":
    main()
