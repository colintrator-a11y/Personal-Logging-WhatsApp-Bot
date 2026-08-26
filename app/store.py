"""Local disk state: debug log, failed-write queue, undo pointer, pending asks."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from app import config

_lock = threading.Lock()

# chat_jid -> raw text we asked a follow-up question about
_pending: dict[str, str] = {}


def _append_jsonl(path, obj: dict[str, Any]) -> None:
    with _lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def log(event: str, **fields: Any) -> None:
    """Every message and parse result lands here for debugging."""
    _append_jsonl(
        config.LOG_FILE,
        {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields},
    )


# ---- failed writes ---------------------------------------------------------


def enqueue(rows: list[list[Any]]) -> None:
    _append_jsonl(config.QUEUE_FILE, {"rows": rows})


def drain_queue() -> list[list[list[Any]]]:
    """Take everything off the queue. Caller re-queues what it cannot write."""
    with _lock:
        if not config.QUEUE_FILE.exists():
            return []
        raw = config.QUEUE_FILE.read_text(encoding="utf-8")
        config.QUEUE_FILE.unlink()
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line)["rows"])
        except (ValueError, KeyError):
            continue
    return out


def queue_depth() -> int:
    if not config.QUEUE_FILE.exists():
        return 0
    return sum(1 for line in config.QUEUE_FILE.read_text(encoding="utf-8").splitlines() if line.strip())


# ---- undo pointer ----------------------------------------------------------


def remember_write(count: int) -> None:
    """How many rows the last logged message produced, so `undo` can drop them."""
    with _lock:
        config.LAST_WRITE_FILE.write_text(json.dumps({"count": count}), encoding="utf-8")


def last_write_count() -> int:
    if not config.LAST_WRITE_FILE.exists():
        return 0
    try:
        return int(json.loads(config.LAST_WRITE_FILE.read_text(encoding="utf-8"))["count"])
    except (ValueError, KeyError, OSError):
        return 0


def clear_write() -> None:
    config.LAST_WRITE_FILE.unlink(missing_ok=True)


# ---- backups ---------------------------------------------------------------


def backup_rows(rows: list[list[Any]]):
    """Dump the sheet to disk before a destructive command."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = config.DATA_DIR / f"sheet-backup-{stamp}.json"
    with _lock:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# ---- pending clarification -------------------------------------------------


def set_pending(chat: str, text: str) -> None:
    _pending[chat] = text


def take_pending(chat: str) -> str | None:
    return _pending.pop(chat, None)


def peek_pending(chat: str) -> str | None:
    return _pending.get(chat)
