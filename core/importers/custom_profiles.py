"""Self-service bank profiles: the same {detect, parse} shape as the
built-in YAML files in core/importers/profiles/, but created through the
Settings page's 'Add a bank profile' wizard and stored in THIS device's own
database instead of a repo file — so adding a new bank never means editing
YAML or code, and a friend's custom profile stays on their machine, never
in shared/version-controlled config.
"""

import json
import re
from datetime import datetime, timezone


def list_custom_profiles(conn) -> list[dict]:
    rows = conn.execute("SELECT id, display_name, config FROM bank_profiles ORDER BY display_name").fetchall()
    return [_row_to_profile(r) for r in rows]


def get_custom_profile(conn, profile_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, display_name, config FROM bank_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    return _row_to_profile(row) if row else None


def _row_to_profile(row) -> dict:
    cfg = json.loads(row["config"])
    return {"id": row["id"], "display_name": row["display_name"], **cfg}


def slugify(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_")
    return slug or "profile"


def unique_profile_id(conn, display_name: str, builtin_ids: set) -> str:
    """A friendly display name -> a safe, unique profile id, so nobody has
    to invent a technical identifier by hand."""
    base = slugify(display_name)
    existing_custom = {r["id"] for r in conn.execute("SELECT id FROM bank_profiles").fetchall()}
    taken = builtin_ids | existing_custom
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def save_custom_profile(
    conn, profile_id: str, display_name: str, detect_headers: list[str], parse_cfg: dict
) -> None:
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Profile name can't be blank")
    if not detect_headers:
        raise ValueError("No columns to detect this bank by")
    config = {"detect": {"headers": detect_headers}, "parse": parse_cfg}
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO bank_profiles (id, display_name, config, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name, config = excluded.config",
        (profile_id, display_name, json.dumps(config), now),
    )
    conn.commit()


def delete_custom_profile(conn, profile_id: str) -> None:
    in_use = conn.execute(
        "SELECT COUNT(*) AS n FROM accounts WHERE profile_id = ?", (profile_id,)
    ).fetchone()["n"]
    if in_use:
        raise ValueError("This profile is used by an account — change that account's profile first")
    conn.execute("DELETE FROM bank_profiles WHERE id = ?", (profile_id,))
    conn.commit()
