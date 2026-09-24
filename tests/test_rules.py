from core.importers import import_file
from core.rules import (
    apply_rules_to_all,
    apply_rules_to_transactions,
    create_rule,
    delete_rule,
    get_rule,
    get_rule_split_failures,
    list_rules,
    preview_rules_run,
    rule_matches,
    suggest_rule_draft,
    update_rule,
)
from core.splits import get_splits, get_transaction, replace_splits, set_reviewed, set_type
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


def _insert_txn(conn, account_id_, txn_date, description, amount, txn_type="unreviewed"):
    batch = conn.execute(
        "INSERT INTO import_batches (account_id, file_name, imported_at, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, 'manual.csv', datetime('now'), 1, 1, 0)",
        (account_id_,),
    )
    cur = conn.execute(
        "INSERT INTO transactions "
        "(account_id, batch_id, txn_date, description, amount, raw_row, fingerprint, type) "
        "VALUES (?, ?, ?, ?, ?, '{}', ?, ?)",
        (account_id_, batch.lastrowid, txn_date, description, amount, f"fp-{txn_date}-{description}-{amount}", txn_type),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Condition matching
# ---------------------------------------------------------------------------


def test_contains_and_not_contains_are_case_insensitive():
    ctx = {"description": "ROGERS PAYMENT THANK YOU"}
    assert rule_matches({"field": "description", "op": "contains", "value": "rogers"}, ctx)
    assert not rule_matches({"field": "description", "op": "not_contains", "value": "rogers"}, ctx)


def test_starts_with_and_equals():
    ctx = {"account": "Scotia Chequing"}
    assert rule_matches({"field": "account", "op": "starts_with", "value": "scotia"}, ctx)
    assert rule_matches({"field": "account", "op": "equals", "value": "SCOTIA CHEQUING"}, ctx)
    assert not rule_matches({"field": "account", "op": "equals", "value": "Rogers MC"}, ctx)


def test_regex_operator():
    ctx = {"description": "bill payment | Mb-Rogers Bank Mastercard 12345678"}
    assert rule_matches({"field": "description", "op": "regex", "value": r"Mb-Rogers.*Mastercard"}, ctx)
    assert not rule_matches({"field": "description", "op": "regex", "value": r"^Rogers"}, ctx)


def test_in_operator_text_and_numeric():
    assert rule_matches({"field": "account", "op": "in", "value": ["Rogers MC", "Scotia Chequing"]}, {"account": "rogers mc"})
    assert rule_matches({"field": "day_of_month", "op": "in", "value": [1, 15, 30]}, {"day_of_month": 15})
    assert not rule_matches({"field": "day_of_month", "op": "in", "value": [1, 15, 30]}, {"day_of_month": 2})


def test_between_gt_lt_numeric():
    ctx = {"amount_abs": 5000}
    assert rule_matches({"field": "amount_abs", "op": "between", "value": [4000, 6000]}, ctx)
    assert not rule_matches({"field": "amount_abs", "op": "between", "value": [6000, 7000]}, ctx)
    assert rule_matches({"field": "amount_abs", "op": "gt", "value": 4000}, ctx)
    assert rule_matches({"field": "amount_abs", "op": "lt", "value": 6000}, ctx)


def test_direction_field():
    assert rule_matches({"field": "direction", "op": "equals", "value": "out"}, {"direction": "out"})


def test_all_group_requires_every_condition():
    conditions = {
        "all": [
            {"field": "account", "op": "equals", "value": "Scotia Chequing"},
            {"field": "direction", "op": "equals", "value": "out"},
        ]
    }
    assert rule_matches(conditions, {"account": "Scotia Chequing", "direction": "out"})
    assert not rule_matches(conditions, {"account": "Scotia Chequing", "direction": "in"})


def test_any_group_requires_one_condition():
    conditions = {
        "any": [
            {"field": "description", "op": "contains", "value": "COFFEE"},
            {"field": "description", "op": "contains", "value": "TEA"},
        ]
    }
    assert rule_matches(conditions, {"description": "Fake Coffee Shop"})
    assert rule_matches(conditions, {"description": "Fake Tea House"})
    assert not rule_matches(conditions, {"description": "Fake Grocery Store"})


def test_nested_any_inside_all():
    conditions = {
        "all": [
            {"field": "direction", "op": "equals", "value": "out"},
            {"any": [
                {"field": "description", "op": "contains", "value": "COFFEE"},
                {"field": "description", "op": "contains", "value": "TEA"},
            ]},
        ]
    }
    assert rule_matches(conditions, {"direction": "out", "description": "Fake Coffee Shop"})
    assert not rule_matches(conditions, {"direction": "in", "description": "Fake Coffee Shop"})


# ---------------------------------------------------------------------------
# Applying rules: set_type, set_category, locking, stop_processing, priority
# ---------------------------------------------------------------------------


def test_rule_sets_type_on_matching_transaction(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-19", "payroll deposit | Fake Employer Inc.", 220000)

    create_rule(
        db_conn,
        name="Payroll -> income",
        conditions={"field": "description", "op": "contains", "value": "PAYROLL"},
        actions={"set_type": "income"},
    )

    result = apply_rules_to_transactions(db_conn, [txn_id])
    assert result["matched"] == 1
    assert get_transaction(db_conn, txn_id)["type"] == "income"


def test_rule_does_not_touch_manually_locked_type(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-19", "payroll deposit | Fake Employer Inc.", 220000)
    set_type(db_conn, txn_id, "expense", lock=True)  # manual override, deliberately "wrong"

    create_rule(
        db_conn,
        name="Payroll -> income",
        conditions={"field": "description", "op": "contains", "value": "PAYROLL"},
        actions={"set_type": "income"},
    )
    apply_rules_to_transactions(db_conn, [txn_id])

    assert get_transaction(db_conn, txn_id)["type"] == "expense"  # untouched


def test_rule_sets_single_category(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)

    create_rule(
        db_conn,
        name="Coffee",
        conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense", "set_category": "Coffee"},
    )
    apply_rules_to_transactions(db_conn, [txn_id])

    splits = get_splits(db_conn, txn_id)
    assert len(splits) == 1
    assert splits[0]["category_id"] == coffee_cat
    assert splits[0]["locked"] == 0  # rule-applied, not a manual edit
    assert splits[0]["created_by_rule"] is not None


def test_rule_does_not_overwrite_a_manually_split_transaction(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    groceries_cat = _category_id(db_conn, "Groceries")
    kavan = _person_id(db_conn, "Kavan")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -1000)
    replace_splits(
        db_conn, txn_id, [{"amount": -500, "category_id": groceries_cat}, {"amount": -500, "person_id": kavan}]
    )  # manual, locked=True by default

    create_rule(
        db_conn,
        name="Coffee",
        conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense", "set_category": "Coffee"},
    )
    apply_rules_to_transactions(db_conn, [txn_id])

    splits = get_splits(db_conn, txn_id)
    assert len(splits) == 2  # untouched manual split survives
    assert {s["category_id"] for s in splits} == {groceries_cat}.union({None})


def test_stop_processing_prevents_later_rules_from_running(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)

    create_rule(
        db_conn,
        name="Coffee first",
        conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense"},
        priority=10,
        stop_processing=True,
    )
    create_rule(
        db_conn,
        name="Coffee second (should never run)",
        conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "ignore"},
        priority=20,
    )

    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_transaction(db_conn, txn_id)["type"] == "expense"


def test_rules_run_in_priority_order(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)

    create_rule(
        db_conn, name="Later, lower priority number wins first", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "ignore"}, priority=50,
    )
    create_rule(
        db_conn, name="Runs first", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense"}, priority=5,
    )
    # Neither rule stops processing, so the transaction ends up with the
    # LAST rule's outcome in priority order (priority 50 runs after priority 5).
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_transaction(db_conn, txn_id)["type"] == "ignore"


def test_disabled_rule_never_matches(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)

    create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense"}, enabled=False,
    )
    result = apply_rules_to_transactions(db_conn, [txn_id])
    assert result["matched"] == 0
    assert get_transaction(db_conn, txn_id)["type"] == "unreviewed"


