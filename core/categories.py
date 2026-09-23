"""Flat listings of categories and people for dropdowns. Categories/people
management (add/rename/merge/deactivate) lands in Settings, Phase 7 — for now
this just reads what's seeded."""


def list_categories(conn) -> list[dict]:
    """Active categories as flat {id, label} pairs, 'Parent > Child' style,
    ordered so a parent always appears before its children."""
    rows = conn.execute(
        "SELECT id, name, parent_id FROM categories WHERE active = 1 ORDER BY parent_id IS NOT NULL, name"
    ).fetchall()
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
    """Add a person to split expenses with. Full rename/deactivate management
    is Phase 7 (Settings) — this is just the quick unblock for now."""
    name = name.strip()
    if not name:
        raise ValueError("Name can't be blank")
    existing = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute("INSERT INTO people (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid
