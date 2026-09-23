from core.importers import import_file
from core.reports import (
    coverage_warnings,
    list_available_months,
    month_bounds,
    spending_by_account,
    spending_by_category,
    splitwise_totals,
    top_merchants,
    total_income,
    total_spending,
    transfer_by_category,
)
from core.splits import replace_splits, set_type
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


def _person_id(conn, name):
    conn.execute("INSERT OR IGNORE INTO people (name) VALUES (?)", (name,))
    conn.commit()
    return conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()["id"]


def _setup_august_scenario(conn, fixtures_dir):
    """Scotia August fixture, reviewed and categorized like a real month:
    coffee purchases, a fee, a reimbursed rent split, a transfer, income."""
    acc = account_id(conn, "Scotia Chequing")
    import_file(conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())

    coffee_cat = _category_id(conn, "Coffee")
    rent_cat = _category_id(conn, "Rent", parent_name="Housing")
    fees_cat = _category_id(conn, "Fees & Interest")
    roommate = _person_id(conn, "Roommate A")

    for row in conn.execute("SELECT id, amount, description FROM transactions"):
        if "COFFEE" in row["description"].upper():
            replace_splits(conn, row["id"], [{"amount": row["amount"], "category_id": coffee_cat}])
            set_type(conn, row["id"], "expense")
        elif "OVERDRAFT" in row["description"].upper():
            replace_splits(conn, row["id"], [{"amount": row["amount"], "category_id": fees_cat}])
            set_type(conn, row["id"], "expense")
        elif "PAYROLL" in row["description"].upper():
            set_type(conn, row["id"], "income")
        elif "ROGERS" in row["description"].upper():
            set_type(conn, row["id"], "transfer")
        elif row["amount"] == -105300:  # rent
            replace_splits(
                conn,
                row["id"],
                [
                    {"amount": -35100, "category_id": rent_cat},
                    {"amount": -35100, "person_id": roommate},
                    {"amount": -35100, "person_id": roommate},
                ],
            )
            set_type(conn, row["id"], "expense")
        elif row["amount"] == 35100:  # incoming e-transfer reimbursing rent
            set_type(conn, row["id"], "reimbursement")

    return acc


def test_month_bounds():
    assert month_bounds(2025, 8) == ("2025-08-01", "2025-08-31")
    assert month_bounds(2024, 2) == ("2024-02-01", "2024-02-29")  # leap year


def test_list_available_months(db_conn, fixtures_dir):
    _setup_august_scenario(db_conn, fixtures_dir)
    months = list_available_months(db_conn)
    assert (2025, 8) in months


def test_total_spending_excludes_transfers_receivables_and_income(db_conn, fixtures_dir):
    _setup_august_scenario(db_conn, fixtures_dir)
    start, end = month_bounds(2025, 8)

    spending = total_spending(db_conn, start, end)
    # Coffee: -4.50 -3.50 -3.50 = -11.50; Rent (my share only): -351.00; Fees: -0.01
    assert spending == -1150 - 35100 - 1


def test_receivable_tagged_with_a_category_still_excluded_from_spending(db_conn, fixtures_dir):
    """A receivable line can carry a category as a plain descriptive tag
    (e.g. 'this $351 from Kavan was for Rent') without leaking into spending —
    person_id alone governs the exclusion, category presence doesn't."""
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    kavan = _person_id(db_conn, "Kavan")
    reimbursement = db_conn.execute("SELECT id FROM transactions WHERE amount = 35100").fetchone()
    replace_splits(db_conn, reimbursement["id"], [{"amount": 35100, "category_id": rent_cat, "person_id": kavan}])
    set_type(db_conn, reimbursement["id"], "reimbursement")

    start, end = month_bounds(2025, 8)
    by_cat = {r["category"]: r["amount"] for r in spending_by_category(db_conn, start, end)}
    assert "Housing" not in by_cat  # the tagged-but-receivable line must not show up as spending


def test_total_income_excludes_reimbursement(db_conn, fixtures_dir):
    _setup_august_scenario(db_conn, fixtures_dir)
    start, end = month_bounds(2025, 8)
    assert total_income(db_conn, start, end) == 220000  # payroll only, not the $351 reimbursement


def test_spending_by_category_rolls_up_to_parent(db_conn, fixtures_dir):
    _setup_august_scenario(db_conn, fixtures_dir)
    start, end = month_bounds(2025, 8)
    by_cat = {r["category"]: r["amount"] for r in spending_by_category(db_conn, start, end)}
    assert by_cat["Coffee"] == -1150
    assert by_cat["Housing"] == -35100  # Rent rolls up to its parent
    assert by_cat["Fees & Interest"] == -1
    assert "Uncategorized" not in by_cat  # bill payment (transfer) shouldn't leak in here