def test_times_applied_increments_on_match(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    rule_id = create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense"},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_rule(db_conn, rule_id)["times_applied"] == 1


def test_dry_run_preview_does_not_mutate(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    rule_id = create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense", "set_category": "Coffee"},
    )
    result = preview_rules_run(db_conn, [txn_id])
    assert result["matched"] == 1
    assert result["preview"][0]["changes"]["type"] == "expense"

    assert get_transaction(db_conn, txn_id)["type"] == "unreviewed"  # nothing actually written
    assert get_splits(db_conn, txn_id) == []
    assert get_rule(db_conn, rule_id)["times_applied"] == 0


def test_rerunning_an_already_applied_rule_reports_no_change(db_conn):
    """Re-running rules is supposed to be a safe no-op once things are
    already categorized — the dry-run preview and 'changed' count would be
    actively misleading (and every run would needlessly rewrite splits) if
    it reported the same outcome as a change every single time."""
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense", "set_category": "Coffee"},
    )
    first = apply_rules_to_transactions(db_conn, [txn_id])
    assert first["changed"] == 1

    second = apply_rules_to_transactions(db_conn, [txn_id])
    assert second["matched"] == 1  # the rule still matches...
    assert second["changed"] == 0  # ...but nothing was different to change


def test_rerunning_an_already_applied_split_template_reports_no_change(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    _person_id(db_conn, "Roommate A")
    _person_id(db_conn, "Roommate B")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-21", "withdrawal | Free Interac E-Transfer", -105300)
    create_rule(
        db_conn,
        name="Rent",
        conditions={"field": "amount_abs", "op": "equals", "value": 105300},
        actions={
            "split_template": [
                {"share": "fixed", "value": 351.00, "category": "Housing > Rent"},
                {"share": "fixed", "value": 351.00, "person": "Roommate A"},
                {"share": "remainder", "person": "Roommate B"},
            ]
        },
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    second = apply_rules_to_transactions(db_conn, [txn_id])
    assert second["changed"] == 0


def test_apply_rules_to_all_covers_every_transaction(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_type": "expense"},
    )
    apply_rules_to_all(db_conn)
    assert get_transaction(db_conn, txn_id)["type"] == "expense"


# ---------------------------------------------------------------------------
# Split templates
# ---------------------------------------------------------------------------


def test_split_template_fixed_and_remainder(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    rent_cat = _category_id(db_conn, "Rent", parent_name="Housing")
    _person_id(db_conn, "Roommate A")
    _person_id(db_conn, "Roommate B")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-21", "withdrawal | Free Interac E-Transfer", -105300)

    create_rule(
        db_conn,
        name="Rent",
        conditions={"field": "amount_abs", "op": "equals", "value": 105300},
        actions={
            "set_type": "expense",
            "split_template": [
                {"share": "fixed", "value": 351.00, "category": "Housing > Rent"},
                {"share": "fixed", "value": 351.00, "person": "Roommate A"},
                {"share": "remainder", "person": "Roommate B"},
            ],
        },
    )
    apply_rules_to_transactions(db_conn, [txn_id])

    splits = get_splits(db_conn, txn_id)
    assert sum(s["amount"] for s in splits) == -105300
    by_category = next(s for s in splits if s["category_id"] == rent_cat)
    assert by_category["amount"] == -35100
    remainder_line = next(s for s in splits if s["person_name"] == "Roommate B")
    assert remainder_line["amount"] == -35100


def test_split_template_survives_a_rent_increase_via_remainder(db_conn):
    """The whole point of 'remainder': the fixed lines stay the same, and the
    remainder line simply absorbs however much rent actually changed by."""
    scotia = account_id(db_conn, "Scotia Chequing")
    _person_id(db_conn, "Roommate A")
    _person_id(db_conn, "Roommate B")
    txn_id = _insert_txn(db_conn, scotia, "2025-09-21", "withdrawal | Free Interac E-Transfer", -110000)  # rent went up

    create_rule(
        db_conn,
        name="Rent",
        conditions={"field": "description", "op": "contains", "value": "E-TRANSFER"},
        actions={
            "set_type": "expense",
            "split_template": [
                {"share": "fixed", "value": 351.00, "category": "Housing > Rent"},
                {"share": "fixed", "value": 351.00, "person": "Roommate A"},
                {"share": "remainder", "person": "Roommate B"},
            ],
        },
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    splits = get_splits(db_conn, txn_id)
    assert sum(s["amount"] for s in splits) == -110000
    remainder_line = next(s for s in splits if s["person_name"] == "Roommate B")
    assert remainder_line["amount"] == -39800  # absorbed the $398 increase, not still -351


def test_split_template_fails_when_fixed_lines_exceed_the_amount(db_conn):
    """Rent dropped below what the fixed lines alone add up to — nothing
    sensible for 'remainder' to be, so the template must not apply."""
    scotia = account_id(db_conn, "Scotia Chequing")
    _person_id(db_conn, "Roommate A")
    _person_id(db_conn, "Roommate B")
    txn_id = _insert_txn(db_conn, scotia, "2025-09-21", "withdrawal | Free Interac E-Transfer", -60000)

    create_rule(
        db_conn,
        name="Rent",
        conditions={"field": "description", "op": "contains", "value": "E-TRANSFER"},
        actions={
            "split_template": [
                {"share": "fixed", "value": 351.00, "category": "Housing > Rent"},
                {"share": "fixed", "value": 351.00, "person": "Roommate A"},
                {"share": "remainder", "person": "Roommate B"},
            ],
        },
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_splits(db_conn, txn_id) == []  # left alone, not force-applied


def test_split_template_fails_with_no_absorbing_line_and_mismatched_total(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -1000)
    groceries_cat_label = "Groceries"

    create_rule(
        db_conn,
        name="Bad template",
        conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"split_template": [{"share": "fixed", "value": 5.00, "category": groceries_cat_label}]},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_splits(db_conn, txn_id) == []


def test_split_template_percent_and_equal(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    groceries_cat = _category_id(db_conn, "Groceries")
    household_cat = _category_id(db_conn, "Other")
    kavan = _person_id(db_conn, "Kavan")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-24", "pos purchase | Fake Costco", -12000)

    create_rule(
        db_conn,
        name="Costco split",
        conditions={"field": "description", "op": "contains", "value": "COSTCO"},
        actions={
            "split_template": [
                {"share": "percent", "value": 50, "category": "Groceries"},
                {"share": "equal", "category": "Other"},
                {"share": "equal", "person": "Kavan"},
            ]
        },
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    splits = get_splits(db_conn, txn_id)
    assert sum(s["amount"] for s in splits) == -12000
    grocery_line = next(s for s in splits if s["category_id"] == groceries_cat)
    assert grocery_line["amount"] == -6000
    other_lines = [s for s in splits if s["category_id"] == household_cat or s["person_id"] == kavan]
    assert {s["amount"] for s in other_lines} == {-3000}


def test_split_template_fails_on_unknown_category(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    create_rule(
        db_conn, name="Bad category", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"split_template": [{"share": "fixed", "value": 4.50, "category": "Nonexistent Category"}]},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_splits(db_conn, txn_id) == []


# ---------------------------------------------------------------------------
# add_tags, set_note, mark_reviewed
# ---------------------------------------------------------------------------


def test_add_tags_attaches_to_the_created_split(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_category": "Coffee", "add_tags": ["daily"]},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    split_id = get_splits(db_conn, txn_id)[0]["id"]
    tag = db_conn.execute(
        "SELECT t.name FROM tags t JOIN split_tags st ON st.tag_id = t.id WHERE st.split_id = ?", (split_id,)
    ).fetchone()
    assert tag["name"] == "daily"


def test_set_note_only_applies_when_note_is_empty(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    db_conn.execute("UPDATE transactions SET note = 'my own note' WHERE id = ?", (txn_id,))
    db_conn.commit()

    create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_note": "rule note"},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_transaction(db_conn, txn_id)["note"] == "my own note"


def test_mark_reviewed_action(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_category": "Coffee", "mark_reviewed": True},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_transaction(db_conn, txn_id)["reviewed"] == 1


# ---------------------------------------------------------------------------
# Rule-split failures queue
# ---------------------------------------------------------------------------


def test_get_rule_split_failures_surfaces_unreconciled_templates(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    _person_id(db_conn, "Roommate A")
    _person_id(db_conn, "Roommate B")
    txn_id = _insert_txn(db_conn, scotia, "2025-09-21", "withdrawal | Free Interac E-Transfer", -60000)

    create_rule(
        db_conn,
        name="Rent",
        conditions={"field": "description", "op": "contains", "value": "E-TRANSFER"},
        actions={
            "split_template": [
                {"share": "fixed", "value": 351.00, "category": "Housing > Rent"},
                {"share": "fixed", "value": 351.00, "person": "Roommate A"},
                {"share": "remainder", "person": "Roommate B"},
            ]
        },
    )
    failures = get_rule_split_failures(db_conn)
    assert len(failures) == 1
    assert failures[0]["transaction_id"] == txn_id
    assert failures[0]["rule_name"] == "Rent"


def test_get_rule_split_failures_ignores_reviewed_transactions(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    _person_id(db_conn, "Roommate A")
    txn_id = _insert_txn(db_conn, scotia, "2025-09-21", "withdrawal | Free Interac E-Transfer", -60000)
    set_reviewed(db_conn, txn_id, True)

    create_rule(
        db_conn,
        name="Rent",
        conditions={"field": "description", "op": "contains", "value": "E-TRANSFER"},
        actions={"split_template": [{"share": "fixed", "value": 351.00, "person": "Roommate A"}]},
    )
    assert get_rule_split_failures(db_conn) == []


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------


def test_create_update_delete_rule(db_conn):
    rule_id = create_rule(
        db_conn, name="Draft", conditions={"field": "description", "op": "contains", "value": "X"},
        actions={"set_type": "ignore"},
    )
    assert get_rule(db_conn, rule_id)["name"] == "Draft"

    update_rule(
        db_conn, rule_id, name="Renamed", conditions={"field": "description", "op": "contains", "value": "Y"},
        actions={"set_type": "expense"}, priority=42, enabled=False, stop_processing=True,
    )
    updated = get_rule(db_conn, rule_id)
    assert updated["name"] == "Renamed"
    assert updated["priority"] == 42
    assert updated["enabled"] is False
    assert updated["stop_processing"] is True

    delete_rule(db_conn, rule_id)
    assert all(r["id"] != rule_id for r in list_rules(db_conn))


def test_delete_rule_detaches_splits_it_created_instead_of_failing(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450)
    rule_id = create_rule(
        db_conn, name="Coffee", conditions={"field": "description", "op": "contains", "value": "COFFEE"},
        actions={"set_category": "Coffee"},
    )
    apply_rules_to_transactions(db_conn, [txn_id])
    assert get_splits(db_conn, txn_id)[0]["created_by_rule"] == rule_id

    delete_rule(db_conn, rule_id)  # must not raise a foreign key error
    splits = get_splits(db_conn, txn_id)
    assert len(splits) == 1  # the split itself survives
    assert splits[0]["created_by_rule"] is None


# ---------------------------------------------------------------------------
# "Make this a rule"
# ---------------------------------------------------------------------------


def test_suggest_rule_draft_skips_generic_words_for_the_keyword(db_conn):
    """'pos purchase' shows up on nearly every card transaction — the
    suggested keyword should land on the actual merchant name ('Coffee'),
    not a generic word that would match everything."""
    scotia = account_id(db_conn, "Scotia Chequing")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450, txn_type="expense")

    draft = suggest_rule_draft(db_conn, txn_id)
    keyword = draft["conditions"]["all"][0]["value"]
    assert keyword == "COFFEE"


def test_suggest_rule_draft_prefills_from_a_categorized_transaction(db_conn):
    scotia = account_id(db_conn, "Scotia Chequing")
    coffee_cat = _category_id(db_conn, "Coffee")
    txn_id = _insert_txn(db_conn, scotia, "2025-08-18", "pos purchase | Fake Coffee Shop", -450, txn_type="expense")
    replace_splits(db_conn, txn_id, [{"amount": -450, "category_id": coffee_cat}])

    draft = suggest_rule_draft(db_conn, txn_id)
    assert draft["actions"]["set_type"] == "expense"
    assert draft["actions"]["set_category"] == "Coffee"
    ctx = {"description": "pos purchase | Fake Coffee Shop", "account": "Scotia Chequing", "direction": "out", "amount_abs": 450}
    assert rule_matches(draft["conditions"], ctx)


# ---------------------------------------------------------------------------
# End-to-end: wired into import
# ---------------------------------------------------------------------------


def test_rule_applies_automatically_on_import(db_conn, fixtures_dir):
    scotia = account_id(db_conn, "Scotia Chequing")
    create_rule(
        db_conn, name="Payroll -> income", conditions={"field": "description", "op": "contains", "value": "PAYROLL"},
        actions={"set_type": "income"},
    )
    result = import_file(db_conn, scotia, "scotia_sample.csv", (fixtures_dir / "scotia_sample.csv").read_bytes())
    assert result["rules_matched"] == 1

    payroll_txn = db_conn.execute("SELECT * FROM transactions WHERE description LIKE '%payroll%'").fetchone()
    assert payroll_txn["type"] == "income"
