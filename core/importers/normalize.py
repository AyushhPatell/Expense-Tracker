import json
from datetime import datetime


def normalize_row(raw_row: dict, profile: dict) -> dict | None:
    """Convert one raw CSV row into a canonical transaction dict.

    Returns None if the row can't be normalized as a transaction (e.g. a
    trailing summary line with no parseable date) — the caller counts these
    as skipped rather than failing the whole import.
    """
    parse_cfg = profile["parse"]

    try:
        txn_date = _parse_date(raw_row[parse_cfg["date_column"]], parse_cfg["date_format"])
    except (KeyError, ValueError, AttributeError):
        return None

    posted_date = None
    posted_col = parse_cfg.get("posted_date_column")
    if posted_col and raw_row.get(posted_col):
        try:
            posted_date = _parse_date(raw_row[posted_col], parse_cfg["date_format"])
        except (ValueError, AttributeError):
            posted_date = None

    desc_cols = parse_cfg.get("description_columns", [])
    description_parts = [str(raw_row.get(c) or "").strip() for c in desc_cols]
    description = " | ".join(part for part in description_parts if part)

    amount_cents = _resolve_amount(raw_row, parse_cfg["amount"])

    currency_col = parse_cfg.get("currency_column")
    currency = (raw_row.get(currency_col) if currency_col else None) or "CAD"

    return {
        "txn_date": txn_date,
        "posted_date": posted_date,
        "description": description,
        "amount_cents": amount_cents,
        "currency": currency,
        "raw_row": json.dumps(raw_row, ensure_ascii=False),
    }


def _parse_date(value: str, fmt: str) -> str:
    return datetime.strptime(value.strip(), fmt).date().isoformat()


def _resolve_amount(raw_row: dict, amount_cfg: dict) -> int:
    mode = amount_cfg["mode"]
    if mode == "single":
        cents = _parse_amount(raw_row.get(amount_cfg["column"]))
        if amount_cfg.get("invert"):
            cents = -cents
        return cents
    if mode == "debit_credit":
        debit = abs(_parse_amount(raw_row.get(amount_cfg["debit_column"])))
        credit = abs(_parse_amount(raw_row.get(amount_cfg["credit_column"])))
        return credit - debit
    raise ValueError(f"Unknown amount mode: {mode!r}")


def _parse_amount(value) -> int:
    """Parse a money string into integer cents. Handles '$', thousands
    commas, and '(123.45)' parentheses-as-negative notation."""
    if value is None:
        return 0
    s = str(value).strip()
    if s == "":
        return 0

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    s = s.replace("$", "").replace(",", "").strip()

    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if s == "":
        return 0

    cents = round(float(s) * 100)
    return -cents if negative else cents
