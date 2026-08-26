"""Environment-backed settings. Everything secret lives in .env, never here."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Categories are learned from what is already in the sheet. These only seed an
# empty sheet so the first few messages land somewhere sensible; after that the
# live column drives the list. See sheets.known_categories().
SEED_CATEGORIES = ["food", "transport", "groceries", "bills", "health", "other"]
FALLBACK_CATEGORY = "other"

# How long a category list read from the sheet is reused before re-reading.
CATEGORY_CACHE_SECONDS = int(os.getenv("CATEGORY_CACHE_SECONDS", "60"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Tried when the primary is rate limited, overloaded or timing out. Individual
# models get slow or unavailable for hours at a time; two keeps the bot alive.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
# The API rejects any deadline under 10s with a 400, so do not let one through.
GEMINI_TIMEOUT_MS = max(int(os.getenv("GEMINI_TIMEOUT_MS", "12000")), 10000)

SHEET_ID = os.getenv("SHEET_ID", "")
SHEET_NAME = os.getenv("SHEET_NAME", "Log")
SERVICE_ACCOUNT_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL", "")
# Stored on one line with literal \n sequences, so unescape them here.
PRIVATE_KEY = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n").strip('"')

TZ_NAME = os.getenv("TZ", "Asia/Tokyo")
TZ = ZoneInfo(TZ_NAME)
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "JPY").upper()

BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
BRAIN_HOST = os.getenv("BRAIN_HOST", "127.0.0.1")
BRAIN_PORT = int(os.getenv("BRAIN_PORT", "8000"))

QUEUE_FLUSH_MS = int(os.getenv("QUEUE_FLUSH_MS", "60000"))

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "bot.jsonl"
QUEUE_FILE = DATA_DIR / "queue.jsonl"
LAST_WRITE_FILE = DATA_DIR / "last_write.json"

HEADERS = [
    "timestamp",
    "date",
    "amount",
    "currency",
    "category",
    "description",
    "raw_message",
]


def _key_looks_real() -> bool:
    """The placeholder in .env.example is non-empty, so emptiness is not enough."""
    return (
        "BEGIN PRIVATE KEY" in PRIVATE_KEY
        and "MIIE..." not in PRIVATE_KEY
        and len(PRIVATE_KEY) > 500
    )


def missing() -> list[str]:
    """Config that must be present before the bot can do anything useful."""
    out = []
    for name, val in (
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("SHEET_ID", SHEET_ID),
        ("GOOGLE_SERVICE_ACCOUNT_EMAIL", SERVICE_ACCOUNT_EMAIL),
    ):
        if not val:
            out.append(name)
    if not _key_looks_real():
        out.append("GOOGLE_PRIVATE_KEY")
    return out
