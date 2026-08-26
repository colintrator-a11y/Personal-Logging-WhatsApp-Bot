"""Turn free text into structured expense entries, with a fixed category list."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime

from google import genai
from google.genai import types

from app import config

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


# The enum here is what stops the model inventing categories. `date` is absolute
# because we hand it today's date, so relative words resolve before we see them.
SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                    "category": {"type": "string", "enum": config.CATEGORIES},
                    "description": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["amount", "currency", "category", "description", "date"],
                "propertyOrdering": ["amount", "currency", "category", "description", "date"],
            },
        },
        "needs_clarification": {"type": "boolean"},
        "clarification": {"type": "string"},
    },
    "required": ["entries", "needs_clarification", "clarification"],
    "propertyOrdering": ["entries", "needs_clarification", "clarification"],
}

SYSTEM = """You extract expenses from one short message in a personal expense log.

Categories: you MUST pick exactly one of {categories} for every entry. These are
the only categories that exist. If nothing fits, use "other". Never invent one.

Dates: today is {today} ({weekday}) in timezone {tz}. Resolve any relative date
in any language to an absolute YYYY-MM-DD ("yesterday", "ayer", "昨日" -> the day
before today; "last friday", "el 3" -> the actual date). If no date is mentioned,
use today.

Currency: return an ISO 4217 code. Map symbols: ¥ -> JPY, $ -> USD, € -> EUR,
£ -> GBP, ₩ -> KRW, ₹ -> INR. If no currency is given, use {default_currency}.

Multiple expenses: "food 40 and parking 12" is TWO entries, one per expense.

Description: a few words taken from the message, in the message's own language.
Do not translate. Do not add words that are not implied by the message.

If an amount is missing, or you cannot tell which number is the amount, set
needs_clarification to true, return an empty entries list, and put a one-line
question in clarification, written in the same language as the message. Do not
guess an amount, ever.

If the message contains no expense at all, return empty entries,
needs_clarification false, and an empty clarification.
"""


@dataclass
class Entry:
    amount: float
    currency: str
    category: str
    description: str
    on: date


@dataclass
class ParseResult:
    entries: list[Entry]
    needs_clarification: bool
    clarification: str
    raw: dict


class GeminiError(RuntimeError):
    pass


def _status_code(err: Exception) -> int | None:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int):
        return code
    if "429" in str(err) or "RESOURCE_EXHAUSTED" in str(err):
        return 429
    return None


def _retryable(err: Exception) -> bool:
    """Rate limits, server faults and transport hiccups. Nothing else."""
    code = _status_code(err)
    if code is not None:
        return code == 429 or code >= 500
    if isinstance(err, (TimeoutError, ConnectionError, GeminiError)):
        return True
    text = str(err).lower()
    return any(w in text for w in ("timeout", "timed out", "connection", "temporarily"))


def _call(text: str) -> str:
    today = datetime.now(config.TZ).date()
    system = SYSTEM.format(
        categories=", ".join(config.CATEGORIES),
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        tz=config.TZ_NAME,
        default_currency=config.DEFAULT_CURRENCY,
    )
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=SCHEMA,
        temperature=0,
        http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_MS),
        # We hand it no tools; turning AFC off silences the SDK's warning.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    # 1s, 2s, 4s, 8s on rate limits and transport faults; bad requests fail at once.
    delays = [1, 2, 4, 8]
    last: Exception | None = None
    for attempt in range(len(delays) + 1):
        try:
            resp = client().models.generate_content(
                model=config.GEMINI_MODEL, contents=text, config=cfg
            )
            if not resp.text:
                raise GeminiError("empty response")
            return resp.text
        except Exception as err:  # noqa: BLE001 - retry policy depends on the code
            last = err
            if attempt >= len(delays) or not _retryable(err):
                break
            time.sleep(delays[attempt])
    raise GeminiError(f"gemini failed: {last}") from last


def _coerce(payload: dict) -> ParseResult:
    today = datetime.now(config.TZ).date()
    entries: list[Entry] = []

    for item in payload.get("entries") or []:
        try:
            amount = float(item["amount"])
        except (KeyError, TypeError, ValueError):
            continue
        if amount <= 0:
            continue

        # Second gate on the category, in case the schema is ever relaxed.
        category = str(item.get("category", "")).strip().lower()
        if category not in config.CATEGORIES:
            category = config.FALLBACK_CATEGORY

        currency = (str(item.get("currency") or config.DEFAULT_CURRENCY)).strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            currency = config.DEFAULT_CURRENCY

        try:
            on = date.fromisoformat(str(item.get("date", "")))
        except ValueError:
            on = today

        entries.append(
            Entry(
                amount=amount,
                currency=currency,
                category=category,
                description=str(item.get("description") or "").strip()[:200],
                on=on,
            )
        )

    return ParseResult(
        entries=entries,
        needs_clarification=bool(payload.get("needs_clarification")) and not entries,
        clarification=str(payload.get("clarification") or "").strip(),
        raw=payload,
    )


def parse(text: str) -> ParseResult:
    raw = _call(text)
    try:
        payload = json.loads(raw)
    except ValueError as err:
        raise GeminiError(f"non-JSON response: {raw[:200]}") from err
    if not isinstance(payload, dict):
        raise GeminiError(f"unexpected response shape: {raw[:200]}")
    return _coerce(payload)
