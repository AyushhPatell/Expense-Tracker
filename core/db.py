import sqlite3
from pathlib import Path

from core.config import get_db_path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def get_connection(db_path=None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_version")}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version in applied:
            continue
        conn.executescript(path.read_text())
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        conn.commit()


def init_db(db_path=None) -> sqlite3.Connection:
    conn = get_connection(db_path)
    run_migrations(conn)
    return conn
