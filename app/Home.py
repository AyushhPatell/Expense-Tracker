import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from core.config import get_data_dir
from core.db import init_db

st.set_page_config(page_title="Expense Tracker", layout="wide")

conn = init_db()

st.title("Expense Tracker")
st.caption(f"Data stored locally at: {get_data_dir()}")

accounts = conn.execute("SELECT * FROM accounts WHERE active = 1 ORDER BY name").fetchall()
batch_count = conn.execute("SELECT COUNT(*) AS n FROM import_batches").fetchone()["n"]
txn_count = conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
unreviewed_count = conn.execute(
    "SELECT COUNT(*) AS n FROM transactions WHERE type = 'unreviewed'"
).fetchone()["n"]

col1, col2, col3 = st.columns(3)
col1.metric("Accounts", len(accounts))
col2.metric("Imported transactions", txn_count)
col3.metric("Unreviewed", unreviewed_count)

st.subheader("Accounts")
if accounts:
    st.table(
        [{"Name": a["name"], "Kind": a["kind"], "Profile": a["profile_id"]} for a in accounts]
    )
else:
    st.info("No accounts configured yet.")

st.divider()
st.write(
    "Go to **Import** to upload a bank CSV, **Review** to classify transactions, "
    "**Dashboard** to see your spending, or **Settings** for accounts, categories, "
    "people, and backups."
)
st.caption(f"{batch_count} import batch(es) so far.")
