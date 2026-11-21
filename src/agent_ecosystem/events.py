"""Append-only JSONL event log.

Adapted from AgentForge@7cee900 src/agentforge/persistence/events.py (near-verbatim).
Events complement SQLite: cheap, append-only, useful for following a live
workflow or debugging failures. Every workflow decision point writes one line.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLogger:
    """Thread-safe JSONL appender; one line per event, flushed immediately."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)

    def event(self, name: str, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": name,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()