def test_transfer_by_category_tracks_tuition_separately_from_spending(db_conn, fixtures_dir):
    """A big tuition payment: excluded from 'my spending' (like Investments)
    but still visible as its own tracked line, not just invisible."""
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())

    tuition_cat = _category_id(db_conn, "Tuition", parent_name="Education")
    rent_txn = db_conn.execute("SELECT id FROM transactions WHERE amount = -105300").fetchone()
    replace_splits(db_conn, rent_txn["id"], [{"amount": -105300, "category_id": tuition_cat}])
    set_type(db_conn, rent_txn["id"], "transfer")

    start, end = month_bounds(2025, 8)
    by_cat = {r["category"]: r["amount"] for r in spending_by_category(db_conn, start, end)}
    transfers = {r["category"]: r["amount"] for r in transfer_by_category(db_conn, start, end)}

    assert "Education" not in by_cat  # never inflates regular spending
    assert transfers["Education"] == -105300  # but is visible as its own tracked total


def test_transfer_by_category_ignores_uncategorized_transfers(db_conn, fixtures_dir):
    """A plain credit-card-payment-style transfer with no category shouldn't
    show up as a meaningless empty line."""
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())
    bill = db_conn.execute("SELECT id FROM transactions WHERE amount = -50000").fetchone()
    set_type(db_conn, bill["id"], "transfer")

    start, end = month_bounds(2025, 8)
    assert transfer_by_category(db_conn, start, end) == []


def test_spending_by_category_includes_uncategorized_implicit_line(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())
    # Leave everything unreviewed/uncategorized (the default state) — every
    # fixture row nets into one "Uncategorized" bucket, income included,
    # since nothing's been marked type='income' yet to exclude it.
    start, end = month_bounds(2025, 8)
    by_cat = {r["category"]: r["amount"] for r in spending_by_category(db_conn, start, end)}
    assert by_cat["Uncategorized"] == 98649


def test_spending_by_account(db_conn, fixtures_dir):
    _setup_august_scenario(db_conn, fixtures_dir)
    start, end = month_bounds(2025, 8)
    by_account = {r["account"]: r["amount"] for r in spending_by_account(db_conn, start, end)}
    assert by_account["Scotia Chequing"] == -1150 - 35100 - 1


def test_top_merchants(db_conn, fixtures_dir):
    _setup_august_scenario(db_conn, fixtures_dir)
    start, end = month_bounds(2025, 8)
    merchants = top_merchants(db_conn, start, end)
    descriptions = [m["description"] for m in merchants]
    assert any("Fake Coffee Shop" in d for d in descriptions)


def test_splitwise_totals_sent_and_received(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Rogers MC")
    import_file(db_conn, acc, "rogers_sample.csv", (fixtures_dir / "rogers_sample.csv").read_bytes())

    grocery = db_conn.execute("SELECT id, amount FROM transactions WHERE description LIKE '%Grocery%'").fetchone()
    replace_splits(db_conn, grocery["id"], [{"amount": grocery["amount"], "is_splitwise": True}])

    start, end = month_bounds(2025, 7)
    totals = splitwise_totals(db_conn, start, end)
    assert totals["sent"] == grocery["amount"]
    assert totals["received"] == 0


def test_coverage_warnings_flags_incomplete_month(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())
    # scotia_sample.csv only covers 2025-08-17 through 2025-08-25.
    start, end = month_bounds(2025, 8)
    warnings = coverage_warnings(db_conn, start, end)
    assert any("Scotia Chequing" in w for w in warnings)


def test_coverage_warnings_clean_when_fully_covered(db_conn):
    # Every active account (both seeded ones) needs coverage, not just one.
    for name in ("Scotia Chequing", "Rogers MC"):
        acc = account_id(db_conn, name)
        db_conn.execute(
            "INSERT INTO import_batches (account_id, file_name, imported_at, date_from, date_to, "
            "rows_total, rows_new, rows_duplicate) VALUES (?, 'x.csv', datetime('now'), '2025-08-01', '2025-08-31', 1, 1, 0)",
            (acc,),
        )
    db_conn.commit()
    start, end = month_bounds(2025, 8)
    assert coverage_warnings(db_conn, start, end) == []


def test_coverage_warnings_no_data_at_all(db_conn):
    start, end = month_bounds(2025, 8)
    warnings = coverage_warnings(db_conn, start, end)
    assert len(warnings) == 2  # both seeded accounts, no imports at all
    assert all("no imported data" in w for w in warnings)
