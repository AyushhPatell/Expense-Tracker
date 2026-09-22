import hashlib
import re
from collections import defaultdict


def normalize_description(description: str) -> str:
    return re.sub(r"\s+", " ", description or "").strip().upper()


def compute_fingerprint(
    account_id: int, txn_date: str, amount_cents: int, description: str, occurrence_index: int
) -> str:
    norm_desc = normalize_description(description)
    key = f"{account_id}|{txn_date}|{amount_cents}|{norm_desc}|{occurrence_index}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def assign_occurrence_indexes(rows: list[dict]) -> list[int]:
    """Return the 1-based occurrence index of each row among rows in this same
    file sharing the same (txn_date, amount_cents, description). Counting is
    scoped to a single file/import call, not the whole account history: that's
    what lets a later overlapping statement reproduce the same fingerprints
    for the rows it re-exports (correctly deduped) while two genuine same-day
    identical charges within one file still get distinct fingerprints.
    """
    counts: dict[tuple, int] = defaultdict(int)
    indexes = []
    for row in rows:
        key = (row["txn_date"], row["amount_cents"], normalize_description(row["description"]))
        counts[key] += 1
        indexes.append(counts[key])
    return indexes
