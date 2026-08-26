"""Google Sheets access via a service account. Blocking; callers use to_thread."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_svc = None
_sheet_gid: int | None = None


class SheetsError(RuntimeError):
    pass


def service():
    global _svc
    if _svc is None:
        creds = Credentials.from_service_account_info(
            {
                "type": "service_account",
                "client_email": config.SERVICE_ACCOUNT_EMAIL,
                "private_key": config.PRIVATE_KEY,
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            scopes=SCOPES,
        )
        _svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _svc


def _values():
    return service().spreadsheets().values()


def gid() -> int:
    """Numeric id of the target tab, needed to delete rows."""
    global _sheet_gid
    if _sheet_gid is None:
        meta = (
            service()
            .spreadsheets()
            .get(spreadsheetId=config.SHEET_ID, fields="sheets(properties(sheetId,title))")
            .execute()
        )
        for sheet in meta.get("sheets", []):
            props = sheet["properties"]
            if props["title"] == config.SHEET_NAME:
                _sheet_gid = int(props["sheetId"])
                break
        else:
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            raise SheetsError(f"no tab named {config.SHEET_NAME!r}; found {titles}")
    return _sheet_gid


def ensure_headers() -> None:
    got = (
        _values()
        .get(spreadsheetId=config.SHEET_ID, range=f"{config.SHEET_NAME}!A1:G1")
        .execute()
        .get("values", [])
    )
    if got and got[0]:
        return
    _values().update(
        spreadsheetId=config.SHEET_ID,
        range=f"{config.SHEET_NAME}!A1:G1",
        valueInputOption="RAW",
        body={"values": [config.HEADERS]},
    ).execute()


# The cells hold real date/datetime values; these control only how they read.
TIMESTAMP_FORMAT = "mm/dd/yyyy hh:mm:ss"
DATE_FORMAT = "mm/dd/yyyy"


def ensure_formats() -> None:
    """Display both date columns as mm/dd/yyyy. Safe to run on every start."""
    sheet_id = gid()

    def col(index: int, pattern: str) -> dict:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,  # leave the header alone
                    "startColumnIndex": index,
                    "endColumnIndex": index + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "DATE_TIME" if index == 0 else "DATE",
                                         "pattern": pattern}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }

    service().spreadsheets().batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={"requests": [col(0, TIMESTAMP_FORMAT), col(1, DATE_FORMAT)]},
    ).execute()


def append(rows: list[list[Any]]) -> None:
    """One API call for however many rows the message produced."""
    if not rows:
        return
    invalidate_categories()
    _values().append(
        spreadsheetId=config.SHEET_ID,
        range=f"{config.SHEET_NAME}!A:G",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def append_with_retry(rows: list[list[Any]], attempts: int = 3) -> None:
    """Three tries with backoff. Raises so the caller can queue instead."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            append(rows)
            return
        except Exception as err:  # noqa: BLE001 - any failure is retryable here
            last = err
            if i < attempts - 1:
                time.sleep(2**i)
    raise SheetsError(f"sheets append failed after {attempts} tries: {last}") from last


# A date cell written with USER_ENTERED comes back as a serial number, not the
# "2026-08-26" we sent. Everything that reads the date column goes through here.
SHEETS_EPOCH = date(1899, 12, 30)
_TEXT_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y")


def to_date(value: Any) -> date | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return SHEETS_EPOCH + timedelta(days=int(value))
        except (OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _TEXT_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_rows() -> list[list[Any]]:
    """Data rows only, header stripped, numbers as numbers."""
    got = (
        _values()
        .get(
            spreadsheetId=config.SHEET_ID,
            range=f"{config.SHEET_NAME}!A2:G",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
        .get("values", [])
    )
    return [row for row in got if row and any(str(c).strip() for c in row)]


# ---- categories learned from the sheet -------------------------------------

_cat_cache: tuple[float, list[str]] | None = None


def invalidate_categories() -> None:
    global _cat_cache
    _cat_cache = None


def known_categories(force: bool = False) -> list[str]:
    """Distinct categories already in column E, most-used first.

    Falls back to the seed list while the sheet is empty, and on any read error
    so a Sheets hiccup cannot strip the model of its vocabulary mid-message.
    """
    global _cat_cache
    now = time.monotonic()
    if not force and _cat_cache and now - _cat_cache[0] < config.CATEGORY_CACHE_SECONDS:
        return _cat_cache[1]

    try:
        got = (
            _values()
            .get(spreadsheetId=config.SHEET_ID, range=f"{config.SHEET_NAME}!E2:E")
            .execute()
            .get("values", [])
        )
    except Exception:  # noqa: BLE001 - keep parsing with whatever we had
        return _cat_cache[1] if _cat_cache else list(config.SEED_CATEGORIES)

    counts: dict[str, int] = {}
    for row in got:
        if not row:
            continue
        name = str(row[0]).strip().lower()
        if name:
            counts[name] = counts.get(name, 0) + 1

    cats = sorted(counts, key=lambda c: (-counts[c], c)) or list(config.SEED_CATEGORIES)
    _cat_cache = (now, cats)
    return cats


# ---- wholesale delete ------------------------------------------------------


def clear_all_rows() -> int:
    """Delete every data row, keeping the header. Returns how many went."""
    total = row_count()
    if total <= 1:
        return 0
    service().spreadsheets().batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": gid(),
                            "dimension": "ROWS",
                            "startIndex": 1,  # 0-based: row 2
                            "endIndex": total,
                        }
                    }
                }
            ]
        },
    ).execute()
    invalidate_categories()
    return total - 1


def row_count() -> int:
    got = (
        _values()
        .get(spreadsheetId=config.SHEET_ID, range=f"{config.SHEET_NAME}!A:A")
        .execute()
        .get("values", [])
    )
    return len(got)


def delete_last(n: int) -> int:
    """Drop the last n data rows. Returns how many were actually removed."""
    total = row_count()
    data_rows = max(total - 1, 0)  # row 1 is the header
    n = min(n, data_rows)
    if n <= 0:
        return 0
    start = total - n  # zero-based, inclusive
    service().spreadsheets().batchUpdate(
        spreadsheetId=config.SHEET_ID,
        body={
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": gid(),
                            "dimension": "ROWS",
                            "startIndex": start,
                            "endIndex": total,
                        }
                    }
                }
            ]
        },
    ).execute()
    return n
