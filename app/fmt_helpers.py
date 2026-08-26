"""Reply text. Kept apart from logic so wording is easy to tweak."""
from __future__ import annotations

from datetime import date

SYMBOLS = {"JPY": "¥", "USD": "$", "EUR": "€", "GBP": "£", "KRW": "₩", "INR": "₹"}
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "COP", "PYG"}


def money(amount: float, currency: str) -> str:
    currency = currency.upper()
    if currency in ZERO_DECIMAL:
        body = f"{round(amount):,}"
    else:
        body = f"{amount:,.2f}"
    symbol = SYMBOLS.get(currency)
    return f"{symbol}{body}" if symbol else f"{body} {currency}"


def day(on: date) -> str:
    return f"{on.day} {on.strftime('%b')}"


def totals(sums: dict[str, float]) -> str:
    """Sums keyed by currency — no conversion, so each stands on its own."""
    if not sums:
        return "0"
    return " + ".join(money(v, k) for k, v in sorted(sums.items()))
