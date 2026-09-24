from core.backup import create_backup, list_backups, prune_backups, restore_backup
from core.db import init_db
from core.splits import replace_splits
from tests.conftest import account_id


def _insert_txn(conn, account_id_, amount):
    batch = conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, 'manual.csv', datetime('now'), 1, 1, 0)",
        (account_id_,),
    )
    cur = conn.execute(
        "INSERT INTO transactions (account_id, batch_id, txn_date, description, amount, raw_row, fingerprint) "
        "VALUES (?, ?, '2025-08-01', 'test', ?, '{}', ?)",
        (account_id_, batch.lastrowid, amount, f"fp-{amount}"),
    )
    conn.commit()
    return cur.lastrowid


def test_create_backup_produces_a_readable_copy(db_conn, tmp_path):
    scotia = account_id(db_conn, "Scotia Chequing")
    _insert_txn(db_conn, scotia, -1234)

    backups_dir = tmp_path / "backups"
    backup_path = create_backup(db_conn, backups_dir)

    assert backup_path.exists()
    check = init_db(backup_path)
    row = check.execute("SELECT amount FROM transactions WHERE amount = -1234").fetchone()
    assert row is not None
    check.close()


def test_create_backup_prunes_to_keep_last_n(db_conn, tmp_path):
    backups_dir = tmp_path / "backups"
    for _ in range(5):
        create_backup(db_conn, backups_dir, keep_last=3)

    assert len(list_backups(backups_dir)) == 3


def test_prune_backups_keeps_the_newest(db_conn, tmp_path):
    backups_dir = tmp_path / "backups"
    paths = []
    for _ in range(4):
        paths.append(create_backup(db_conn, backups_dir, keep_last=100))

    prune_backups(backups_dir, keep_last=2)
    remaining = list_backups(backups_dir)
    assert len(remaining) == 2
    assert set(p.name for p in remaining) == {paths[-1].name, paths[-2].name}


def test_restore_backup_overwrites_the_target_db(db_conn, tmp_path):
    scotia = account_id(db_conn, "Scotia Chequing")
    _insert_txn(db_conn, scotia, -1234)

    backups_dir = tmp_path / "backups"
    backup_path = create_backup(db_conn, backups_dir)

    # Simulate further changes happening AFTER the backup was taken.
    _insert_txn(db_conn, scotia, -9999)
    db_conn.close()

    live_db_path = tmp_path / "test.db"  # matches the db_conn fixture's own path
    restore_backup(backup_path, live_db_path)

    restored = init_db(live_db_path)
    amounts = {r["amount"] for r in restored.execute("SELECT amount FROM transactions").fetchall()}
    assert -1234 in amounts
    assert -9999 not in amounts  # the post-backup change is gone, as expected
    restored.close()
