"""Heuristics for the self-service 'Add a bank profile' wizard: a sensible
starting guess for the date column/format, the amount column(s), and the
description column(s) from a handful of sample rows — so setting up a new
bank is mostly 'confirm this looks right and save' instead of filling in
every field from a blank form.

Every guess is just a pre-filled, fully editable starting point the wizard
shows with a live preview — nothing here is ever applied silently. A wrong
guess costs the user one click to fix, not a bad import.
"""

from datetime import datetime

COMMON_DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%d/%m/%y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%d-%m-%Y",
]

_DEBIT_HINTS = ("debit", "withdrawal", "withdraw", "out")
_CREDIT_HINTS = ("credit", "deposit", "in")
_DESCRIPTION_HINTS = ("description", "merchant", "payee", "memo", "narrative", "details", "name")


def _sample_values(rows: list[dict], column: str, limit: int = 20) -> list[str]:
    values = []
    for row in rows:
        v = (row.get(column) or "").strip()
        if v:
            values.append(v)
        if len(values) >= limit:
            break
    return values


def guess_date_column(rows: list[dict], headers: list[str]) -> tuple[str | None, str | None]:
    """The (column, format) pair where EVERY sampled value parses cleanly —
    a partial match isn't trusted, since that's more likely a coincidence
    (e.g. a reference-number column that happens to look like a date once)
    than the real date column."""
    for col in headers:
        values = _sample_values(rows, col)
        if not values:
            continue
        for fmt in COMMON_DATE_FORMATS:
            if all(_parses(v, fmt) for v in values):
                return col, fmt
    return None, None


def _parses(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _looks_numeric(value: str) -> bool:
    s = value.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").strip()
    if s.startswith(("-", "+")):
        s = s[1:]
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def guess_amount_config(rows: list[dict], headers: list[str], exclude: set) -> dict:
    """Guesses single-column vs. separate-debit/credit-column mode.
    `exclude` keeps already-claimed columns (the date column) from ever
    being mistaken for an amount column."""
    numeric_cols = []
    for col in headers:
        if col in exclude:
            continue
        values = _sample_values(rows, col)
        if values and all(_looks_numeric(v) for v in values):
            numeric_cols.append(col)

    debit_col = next((c for c in numeric_cols if any(h in c.lower() for h in _DEBIT_HINTS)), None)
    credit_col = next((c for c in numeric_cols if any(h in c.lower() for h in _CREDIT_HINTS)), None)
    if debit_col and credit_col and debit_col != credit_col:
        return {"mode": "debit_credit", "debit_column": debit_col, "credit_column": credit_col}

    single_col = next((c for c in numeric_cols if "amount" in c.lower()), None) or (
        numeric_cols[0] if numeric_cols else None
    )
    return {"mode": "single", "column": single_col, "invert": False}


def guess_description_columns(headers: list[str], claimed: set) -> list[str]:
    return [h for h in headers if h not in claimed and any(hint in h.lower() for hint in _DESCRIPTION_HINTS)]
