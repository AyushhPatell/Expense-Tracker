"""Flat listings of categories and people for dropdowns, plus (below)
category/people management for the Settings page: add/rename/merge/
deactivate."""


def list_categories(conn, include_inactive: bool = False) -> list[dict]:
    """Categories (active-only by default) as flat {id, label} pairs,
    'Parent > Child' style, ordered so a parent always appears before its
    children. include_inactive=True is for Settings, where a deactivated
    category still needs to be visible to manage (e.g. reactivate)."""
    query = "SELECT id, name, parent_id FROM categories"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY parent_id IS NOT NULL, name"
    rows = conn.execute(query).fetchall()
    by_id = {r["id"]: r for r in rows}

    def label(row) -> str:
        if row["parent_id"] and row["parent_id"] in by_id:
            return f"{by_id[row['parent_id']]['name']} > {row['name']}"
        return row["name"]

    parents = [r for r in rows if r["parent_id"] is None]
    children = [r for r in rows if r["parent_id"] is not None]
    ordered = sorted(parents, key=lambda r: r["name"])
    result = []
    for parent in ordered:
        result.append({"id": parent["id"], "label": parent["name"]})
        for child in sorted(
            [c for c in children if c["parent_id"] == parent["id"]], key=lambda r: r["name"]
        ):
            result.append({"id": child["id"], "label": label(child)})
    return result


def list_people(conn) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM people WHERE active = 1 ORDER BY name").fetchall()
    return [{"id": r["id"], "label": r["name"]} for r in rows]


def create_person(conn, name: str) -> int:
    """Add a person to split expenses with."""
    name = name.strip()
    if not name:
        raise ValueError("Name can't be blank")
    existing = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute("INSERT INTO people (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def rename_person(conn, person_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Name can't be blank")
    if conn.execute("SELECT id FROM people WHERE name = ? AND id != ?", (new_name, person_id)).fetchone():
        raise ValueError(f"'{new_name}' already exists")
    conn.execute("UPDATE people SET name = ? WHERE id = ?", (new_name, person_id))
    conn.commit()


def deactivate_person(conn, person_id: int) -> None:
    """Refuses to deactivate anyone with an open (unsettled) balance —
    otherwise they'd silently vanish from Owed to Me's balances page while
    still genuinely owing/being owed money (person_balances filters to
    active people only)."""
    balance = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS net FROM splits WHERE person_id = ? AND settled = 0", (person_id,)
    ).fetchone()["net"]
    if balance != 0:
        raise ValueError("This person has an open balance — settle up on the Owed to Me page first")
    conn.execute("UPDATE people SET active = 0 WHERE id = ?", (person_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Category management (spec: Settings page, 'categories: add/rename/merge/
# deactivate; merging reassigns splits')
# ---------------------------------------------------------------------------
#
# Rules (core/rules.py) reference a category by its text LABEL inside their
# stored conditions/actions JSON, not by id — renaming or merging a category
# here does NOT update any rule that mentions its old name. A rule with a
# stale set_category will just silently stop applying that action; a stale
# split_template will start failing and show up in the Rules page's
# 'Rule-split failures' queue. Both functions below are deliberately quiet
# about this in code (it's a UI-layer warning, not something to block on)
# but the Settings page surfaces a caption about it.


def create_category(conn, name: str, parent_id: int | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Category name can't be blank")
    existing = conn.execute(
        "SELECT id FROM categories WHERE name = ? AND parent_id IS ?", (name, parent_id)
    ).fetchone()
    if existing:
        raise ValueError(f"'{name}' already exists at this level")
    cur = conn.execute("INSERT INTO categories (name, parent_id) VALUES (?, ?)", (name, parent_id))
    conn.commit()
    return cur.lastrowid


def rename_category(conn, category_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Category name can't be blank")
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
    conn.commit()


def set_category_active(conn, category_id: int, active: bool) -> None:
    if not active:
        has_active_children = conn.execute(
            "SELECT COUNT(*) AS n FROM categories WHERE parent_id = ? AND active = 1", (category_id,)
        ).fetchone()["n"]
        if has_active_children:
            raise ValueError("Deactivate or reassign its child categories first")
    conn.execute("UPDATE categories SET active = ? WHERE id = ?", (1 if active else 0, category_id))
    conn.commit()


def merge_categories(conn, source_id: int, target_id: int) -> int:
    """Reassigns every split from source_id to target_id, then deactivates
    source_id. Returns how many splits were reassigned. Existing rules that
    reference source_id's old name by text are unaffected (see module note
    above) — check the Rules page afterward if this category was used in one."""
    if source_id == target_id:
        raise ValueError("Can't merge a category into itself")
    cur = conn.execute("UPDATE splits SET category_id = ? WHERE category_id = ?", (target_id, source_id))
    reassigned = cur.rowcount
    conn.execute("UPDATE categories SET active = 0 WHERE id = ?", (source_id,))
    conn.commit()
    return reassigned
