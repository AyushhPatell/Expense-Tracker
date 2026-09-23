from core.importers import import_file
from core.splits import set_type
from core.transfers import (
    apply_auto_pairing,
    confirm_pair,
    find_candidate_pairs,
    get_suggested_pairs,
    get_unmatched_transfers,
)
from tests.conftest import account_id


def _import_pair_fixtures(conn, fixtures_dir):
    scotia = account_id(conn, "Scotia Chequing")
    rogers = account_id(conn, "Rogers MC")
    import_file(conn, scotia, "scotia_transfer_pair.csv", (fixtures_dir / "scotia_transfer_pair.csv").read_bytes())
    import_file(conn, rogers, "rogers_transfer_pair.csv", (fixtures_dir / "rogers_transfer_pair.csv").read_bytes())
    return scotia, rogers


def _txn_by_amount(conn, amount):
    return conn.execute("SELECT * FROM transactions WHERE amount = ?", (amount,)).fetchone()


def test_high_confidence_pair_auto_applied_on_import(db_conn, fixtures_dir):
    """The literal Phase 3 'done when': a Scotia -> Rogers payment pairs
    automatically and both sides flip to 'transfer'."""
    _import_pair_fixtures(db_conn, fixtures_dir)

    scotia_payment = _txn_by_amount(db_conn, -50000)
    rogers_payment = _txn_by_amount(db_conn, 50000)

    assert scotia_payment["type"] == "transfer"
    assert rogers_payment["type"] == "transfer"

    link = db_conn.execute(
        "SELECT * FROM links WHERE kind = 'transfer_pair' AND "
        "((from_txn_id = ? AND to_txn_id = ?) OR (from_txn_id = ? AND to_txn_id = ?))",
        (scotia_payment["id"], rogers_payment["id"], rogers_payment["id"], scotia_payment["id"]),
    ).fetchone()
    assert link is not None
    assert link["auto"] == 1


def test_unrelated_transactions_not_paired(db_conn, fixtures_dir):
    _import_pair_fixtures(db_conn, fixtures_dir)

    coffee = _txn_by_amount(db_conn, -625)
    diner = _txn_by_amount(db_conn, -940)  # Rogers purchase, invert -> negative expense
    assert coffee["type"] == "unreviewed"
    assert diner["type"] == "unreviewed"


def test_low_confidence_pair_suggested_not_applied(db_conn, fixtures_dir):
    _import_pair_fixtures(db_conn, fixtures_dir)

    vendor_scotia = _txn_by_amount(db_conn, -2000)
    vendor_rogers = _txn_by_amount(db_conn, 2000)

    # No PAYMENT/TRANSFER keyword on either side -> not auto-applied.
    assert vendor_scotia["type"] == "unreviewed"
    assert vendor_rogers["type"] == "unreviewed"

    suggested = get_suggested_pairs(db_conn)
    ids = {(p["txn_a_id"], p["txn_b_id"]) for p in suggested}
    assert (vendor_scotia["id"], vendor_rogers["id"]) in ids or (
        vendor_rogers["id"],
        vendor_scotia["id"],
    ) in ids


def test_confirm_pair_links_and_types_low_confidence_pair(db_conn, fixtures_dir):
    _import_pair_fixtures(db_conn, fixtures_dir)
    vendor_scotia = _txn_by_amount(db_conn, -2000)
    vendor_rogers = _txn_by_amount(db_conn, 2000)

    confirm_pair(db_conn, vendor_scotia["id"], vendor_rogers["id"])

    a = db_conn.execute("SELECT type FROM transactions WHERE id = ?", (vendor_scotia["id"],)).fetchone()
    b = db_conn.execute("SELECT type FROM transactions WHERE id = ?", (vendor_rogers["id"],)).fetchone()
    assert a["type"] == "transfer"
    assert b["type"] == "transfer"

    link = db_conn.execute(
        "SELECT auto FROM links WHERE kind = 'transfer_pair' AND from_txn_id = ?", (vendor_scotia["id"],)
    ).fetchone()
    assert link["auto"] == 0


