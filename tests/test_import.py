from core.importers import import_file
from tests.conftest import account_id


def _read(path):
    return path.read_bytes()


def test_dedupe_same_file_imported_twice(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    bytes_ = _read(fixtures_dir / "scotia_sample.csv")

    first = import_file(db_conn, acc, "scotia_sample.csv", bytes_)
    assert first["rows_new"] == 8
    assert first["rows_duplicate"] == 0

    second = import_file(db_conn, acc, "scotia_sample.csv", bytes_)
    assert second["rows_new"] == 0
    assert second["rows_duplicate"] == 8

    total = db_conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
    assert total == 8


def test_overlapping_file_imports_only_new_rows(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")

    first = import_file(db_conn, acc, "scotia_sample.csv", _read(fixtures_dir / "scotia_sample.csv"))
    assert first["rows_new"] == 8

    # Overlap file re-exports the two 08-25 coffee rows and adds two new ones.
    second = import_file(
        db_conn, acc, "scotia_sample_overlap.csv", _read(fixtures_dir / "scotia_sample_overlap.csv")
    )
    assert second["rows_duplicate"] == 2
    assert second["rows_new"] == 2

    total = db_conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
    assert total == 10


def test_identical_same_day_rows_both_kept(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", _read(fixtures_dir / "scotia_sample.csv"))

    coffee_rows = db_conn.execute(
        "SELECT fingerprint FROM transactions WHERE txn_date = '2025-08-25'"
    ).fetchall()
    assert len(coffee_rows) == 2
    assert coffee_rows[0]["fingerprint"] != coffee_rows[1]["fingerprint"]


def test_scotia_sign_convention(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, acc, "scotia_sample.csv", _read(fixtures_dir / "scotia_sample.csv"))

    purchase = db_conn.execute(
        "SELECT amount FROM transactions WHERE description LIKE '%COFFEE SHOP%' LIMIT 1"
    ).fetchone()
    assert purchase["amount"] < 0

    paycheque = db_conn.execute(
        "SELECT amount FROM transactions WHERE description LIKE '%PAYROLL%'"
    ).fetchone()
    assert paycheque["amount"] == 220000


def test_rogers_sign_convention(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Rogers MC")
    import_file(db_conn, acc, "rogers_sample.csv", _read(fixtures_dir / "rogers_sample.csv"))

    purchase = db_conn.execute(
        "SELECT amount FROM transactions WHERE description LIKE '%GROCERY%'"
    ).fetchone()
    assert purchase["amount"] == -4523  # raw positive charge -> negative expense

    payment = db_conn.execute(
        "SELECT amount FROM transactions WHERE description LIKE '%PAYMENT THANK YOU%'"
    ).fetchone()
    assert payment["amount"] == 50000  # raw negative credit -> positive


def test_import_batch_date_range_recorded(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Rogers MC")
    result = import_file(db_conn, acc, "rogers_sample.csv", _read(fixtures_dir / "rogers_sample.csv"))
    assert result["date_from"] == "2025-07-05"
    assert result["date_to"] == "2025-08-01"


def test_min_date_excludes_rows_before_cutoff(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    result = import_file(
        db_conn, acc, "scotia_sample.csv", _read(fixtures_dir / "scotia_sample.csv"), min_date="2025-08-20"
    )
    # scotia_sample.csv has 3 rows before 2025-08-20 (08-17, 08-18, 08-19) and 5 on/after.
    assert result["rows_before_cutoff"] == 3
    assert result["rows_new"] == 5
    assert result["date_from"] == "2025-08-20"

    remaining = db_conn.execute("SELECT MIN(txn_date) AS earliest FROM transactions").fetchone()
    assert remaining["earliest"] == "2025-08-20"


def test_min_date_none_imports_everything(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    result = import_file(
        db_conn, acc, "scotia_sample.csv", _read(fixtures_dir / "scotia_sample.csv"), min_date=None
    )
    assert result["rows_before_cutoff"] == 0
    assert result["rows_new"] == 8


def test_delete_batch_cascades_to_transactions(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Scotia Chequing")
    result = import_file(db_conn, acc, "scotia_sample.csv", _read(fixtures_dir / "scotia_sample.csv"))

    db_conn.execute("DELETE FROM import_batches WHERE id = ?", (result["batch_id"],))
    db_conn.commit()

    remaining = db_conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
    assert remaining == 0
