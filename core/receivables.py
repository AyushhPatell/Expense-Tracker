"""Owed-to-me balances: how much each person currently owes me (or I owe
them, if negative), computed from every OPEN (not settled) split ever tagged
to that person — regardless of the parent transaction's type or sign.

A split's sign carries the meaning: a negative person-split is money fronted
for them (they owe me more); a positive one is money they've paid back
(reduces what they owe). This works for both the "shared expense, partial
receivable" pattern (spec 8.2, e.g. rent) and a plain personal loan (the
whole transaction tagged to one person, no category) — same mechanism
either way. A split can also carry a category purely as a descriptive tag
even while person-owed (e.g. "$351 from Kavan, for Rent") — person_id alone
governs the exclusion from spending, not category presence.

'Settled' lets old activity — especially stuff reconciled entirely inside an
external app like Splitwise, which this tool never sees a matching transfer
for — stop inflating the balance forever, without deleting the record.
'Flagged' marks an entry for later follow-up, independent of settling.
"""


def person_balances(conn) -> list[dict]:
    """Net OPEN balance per active person. Positive = they owe me; negative
    = I owe them (e.g. they overpaid). Settled splits don't count."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, COALESCE(SUM(s.amount), 0) AS net
        FROM people p
        LEFT JOIN splits s ON s.person_id = p.id AND s.settled = 0
        WHERE p.active = 1
        GROUP BY p.id, p.name
        ORDER BY p.name
        """
    ).fetchall()
    return [{"person_id": r["id"], "person": r["name"], "balance": -r["net"]} for r in rows]


def person_ledger(conn, person_id: int) -> list:
    """Every split ever tagged to this person, oldest first — open and
    settled alike, with category/note/flag state, for a full paper trail
    behind the balance. Callers decide how much of it to show."""
    return conn.execute(
        """
        SELECT s.id AS split_id, t.txn_date, t.description, t.type, s.amount,
               s.note, s.settled, s.flagged,
               COALESCE(parent.name || ' > ' || cat.name, cat.name) AS category
        FROM splits s
        JOIN transactions t ON t.id = s.transaction_id
        LEFT JOIN categories cat ON cat.id = s.category_id
        LEFT JOIN categories parent ON parent.id = cat.parent_id
        WHERE s.person_id = ?
        ORDER BY t.txn_date, s.id
        """,
        (person_id,),
    ).fetchall()


def settle_through(conn, person_id: int, cutoff_date: str) -> int:
    """Mark every OPEN split for this person dated on/before cutoff_date as
    settled — 'everything up to here is reconciled, stop counting it.'
    Returns how many splits were affected."""
    cur = conn.execute(
        """
        UPDATE splits
        SET settled = 1
        WHERE person_id = ? AND settled = 0
          AND transaction_id IN (SELECT id FROM transactions WHERE txn_date <= ?)
        """,
        (person_id, cutoff_date),
    )
    conn.commit()
    return cur.rowcount


def unsettle_all(conn, person_id: int) -> int:
    """Undo: reopen every settled split for this person. A safety net for
    settling the wrong cutoff by mistake."""
    cur = conn.execute("UPDATE splits SET settled = 0 WHERE person_id = ? AND settled = 1", (person_id,))
    conn.commit()
    return cur.rowcount


def set_flag(conn, split_id: int, flagged: bool) -> None:
    conn.execute("UPDATE splits SET flagged = ? WHERE id = ?", (1 if flagged else 0, split_id))
    conn.commit()
