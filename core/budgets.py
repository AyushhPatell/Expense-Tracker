"""Budgets (spec section 6): a monthly budget per category, actual-vs-budget
for a period, and optional single-hop rollover of unused budget.

Rollover design: when a category has rollover on, its EFFECTIVE budget for a
period is its own monthly_budget plus whatever was left unused at the end of
the immediately preceding month, floored at 0 — overspending never carries a
*debt* forward, only unused budget carries forward as a bonus. This is a
single hop, not an indefinite running ledger: it looks exactly one month
back, using that month's own plain budget and actual spend (not that
month's own rollover-adjusted total). That keeps the feature bounded and
easy to reason about instead of building a YNAB-style perpetual
carry-forward ledger, which is more machinery than a personal tracker like
this needs.

A category's "actual" here always includes its children's spending too (so
a budget set on a parent like 'Housing' covers everything under it), unlike
core/reports.py's spending_by_category, which rolls children INTO the
parent for the Dashboard chart but doesn't let you ask for one specific
category's own total independent of that display concern.
"""

from collections import defaultdict

from core.reports import month_bounds, spending_by_category_id


def list_category_budgets(conn) -> list[dict]:
    """Every active category, parent and child, flat 'Parent > Child'
    labeled, with its own monthly_budget/rollover — for the Budgets page's
    editable list. Independent of any specific period; a budget is a
    standing setting, not something scoped to one month."""
    rows = conn.execute(
        "SELECT id, name, parent_id, monthly_budget, budget_rollover FROM categories WHERE active = 1 "
        "ORDER BY parent_id IS NOT NULL, name"
    ).fetchall()
    by_id = {r["id"]: r for r in rows}

    def label(row) -> str:
        if row["parent_id"] and row["parent_id"] in by_id:
            return f"{by_id[row['parent_id']]['name']} > {row['name']}"
        return row["name"]

    return [
        {
            "id": r["id"],
            "label": label(r),
            "monthly_budget": r["monthly_budget"],
            "rollover": bool(r["budget_rollover"]),
        }
        for r in rows
    ]


def set_category_budget(conn, category_id: int, monthly_budget: int | None, rollover: bool) -> None:
    conn.execute(
        "UPDATE categories SET monthly_budget = ?, budget_rollover = ? WHERE id = ?",
        (monthly_budget, 1 if rollover else 0, category_id),
    )
    conn.commit()


def _actuals_by_category(conn, start_date: str, end_date: str) -> dict[int, int]:
    """Actual spend per category id, WITH each child's spend folded into its
    parent (a parent-level budget should cover its children automatically).
    There's only one level of nesting in this schema, so this is a single
    pass, not a recursive tree walk."""
    direct = spending_by_category_id(conn, start_date, end_date)
    rows = conn.execute("SELECT id, parent_id FROM categories WHERE active = 1").fetchall()
    children_of: dict = defaultdict(list)
    for r in rows:
        if r["parent_id"]:
            children_of[r["parent_id"]].append(r["id"])

    totals = {}
    for r in rows:
        total = direct.get(r["id"], 0)
        for child_id in children_of.get(r["id"], []):
            total += direct.get(child_id, 0)
        totals[r["id"]] = total
    return totals


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _rollover_amount(conn, category_id: int, year: int, month: int, monthly_budget: int) -> int:
    prev_year, prev_month = _prev_month(year, month)
    prev_start, prev_end = month_bounds(prev_year, prev_month)
    prev_spent = -_actuals_by_category(conn, prev_start, prev_end).get(category_id, 0)
    return max(0, monthly_budget - prev_spent)


def budgets_for_period(conn, year: int, month: int) -> list[dict]:
    """Budget vs actual for every category that has a budget set (spec:
    'Monthly budget per category, actual vs budget with progress bars,
    over-budget highlighting'). Categories with no budget assigned are
    simply left out — nothing to track against."""
    start, end = month_bounds(year, month)
    actuals = _actuals_by_category(conn, start, end)

    result = []
    for row in list_category_budgets(conn):
        if row["monthly_budget"] is None:
            continue
        effective_budget = row["monthly_budget"]
        if row["rollover"]:
            effective_budget += _rollover_amount(conn, row["id"], year, month, row["monthly_budget"])

        spent = -actuals.get(row["id"], 0)
        result.append(
            {
                "category_id": row["id"],
                "category": row["label"],
                "budget": row["monthly_budget"],
                "effective_budget": effective_budget,
                "rollover": row["rollover"],
                "spent": spent,
                "remaining": effective_budget - spent,
                "pct_used": (spent / effective_budget) if effective_budget > 0 else None,
                "over_budget": spent > effective_budget,
            }
        )
    return sorted(result, key=lambda r: r["category"])
