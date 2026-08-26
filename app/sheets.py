"""Google Sheets access via a service account. Blocking; callers use to_thread."""
from __future__ import annotations

import time
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


def append(rows: list[list[Any]]) -> None:
    """One API call for however many rows the message produced."""
    if not rows:
        return
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
