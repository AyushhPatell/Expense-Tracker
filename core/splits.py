"""Split creation/validation and the small per-transaction edits (type, note,
reviewed flag) that the Review page needs. Raw transaction fields are never
touched here — only the user/rule layer (splits, type, note, reviewed)."""


def get_transaction(conn, transaction_id: int):
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown transaction id {transaction_id}")
    return row


def get_splits(conn, transaction_id: int) -> list:
    return conn.execute(
        "SELECT s.*, c.name AS category_name, p.name AS person_name "
        "FROM splits s "
        "LEFT JOIN categories c ON c.id = s.category_id "
        "LEFT JOIN people p ON p.id = s.person_id "
        "WHERE s.transaction_id = ? ORDER BY s.id",
        (transaction_id,),
    ).fetchall()


def is_simple_split(conn, transaction_id: int) -> bool:
    """True if this transaction has no real split yet — 0 or 1 lines, and
    that one line (if present) isn't a receivable or Splitwise portion. Quick
    single-category edit paths (the Review grid's Category column, bulk
    actions) are only safe on a transaction in this state; overwriting a
    transaction that already has a real split would silently destroy it."""
    splits = get_splits(conn, transaction_id)
    return len(splits) <= 1 and all(
        s["person_id"] is None and not s["is_splitwise"] for s in splits
    )


def validate_split_lines(transaction_amount: int, lines: list[dict]) -> None:
    if not lines:
        raise ValueError("At least one split line is required")

    total = sum(line["amount"] for line in lines)
    if total != transaction_amount:
        raise ValueError(
            f"Split lines total ${total / 100:.2f} but the transaction is ${transaction_amount / 100:.2f}"
        )

    for line in lines:
        has_category = bool(line.get("category_id"))
        has_receivable = bool(line.get("person_id")) or bool(line.get("is_splitwise"))
        # Category can coexist with person/Splitwise — it's just a
        # descriptive tag then (e.g. "this $351 from Kavan was for Rent"),
        # since person_id/is_splitwise alone is what excludes a line from
        # spending totals, regardless of whether a category is also set.
        if not has_category and not has_receivable:
            raise ValueError("Each split line needs a category, a person, or Splitwise")


def replace_splits(
    conn, transaction_id: int, lines: list[dict], locked: bool = True, rule_id: int | None = None
) -> None:
    """Validate and overwrite all split lines for a transaction. This is the
    only way splits get written, so every save here counts as a manual edit
    (locked=1) unless the caller is a rule applying a fresh, unlocked split
    (locked=False, rule_id=that rule's id — see core/rules.py)."""
    txn = get_transaction(conn, transaction_id)
    validate_split_lines(txn["amount"], lines)

    conn.execute("DELETE FROM splits WHERE transaction_id = ?", (transaction_id,))
    for line in lines:
        conn.execute(
            "INSERT INTO splits "
            "(transaction_id, amount, category_id, person_id, is_splitwise, note, locked, created_by_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                transaction_id,
                line["amount"],
                line.get("category_id"),
                line.get("person_id"),
                1 if line.get("is_splitwise") else 0,
                line.get("note"),
                1 if locked else 0,
                rule_id,
            ),
        )
    conn.commit()


def set_single_category(
    conn, transaction_id: int, category_id: int, locked: bool = True, rule_id: int | None = None
) -> None:
    """Quick path for the common case: the whole transaction is one category,
    no split needed. Overwrites any existing split lines."""
    txn = get_transaction(conn, transaction_id)
    replace_splits(
        conn, transaction_id, [{"amount": txn["amount"], "category_id": category_id}], locked=locked, rule_id=rule_id
    )


def has_locked_split(conn, transaction_id: int) -> bool:
    """True if any split line on this transaction was set by hand. Rules must
    never overwrite these ('manual beats automatic', spec section 2) — unlike
    is_simple_split (which is only about whether a 1-line collapse is safe),
    this is the guard rules use before touching a transaction's splits at
    all, including multi-line ones they created themselves earlier."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM splits WHERE transaction_id = ? AND locked = 1", (transaction_id,)
    ).fetchone()
    return row["n"] > 0


def equal_shares(total_cents: int, n: int) -> list[int]:
    """Split total_cents into n integer shares that sum exactly to total_cents,
    distributing the leftover penny(s) across the first shares."""
    if n <= 0:
        raise ValueError("n must be positive")
    base = total_cents // n
    remainder = total_cents - base * n
    shares = [base] * n
    step = 1 if remainder > 0 else -1
    for i in range(abs(remainder)):
        shares[i] += step
    return shares


def set_type(conn, transaction_id: int, new_type: str, lock: bool = True) -> None:
    conn.execute(
        "UPDATE transactions SET type = ?, type_locked = ? WHERE id = ?",
        (new_type, 1 if lock else 0, transaction_id),
    )
    conn.commit()


def set_note(conn, transaction_id: int, note: str | None) -> None:
    conn.execute("UPDATE transactions SET note = ? WHERE id = ?", (note, transaction_id))
    conn.commit()


def set_reviewed(conn, transaction_id: int, reviewed: bool) -> None:
    conn.execute(
        "UPDATE transactions SET reviewed = ? WHERE id = ?", (1 if reviewed else 0, transaction_id)
    )
    conn.commit()
