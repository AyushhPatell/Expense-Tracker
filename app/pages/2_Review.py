import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from core.categories import create_person, list_categories, list_people
from core.db import init_db
from core.rules import create_rule, suggest_rule_draft, test_conditions_against_existing
from core.settings import get_transfer_keywords, get_transfer_window_days
from core.splits import (
    equal_shares,
    get_splits,
    get_transaction,
    is_simple_split,
    replace_splits,
    set_note,
    set_reviewed,
    set_single_category,
    set_type,
)
from core.transfers import apply_auto_pairing, confirm_pair, get_suggested_pairs, get_unmatched_transfers

st.set_page_config(page_title="Review", layout="wide")
st.title("Review")

conn = init_db()


def _flash(message: str, icon: str = "✅") -> None:
    """Queue a short toast for the next render. A toast fired in the same
    run as st.rerun() never reaches the browser — the rerun cuts the run off
    first — so we stash it and show it right after the page reloads."""
    st.session_state["_flash_message"] = (message, icon)


if "_flash_message" in st.session_state:
    _msg, _icon = st.session_state.pop("_flash_message")
    st.toast(_msg, icon=_icon)

TYPES = ["unreviewed", "expense", "income", "transfer", "reimbursement", "refund", "ignore"]

accounts = conn.execute("SELECT * FROM accounts WHERE active = 1 ORDER BY name").fetchall()
categories = list_categories(conn)
people = list_people(conn)
category_label_by_id = {c["id"]: c["label"] for c in categories}
category_id_by_label = {c["label"]: c["id"] for c in categories}
person_label_by_id = {p["id"]: p["label"] for p in people}
person_id_by_label = {p["label"]: p["id"] for p in people}

if not accounts:
    st.error("No accounts configured. Add one in Settings first.")
    st.stop()

account_name_by_id = {a["id"]: a["name"] for a in accounts}

# ---------------------------------------------------------------------------
# Transfer queues + Filters, side by side to save vertical space
# ---------------------------------------------------------------------------
top_left, top_right = st.columns(2)

with top_left:
    with st.expander("Transfer pairs", expanded=False):
        if st.button("Re-run transfer detection"):
            applied = apply_auto_pairing(
                conn, window_days=get_transfer_window_days(conn), keywords=get_transfer_keywords(conn)
            )
            _flash(f"Auto-paired {applied} transfer(s).")
            st.rerun()

        def _txn_label(txn_id: int) -> str:
            row = conn.execute(
                "SELECT t.txn_date, t.description, t.amount, a.name AS account_name "
                "FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?",
                (txn_id,),
            ).fetchone()
            return f"{row['txn_date']} | {row['account_name']} | {row['description']} | ${row['amount'] / 100:.2f}"

        st.markdown("**Suggested transfer pairs** (no keyword match — confirm by hand)")
        suggested = get_suggested_pairs(
            conn, window_days=get_transfer_window_days(conn), keywords=get_transfer_keywords(conn)
        )
        if not suggested:
            st.caption("None right now.")
        else:
            for pair in suggested:
                c1, c2, c3 = st.columns([4, 4, 1])
                c1.write(_txn_label(pair["txn_a_id"]))
                c2.write(_txn_label(pair["txn_b_id"]))
                if c3.button("Confirm", key=f"confirm_{pair['txn_a_id']}_{pair['txn_b_id']}"):
                    confirm_pair(conn, pair["txn_a_id"], pair["txn_b_id"])
                    st.rerun()

        st.markdown("**Unmatched transfers** (typed transfer, but no partner found yet)")
        unmatched = get_unmatched_transfers(conn)
        if not unmatched:
            st.caption("None right now.")
        else:
            st.table(
                [
                    {
                        "Date": u["txn_date"],
                        "Account": u["account_name"],
                        "Description": u["description"],
                        "Amount": f"${u['amount'] / 100:.2f}",
                    }
                    for u in unmatched
                ]
            )

