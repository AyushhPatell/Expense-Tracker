"""Monthly reporting: spending by category, income, Splitwise sent/received,
top merchants, account breakdown, and coverage completeness warnings.

Calendar-month periods only for now — the custom start-day / payday-to-payday
variants from spec 9.1 are Settings-page work (Phase 7), not built yet.
"""

import calendar
from collections import defaultdict
from datetime import date

from core.coverage import get_coverage

SPENDING_TYPES = ("expense", "unreviewed", "refund")


def month_bounds(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start.isoformat(), end.isoformat()


def list_available_months(conn) -> list[tuple[int, int]]:
    """Distinct (year, month) pairs with at least one transaction, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT substr(txn_date, 1, 7) AS ym FROM transactions ORDER BY ym DESC"
    ).fetchall()
    return [(int(r["ym"][:4]), int(r["ym"][5:7])) for r in rows]


def _spending_lines(conn, start_date: str, end_date: str) -> list:
    """Every 'my spending' line for the period: category-only split lines
    (never a receivable or Splitwise line) from expense/unreviewed/refund
    transactions, plus an implicit whole-amount line for any such
    transaction that has no splits at all (spec 5.3). Transfers, ignored
    rows, income, and reimbursements never appear here."""
    placeholders = ",".join("?" * len(SPENDING_TYPES))
    return conn.execute(
        f"""
        SELECT t.id AS txn_id, t.txn_date, t.description, t.account_id, a.name AS account_name,
               COALESCE(parent.name, cat.name) AS category, s.amount AS amount
        FROM splits s
        JOIN transactions t ON t.id = s.transaction_id
        JOIN accounts a ON a.id = t.account_id
        JOIN categories cat ON cat.id = s.category_id
        LEFT JOIN categories parent ON parent.id = cat.parent_id
        WHERE t.txn_date BETWEEN ? AND ?
          AND t.type IN ({placeholders})
          AND s.person_id IS NULL AND s.is_splitwise = 0

        UNION ALL

        SELECT t.id AS txn_id, t.txn_date, t.description, t.account_id, a.name AS account_name,
               NULL AS category, t.amount AS amount
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.txn_date BETWEEN ? AND ?
          AND t.type IN ({placeholders})
          AND NOT EXISTS (SELECT 1 FROM splits s2 WHERE s2.transaction_id = t.id)
        """,
        (start_date, end_date, *SPENDING_TYPES, start_date, end_date, *SPENDING_TYPES),
    ).fetchall()


def total_spending(conn, start_date: str, end_date: str) -> int:
    return sum(r["amount"] for r in _spending_lines(conn, start_date, end_date))


def total_income(conn, start_date: str, end_date: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE txn_date BETWEEN ? AND ? AND type = 'income'",
        (start_date, end_date),
    ).fetchone()
    return row["total"]


def spending_by_category(conn, start_date: str, end_date: str) -> list[dict]:
    totals: dict = defaultdict(int)
    for r in _spending_lines(conn, start_date, end_date):
        totals[r["category"] or "Uncategorized"] += r["amount"]
    return sorted(
        [{"category": k, "amount": v} for k, v in totals.items()],
        key=lambda x: x["amount"],
    )


def spending_lines_for_category(conn, start_date: str, end_date: str, category: str) -> list[dict]:
    """The individual lines behind one bar in spending_by_category — what's
    actually in that total, for a 'what did I spend it on' drill-down."""
    rows = _spending_lines(conn, start_date, end_date)
    matches = [r for r in rows if (r["category"] or "Uncategorized") == category]
    return sorted(
        [
            {
                "txn_date": r["txn_date"],
                "description": r["description"],
                "account": r["account_name"],
                "amount": r["amount"],
            }
            for r in matches
        ],
        key=lambda r: r["amount"],
    )


def transfer_by_category(conn, start_date: str, end_date: str) -> list[dict]:
    """Transfer-typed transactions that still carry a category — money
    that's excluded from spending (type='transfer' always wins, spec 5.3)
    but still worth tracking as its own line: investments, tuition, or
    anything similarly large/irregular you don't want skewing 'my spending'
    but do want to see a real number for (spec 8.1a). Only categorized
    transfer lines are returned — an untagged transfer (e.g. a plain credit
    card payment) has nothing meaningful to show here."""
    rows = conn.execute(
        """
        SELECT COALESCE(parent.name, cat.name) AS category, SUM(s.amount) AS total
        FROM splits s
        JOIN transactions t ON t.id = s.transaction_id
        JOIN categories cat ON cat.id = s.category_id
        LEFT JOIN categories parent ON parent.id = cat.parent_id
        WHERE t.txn_date BETWEEN ? AND ?
          AND t.type = 'transfer'
          AND s.person_id IS NULL AND s.is_splitwise = 0
        GROUP BY category
        HAVING SUM(s.amount) != 0
        ORDER BY total ASC
        """,
        (start_date, end_date),
    ).fetchall()
    return [{"category": r["category"], "amount": r["total"]} for r in rows]


def spending_by_account(conn, start_date: str, end_date: str) -> list[dict]:
    totals: dict = defaultdict(int)
    for r in _spending_lines(conn, start_date, end_date):
        totals[r["account_name"]] += r["amount"]
    return sorted(
        [{"account": k, "amount": v} for k, v in totals.items()],
        key=lambda x: x["amount"],
    )


def top_merchants(conn, start_date: str, end_date: str, limit: int = 10) -> list[dict]:
    totals: dict = defaultdict(int)
    counts: dict = defaultdict(int)
    for r in _spending_lines(conn, start_date, end_date):
        totals[r["description"]] += r["amount"]
        counts[r["description"]] += 1
    merchants = sorted(totals.items(), key=lambda kv: kv[1])[:limit]
    return [{"description": d, "amount": t, "count": counts[d]} for d, t in merchants]


def splitwise_totals(conn, start_date: str, end_date: str) -> dict:
    rows = conn.execute(
        "SELECT s.amount FROM splits s JOIN transactions t ON t.id = s.transaction_id "
        "WHERE t.txn_date BETWEEN ? AND ? AND s.is_splitwise = 1",
        (start_date, end_date),
    ).fetchall()
    sent = sum(r["amount"] for r in rows if r["amount"] < 0)
    received = sum(r["amount"] for r in rows if r["amount"] > 0)
    return {"sent": sent, "received": received}


def _period_covered(coverage: list[tuple[str, str]], start_date: str, end_date: str) -> bool:
    cursor = start_date
    for c_start, c_end in coverage:
        if c_start > cursor:
            return False
        if c_end >= end_date:
            return True
        cursor = max(cursor, c_end)
    return False


def coverage_warnings(conn, start_date: str, end_date: str) -> list[str]:
    """One warning per active account whose imported coverage doesn't fully
    span the period, so numbers are never silently partial (spec 9.2)."""
    warnings = []
    accounts = conn.execute("SELECT id, name FROM accounts WHERE active = 1 ORDER BY name").fetchall()
    for acc in accounts:
        coverage = get_coverage(conn, acc["id"])
        if _period_covered(coverage, start_date, end_date):
            continue
        if not coverage:
            warnings.append(f"{acc['name']} has no imported data at all.")
            continue
        latest_end = max(c[1] for c in coverage)
        earliest_start = min(c[0] for c in coverage)
        if latest_end < end_date:
            warnings.append(f"{acc['name']} has no data after {latest_end}. This period may be partial.")
        elif earliest_start > start_date:
            warnings.append(f"{acc['name']} has no data before {earliest_start}. This period may be partial.")
        else:
            warnings.append(f"{acc['name']}'s coverage has a gap within this period.")
    return warnings
