import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from core.coverage import get_coverage
from core.db import init_db
from core.importers import import_file
from core.importers.detect import detect_profile, load_profiles
from core.importers.normalize import normalize_row
from core.importers.parse import parse_csv

st.set_page_config(page_title="Import", layout="wide")
st.title("Import bank CSVs")

conn = init_db()

accounts = conn.execute("SELECT * FROM accounts WHERE active = 1 ORDER BY name").fetchall()
if not accounts:
    st.error("No accounts configured. Add one in Settings first.")
    st.stop()

account_by_id = {a["id"]: a for a in accounts}
profile_by_id = {p["id"]: p for p in load_profiles()}


def _sniff_headers(file_bytes: bytes) -> list[str]:
    try:
        first_line = file_bytes.decode("utf-8-sig").splitlines()[0]
    except (IndexError, UnicodeDecodeError):
        return []
    return next(csv.reader([first_line]), [])


uploaded_files = st.file_uploader(
    "Upload one or more CSV exports", type=["csv"], accept_multiple_files=True
)

selections: dict[str, tuple] = {}

if uploaded_files:
    st.subheader("Review before importing")

    for uf in uploaded_files:
        file_bytes = uf.getvalue()
        headers = _sniff_headers(file_bytes)
        matches = detect_profile(headers)
        match_ids = {m["id"] for m in matches}
        candidate_account_ids = [a["id"] for a in accounts if a["profile_id"] in match_ids]

        st.markdown(f"**{uf.name}**")

        if len(candidate_account_ids) == 1:
            default_index = [a["id"] for a in accounts].index(candidate_account_ids[0])
        else:
            default_index = 0
            if len(candidate_account_ids) > 1:
                st.warning("Header row matches more than one account. Pick the right one.")
            elif matches:
                st.warning(
                    "Header row matched a bank profile, but no account uses it. "
                    "Check Settings, or pick the closest account manually."
                )
            else:
                st.warning("Couldn't auto-detect a bank profile from the header row. Pick the account manually.")

        chosen_id = st.selectbox(
            f"Account for {uf.name}",
            options=[a["id"] for a in accounts],
            index=default_index,
            format_func=lambda aid: f"{account_by_id[aid]['name']} ({account_by_id[aid]['profile_id']})",
            key=f"account_select_{uf.name}",
        )
        chosen_account = account_by_id[chosen_id]
        selections[uf.name] = (uf, chosen_account)

        profile = profile_by_id.get(chosen_account["profile_id"])
        if profile is None:
            st.error(f"No profile file found for '{chosen_account['profile_id']}'.")
            continue

        try:
            raw_rows, _ = parse_csv(file_bytes, profile)
            preview_rows = []
            for raw in raw_rows[:5]:
                norm = normalize_row(raw, profile)
                if norm:
                    preview_rows.append(
                        {
                            "Date": norm["txn_date"],
                            "Description": norm["description"],
                            "Amount": f"{norm['amount_cents'] / 100:.2f}",
                        }
                    )
            if preview_rows:
                st.dataframe(preview_rows, use_container_width=True)
            else:
                st.info("No previewable rows found with this profile — check the account selection.")
        except Exception as exc:  # malformed file / wrong profile picked
            st.error(f"Couldn't preview this file with the selected profile: {exc}")

    if st.button("Import all files", type="primary"):
        results = []
        for filename, (uf, chosen_account) in selections.items():
            result = import_file(conn, chosen_account["id"], filename, uf.getvalue())
            result["file_name"] = filename
            result["account"] = chosen_account["name"]
            results.append(result)

        st.success("Import complete.")
        st.dataframe(
            [
                {
                    "File": r["file_name"],
                    "Account": r["account"],
                    "Total rows": r["rows_total"],
                    "New": r["rows_new"],
                    "Duplicates": r["rows_duplicate"],
                    "Skipped": r["rows_skipped"],
                    "Date range": f"{r['date_from']} – {r['date_to']}" if r["date_from"] else "n/a",
                }
                for r in results
            ],
            use_container_width=True,
        )
        st.rerun()

st.divider()
st.subheader("Import history")

batches = conn.execute(
    "SELECT ib.*, a.name AS account_name FROM import_batches ib "
    "JOIN accounts a ON a.id = ib.account_id ORDER BY ib.imported_at DESC"
).fetchall()

if not batches:
    st.info("No imports yet.")
else:
    for b in batches:
        cols = st.columns([3, 2, 3, 2, 2, 1])
        cols[0].write(b["file_name"])
        cols[1].write(b["account_name"])
        cols[2].write(f"{b['date_from']} – {b['date_to']}" if b["date_from"] else "n/a")
        cols[3].write(f"{b['rows_new']} new")
        cols[4].write(f"{b['rows_duplicate']} dup")

        confirm_key = f"confirm_delete_{b['id']}"
        if cols[5].button("Delete", key=f"delete_btn_{b['id']}"):
            st.session_state[confirm_key] = True

        if st.session_state.get(confirm_key):
            st.warning(f"Delete batch '{b['file_name']}' and all its transactions? This cannot be undone.")
            c1, c2 = st.columns(2)
            if c1.button("Yes, delete", key=f"confirm_yes_{b['id']}"):
                conn.execute("DELETE FROM import_batches WHERE id = ?", (b["id"],))
                conn.commit()
                del st.session_state[confirm_key]
                st.rerun()
            if c2.button("Cancel", key=f"confirm_no_{b['id']}"):
                del st.session_state[confirm_key]
                st.rerun()

st.divider()
st.subheader("Coverage")

for a in accounts:
    coverage = get_coverage(conn, a["id"])
    st.markdown(f"**{a['name']}**")
    if not coverage:
        st.caption("No data imported yet.")
    else:
        st.table([{"From": c[0], "To": c[1]} for c in coverage])
