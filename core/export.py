"""Local export (spec 10.7): transactions and reports to CSV/Excel. Nothing
leaves the machine here — this only builds bytes in memory; the Settings
page hands them to Streamlit's own download_button, which saves straight to
the browser's local Downloads folder, no network call involved."""

import io

import pandas as pd

from core.splits import get_splits


def transactions_dataframe(conn, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """One row per transaction, with its split lines flattened into a single
    readable summary column — detailed enough to be useful without exploding
    into a separate row per split line."""
    where = ["1 = 1"]
    params: list = []
    if start_date:
        where.append("t.txn_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("t.txn_date <= ?")
        params.append(end_date)

    rows = conn.execute(
        f"SELECT t.*, a.name AS account_name FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {' AND '.join(where)} "
        f"ORDER BY t.txn_date, t.id",
        params,
    ).fetchall()

    records = []
    for r in rows:
        splits = get_splits(conn, r["id"])
        parts = []
        for s in splits:
            label = s["category_name"] or s["person_name"] or ("Splitwise" if s["is_splitwise"] else "Uncategorized")
            parts.append(f"{label}: ${s['amount'] / 100:.2f}")
        records.append(
            {
                "Date": r["txn_date"],
                "Account": r["account_name"],
                "Description": r["description"],
                "Amount": r["amount"] / 100,
                "Type": r["type"],
                "Reviewed": bool(r["reviewed"]),
                "Note": r["note"] or "",
                "Splits": "; ".join(parts),
            }
        )
    return pd.DataFrame(records)


def spending_by_category_dataframe(conn, start_date: str, end_date: str) -> pd.DataFrame:
    from core.reports import spending_by_category

    rows = spending_by_category(conn, start_date, end_date)
    return pd.DataFrame([{"Category": r["category"], "Amount": -r["amount"] / 100} for r in rows]).sort_values(
        "Amount", ascending=False
    )


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buffer.getvalue()
