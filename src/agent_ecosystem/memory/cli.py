"""Inspect/approve per-agent memories and task trace."""
from __future__ import annotations
import argparse
import json
from .store import MemoryDB


def main():
    parser=argparse.ArgumentParser(description="Agent memory inspection and review")
    parser.add_argument("--db",default="data/agent_memory.sqlite")
    sub=parser.add_subparsers(dest="cmd",required=True)
    sub.add_parser("stats")
    ls=sub.add_parser("list")
    ls.add_argument("--agent",choices=["research","coding","analysis"],required=True)
    ls.add_argument("--all",action="store_true")
    search=sub.add_parser("search")
    search.add_argument("--agent",choices=["research","coding","analysis"],required=True)
    search.add_argument("--query",required=True)
    add=sub.add_parser("add")
    add.add_argument("--agent",choices=["research","coding","analysis"],required=True)
    add.add_argument("--kind",choices=["episodic","semantic","procedural"],default="procedural")
    add.add_argument("--content",required=True)
    add.add_argument("--source-task",required=True)
    rev=sub.add_parser("review")
    rev.add_argument("--agent",choices=["research","coding","analysis"],required=True)
    rev.add_argument("--id",required=True)
    rev.add_argument("--status",choices=["verified","rejected","pending"],required=True)
    task=sub.add_parser("task")
    task.add_argument("--agent",choices=["research","coding","analysis"],required=True)
    task.add_argument("--id",required=True)
    args=parser.parse_args()
    db=MemoryDB(args.db)
    if args.cmd=="stats":
        output=db.stats()
    elif args.cmd=="list":
        output=db.memories(args.agent,verified_only=not args.all)
    elif args.cmd=="search":
        output=db.search(args.agent,args.query)
    elif args.cmd=="add":
        output={"memory_id":db.put_memory(args.agent,args.kind,args.content,args.source_task,status="pending"),"status":"pending"}
    elif args.cmd=="review":
        output={"updated":db.set_memory_status(args.agent,args.id,args.status)}
    else:
        output={"task":db.task(args.agent,args.id),"events":db.events(args.agent,args.id)}
    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
