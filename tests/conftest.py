import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.db import init_db


@pytest.fixture
def db_conn(tmp_path):
    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
def fixtures_dir():
    return Path(__file__).resolve().parent / "fixtures"


def account_id(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    assert row is not None, f"expected seeded account '{name}'"
    return row["id"]
