"""
Money helpers.

Inside the app every amount is a whole number of KOBO (1 naira = 100 kobo).
Whole numbers never suffer the tiny rounding errors that decimals/floats can
(0.1 + 0.2 = 0.30000000000000004), so totals always add up exactly.
The database still stores normal naira amounts as NUMERIC(18,2).
"""
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

CURRENCY_SYMBOL = "\u20a6"  # naira sign


def to_kobo(value) -> int:
    """Convert a naira amount (float / str / Decimal / None) to whole kobo."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(d * 100)


def kobo_to_db(kobo: int, is_postgres: bool):
    """Value to send to the database for a kobo amount."""
    if is_postgres:
        return Decimal(int(kobo)).scaleb(-2)
    return int(kobo) / 100  # SQLite has no Decimal type


def series_to_kobo(series: pd.Series) -> pd.Series:
    """Convert a DataFrame column coming from the database into int64 kobo."""
    as_float = pd.to_numeric(series, errors="coerce").fillna(0).astype(float)
    return (as_float * 100).round().astype("int64")


def kobo_to_naira(kobo) -> float:
    return int(kobo) / 100


def fmt(kobo, symbol: bool = True) -> str:
    """Format kobo as '₦1,234.56' (negative numbers as '-₦1,234.56')."""
    k = int(kobo)
    sign = "-" if k < 0 else ""
    text = f"{abs(k) / 100:,.2f}"
    return f"{sign}{CURRENCY_SYMBOL if symbol else ''}{text}"
