"""Account management (spec: Settings page, 'Accounts: add/edit, assign
profile'). Previously accounts only ever came from migrations/002_seed.sql —
this is the first way to add or edit one without touching SQL by hand."""

from core.importers.detect import load_all_profiles

ACCOUNT_KINDS = ("chequing", "savings", "credit", "other")


def list_accounts(conn, active_only: bool = False) -> list:
    query = "SELECT * FROM accounts"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    return conn.execute(query).fetchall()


def list_profile_choices(conn) -> list[dict]:
    """Built-in bank profiles plus this device's own custom ones, for the
    account add/edit form's profile picker."""
    return [{"id": p["id"], "display_name": p.get("display_name", p["id"])} for p in load_all_profiles(conn)]


def create_account(conn, name: str, kind: str, profile_id: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Account name can't be blank")
    if kind not in ACCOUNT_KINDS:
        raise ValueError(f"Unknown account kind '{kind}'")
    if conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"An account named '{name}' already exists")
    cur = conn.execute(
        "INSERT INTO accounts (name, kind, profile_id, active) VALUES (?, ?, ?, 1)", (name, kind, profile_id)
    )
    conn.commit()
    return cur.lastrowid


def update_account(conn, account_id: int, name: str, kind: str, profile_id: str, active: bool) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Account name can't be blank")
    if kind not in ACCOUNT_KINDS:
        raise ValueError(f"Unknown account kind '{kind}'")
    if conn.execute("SELECT id FROM accounts WHERE name = ? AND id != ?", (name, account_id)).fetchone():
        raise ValueError(f"An account named '{name}' already exists")
    conn.execute(
        "UPDATE accounts SET name = ?, kind = ?, profile_id = ?, active = ? WHERE id = ?",
        (name, kind, profile_id, 1 if active else 0, account_id),
    )
    conn.commit()