def test_confirm_pair_rejects_mismatched_amounts(db_conn, fixtures_dir):
    _import_pair_fixtures(db_conn, fixtures_dir)
    coffee = _txn_by_amount(db_conn, -625)
    diner = _txn_by_amount(db_conn, -940)

    try:
        confirm_pair(db_conn, coffee["id"], diner["id"])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_unmatched_transfer_when_only_one_side_imported(db_conn, fixtures_dir):
    """Phase 3 'done when': a payment with only one side imported shows as
    unmatched and is still excluded (type stays 'transfer', no link)."""
    rogers = account_id(db_conn, "Rogers MC")
    import_file(db_conn, rogers, "rogers_transfer_pair.csv", (fixtures_dir / "rogers_transfer_pair.csv").read_bytes())

    rogers_payment = _txn_by_amount(db_conn, 50000)
    # Nothing to auto-pair with yet; simulate a rule/manual flag recognizing
    # it as a transfer before its Scotia counterpart has been imported.
    set_type(db_conn, rogers_payment["id"], "transfer", lock=False)

    unmatched = get_unmatched_transfers(db_conn)
    assert any(u["id"] == rogers_payment["id"] for u in unmatched)

    # Now the Scotia side arrives; a re-run (or the next import's post-step)
    # should pair them and clear it from the unmatched list.
    scotia = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, scotia, "scotia_transfer_pair.csv", (fixtures_dir / "scotia_transfer_pair.csv").read_bytes())

    unmatched_after = get_unmatched_transfers(db_conn)
    assert not any(u["id"] == rogers_payment["id"] for u in unmatched_after)


def test_locked_transaction_not_offered_as_candidate(db_conn, fixtures_dir):
    scotia = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, scotia, "scotia_transfer_pair.csv", (fixtures_dir / "scotia_transfer_pair.csv").read_bytes())

    scotia_payment = _txn_by_amount(db_conn, -50000)
    set_type(db_conn, scotia_payment["id"], "expense")  # manual lock

    rogers = account_id(db_conn, "Rogers MC")
    import_file(db_conn, rogers, "rogers_transfer_pair.csv", (fixtures_dir / "rogers_transfer_pair.csv").read_bytes())

    refreshed = db_conn.execute(
        "SELECT type FROM transactions WHERE id = ?", (scotia_payment["id"],)
    ).fetchone()
    assert refreshed["type"] == "expense"  # not overwritten by auto-pairing


def test_manually_locked_transfer_still_pairs_with_its_match(db_conn, fixtures_dir):
    """A transaction the user already locked to 'transfer' by hand (e.g. via
    the grid) must still be linkable — the lock protects the type from being
    changed, not the transaction from ever being paired."""
    scotia = account_id(db_conn, "Scotia Chequing")
    import_file(db_conn, scotia, "scotia_transfer_pair.csv", (fixtures_dir / "scotia_transfer_pair.csv").read_bytes())

    scotia_payment = _txn_by_amount(db_conn, -50000)
    set_type(db_conn, scotia_payment["id"], "transfer")  # manual, locked
    assert (
        db_conn.execute(
            "SELECT type_locked FROM transactions WHERE id = ?", (scotia_payment["id"],)
        ).fetchone()["type_locked"]
        == 1
    )

    rogers = account_id(db_conn, "Rogers MC")
    import_file(db_conn, rogers, "rogers_transfer_pair.csv", (fixtures_dir / "rogers_transfer_pair.csv").read_bytes())

    link = db_conn.execute(
        "SELECT * FROM links WHERE kind = 'transfer_pair' AND (from_txn_id = ? OR to_txn_id = ?)",
        (scotia_payment["id"], scotia_payment["id"]),
    ).fetchone()
    assert link is not None

    unmatched = get_unmatched_transfers(db_conn)
    assert not any(u["id"] == scotia_payment["id"] for u in unmatched)


def test_one_to_one_pairing_does_not_double_claim(db_conn, fixtures_dir):
    scotia = account_id(db_conn, "Scotia Chequing")
    rogers = account_id(db_conn, "Rogers MC")
    import_file(db_conn, scotia, "scotia_transfer_pair.csv", (fixtures_dir / "scotia_transfer_pair.csv").read_bytes())
    import_file(db_conn, rogers, "rogers_transfer_pair.csv", (fixtures_dir / "rogers_transfer_pair.csv").read_bytes())

    pairs = find_candidate_pairs(db_conn)
    seen = set()
    for p in pairs:
        assert p["txn_a_id"] not in seen
        assert p["txn_b_id"] not in seen
        seen.add(p["txn_a_id"])
        seen.add(p["txn_b_id"])
