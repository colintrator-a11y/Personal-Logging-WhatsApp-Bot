"""Command routing and the log-an-expense flow. Nothing here talks to WhatsApp."""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime

from app import config, fmt_helpers as fmt, gemini, sheets, store

HELP = (
    "Send an expense: `almuerzo 1200`, `taxi 3500`, `¥480 coffee`, "
    "`food 40 and parking 12`.\n"
    "Relative dates work: `taxi 900 yesterday`.\n\n"
    "*undo* — remove the last thing logged\n"
    "*total* — this month\n"
    "*total food* — this month, one category\n"
    f"Categories: {', '.join(config.CATEGORIES)}"
)

# A reply to "how much?" — just a number, maybe with a symbol or currency code.
BARE_AMOUNT = re.compile(r"^[¥$€£₩₹]?\s*[\d][\d.,]*\s*[¥$€£₩₹]?\s*[a-zA-Z]{0,3}$")


async def handle(text: str, chat: str) -> str | None:
    """Returns the reply to send, or None to stay silent."""
    stripped = text.strip()
    lowered = stripped.lower()

    if lowered in ("help", "?", "ayuda", "ヘルプ"):
        store.take_pending(chat)
        return HELP

    if lowered == "undo":
        store.take_pending(chat)
        return await _undo()

    if lowered == "total" or lowered.startswith("total "):
        store.take_pending(chat)
        return await _total(stripped[5:].strip().lower() or None)

    return await _log(stripped, chat)


# ---- logging ---------------------------------------------------------------


async def _log(text: str, chat: str) -> str | None:
    # If we just asked "how much?" and this is only a number, glue them together
    # so the follow-up carries the original description.
    to_parse = text
    pending = store.peek_pending(chat)
    if pending and BARE_AMOUNT.match(text):
        to_parse = f"{pending} {text}"
        store.take_pending(chat)

    store.log("incoming", chat=chat, text=text, parsed_as=to_parse)

    try:
        result = await asyncio.to_thread(gemini.parse, to_parse)
    except gemini.GeminiError as err:
        store.log("parse_error", chat=chat, text=to_parse, error=str(err))
        return "⚠️ Could not reach the parser. Nothing was logged — send it again."

    store.log("parsed", chat=chat, text=to_parse, result=result.raw)

    if result.needs_clarification:
        store.set_pending(chat, to_parse)
        return f"❓ {result.clarification or 'How much was that?'}"

    if not result.entries:
        store.take_pending(chat)
        return "🤔 No expense found in that. Send `help` to see the format."

    store.take_pending(chat)

    now = datetime.now(config.TZ)
    rows = [
        [
            now.isoformat(timespec="seconds"),
            e.on.isoformat(),
            e.amount,
            e.currency,
            e.category,
            e.description,
            to_parse,
        ]
        for e in result.entries
    ]

    try:
        await asyncio.to_thread(sheets.append_with_retry, rows)
    except Exception as err:  # noqa: BLE001 - queue anything the sheet rejects
        store.enqueue(rows)
        store.log("write_failed", chat=chat, error=str(err), rows=rows)
        # No checkmark: the row is not in the sheet yet.
        return (
            "⚠️ Sheet write failed. Saved locally and queued — it will go in "
            f"automatically once Sheets responds ({store.queue_depth()} waiting)."
        )

    store.remember_write(len(rows))
    store.log("written", chat=chat, rows=rows)

    lines = [
        f"✅ {fmt.money(e.amount, e.currency)} · {e.category} · {fmt.day(e.on)}"
        for e in result.entries
    ]
    return "\n".join(lines)


# ---- undo ------------------------------------------------------------------


async def _undo() -> str:
    count = store.last_write_count()
    if count <= 0:
        return "Nothing to undo."
    try:
        rows = await asyncio.to_thread(sheets.read_rows)
        doomed = rows[-count:] if count <= len(rows) else rows
        removed = await asyncio.to_thread(sheets.delete_last, count)
    except Exception as err:  # noqa: BLE001
        store.log("undo_failed", error=str(err))
        return "⚠️ Could not reach the sheet. Nothing was removed — try `undo` again."

    store.clear_write()
    if removed == 0:
        return "Nothing to undo."
    store.log("undone", rows=doomed)

    detail = []
    for row in doomed[:5]:
        try:
            detail.append(f"{fmt.money(float(row[2]), str(row[3]))} · {row[4]}")
        except (IndexError, TypeError, ValueError):
            continue
    suffix = ("\n" + "\n".join(detail)) if detail else ""
    noun = "row" if removed == 1 else "rows"
    return f"↩️ Removed {removed} {noun}.{suffix}"


# ---- totals ----------------------------------------------------------------


async def _total(category: str | None) -> str:
    if category and category not in config.CATEGORIES:
        return (
            f"Unknown category {category!r}.\n"
            f"Pick one of: {', '.join(config.CATEGORIES)}"
        )

    try:
        rows = await asyncio.to_thread(sheets.read_rows)
    except Exception as err:  # noqa: BLE001
        store.log("total_failed", error=str(err))
        return "⚠️ Could not read the sheet. Try again in a moment."

    now = datetime.now(config.TZ)

    by_currency: dict[str, float] = defaultdict(float)
    by_category: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    count = 0

    for row in rows:
        # date | amount | currency | category live in columns B..E
        if len(row) < 5:
            continue
        on = sheets.to_date(row[1])
        if on is None or (on.year, on.month) != (now.year, now.month):
            continue
        try:
            amount = float(row[2])
        except (TypeError, ValueError):
            continue
        cur = str(row[3]).upper()
        cat = str(row[4]).lower()
        if category and cat != category:
            continue
        by_currency[cur] += amount
        by_category[cat][cur] += amount
        count += 1

    header = f"📊 {now.strftime('%b %Y')}"
    if category:
        header += f" · {category}"
    if not count:
        return f"{header}\nNothing logged yet."

    entries = "entry" if count == 1 else "entries"
    out = [f"{header}\n*{fmt.totals(by_currency)}* · {count} {entries}"]

    if not category:
        ranked = sorted(
            by_category.items(), key=lambda kv: -sum(kv[1].values())
        )
        out += [f"{cat} — {fmt.totals(sums)}" for cat, sums in ranked]

    return "\n".join(out)