with top_right:
    with st.expander("Filters", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            date_from = st.date_input("From", value=None)
            date_to = st.date_input("To", value=None)
        with col2:
            account_filter = st.multiselect(
                "Account", options=[a["id"] for a in accounts],
                format_func=lambda aid: next(a["name"] for a in accounts if a["id"] == aid),
            )
            type_filter = st.multiselect("Type", options=TYPES)
        with col3:
            category_filter = st.selectbox("Category", options=["All"] + [c["label"] for c in categories])
            unreviewed_only = st.checkbox(
                "Unreviewed only",
                value=True,
                help=(
                    "On by default so new imports don't get buried under everything you've "
                    "already reviewed — uncheck to browse full history."
                ),
            )

        col4, col5, col6 = st.columns(3)
        with col4:
            amount_min = st.number_input("Min amount ($, absolute)", value=0.0, step=1.0)
        with col5:
            amount_max = st.number_input("Max amount ($, absolute, 0 = no max)", value=0.0, step=1.0)
        with col6:
            search_text = st.text_input("Search description / note")

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
where = ["1 = 1"]
params: list = []

if date_from:
    where.append("t.txn_date >= ?")
    params.append(date_from.isoformat())
if date_to:
    where.append("t.txn_date <= ?")
    params.append(date_to.isoformat())
if account_filter:
    where.append(f"t.account_id IN ({','.join('?' * len(account_filter))})")
    params.extend(account_filter)
if type_filter:
    where.append(f"t.type IN ({','.join('?' * len(type_filter))})")
    params.extend(type_filter)
if category_filter != "All":
    where.append(
        "t.id IN (SELECT transaction_id FROM splits WHERE category_id = ?)"
    )
    params.append(category_id_by_label[category_filter])
if unreviewed_only:
    where.append("t.reviewed = 0")
if amount_min:
    where.append("ABS(t.amount) >= ?")
    params.append(round(amount_min * 100))
if amount_max:
    where.append("ABS(t.amount) <= ?")
    params.append(round(amount_max * 100))
if search_text:
    where.append("(t.description LIKE ? OR t.note LIKE ?)")
    like = f"%{search_text}%"
    params.extend([like, like])

query = (
    "SELECT t.id, t.txn_date, a.name AS account_name, t.description, t.amount, t.type, "
    "t.note, t.reviewed, t.type_locked, "
    "(SELECT COUNT(*) FROM splits s WHERE s.transaction_id = t.id) AS split_count "
    "FROM transactions t JOIN accounts a ON a.id = t.account_id "
    f"WHERE {' AND '.join(where)} "
    "ORDER BY t.txn_date DESC, t.id DESC LIMIT 500"
)
rows = conn.execute(query, params).fetchall()

st.caption(f"{len(rows)} transaction(s) shown (max 500).")

if not rows:
    st.info("No transactions match these filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


def _single_category_label(txn_id: int) -> str:
    splits = get_splits(conn, txn_id)
    if len(splits) == 1 and splits[0]["category_id"] is not None:
        return category_label_by_id.get(splits[0]["category_id"], "")
    return ""


def _splits_summary(txn_id: int, split_count: int) -> str:
    if split_count == 0:
        return "—"
    if split_count == 1:
        return "1 line"
    return f"{split_count} lines"


records = []
for r in rows:
    records.append(
        {
            "id": r["id"],
            "Date": r["txn_date"],
            "Account": r["account_name"],
            "Description": r["description"],
            "Amount": r["amount"] / 100,
            "Type": r["type"],
            "Splits": _splits_summary(r["id"], r["split_count"]),
            "Category": _single_category_label(r["id"]),
            "Note": r["note"] or "",
            "Reviewed": bool(r["reviewed"]),
        }
    )

df = pd.DataFrame(records).set_index("id")
original_df = df.copy()

# The grid's key includes a generation counter, bumped after every save (see
# below). st.data_editor otherwise keeps showing a cell's typed/selected
# value across reruns even when our code declines to save it (e.g. the
# already-split guard) or after it's genuinely been committed — a fresh key
# forces it to re-initialize purely from the current database state instead
# of a stale in-progress edit.
grid_generation = st.session_state.get("grid_generation", 0)

# Size the grid to the actual row count — a couple of rows shouldn't render
# with a wall of empty grid lines below them. Caps at the old fixed height
# once there's enough data to actually need scrolling.
MAX_GRID_HEIGHT = 420
ROW_HEIGHT = 35
HEADER_HEIGHT = 38
grid_height = min(HEADER_HEIGHT + ROW_HEIGHT * len(df) + 3, MAX_GRID_HEIGHT)

edited_df = st.data_editor(
    df,
    use_container_width=True,
    height=grid_height,
    hide_index=True,
    column_config={
        "Date": st.column_config.TextColumn(disabled=True),
        "Account": st.column_config.TextColumn(disabled=True),
        "Description": st.column_config.TextColumn(disabled=True),
        "Amount": st.column_config.NumberColumn(disabled=True, format="%.2f"),
        "Type": st.column_config.SelectboxColumn(options=TYPES),
        "Splits": st.column_config.TextColumn(disabled=True),
        "Category": st.column_config.SelectboxColumn(options=[""] + list(category_label_by_id.values())),
        "Note": st.column_config.TextColumn(),
        "Reviewed": st.column_config.CheckboxColumn(),
    },
    key=f"review_grid_{grid_generation}",
)
st.caption(
    "Editing Category here replaces any existing split with a single category covering the "
    "full amount. Use the detail panel below for splits, receivables, or Splitwise lines."
)

if st.button("Save grid changes", type="primary"):
    changed = 0
    skipped = []
    for txn_id, edited_row in edited_df.iterrows():
        original_row = original_df.loc[txn_id]

        if edited_row["Type"] != original_row["Type"]:
            set_type(conn, txn_id, edited_row["Type"])
            changed += 1

        if edited_row["Note"] != original_row["Note"]:
            set_note(conn, txn_id, edited_row["Note"] or None)
            changed += 1

        if bool(edited_row["Reviewed"]) != bool(original_row["Reviewed"]):
            set_reviewed(conn, txn_id, bool(edited_row["Reviewed"]))
            changed += 1

        if edited_row["Category"] != original_row["Category"] and edited_row["Category"]:
            # Re-check against the database, not the (possibly stale) grid
            # snapshot: st.data_editor can retain an uncommitted edit from
            # earlier in the session even after the underlying data changes,
            # so trust only what's actually saved right now. Refuse to
            # collapse a transaction that already has a real split — that's
            # destructive and belongs in the detail panel, not this quick path.
            if is_simple_split(conn, txn_id):
                set_single_category(conn, txn_id, category_id_by_label[edited_row["Category"]])
                changed += 1
            else:
                skipped.append(f"{original_row['Date']} | {original_row['Description']}")

    if skipped:
        _flash(
            f"Saved {changed} change(s). Skipped category on {len(skipped)} already-split "
            "transaction(s) — use the detail panel for those instead.",
            icon="⚠️",
        )
    else:
        _flash(f"Saved {changed} change(s).")
    st.session_state["grid_generation"] = grid_generation + 1
    st.rerun()

# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------
st.subheader("Bulk actions")
selected_ids = st.multiselect(
    "Select transactions (by date | description | amount)",
    options=list(df.index),
    format_func=lambda tid: f"{df.loc[tid, 'Date']} | {df.loc[tid, 'Description']} | ${df.loc[tid, 'Amount']:.2f}",
)

bcol1, bcol2, bcol3 = st.columns(3)
with bcol1:
    bulk_type = st.selectbox("Set type to", options=TYPES, key="bulk_type")
    if st.button("Apply type to selected") and selected_ids:
        for tid in selected_ids:
            set_type(conn, tid, bulk_type)
        _flash(f"Set type on {len(selected_ids)} transaction(s).")
        st.rerun()
with bcol2:
    bulk_category = st.selectbox("Set category to", options=list(category_label_by_id.values()), key="bulk_cat")
    if st.button("Apply category to selected") and selected_ids:
        applied, skipped = 0, 0
        for tid in selected_ids:
            if is_simple_split(conn, tid):
                set_single_category(conn, tid, category_id_by_label[bulk_category])
                applied += 1
            else:
                skipped += 1
        if skipped:
            _flash(f"Set category on {applied}. Skipped {skipped} already-split transaction(s).", icon="⚠️")
        else:
            _flash(f"Set category on {applied} transaction(s).")
        st.rerun()
with bcol3:
    if st.button("Mark selected reviewed") and selected_ids:
        for tid in selected_ids:
            set_reviewed(conn, tid, True)
        _flash(f"Marked {len(selected_ids)} transaction(s) reviewed.")
        st.rerun()

# ---------------------------------------------------------------------------
# Detail panel: split editor
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Transaction detail & split editor")


def _new_line(amount: float, category: str = "", person: str = "", splitwise: bool = False) -> dict:
    """A split-editor draft line. Each gets its own unique widget-key id so
    that wholesale-replacing the line list (equal split, my-share split,
    reset) always produces fresh widgets that respect the new values —
    Streamlit ignores a widget's `value=` once a widget with that exact key
    already exists, so reusing positional keys (line index) across a replace
    leaves the old widgets showing stale data even though the underlying
    list is correct."""
    return {"_id": uuid.uuid4().hex, "amount": amount, "category": category, "person": person, "splitwise": splitwise}

with st.expander("Add a new person to split expenses with"):
    with st.form("add_person_form", clear_on_submit=True):
        new_person_name = st.text_input("Name")
        submitted = st.form_submit_button("Add person")
        if submitted and new_person_name.strip():
            create_person(conn, new_person_name.strip())
            _flash(f"Added {new_person_name.strip()}.")
            st.rerun()

detail_id = st.selectbox(
    "Transaction",
    options=list(df.index),
    format_func=lambda tid: f"{df.loc[tid, 'Date']} | {df.loc[tid, 'Description']} | ${df.loc[tid, 'Amount']:.2f}",
    key="detail_select",
)

txn = get_transaction(conn, detail_id)
existing_splits = get_splits(conn, detail_id)

# Selecting a different transaction discards any unsaved draft left over
# from the one we're leaving, so revisiting a transaction later always shows
# its true saved state, not a stale half-edited attempt.
previous_detail_id = st.session_state.get("review_detail_last_id")
if previous_detail_id is not None and previous_detail_id != detail_id:
    st.session_state.pop(f"split_draft_{previous_detail_id}", None)
st.session_state["review_detail_last_id"] = detail_id

draft_key = f"split_draft_{detail_id}"
if draft_key not in st.session_state:
    if existing_splits:
        st.session_state[draft_key] = [
            _new_line(
                amount=s["amount"] / 100,
                category=category_label_by_id.get(s["category_id"], ""),
                person=person_label_by_id.get(s["person_id"], ""),
                splitwise=bool(s["is_splitwise"]),
            )
            for s in existing_splits
        ]
    else:
        st.session_state[draft_key] = [_new_line(amount=txn["amount"] / 100)]

st.write(f"**{txn['description']}** — {txn['txn_date']} — ${txn['amount'] / 100:.2f}")
if txn["type_locked"]:
    st.caption("Type is manually locked; rules won't change it.")

qcol1, qcol2, qcol3 = st.columns(3)
with qcol1:
    n_people = st.number_input(
        "Split equally among N people", min_value=2, max_value=10, value=2, step=1,
        key=f"n_people_{detail_id}",
    )
    if st.button("Apply equal split"):
        shares = equal_shares(txn["amount"], n_people)
        st.session_state[draft_key] = [_new_line(amount=share / 100) for share in shares]
        st.rerun()
with qcol2:
    split_evenly = st.checkbox(
        "Split evenly, including me (skip typing my share)", key=f"split_evenly_{detail_id}"
    )
    my_share_dollars = st.number_input(
        "My share ($)", min_value=0.0, step=1.0, key=f"my_share_input_{detail_id}",
        disabled=split_evenly,
    )
    my_share_category = st.selectbox(
        "...in category", options=list(category_label_by_id.values()), key=f"my_share_cat_{detail_id}"
    )
    rest_people = st.multiselect(
        "Rest owed by", options=list(person_id_by_label.keys()), key=f"rest_people_{detail_id}"
    )
    if st.button("Apply my-share split"):
        if not rest_people:
            st.error("Pick at least one person under 'Rest owed by' first.")
        else:
            if split_evenly:
                # Divide the whole amount into (me + rest_people) equal shares
                # automatically — no manual arithmetic on big/odd amounts.
                shares = equal_shares(txn["amount"], len(rest_people) + 1)
                my_share_cents, rest_shares = shares[0], shares[1:]
            else:
                my_share_cents = round(my_share_dollars * 100)
                sign = 1 if txn["amount"] >= 0 else -1
                my_share_cents = sign * abs(my_share_cents)
                rest_cents = txn["amount"] - my_share_cents
                rest_shares = equal_shares(rest_cents, len(rest_people))
            lines = [_new_line(amount=my_share_cents / 100, category=my_share_category)]
            for person_name, share in zip(rest_people, rest_shares):
                lines.append(_new_line(amount=share / 100, person=person_name))
            st.session_state[draft_key] = lines
            summary = ", ".join(f"${line['amount']:.2f} -> {line['category'] or line['person']}" for line in lines)
            _flash(f"Applied: {summary}")
            st.rerun()
with qcol3:
    if st.button("Add blank line"):
        st.session_state[draft_key].append(_new_line(amount=0.0))
        st.rerun()
    if st.button("Reset to one line"):
        st.session_state[draft_key] = [_new_line(amount=txn["amount"] / 100)]
        st.rerun()

st.markdown("**Lines**")
remove_index = None
for i, line in enumerate(st.session_state[draft_key]):
    line_id = line["_id"]
    c1, c2, c3, c4, c5 = st.columns([2, 3, 3, 2, 1])
    line["amount"] = c1.number_input(
        "Amount", value=float(line["amount"]), step=1.0, key=f"{draft_key}_amount_{line_id}", label_visibility="collapsed"
    )
    line["category"] = c2.selectbox(
        "Category", options=[""] + list(category_label_by_id.values()),
        index=([""] + list(category_label_by_id.values())).index(line["category"]) if line["category"] in category_label_by_id.values() else 0,
        key=f"{draft_key}_cat_{line_id}", label_visibility="collapsed",
    )
    line["person"] = c3.selectbox(
        "Person", options=[""] + list(person_id_by_label.keys()),
        index=([""] + list(person_id_by_label.keys())).index(line["person"]) if line["person"] in person_id_by_label else 0,
        key=f"{draft_key}_person_{line_id}", label_visibility="collapsed",
    )
    line["splitwise"] = c4.checkbox("Splitwise", value=line["splitwise"], key=f"{draft_key}_sw_{line_id}")
    if c5.button("✕", key=f"{draft_key}_remove_{line_id}"):
        remove_index = i

if remove_index is not None:
    st.session_state[draft_key].pop(remove_index)
    st.rerun()

allocated = sum(round(line["amount"] * 100) for line in st.session_state[draft_key])
remaining = txn["amount"] - allocated
st.metric("Remaining to allocate", f"${remaining / 100:.2f}")

if st.button("Save split", type="primary", disabled=(remaining != 0)):
    lines = []
    for line in st.session_state[draft_key]:
        lines.append(
            {
                "amount": round(line["amount"] * 100),
                "category_id": category_id_by_label.get(line["category"]) if line["category"] else None,
                "person_id": person_id_by_label.get(line["person"]) if line["person"] else None,
                "is_splitwise": line["splitwise"],
            }
        )
    try:
        replace_splits(conn, detail_id, lines)
        _flash("Split saved.")
        del st.session_state[draft_key]
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))

