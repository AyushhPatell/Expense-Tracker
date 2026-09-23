"""Transfer pair detection: credit-card payments and other moves between my
own accounts, so a purchase isn't counted once on the card and again as the
bill payment from chequing (spec section 8.1)."""

from datetime import date

DEFAULT_WINDOW_DAYS = 7
DEFAULT_KEYWORDS = ("PAYMENT", "TRANSFER")


def _unlinked_candidates(conn) -> list:
    """Transactions on active accounts that aren't already part of a
    transfer_pair link and aren't manually locked to some OTHER meaning.
    Respecting the lock mirrors 'manual beats automatic' (spec section 2):
    auto-pairing shouldn't relitigate a type the user chose on purpose. But a
    transaction the user already locked to 'transfer' by hand is exactly
    what pairing is for — the lock only needs to stop the *type* from being
    changed (create_transfer_link already respects that), not stop the link
    from being made at all."""
    return conn.execute(
        "SELECT t.id, t.account_id, t.txn_date, t.amount, t.description "
        "FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id AND a.active = 1 "
        "WHERE (t.type_locked = 0 OR t.type = 'transfer') "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM links l WHERE l.kind = 'transfer_pair' "
        "  AND (l.from_txn_id = t.id OR l.to_txn_id = t.id)"
        ")"
    ).fetchall()


def _matches_keyword(description: str, keywords) -> bool:
    upper = (description or "").upper()
    return any(kw.upper() in upper for kw in keywords)


def find_candidate_pairs(
    conn, window_days: int = DEFAULT_WINDOW_DAYS, keywords=DEFAULT_KEYWORDS
) -> list[dict]:
    """One-to-one candidate transfer pairs among currently-unlinked
    transactions: different accounts, opposite signs, identical absolute
    amount, dates within window_days. Smallest date gap wins; each
    transaction can appear in at most one returned pair."""
    candidates = _unlinked_candidates(conn)

    possible = []
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if a["account_id"] == b["account_id"]:
                continue
            if a["amount"] != -b["amount"]:
                continue
            gap = abs((date.fromisoformat(a["txn_date"]) - date.fromisoformat(b["txn_date"])).days)
            if gap > window_days:
                continue
            possible.append((gap, a, b))

    possible.sort(key=lambda p: p[0])

    claimed: set[int] = set()
    pairs = []
    for gap, a, b in possible:
        if a["id"] in claimed or b["id"] in claimed:
            continue
        claimed.add(a["id"])
        claimed.add(b["id"])
        confidence = (
            "high"
            if _matches_keyword(a["description"], keywords) or _matches_keyword(b["description"], keywords)
            else "low"
        )
        pairs.append(
            {
                "txn_a_id": a["id"],
                "txn_b_id": b["id"],
                "date_gap": gap,
                "amount": abs(a["amount"]),
                "confidence": confidence,
            }
        )
    return pairs


def create_transfer_link(conn, txn_a_id: int, txn_b_id: int, amount: int, auto: bool) -> None:
    conn.execute(
        "INSERT INTO links (kind, from_txn_id, to_txn_id, amount, auto) VALUES ('transfer_pair', ?, ?, ?, ?)",
        (txn_a_id, txn_b_id, amount, 1 if auto else 0),
    )
    for txn_id in (txn_a_id, txn_b_id):
        row = conn.execute("SELECT type_locked FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if row["type_locked"] == 0:
            conn.execute("UPDATE transactions SET type = 'transfer' WHERE id = ?", (txn_id,))
    conn.commit()


def apply_auto_pairing(
    conn, window_days: int = DEFAULT_WINDOW_DAYS, keywords=DEFAULT_KEYWORDS
) -> int:
    """Auto-link and mark 'transfer' every high-confidence candidate pair.
    Low-confidence ones are left for get_suggested_pairs to surface for
    manual confirmation instead of being applied silently (spec 8.1)."""
    applied = 0
    for pair in find_candidate_pairs(conn, window_days, keywords):
        if pair["confidence"] == "high":
            create_transfer_link(conn, pair["txn_a_id"], pair["txn_b_id"], pair["amount"], auto=True)
            applied += 1
    return applied


def get_suggested_pairs(
    conn, window_days: int = DEFAULT_WINDOW_DAYS, keywords=DEFAULT_KEYWORDS
) -> list[dict]:
    return [p for p in find_candidate_pairs(conn, window_days, keywords) if p["confidence"] == "low"]


def confirm_pair(conn, txn_a_id: int, txn_b_id: int) -> None:
    a = conn.execute("SELECT amount FROM transactions WHERE id = ?", (txn_a_id,)).fetchone()
    b = conn.execute("SELECT amount FROM transactions WHERE id = ?", (txn_b_id,)).fetchone()
    if a is None or b is None:
        raise ValueError("Unknown transaction id")
    if a["amount"] != -b["amount"]:
        raise ValueError("These transactions don't have matching opposite amounts")
    create_transfer_link(conn, txn_a_id, txn_b_id, abs(a["amount"]), auto=False)


def get_unmatched_transfers(conn) -> list:
    """Transactions typed 'transfer' (by pairing, a rule, or by hand) with no
    transfer_pair link — e.g. one side of a payment hasn't been imported yet,
    or its partner's import batch was deleted."""
    return conn.execute(
        "SELECT t.*, a.name AS account_name FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE t.type = 'transfer' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM links l WHERE l.kind = 'transfer_pair' "
        "  AND (l.from_txn_id = t.id OR l.to_txn_id = t.id)"
        ") ORDER BY t.txn_date DESC"
    ).fetchall()
