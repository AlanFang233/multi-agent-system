#!/usr/bin/env python3
"""Meeting-room booking CLI (the existing booking system).

Usage:
  python3 main.py init
  python3 main.py show --date 2026-11-23 [--room Boardroom]
  python3 main.py book --date 2026-11-23 --room Boardroom --start 10:00 --end 11:00 \
        --title "Sprint review" --organiser Alice
  python3 main.py cancel --id 1   |   python3 main.py cancel --room Boardroom --date 2026-11-23
  python3 main.py list [--date 2026-11-23]
"""
import argparse
import os
import sys

import booking_store as bs


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _fmt(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def cmd_show(conn, args):
    date = args.date
    rooms = bs.ROOMS
    if args.room:
        rooms = [r for r in rooms if r[1] == args.room]
        if not rooms:
            print("unknown room"); return 1
    for rid, name, cap in rooms:
        print("== %s (%s) on %s ==" % (name, date, date))
        taken = {b["start_min"]: b for b in bs.confirmed_bookings(conn, date, rid)}
        for s in bs.all_slots():
            b = taken.get(s)
            print("  %s %s" % (_fmt(s), ("[X] TAKEN - booking #%d: %s (%s)"
                  % (b["id"], b["title"], b["organiser"])) if b else "free"))
    return 0


def cmd_book(conn, args):
    ok, res = bs.book_room(conn, args.date, args.room, _hm(args.start), _hm(args.end),
                           args.title, args.organiser)
    if ok:
        print("SUCCESS: booking #%s confirmed - %s (%s) on %s, %s-%s for %s"
              % (res, args.room, args.title, args.date, args.start, args.end, args.organiser))
        return 0
    print("FAILED: %s" % res)
    return 1


def cmd_cancel(conn, args):
    if args.id:
        ok = bs.cancel_booking(conn, args.id)
        print("SUCCESS: booking #%s cancelled. The slot is now free." % args.id if ok
              else "FAILED: no confirmed booking #%s" % args.id)
        return 0 if ok else 1
    rows = bs.confirmed_bookings(conn, args.date)
    if args.room:
        rid = [r[0] for r in bs.ROOMS if r[1] == args.room]
        rid = rid[0] if rid else None
        rows = [b for b in rows if b["room_id"] == rid]
    n = 0
    for b in rows:
        n += bs.cancel_booking(conn, b["id"])
    print("SUCCESS: cancelled %d booking(s)" % n)
    return 0


def cmd_list(conn, args):
    for b in bs.list_bookings(conn, args.date):
        print("#%-3d %-14s %s %s-%s %-10s %-28s %s"
              % (b["id"], b["room"], b["date"], _fmt(b["start_min"]), _fmt(b["end_min"]),
                 b["status"], b["title"], b["organiser"]))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Meeting room booking system")
    p.add_argument("--db", default=bs.DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init")
    sp = sub.add_parser("show"); sp.add_argument("--date", required=True); sp.add_argument("--room")
    sp = sub.add_parser("book")
    sp.add_argument("--date", required=True); sp.add_argument("--room", required=True)
    sp.add_argument("--start", required=True); sp.add_argument("--end", required=True)
    sp.add_argument("--title", required=True); sp.add_argument("--organiser", required=True)
    sp = sub.add_parser("cancel"); sp.add_argument("--id", type=int); sp.add_argument("--room"); sp.add_argument("--date")
    sp = sub.add_parser("list"); sp.add_argument("--date")
    args = p.parse_args(argv)

    conn = bs.init_db(args.db) if args.cmd == "init" else bs.connect(args.db)
    if args.cmd == "init":
        print("initialised %s" % args.db)
        return 0
    handlers = {"show": cmd_show, "book": cmd_book, "cancel": cmd_cancel, "list": cmd_list}
    if args.cmd in handlers:
        return handlers[args.cmd](conn, args)
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
