"""Local backup/restore (spec 10.6): copy tracker.db via SQLite's own
backup API — safe to run against a live database, unlike a raw file copy,
which could grab a half-written page mid-write — and prune to the last N.

Paths are passed in by the caller rather than read from core.config here,
so this module has no implicit dependency on which data directory is
"live" — the Settings page passes get_data_dir()/get_db_path() explicitly,
and tests pass a tmp_path, without needing to fake an environment variable.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_KEEP_LAST = 10


def create_backup(conn: sqlite3.Connection, backups_dir: Path, keep_last: int = DEFAULT_KEEP_LAST) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond resolution (not just HH:MM:SS) so two backups triggered in
    # quick succession never collide on the same filename.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backups_dir / f"tracker-{timestamp}.db"

    dest = sqlite3.connect(backup_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()

    prune_backups(backups_dir, keep_last)
    return backup_path


def list_backups(backups_dir: Path) -> list[Path]:
    if not backups_dir.exists():
        return []
    return sorted(backups_dir.glob("tracker-*.db"), reverse=True)  # newest first


def prune_backups(backups_dir: Path, keep_last: int) -> int:
    to_delete = list_backups(backups_dir)[keep_last:]
    for path in to_delete:
        path.unlink()
    return len(to_delete)


def restore_backup(backup_path: Path, db_path: Path) -> None:
    """Overwrites db_path's contents with backup_path's, via the backup API
    again (not a raw file copy). Whoever calls this must treat any existing
    connection to db_path as invalid afterward — open a fresh one (or, in
    the Streamlit app, restart the server) rather than continuing to use it."""
    source = sqlite3.connect(backup_path)
    dest = sqlite3.connect(db_path)
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()
