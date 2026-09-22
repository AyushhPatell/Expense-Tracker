import os
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "ExpenseTrackerData"


def get_data_dir() -> Path:
    """Where the database and backups live. Overridable so it can point at an
    encrypted volume. Defaults to a folder in the home directory, outside the repo."""
    data_dir = Path(os.environ.get("EXPENSE_TRACKER_DATA", DEFAULT_DATA_DIR)).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "backups").mkdir(exist_ok=True)
    return data_dir


def get_db_path() -> Path:
    return get_data_dir() / "tracker.db"
