from datetime import datetime, timezone

from core.fingerprint import assign_occurrence_indexes, compute_fingerprint
from core.importers.detect import load_all_profiles
from core.importers.normalize import normalize_row
from core.importers.parse import parse_csv
from core.rules import apply_rules_to_transactions
from core.settings import get_transfer_keywords, get_transfer_window_days
from core.transfers import apply_auto_pairing


def get_profile_for_account(conn, account_id: int) -> dict:
    row = conn.execute("SELECT profile_id FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown account id {account_id}")
    profiles = {p["id"]: p for p in load_all_profiles(conn)}
    profile = profiles.get(row["profile_id"])
    if profile is None:
        raise ValueError(f"No bank profile found for id '{row['profile_id']}'")
    return profile


def import_file(conn, account_id: int, filename: str, file_bytes: bytes, min_date: str | None = None) -> dict:
    """Parse, normalize, deduplicate, and insert one uploaded CSV file for one account.

    Raw bank data is never edited: this only ever inserts new transaction rows.

    min_date (ISO date string), when given, drops rows dated before it before
    they're ever inserted — e.g. importing an overlapping statement just to
    fill a coverage gap, without pulling in a stretch of history you've
    deliberately decided not to track. This is a hard exclusion, not a
    'reviewed as ignore' marker: those rows never touch the database at all.
    """
    profile = get_profile_for_account(conn, account_id)
    raw_rows, skipped_parse = parse_csv(file_bytes, profile)

    normalized = []
    skipped_unparseable = 0
    skipped_before_cutoff = 0
    for raw_row in raw_rows:
        norm = normalize_row(raw_row, profile)
        if norm is None:
            skipped_unparseable += 1
            continue
        if min_date and norm["txn_date"] < min_date:
            skipped_before_cutoff += 1
            continue
        normalized.append(norm)

    # Occurrence index counts identical (date, amount, description) rows within THIS
    # file only, so re-importing an overlapping statement reproduces the same
    # fingerprints for the rows it shares, while two genuine same-day identical
    # charges in one file still get distinct fingerprints.
    occurrence_indexes = assign_occurrence_indexes(normalized)

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO import_batches "
        "(account_id, file_name, imported_at, date_from, date_to, rows_total, rows_new, rows_duplicate) "
        "VALUES (?, ?, ?, NULL, NULL, ?, 0, 0)",
        (account_id, filename, now, len(normalized)),
    )
    batch_id = cur.lastrowid

    rows_new = 0
    rows_duplicate = 0
    dates = []
    new_transaction_ids = []

    for row, occ_index in zip(normalized, occurrence_indexes):
        dates.append(row["txn_date"])
        fingerprint = compute_fingerprint(
            account_id, row["txn_date"], row["amount_cents"], row["description"], occ_index
        )
        existing = conn.execute(
            "SELECT id FROM transactions WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if existing is not None:
            rows_duplicate += 1
            continue
        cur = conn.execute(
            "INSERT INTO transactions "
            "(account_id, batch_id, txn_date, posted_date, description, amount, currency, raw_row, fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                batch_id,
                row["txn_date"],
                row["posted_date"],
                row["description"],
                row["amount_cents"],
                row["currency"],
                row["raw_row"],
                fingerprint,
            ),
        )
        rows_new += 1
        new_transaction_ids.append(cur.lastrowid)

    date_from = min(dates) if dates else None
    date_to = max(dates) if dates else None
    conn.execute(
        "UPDATE import_batches SET date_from = ?, date_to = ?, rows_new = ?, rows_duplicate = ? WHERE id = ?",
        (date_from, date_to, rows_new, rows_duplicate, batch_id),
    )
    conn.commit()

    # Post-import steps (spec 6.4), in order: rules engine first, so a rule
    # can type a payment as 'transfer' (e.g. "Scotia -> Rogers"), then
    # transfer detection, which needs that new row (or its partner, imported
    # earlier or in this same batch) to actually pair against.
    rules_result = apply_rules_to_transactions(conn, new_transaction_ids)
    transfers_paired = apply_auto_pairing(
        conn, window_days=get_transfer_window_days(conn), keywords=get_transfer_keywords(conn)
    )

    return {
        "batch_id": batch_id,
        "rows_total": len(normalized),
        "rows_new": rows_new,
        "rows_duplicate": rows_duplicate,
        "rows_skipped": skipped_parse + skipped_unparseable,
        "rows_before_cutoff": skipped_before_cutoff,
        "date_from": date_from,
        "date_to": date_to,
        "rules_matched": rules_result["matched"],
        "transfers_paired": transfers_paired,
    }
