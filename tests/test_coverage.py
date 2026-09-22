from core.coverage import get_coverage
from tests.conftest import account_id


def _insert_batch(conn, acc, date_from, date_to):
    conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, date_from, date_to, "
        "rows_total, rows_new, rows_duplicate) VALUES (?, 'x.csv', datetime('now'), ?, ?, 1, 1, 0)",
        (acc, date_from, date_to),
    )
    conn.commit()


def test_get_coverage_merges_overlapping_batches(db_conn):
    acc = account_id(db_conn, "Scotia Chequing")
    _insert_batch(db_conn, acc, "2025-08-18", "2025-09-17")
    _insert_batch(db_conn, acc, "2025-09-01", "2025-10-15")

    coverage = get_coverage(db_conn, acc)
    assert coverage == [("2025-08-18", "2025-10-15")]


def test_get_coverage_keeps_separate_gapped_ranges(db_conn):
    acc = account_id(db_conn, "Scotia Chequing")
    _insert_batch(db_conn, acc, "2025-06-01", "2025-06-30")
    _insert_batch(db_conn, acc, "2025-08-01", "2025-08-31")

    coverage = get_coverage(db_conn, acc)
    assert coverage == [("2025-06-01", "2025-06-30"), ("2025-08-01", "2025-08-31")]


def test_get_coverage_empty_when_no_batches(db_conn):
    acc = account_id(db_conn, "Rogers MC")
    assert get_coverage(db_conn, acc) == []
