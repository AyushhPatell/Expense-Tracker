from core.budgets import budgets_for_period, list_category_budgets, set_category_budget
from core.splits import replace_splits
from tests.conftest import account_id


def _category_id(conn, name, parent_name=None):
    if parent_name:
        row = conn.execute(
            "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id "
            "WHERE c.name = ? AND p.name = ?",
            (name, parent_name),
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM categories WHERE name = ? AND parent_id IS NULL", (name,)).fetchone()
    return row["id"]


def _insert_expense(conn, account_id_, txn_date, description, amount_cents, category_id):
    batch = conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, 'manual.csv', datetime('now'), 1, 1, 0)",
        (account_id_,),
    )
    cur = conn.execute(
        "INSERT INTO transactions "
        "(account_id, batch_id, txn_date, description, amount, raw_row, fingerprint, type) "
        "VALUES (?, ?, ?, ?, ?, '{}', ?, 'expense')",
        (account_id_, batch.lastrowid, txn_date, description, amount_cents, f"fp-{txn_date}-{description}-{amount_cents}"),
    )
    txn_id = cur.lastrowid
    conn.commit()
    replace_splits(conn, txn_id, [{"amount": amount_cents, "category_id": category_id}])
    return txn_id


def test_list_category_budgets_defaults_to_unset(db_conn):
    budgets = list_category_budgets(db_conn)
    assert len(budgets) > 0
    assert all(b["monthly_budget"] is None and b["rollover"] is False for b in budgets)
    coffee = next(b for b in budgets if b["label"] == "Coffee")
    housing_rent = next(b for b in budgets if b["label"] == "Housing > Rent")
    assert coffee and housing_rent  # parent-labeled correctly


def test_set_category_budget_persists(db_conn):
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 10000, rollover=True)
    row = next(b for b in list_category_budgets(db_conn) if b["id"] == coffee_cat)
    assert row["monthly_budget"] == 10000
    assert row["rollover"] is True


def test_budgets_for_period_excludes_unbudgeted_categories(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    _insert_expense(db_conn, scotia, "2025-08-05", "Fake Coffee Shop", -450, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    assert result == []  # no budget set on Coffee, so it's not tracked yet


def test_budgets_for_period_computes_spent_and_remaining(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 5000, rollover=False)  # $50 budget
    _insert_expense(db_conn, scotia, "2025-08-05", "Fake Coffee Shop", -450, coffee_cat)
    _insert_expense(db_conn, scotia, "2025-08-10", "Fake Coffee Shop", -350, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    assert len(result) == 1
    coffee = result[0]
    assert coffee["budget"] == 5000
    assert coffee["effective_budget"] == 5000
    assert coffee["spent"] == 800
    assert coffee["remaining"] == 4200
    assert coffee["over_budget"] is False


def test_budgets_for_period_flags_over_budget(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 1000, rollover=False)  # $10 budget
    _insert_expense(db_conn, scotia, "2025-08-05", "Fake Coffee Shop", -1500, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    assert result[0]["spent"] == 1500
    assert result[0]["remaining"] == -500
    assert result[0]["over_budget"] is True


def test_budget_on_parent_category_includes_children(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    housing_cat = _category_id(db_conn, "Housing")
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    utilities_cat = _category_id(db_conn, "Utilities", parent_name="Housing")
    set_category_budget(db_conn, housing_cat, 100000, rollover=False)  # $1000 for all of Housing
    _insert_expense(db_conn, scotia, "2025-08-01", "Rent payment", -80000, rent_cat)
    _insert_expense(db_conn, scotia, "2025-08-05", "Power bill", -12000, utilities_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    assert len(result) == 1
    assert result[0]["category"] == "Housing"
    assert result[0]["spent"] == 92000  # rent + utilities rolled into the parent's budget


def test_rollover_carries_unused_budget_from_previous_month(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 5000, rollover=True)  # $50/mo, rollover on
    # July: only spent $20 of the $50 budget -> $30 unused should roll into August.
    _insert_expense(db_conn, scotia, "2025-07-10", "Fake Coffee Shop", -2000, coffee_cat)
    _insert_expense(db_conn, scotia, "2025-08-05", "Fake Coffee Shop", -1000, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    coffee = result[0]
    assert coffee["effective_budget"] == 8000  # 5000 base + 3000 rolled over
    assert coffee["spent"] == 1000
    assert coffee["remaining"] == 7000


def test_rollover_does_not_carry_a_debt_from_overspending(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 5000, rollover=True)
    # July: overspent by $20 -> nothing carries forward (never a negative rollover).
    _insert_expense(db_conn, scotia, "2025-07-10", "Fake Coffee Shop", -7000, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    coffee = result[0]
    assert coffee["effective_budget"] == 5000  # no bonus, but no penalty either


def test_rollover_is_single_hop_not_compounding(db_conn):
    """Rollover only looks one month back — a big underspend two months ago
    shouldn't keep compounding forward forever."""
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 5000, rollover=True)
    # June: barely spent anything (huge leftover).
    _insert_expense(db_conn, scotia, "2025-06-01", "Fake Coffee Shop", -100, coffee_cat)
    # July: spent the full budget, nothing left over.
    _insert_expense(db_conn, scotia, "2025-07-01", "Fake Coffee Shop", -5000, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 8)
    # August's rollover is based only on July (fully spent -> 0 carried),
    # not on June's leftover from two months back.
    assert result[0]["effective_budget"] == 5000


def test_rollover_across_a_year_boundary(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    set_category_budget(db_conn, coffee_cat, 5000, rollover=True)
    _insert_expense(db_conn, scotia, "2024-12-01", "Fake Coffee Shop", -2000, coffee_cat)

    result = budgets_for_period(db_conn, 2025, 1)
    assert result[0]["effective_budget"] == 8000  # Dec's $30 leftover rolls into Jan
