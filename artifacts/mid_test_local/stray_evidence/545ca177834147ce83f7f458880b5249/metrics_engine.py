#!/usr/bin/env python3
"""Metrics engine: aggregates the booking records into dashboard metrics.

Field contract (matches the QA oracle's re-derivation in validate_metrics.py):
  date_range, total_rows, confirmed_count, cancelled_count, cancel_rate,
  per_room {room: count}, total_reserved_minutes, total_reserved_hours,
  per_day {date: count}, busiest_days [date...], per_day_max,
  per_slot {"HH:MM": count}, busiest_slots ["HH:MM"...], per_slot_max.

The dashboard consumes these fields verbatim; it never recomputes metrics.
"""
import argparse
import json

import booking_store as bs


def compute_metrics(conn, start, end, statuses=("confirmed",)):
    ph = ",".join("?" for _ in statuses)
    rows = conn.execute(
        "SELECT b.*, r.name room FROM bookings b JOIN rooms r ON r.id=b.room_id "
        "WHERE b.date>=? AND b.date<=? AND b.status IN (%s)" % ph,
        (start, end) + tuple(statuses)).fetchall()

    per_room = {r[1]: 0 for r in bs.ROOMS}          # include zero-use rooms
    total_min = 0
    per_day = {}
    per_slot = {}
    for b in rows:
        per_room[b["room"]] = per_room.get(b["room"], 0) + 1
        total_min += b["end_min"] - b["start_min"]
        per_day[b["date"]] = per_day.get(b["date"], 0) + 1
        s = b["start_min"]
        while s < b["end_min"]:
            key = "%02d:%02d" % (s // 60, s % 60)
            per_slot[key] = per_slot.get(key, 0) + 1
            s += bs.SLOT

    total_rows = conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE date>=? AND date<=?", (start, end)).fetchone()[0]
    cancelled = conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE date>=? AND date<=? AND status='cancelled'",
        (start, end)).fetchone()[0]

    per_day = dict(sorted(per_day.items()))
    per_slot = dict(sorted(per_slot.items()))
    day_max = max(per_day.values()) if per_day else 0
    slot_max = max(per_slot.values()) if per_slot else 0

    return {
        "date_range": {"start": start, "end": end},
        "total_rows": total_rows,
        "confirmed_count": len(rows),
        "cancelled_count": cancelled,
        "cancel_rate": (cancelled / total_rows) if total_rows else 0.0,
        "per_room": per_room,
        "total_reserved_minutes": total_min,
        "total_reserved_hours": round(total_min / 60.0, 2),
        "per_day": per_day,
        "busiest_days": [d for d, n in per_day.items() if n == day_max] if per_day else [],
        "per_day_max": day_max,
        "per_slot": per_slot,
        "busiest_slots": [s for s, n in per_slot.items() if n == slot_max] if per_slot else [],
        "per_slot_max": slot_max,
    }


def metrics_for_db(path, start, end, read_only=True):
    conn = bs.connect(path, read_only=read_only)
    try:
        return compute_metrics(conn, start, end)
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description="Compute booking metrics for a date range")
    p.add_argument("--db", default=bs.DEFAULT_DB)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    m = metrics_for_db(args.db, args.start, args.end)
    if args.json:
        print(json.dumps(m, indent=2))
    else:
        print("range %s..%s confirmed=%d cancelled=%d cancel_rate=%.3f"
              % (args.start, args.end, m["confirmed_count"], m["cancelled_count"], m["cancel_rate"]))
        print("per_room:", m["per_room"])
        print("reserved: %d min (%s h)" % (m["total_reserved_minutes"], m["total_reserved_hours"]))
        print("per_day:", m["per_day"], "busiest:", m["busiest_days"])
        print("slot histogram:", m["per_slot"], "busiest:", m["busiest_slots"])


if __name__ == "__main__":
    main()
