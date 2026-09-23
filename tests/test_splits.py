import pytest

from core.db import init_db
from core.importers import import_file
from core.splits import (
    equal_shares,
    get_splits,
    is_simple_split,
    replace_splits,
    set_single_category,
    set_type,
    validate_split_lines,
)
from tests.conftest import account_id


def _import_scotia(conn, fixtures_dir):
    acc = account_id(conn, "Scotia Chequing")
    import_file(conn, acc, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())
    return acc


def _rent_txn_id(conn):
    # Real Scotia Interac descriptions carry no recipient info ("Free Interac
    # E-Transfer" for every transfer in or out), so lookups have to go by
    # amount/date, not description text — the same constraint the rules
    # engine (Phase 5) will face.
    row = conn.execute("SELECT id FROM transactions WHERE amount = -105300").fetchone()
    return row["id"]


def _category_id(conn, name, parent_name=None):
    if parent_name:
        row = conn.execute(
            "SELECT c.id FROM categories c JOIN categories p ON p.id = c.parent_id "
            "WHERE c.name = ? AND p.name = ?",
            (name, parent_name),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM categories WHERE name = ? AND parent_id IS NULL", (name,)
        ).fetchone()
    return row["id"]


def _person_id(conn, name):
    conn.execute("INSERT OR IGNORE INTO people (name) VALUES (?)", (name,))
    conn.commit()
    return conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()["id"]


def test_validate_split_lines_matches_total():
    validate_split_lines(1000, [{"amount": 400, "category_id": 1}, {"amount": 600, "person_id": 2}])


def test_validate_split_lines_rejects_mismatched_total():
    with pytest.raises(ValueError):
        validate_split_lines(1000, [{"amount": 400, "category_id": 1}])


def test_validate_split_lines_allows_category_as_tag_on_a_receivable():
    # Category can coexist with person_id — a descriptive tag ("this $351
    # from Kavan was for Rent"), not a claim that it's my spending too.
    validate_split_lines(1000, [{"amount": 1000, "category_id": 1, "person_id": 2}])


def test_validate_split_lines_rejects_empty_line():
    with pytest.raises(ValueError):
        validate_split_lines(1000, [{"amount": 1000}])


def test_equal_shares_distributes_remainder_pennies():
    shares = equal_shares(1000, 3)
    assert sum(shares) == 1000
    assert shares == [334, 333, 333]


def test_equal_shares_exact_division():
    assert equal_shares(900, 3) == [300, 300, 300]


def test_rent_scenario_from_spec(db_conn, fixtures_dir):
    """The literal Phase 2 'done when': $1,053 -> $351 Rent + two $351 owed lines."""
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)

    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    roommate_a = _person_id(db_conn, "Roommate A")
    roommate_b = _person_id(db_conn, "Roommate B")

    replace_splits(
        db_conn,
        txn_id,
        [
            {"amount": -35100, "category_id": rent_cat},
            {"amount": -35100, "person_id": roommate_a},
            {"amount": -35100, "person_id": roommate_b},
        ],
    )

    splits = get_splits(db_conn, txn_id)
    assert len(splits) == 3
    assert sum(s["amount"] for s in splits) == -105300
    assert all(s["locked"] == 1 for s in splits)


def test_splits_survive_restart(db_conn, fixtures_dir, tmp_path):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    roommate_a = _person_id(db_conn, "Roommate A")
    roommate_b = _person_id(db_conn, "Roommate B")

    replace_splits(
        db_conn,
        txn_id,
        [
            {"amount": -35100, "category_id": rent_cat},
            {"amount": -35100, "person_id": roommate_a},
            {"amount": -35100, "person_id": roommate_b},
        ],
    )
    db_conn.close()

    reopened = init_db(tmp_path / "test.db")
    splits = get_splits(reopened, txn_id)
    assert len(splits) == 3
    assert sum(s["amount"] for s in splits) == -105300


def test_splitwise_line_is_receivable_not_spending(db_conn, fixtures_dir):
    acc = account_id(db_conn, "Rogers MC")
    from core.importers import import_file as _import

    _import(db_conn, acc, "rogers_sample.csv", (fixtures_dir / "rogers_sample.csv").read_bytes())
    txn = db_conn.execute(
        "SELECT id, amount FROM transactions WHERE description LIKE '%GROCERY%'"
    ).fetchone()

    eating_out = _category_id(db_conn, "Eating Out")
    my_share = txn["amount"] // 2
    rest = txn["amount"] - my_share

    replace_splits(
        db_conn,
        txn["id"],
        [
            {"amount": my_share, "category_id": eating_out},
            {"amount": rest, "is_splitwise": True},
        ],
    )

    splits = get_splits(db_conn, txn["id"])
    splitwise_lines = [s for s in splits if s["is_splitwise"]]
    assert len(splitwise_lines) == 1
    assert splitwise_lines[0]["category_id"] is None
    assert splitwise_lines[0]["person_id"] is None


def test_set_single_category_overwrites_existing_split(db_conn, fixtures_dir):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    roommate_a = _person_id(db_conn, "Roommate A")

    replace_splits(
        db_conn,
        txn_id,
        [{"amount": -70200, "category_id": rent_cat}, {"amount": -35100, "person_id": roommate_a}],
    )
    assert len(get_splits(db_conn, txn_id)) == 2

    set_single_category(db_conn, txn_id, rent_cat)
    splits = get_splits(db_conn, txn_id)
    assert len(splits) == 1
    assert splits[0]["category_id"] == rent_cat


def test_set_type_locks_it(db_conn, fixtures_dir):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)

    set_type(db_conn, txn_id, "expense")
    row = db_conn.execute(
        "SELECT type, type_locked FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone()
    assert row["type"] == "expense"
    assert row["type_locked"] == 1


def test_is_simple_split_true_with_no_splits(db_conn, fixtures_dir):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)
    assert is_simple_split(db_conn, txn_id) is True


def test_is_simple_split_true_with_one_category_line(db_conn, fixtures_dir):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    set_single_category(db_conn, txn_id, rent_cat)
    assert is_simple_split(db_conn, txn_id) is True


def test_is_simple_split_false_with_multiple_lines(db_conn, fixtures_dir):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    roommate_a = _person_id(db_conn, "Roommate A")
    roommate_b = _person_id(db_conn, "Roommate B")

    replace_splits(
        db_conn,
        txn_id,
        [
            {"amount": -35100, "category_id": rent_cat},
            {"amount": -35100, "person_id": roommate_a},
            {"amount": -35100, "person_id": roommate_b},
        ],
    )
    assert is_simple_split(db_conn, txn_id) is False


def test_is_simple_split_false_with_single_receivable_line(db_conn, fixtures_dir):
    acc = _import_scotia(db_conn, fixtures_dir)
    txn_id = _rent_txn_id(db_conn)
    roommate_a = _person_id(db_conn, "Roommate A")

    replace_splits(db_conn, txn_id, [{"amount": -105300, "person_id": roommate_a}])
    assert is_simple_split(db_conn, txn_id) is False
