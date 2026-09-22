def get_coverage(conn, account_id: int) -> list[tuple[str, str]]:
    """Merged covered date ranges for an account, as (date_from, date_to) ISO
    date string pairs, sorted and with overlapping/adjacent batches merged."""
    rows = conn.execute(
        "SELECT date_from, date_to FROM import_batches "
        "WHERE account_id = ? AND date_from IS NOT NULL AND date_to IS NOT NULL "
        "ORDER BY date_from",
        (account_id,),
    ).fetchall()

    intervals = [(r["date_from"], r["date_to"]) for r in rows]
    if not intervals:
        return []

    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
