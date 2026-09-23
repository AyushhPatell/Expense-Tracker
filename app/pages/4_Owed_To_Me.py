import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from core.db import init_db
from core.receivables import person_balances, person_ledger, set_flag, settle_through, unsettle_all

st.set_page_config(page_title="Owed to Me", layout="wide")
st.title("Owed to Me")

conn = init_db()


def _flash(message: str, icon: str = "✅") -> None:
    """Queue a short toast for the next render — a toast fired in the same
    run as st.rerun() never reaches the browser, since the rerun cuts the
    run off first."""
    st.session_state["_flash_message"] = (message, icon)


if "_flash_message" in st.session_state:
    _msg, _icon = st.session_state.pop("_flash_message")
    st.toast(_msg, icon=_icon)

st.caption(
    "Running balance per person, from every split ever tagged to them — a shared expense "
    "(like rent) or a plain personal loan work the same way. 'Settle up through' closes out "
    "old activity (e.g. already reconciled inside Splitwise) without deleting the record; "
    "it just stops counting toward the balance below."
)

balances = person_balances(conn)
if not balances:
    st.info("No people added yet — add one from the Review page's split editor.")
    st.stop()

active = [b for b in balances if b["balance"] != 0]
settled_people = [b["person"] for b in balances if b["balance"] == 0]

if active:
    cards_per_row = 3  # 4 clips a longer value like "$1,501.00" at tablet widths
    for row_start in range(0, len(active), cards_per_row):
        row = active[row_start : row_start + cards_per_row]
        cols = st.columns(cards_per_row)
        for col, b in zip(cols, row):
            with col:
                with st.container(border=True):
                    if b["balance"] > 0:
                        st.metric(b["person"], f"${b['balance'] / 100:,.2f}", help="Owes you")
                    else:
                        st.metric(b["person"], f"-${-b['balance'] / 100:,.2f}", help="You owe them")
else:
    st.info("Everyone's settled up.")

if settled_people:
    st.caption("No open balance: " + ", ".join(settled_people))

st.divider()
st.subheader("Ledger")

person_names = [b["person"] for b in balances]
selected = st.selectbox("Person", options=person_names, key="ledger_person")
selected_id = next(b["person_id"] for b in balances if b["person"] == selected)

ledger = person_ledger(conn, selected_id)
open_entries = [row for row in ledger if not row["settled"]]
settled_entries = [row for row in ledger if row["settled"]]

lcol1, lcol2 = st.columns(2)
lcol1.metric("Open balance", f"${-sum(r['amount'] for r in open_entries) / 100:,.2f}")
lcol2.metric("Settled (historical)", f"${len(settled_entries)} entr{'y' if len(settled_entries) == 1 else 'ies'}")

# ---------------------------------------------------------------------------
# Settle up through a chosen entry
# ---------------------------------------------------------------------------
if open_entries:
    with st.expander("Settle up through...", expanded=False):
        cutoff_options = [
            f"{row['txn_date']} | {row['description']} | ${row['amount'] / 100:.2f}" for row in open_entries
        ]
        cutoff_choice = st.selectbox(
            "Mark everything on/before this entry as settled", options=cutoff_options, key=f"cutoff_{selected_id}"
        )
        cutoff_date = open_entries[cutoff_options.index(cutoff_choice)]["txn_date"]
        if st.button(f"Settle {selected} up through {cutoff_date}", type="primary"):
            affected = settle_through(conn, selected_id, cutoff_date)
            st.session_state["ledger_generation"] = st.session_state.get("ledger_generation", 0) + 1
            _flash(f"Settled {affected} entr{'y' if affected == 1 else 'ies'} for {selected} through {cutoff_date}.")
            st.rerun()

if settled_entries:
    if st.button(f"Reopen all settled entries for {selected}"):
        reopened = unsettle_all(conn, selected_id)
        st.session_state["ledger_generation"] = st.session_state.get("ledger_generation", 0) + 1
        _flash(f"Reopened {reopened} entr{'y' if reopened == 1 else 'ies'} for {selected}.")
        st.rerun()

# ---------------------------------------------------------------------------
# Ledger table (flag toggling, settled entries hidden by default)
# ---------------------------------------------------------------------------
show_settled = st.checkbox("Show settled entries too", value=False, key=f"show_settled_{selected_id}")
visible = ledger if show_settled else open_entries

if not visible:
    st.caption("Nothing to show.")
else:
    ledger_generation = st.session_state.get("ledger_generation", 0)
    df = pd.DataFrame(
        [
            {
                "split_id": row["split_id"],
                "Flagged": bool(row["flagged"]),
                "Date": row["txn_date"],
                "Description": row["description"],
                "Amount": row["amount"] / 100,
                "Category": row["category"] or "",
                "Type": row["type"],
                "Note": row["note"] or "",
                "Settled": bool(row["settled"]),
            }
            for row in visible
        ]
    ).set_index("split_id")
    original_df = df.copy()

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            # Flagged first: it's the one editable, frequently-used action
            # here, and putting it up front means never needing to scroll
            # the grid horizontally to reach it.
            "Flagged": st.column_config.CheckboxColumn(help="Mark for later follow-up"),
            "Date": st.column_config.TextColumn(disabled=True),
            "Description": st.column_config.TextColumn(disabled=True),
            "Amount": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "Category": st.column_config.TextColumn(disabled=True),
            "Type": st.column_config.TextColumn(disabled=True),
            "Note": st.column_config.TextColumn(disabled=True),
            "Settled": st.column_config.CheckboxColumn(disabled=True),
        },
        # show_settled is part of the key too: it changes which/how many rows
        # are passed in, and st.data_editor can retain stale internal state
        # tied to row positions from a previous render if the key stays the
        # same while the underlying row-set changes shape (same bug class as
        # the Review grid's Category column and the split editor's Lines).
        key=f"ledger_editor_{selected_id}_{ledger_generation}_{show_settled}",
    )

    if st.button("Save flag changes"):
        changed = 0
        for split_id, edited_row in edited_df.iterrows():
            if bool(edited_row["Flagged"]) != bool(original_df.loc[split_id, "Flagged"]):
                set_flag(conn, split_id, bool(edited_row["Flagged"]))
                changed += 1
        st.session_state["ledger_generation"] = ledger_generation + 1
        _flash(f"Updated {changed} flag(s).")
        st.rerun()
