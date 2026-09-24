from core.settings import (
    DEFAULT_TRANSFER_KEYWORDS,
    DEFAULT_TRANSFER_WINDOW_DAYS,
    get_setting,
    get_transfer_keywords,
    get_transfer_window_days,
    set_setting,
    set_transfer_matching,
)


def test_get_setting_returns_default_when_unset(db_conn):
    assert get_setting(db_conn, "nope", "fallback") == "fallback"
    assert get_setting(db_conn, "nope") is None


def test_set_and_get_setting_round_trips_json_types(db_conn):
    set_setting(db_conn, "a_number", 42)
    set_setting(db_conn, "a_list", ["x", "y"])
    set_setting(db_conn, "a_string", "hello")
    assert get_setting(db_conn, "a_number") == 42
    assert get_setting(db_conn, "a_list") == ["x", "y"]
    assert get_setting(db_conn, "a_string") == "hello"


def test_set_setting_overwrites_existing_value(db_conn):
    set_setting(db_conn, "k", "first")
    set_setting(db_conn, "k", "second")
    assert get_setting(db_conn, "k") == "second"


def test_transfer_matching_defaults(db_conn):
    assert get_transfer_window_days(db_conn) == DEFAULT_TRANSFER_WINDOW_DAYS
    assert get_transfer_keywords(db_conn) == DEFAULT_TRANSFER_KEYWORDS


def test_set_transfer_matching(db_conn):
    set_transfer_matching(db_conn, 14, ["ETRANSFER", "XFER"])
    assert get_transfer_window_days(db_conn) == 14
    assert get_transfer_keywords(db_conn) == ["ETRANSFER", "XFER"]
