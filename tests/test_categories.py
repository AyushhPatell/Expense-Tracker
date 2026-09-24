import pytest

from core.categories import (
    create_category,
    create_person,
    deactivate_person,
    list_categories,
    list_people,
    merge_categories,
    rename_category,
    rename_person,
    set_category_active,
)
from core.splits import get_splits, replace_splits
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


def _insert_txn(conn, account_id_, amount):
    batch = conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, 'manual.csv', datetime('now'), 1, 1, 0)",
        (account_id_,),
    )
    cur = conn.execute(
        "INSERT INTO transactions (account_id, batch_id, txn_date, description, amount, raw_row, fingerprint) "
        "VALUES (?, ?, '2025-08-01', 'test', ?, '{}', ?)",
        (account_id_, batch.lastrowid, amount, f"fp-{amount}-{batch.lastrowid}"),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def test_create_category_top_level_and_child(db_conn):
    top_id = create_category(db_conn, "Pets")
    child_id = create_category(db_conn, "Vet Bills", parent_id=top_id)
    labels = {c["id"]: c["label"] for c in list_categories(db_conn)}
    assert labels[top_id] == "Pets"
    assert labels[child_id] == "Pets > Vet Bills"


def test_create_category_rejects_duplicate_at_same_level(db_conn):
    with pytest.raises(ValueError):
        create_category(db_conn, "Coffee")  # already exists top-level


def test_create_category_rejects_blank_name(db_conn):
    with pytest.raises(ValueError):
        create_category(db_conn, "   ")


def test_rename_category(db_conn):
    cat_id = create_category(db_conn, "Pets")
    rename_category(db_conn, cat_id, "Pet Care")
    labels = {c["id"]: c["label"] for c in list_categories(db_conn)}
    assert labels[cat_id] == "Pet Care"


def test_set_category_active_false_then_true(db_conn):
    cat_id = create_category(db_conn, "Pets")
    set_category_active(db_conn, cat_id, False)
    assert cat_id not in {c["id"] for c in list_categories(db_conn)}
    assert cat_id in {c["id"] for c in list_categories(db_conn, include_inactive=True)}

    set_category_active(db_conn, cat_id, True)
    assert cat_id in {c["id"] for c in list_categories(db_conn)}


def test_deactivating_category_with_active_children_is_refused(db_conn):
    parent_id = create_category(db_conn, "Pets")
    create_category(db_conn, "Vet Bills", parent_id=parent_id)
    with pytest.raises(ValueError):
        set_category_active(db_conn, parent_id, False)


def test_merge_categories_reassigns_splits_and_deactivates_source(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    eating_out_cat = _category_id(db_conn, "Eating Out")
    txn_id = _insert_txn(db_conn, scotia, -450)
    replace_splits(db_conn, txn_id, [{"amount": -450, "category_id": coffee_cat}])

    reassigned = merge_categories(db_conn, coffee_cat, eating_out_cat)
    assert reassigned == 1
    assert get_splits(db_conn, txn_id)[0]["category_id"] == eating_out_cat
    assert coffee_cat not in {c["id"] for c in list_categories(db_conn)}


def test_merge_categories_rejects_self_merge(db_conn):
    coffee_cat = _category_id(db_conn, "Coffee")
    with pytest.raises(ValueError):
        merge_categories(db_conn, coffee_cat, coffee_cat)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def test_rename_person(db_conn):
    person_id = create_person(db_conn, "Kavan")
    rename_person(db_conn, person_id, "Kavan P.")
    names = {p["id"]: p["label"] for p in list_people(db_conn)}
    assert names[person_id] == "Kavan P."


def test_rename_person_rejects_duplicate(db_conn):
    create_person(db_conn, "Kavan")
    other_id = create_person(db_conn, "Alex")
    with pytest.raises(ValueError):
        rename_person(db_conn, other_id, "Kavan")


def test_deactivate_person_with_no_balance(db_conn):
    person_id = create_person(db_conn, "Kavan")
    deactivate_person(db_conn, person_id)
    assert person_id not in {p["id"] for p in list_people(db_conn)}


def test_deactivate_person_with_open_balance_is_refused(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    person_id = create_person(db_conn, "Kavan")
    txn_id = _insert_txn(db_conn, scotia, -1000)
    replace_splits(db_conn, txn_id, [{"amount": -1000, "person_id": person_id}])

    with pytest.raises(ValueError):
        deactivate_person(db_conn, person_id)
