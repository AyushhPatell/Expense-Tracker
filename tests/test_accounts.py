import pytest

from core.accounts import create_account, list_accounts, list_profile_choices, update_account


def test_list_profile_choices_returns_seeded_bank_profiles():
    choices = list_profile_choices()
    ids = {c["id"] for c in choices}
    assert {"scotia_chequing", "rogers_mc"}.issubset(ids)


def test_create_account(db_conn):
    account_id = create_account(db_conn, "TFSA", "savings", "scotia_chequing")
    accounts = {a["name"]: a for a in list_accounts(db_conn)}
    assert "TFSA" in accounts
    assert accounts["TFSA"]["id"] == account_id
    assert accounts["TFSA"]["active"] == 1


def test_create_account_rejects_duplicate_name(db_conn):
    with pytest.raises(ValueError):
        create_account(db_conn, "Scotia Chequing", "chequing", "scotia_chequing")


def test_create_account_rejects_blank_name(db_conn):
    with pytest.raises(ValueError):
        create_account(db_conn, "   ", "chequing", "scotia_chequing")


def test_create_account_rejects_unknown_kind(db_conn):
    with pytest.raises(ValueError):
        create_account(db_conn, "New", "crypto_wallet", "scotia_chequing")


def test_update_account_renames_and_deactivates(db_conn):
    account_id = create_account(db_conn, "TFSA", "savings", "scotia_chequing")
    update_account(db_conn, account_id, "TFSA (old)", "savings", "scotia_chequing", active=False)

    accounts = {a["id"]: a for a in list_accounts(db_conn)}
    assert accounts[account_id]["name"] == "TFSA (old)"
    assert accounts[account_id]["active"] == 0

    active_only = [a["id"] for a in list_accounts(db_conn, active_only=True)]
    assert account_id not in active_only


def test_update_account_rejects_renaming_to_an_existing_name(db_conn):
    create_account(db_conn, "TFSA", "savings", "scotia_chequing")
    other_id = create_account(db_conn, "RRSP", "savings", "scotia_chequing")
    with pytest.raises(ValueError):
        update_account(db_conn, other_id, "TFSA", "savings", "scotia_chequing", active=True)
