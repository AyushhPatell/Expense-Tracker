from core.receivables import person_balances, person_ledger, set_flag, settle_through, unsettle_all
from core.splits import replace_splits


def _person_id(conn, name):
    conn.execute("INSERT OR IGNORE INTO people (name) VALUES (?)", (name,))
    conn.commit()
    return conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()["id"]


def _make_txn(conn, account_name, txn_date, amount, description="test"):
    account_id = conn.execute("SELECT id FROM accounts WHERE name = ?", (account_name,)).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, 'x.csv', datetime('now'), 1, 1, 0)",
        (account_id,),
    )
    batch_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO transactions (account_id, batch_id, txn_date, description, amount, currency, raw_row, fingerprint) "
        "VALUES (?, ?, ?, ?, ?, 'CAD', '{}', ?)",
        (account_id, batch_id, txn_date, description, amount, f"fp-{txn_date}-{amount}-{description}"),
    )
    conn.commit()
    return cur.lastrowid


def test_person_balance_zero_with_no_splits(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    balances = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances["Kavan"] == 0


def test_lending_money_shows_as_owed(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    loan_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-01", -20000, "loan to Kavan")
    replace_splits(db_conn, loan_txn, [{"amount": -20000, "person_id": kavan}])

    balances = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances["Kavan"] == 20000  # they owe me $200


def test_repayment_reduces_balance(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    loan_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-01", -20000, "loan to Kavan")
    replace_splits(db_conn, loan_txn, [{"amount": -20000, "person_id": kavan}])

    repay_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-15", 20000, "Kavan paid me back")
    replace_splits(db_conn, repay_txn, [{"amount": 20000, "person_id": kavan}])

    balances = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances["Kavan"] == 0


def test_partial_repayment_leaves_remaining_balance(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    loan_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-01", -20000, "loan to Kavan")
    replace_splits(db_conn, loan_txn, [{"amount": -20000, "person_id": kavan}])

    repay_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-15", 5000, "Kavan partial repayment")
    replace_splits(db_conn, repay_txn, [{"amount": 5000, "person_id": kavan}])

    balances = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances["Kavan"] == 15000


def test_late_reimbursement_across_months_nets_correctly(db_conn):
    """Two months of rent, reimbursements arriving late/out of order — the
    running balance should still land at zero once everything's paid."""
    kavan = _person_id(db_conn, "Kavan")
    rent_cat = db_conn.execute(
        "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id "
        "WHERE c.name = 'Rent' AND p.name = 'Housing'"
    ).fetchone()["id"]

    oct_rent = _make_txn(db_conn, "Scotia Chequing", "2025-09-28", -105300, "rent")
    replace_splits(
        db_conn, oct_rent,
        [{"amount": -35100, "category_id": rent_cat}, {"amount": -35100, "person_id": kavan}, {"amount": -35100, "person_id": kavan}],
    )
    nov_rent = _make_txn(db_conn, "Scotia Chequing", "2025-10-28", -105300, "rent")
    replace_splits(
        db_conn, nov_rent,
        [{"amount": -35100, "category_id": rent_cat}, {"amount": -35100, "person_id": kavan}, {"amount": -35100, "person_id": kavan}],
    )

    # Kavan pays October's share late (Nov 17), then November's on time (Nov 29).
    late_payment = _make_txn(db_conn, "Scotia Chequing", "2025-11-17", 35100 + 35100, "late Oct rent")
    replace_splits(db_conn, late_payment, [{"amount": 35100 + 35100, "person_id": kavan}])
    on_time_payment = _make_txn(db_conn, "Scotia Chequing", "2025-11-29", 35100 + 35100, "Nov rent")
    replace_splits(db_conn, on_time_payment, [{"amount": 35100 + 35100, "person_id": kavan}])

    balances = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances["Kavan"] == 0


def test_person_ledger_returns_chronological_entries(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    loan_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-01", -20000, "loan to Kavan")
    replace_splits(db_conn, loan_txn, [{"amount": -20000, "person_id": kavan}])
    repay_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-15", 20000, "Kavan paid me back")
    replace_splits(db_conn, repay_txn, [{"amount": 20000, "person_id": kavan}])

    ledger = person_ledger(db_conn, kavan)
    assert len(ledger) == 2
    assert ledger[0]["txn_date"] == "2025-08-01"
    assert ledger[1]["txn_date"] == "2025-08-15"


def test_person_ledger_includes_category_as_a_tag(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    rent_cat = db_conn.execute(
        "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id "
        "WHERE c.name = 'Rent' AND p.name = 'Housing'"
    ).fetchone()["id"]
    repay_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-25", 35100, "rent reimbursement")
    replace_splits(db_conn, repay_txn, [{"amount": 35100, "category_id": rent_cat, "person_id": kavan}])

    ledger = person_ledger(db_conn, kavan)
    assert ledger[0]["category"] == "Housing > Rent"


def test_settle_through_excludes_old_splits_from_balance(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    old_txn = _make_txn(db_conn, "Scotia Chequing", "2025-07-15", -5000, "old Uber split")
    replace_splits(db_conn, old_txn, [{"amount": -5000, "person_id": kavan}])
    new_txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-10", -3000, "new Instacart split")
    replace_splits(db_conn, new_txn, [{"amount": -3000, "person_id": kavan}])

    balances_before = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances_before["Kavan"] == 8000

    affected = settle_through(db_conn, kavan, "2025-08-02")  # settled in Splitwise up to Aug 2
    assert affected == 1  # only the July split, not the August one

    balances_after = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances_after["Kavan"] == 3000  # only the new, unsettled split remains


def test_settle_through_shows_up_in_ledger_but_not_balance(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    old_txn = _make_txn(db_conn, "Scotia Chequing", "2025-07-15", -5000, "old Uber split")
    replace_splits(db_conn, old_txn, [{"amount": -5000, "person_id": kavan}])
    settle_through(db_conn, kavan, "2025-08-02")

    ledger = person_ledger(db_conn, kavan)
    assert len(ledger) == 1  # settled entries stay in the ledger for history
    assert ledger[0]["settled"] == 1


def test_unsettle_all_reopens_everything(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    old_txn = _make_txn(db_conn, "Scotia Chequing", "2025-07-15", -5000, "old Uber split")
    replace_splits(db_conn, old_txn, [{"amount": -5000, "person_id": kavan}])
    settle_through(db_conn, kavan, "2025-08-02")

    reopened = unsettle_all(db_conn, kavan)
    assert reopened == 1
    balances = {b["person"]: b["balance"] for b in person_balances(db_conn)}
    assert balances["Kavan"] == 5000


def test_set_flag_toggles_on_and_off(db_conn):
    kavan = _person_id(db_conn, "Kavan")
    txn = _make_txn(db_conn, "Scotia Chequing", "2025-08-01", -5000, "not sure about this one")
    replace_splits(db_conn, txn, [{"amount": -5000, "person_id": kavan}])
    split_id = person_ledger(db_conn, kavan)[0]["split_id"]

    set_flag(db_conn, split_id, True)
    assert person_ledger(db_conn, kavan)[0]["flagged"] == 1

    set_flag(db_conn, split_id, False)
    assert person_ledger(db_conn, kavan)[0]["flagged"] == 0
