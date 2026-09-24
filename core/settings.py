"""Small key/value app settings (spec: Settings page). Values round-trip as
JSON in the `settings` table so any type (int, str, list) works without a
schema change per setting.

Only the transfer-matching window/keywords are wired up here. Spec 9.1's
month-definition setting (custom start day, or payday-to-payday) is
deliberately NOT included yet — every report query (list_available_months,
month_bounds, and everything built on them in reports.py/budgets.py) assumes
plain calendar months, and a custom period boundary would need to change how
"which month is this transaction in" is decided everywhere, not just add a
config value. The spec's own open question about this defaults to "probably
calendar month," and nothing so far has needed it, so it's left as a
clearly-scoped future task rather than half-wired in.
"""

import json

TRANSFER_WINDOW_DAYS_KEY = "transfer_window_days"
TRANSFER_KEYWORDS_KEY = "transfer_keywords"

DEFAULT_TRANSFER_WINDOW_DAYS = 7
DEFAULT_TRANSFER_KEYWORDS = ["PAYMENT", "TRANSFER"]


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()


def get_transfer_window_days(conn) -> int:
    return get_setting(conn, TRANSFER_WINDOW_DAYS_KEY, DEFAULT_TRANSFER_WINDOW_DAYS)


def get_transfer_keywords(conn) -> list[str]:
    return get_setting(conn, TRANSFER_KEYWORDS_KEY, DEFAULT_TRANSFER_KEYWORDS)


def set_transfer_matching(conn, window_days: int, keywords: list[str]) -> None:
    set_setting(conn, TRANSFER_WINDOW_DAYS_KEY, window_days)
    set_setting(conn, TRANSFER_KEYWORDS_KEY, keywords)
