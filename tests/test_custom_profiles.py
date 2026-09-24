import pytest

from core.importers.custom_profiles import (
    delete_custom_profile,
    get_custom_profile,
    list_custom_profiles,
    save_custom_profile,
    slugify,
    unique_profile_id,
)
from core.importers.detect import detect_profile, load_all_profiles, load_profiles


def _sample_parse_cfg():
    return {
        "encoding": "utf-8-sig",
        "skip_rows": 0,
        "date_column": "Date",
        "date_format": "%Y-%m-%d",
        "description_columns": ["Description"],
        "amount": {"mode": "single", "column": "Amount", "invert": False},
        "currency_column": None,
        "skip_if": [],
    }


def test_slugify():
    assert slugify("TD Chequing") == "td_chequing"
    assert slugify("  Weird!! Name??  ") == "weird_name"
    assert slugify("") == "profile"


def test_unique_profile_id_avoids_builtin_collision(db_conn):
    builtin_ids = {p["id"] for p in load_profiles()}
    new_id = unique_profile_id(db_conn, "Scotia Chequing", builtin_ids)
    assert new_id != "scotia_chequing"
    assert new_id.startswith("scotia_chequing")


def test_unique_profile_id_avoids_existing_custom_collision(db_conn):
    builtin_ids = {p["id"] for p in load_profiles()}
    first_id = unique_profile_id(db_conn, "TD Chequing", builtin_ids)
    save_custom_profile(db_conn, first_id, "TD Chequing", ["Date", "Amount"], _sample_parse_cfg())

    second_id = unique_profile_id(db_conn, "TD Chequing", builtin_ids)
    assert second_id != first_id


def test_save_and_list_custom_profile(db_conn):
    save_custom_profile(db_conn, "td_chequing", "TD Chequing", ["Date", "Description", "Amount"], _sample_parse_cfg())
    profiles = list_custom_profiles(db_conn)
    assert len(profiles) == 1
    assert profiles[0]["id"] == "td_chequing"
    assert profiles[0]["display_name"] == "TD Chequing"
    assert profiles[0]["detect"]["headers"] == ["Date", "Description", "Amount"]
    assert profiles[0]["parse"]["date_column"] == "Date"


def test_save_custom_profile_upserts(db_conn):
    save_custom_profile(db_conn, "td_chequing", "TD Chequing", ["Date"], _sample_parse_cfg())
    updated_cfg = _sample_parse_cfg()
    updated_cfg["date_column"] = "Transaction Date"
    save_custom_profile(db_conn, "td_chequing", "TD Chequing (renamed)", ["Date"], updated_cfg)

    profiles = list_custom_profiles(db_conn)
    assert len(profiles) == 1
    assert profiles[0]["display_name"] == "TD Chequing (renamed)"
    assert profiles[0]["parse"]["date_column"] == "Transaction Date"


def test_save_custom_profile_rejects_blank_name(db_conn):
    with pytest.raises(ValueError):
        save_custom_profile(db_conn, "x", "   ", ["Date"], _sample_parse_cfg())


def test_get_custom_profile_returns_none_when_missing(db_conn):
    assert get_custom_profile(db_conn, "nope") is None


def test_delete_custom_profile(db_conn):
    save_custom_profile(db_conn, "td_chequing", "TD Chequing", ["Date"], _sample_parse_cfg())
    delete_custom_profile(db_conn, "td_chequing")
    assert list_custom_profiles(db_conn) == []


def test_delete_custom_profile_in_use_is_refused(db_conn):
    save_custom_profile(db_conn, "td_chequing", "TD Chequing", ["Date"], _sample_parse_cfg())
    db_conn.execute(
        "INSERT INTO accounts (name, kind, profile_id, active) VALUES ('My TD', 'chequing', 'td_chequing', 1)"
    )
    db_conn.commit()
    with pytest.raises(ValueError):
        delete_custom_profile(db_conn, "td_chequing")


def test_load_all_profiles_includes_custom_ones(db_conn):
    save_custom_profile(db_conn, "td_chequing", "TD Chequing", ["Date", "Amount"], _sample_parse_cfg())
    ids = {p["id"] for p in load_all_profiles(db_conn)}
    assert {"scotia_chequing", "rogers_mc", "td_chequing"}.issubset(ids)


def test_detect_profile_finds_a_custom_profile_by_headers(db_conn):
    save_custom_profile(
        db_conn, "td_chequing", "TD Chequing", ["Date", "Description", "Amount"], _sample_parse_cfg()
    )
    all_profiles = load_all_profiles(db_conn)
    matches = detect_profile(["Date", "Description", "Amount", "Balance"], profiles=all_profiles)
    assert any(p["id"] == "td_chequing" for p in matches)