# ---------------------------------------------------------------------------
# Make this a rule (spec 7.3): pre-fill a rule from how this transaction is
# already typed/categorized, so a recurring one (rent, payroll, coffee)
# doesn't need to be classified by hand every single month.
# ---------------------------------------------------------------------------
with st.expander("Make this a rule"):
    rule_draft_key = f"rule_from_txn_{detail_id}"
    if rule_draft_key not in st.session_state:
        st.session_state[rule_draft_key] = suggest_rule_draft(conn, detail_id)
    rule_draft = st.session_state[rule_draft_key]

    st.caption("Pre-filled from this transaction's current type/category and description/account/amount. Edit before saving.")
    rule_draft["name"] = st.text_input("Rule name", value=rule_draft["name"], key=f"{rule_draft_key}_name")

    keyword_leaf = rule_draft["conditions"]["all"][0]
    keyword_leaf["value"] = st.text_input(
        "Description contains", value=keyword_leaf["value"], key=f"{rule_draft_key}_keyword",
        help="Real Scotia e-transfer descriptions are generic ('Free Interac E-Transfer') — the "
        "account/direction/amount conditions below do the real narrowing for those.",
    )
    st.json(rule_draft["conditions"], expanded=False)
    if rule_draft["actions"]:
        st.caption("Actions: " + ", ".join(f"{k}={v}" for k, v in rule_draft["actions"].items()))
    else:
        st.caption("No type/category actions pre-filled — this transaction isn't simply categorized yet.")

    if st.button("Test against existing transactions", key=f"{rule_draft_key}_test"):
        st.session_state[f"{rule_draft_key}_test_result"] = test_conditions_against_existing(conn, rule_draft["conditions"])
    test_result = st.session_state.get(f"{rule_draft_key}_test_result")
    if test_result:
        st.caption(f"Matches {test_result['total']} existing transaction(s).")

    if st.button("Save as rule", type="primary", key=f"{rule_draft_key}_save"):
        try:
            create_rule(
                conn, rule_draft["name"], rule_draft["conditions"], rule_draft["actions"],
                priority=rule_draft["priority"], enabled=rule_draft["enabled"], stop_processing=rule_draft["stop_processing"],
            )
            _flash(f"Created rule '{rule_draft['name']}'. Edit further on the Rules page any time.")
            del st.session_state[rule_draft_key]
            st.session_state.pop(f"{rule_draft_key}_test_result", None)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
