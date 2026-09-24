import io

import pandas as pd

from core.export import spending_by_category_dataframe, to_csv_bytes, to_excel_bytes, transactions_dataframe
from core.splits import replace_splits
from tests.conftest import account_id


def _category_id(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ? AND parent_id IS NULL", (name,)).fetchone()["id"]


def _insert_txn(conn, account_id_, txn_date, description, amount):
    batch = conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, 'manual.csv', datetime('now'), 1, 1, 0)",
        (account_id_,),
    )
    cur = conn.execute(
        "INSERT INTO transactions (account_id, batch_id, txn_date, description, amount, raw_row, fingerprint) "
        "VALUES (?, ?, ?, ?, ?, '{}', ?)",
        (account_id_, batch.lastrowid, txn_date, description, amount, f"fp-{txn_date}-{description}-{amount}"),
    )
    conn.commit()
    return cur.lastrowid


def test_transactions_dataframe_includes_split_summary(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-05", "Fake Coffee Shop", -450)
    replace_splits(db_conn, txn_id, [{"amount": -450, "category_id": coffee_cat}])

    df = transactions_dataframe(db_conn)
    assert len(df) == 1
    assert df.iloc[0]["Amount"] == -4.50
    assert "Coffee" in df.iloc[0]["Splits"]


def test_transactions_dataframe_filters_by_date_range(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    _insert_txn(db_conn, scotia, "2025-07-01", "July txn", -100)
    _insert_txn(db_conn, scotia, "2025-08-01", "August txn", -200)

    df = transactions_dataframe(db_conn, start_date="2025-08-01", end_date="2025-08-31")
    assert len(df) == 1
    assert df.iloc[0]["Description"] == "August txn"


def test_spending_by_category_dataframe(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-05", "Fake Coffee Shop", -450)
    replace_splits(db_conn, txn_id, [{"amount": -450, "category_id": coffee_cat}])

    df = spending_by_category_dataframe(db_conn, "2025-08-01", "2025-08-31")
    row = df[df["Category"] == "Coffee"].iloc[0]
    assert row["Amount"] == 4.50


def test_to_csv_bytes_round_trips():
    df = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    csv_bytes = to_csv_bytes(df)
    result = pd.read_csv(io.BytesIO(csv_bytes))
    assert result.to_dict(orient="records") == df.to_dict(orient="records")


def test_to_excel_bytes_round_trips():
    df = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    excel_bytes = to_excel_bytes(df)
    result = pd.read_excel(io.BytesIO(excel_bytes))
    assert result.to_dict(orient="records") == df.to_dict(orient="records")
