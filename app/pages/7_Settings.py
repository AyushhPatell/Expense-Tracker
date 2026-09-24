import calendar
import csv as csv_module
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from core.accounts import ACCOUNT_KINDS, create_account, list_accounts, list_profile_choices, update_account
from core.categories import (
    create_category,
    create_person,
    deactivate_person,
    list_categories,
    list_people,
    merge_categories,
    rename_category,
    rename_person,
    set_category_active,
)
from core.backup import DEFAULT_KEEP_LAST, create_backup, list_backups, restore_backup
from core.config import get_data_dir, get_db_path
from core.db import init_db
from core.export import spending_by_category_dataframe, to_csv_bytes, to_excel_bytes, transactions_dataframe
from core.importers.custom_profiles import (
    delete_custom_profile,
    list_custom_profiles,
    save_custom_profile,
    unique_profile_id,
)
from core.importers.detect import load_profiles
from core.importers.guess import guess_amount_config, guess_date_column, guess_description_columns
from core.importers.normalize import normalize_row
from core.importers.parse import parse_csv
from core.reports import list_available_months, month_bounds
from core.settings import get_transfer_keywords, get_transfer_window_days, set_transfer_matching

st.set_page_config(page_title="Settings", layout="wide")
st.title("Settings")

conn = init_db()


def _try_parse_date(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _flash(message: str, icon: str = "✅") -> None:
    """Queue a short toast for the next render — a toast fired in the same
    run as st.rerun() never reaches the browser, since the rerun cuts the
    run off first."""
    st.session_state["_flash_message"] = (message, icon)


if "_flash_message" in st.session_state:
    _msg, _icon = st.session_state.pop("_flash_message")
    st.toast(_msg, icon=_icon)

# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
st.subheader("Accounts")

accounts_generation = st.session_state.get("accounts_generation", 0)
profile_choices = list_profile_choices(conn)
profile_ids = [p["id"] for p in profile_choices]
accounts = list_accounts(conn)

df = pd.DataFrame(
    [
        {
            "id": a["id"],
            "Name": a["name"],
            "Kind": a["kind"],
            "Profile": a["profile_id"],
            "Active": bool(a["active"]),
        }
        for a in accounts
    ]
).set_index("id")
original_df = df.copy()

edited_df = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Name": st.column_config.TextColumn(),
        "Kind": st.column_config.SelectboxColumn(options=list(ACCOUNT_KINDS)),
        "Profile": st.column_config.SelectboxColumn(options=profile_ids),
        "Active": st.column_config.CheckboxColumn(help="Inactive accounts are hidden from Import but existing history stays visible everywhere else"),
    },
    key=f"accounts_grid_{accounts_generation}",
)

if st.button("Save account changes", type="primary"):
    changed = 0
    errors = []
    for account_id, edited_row in edited_df.iterrows():
        original_row = original_df.loc[account_id]
        if (
            edited_row["Name"] != original_row["Name"]
            or edited_row["Kind"] != original_row["Kind"]
            or edited_row["Profile"] != original_row["Profile"]
            or bool(edited_row["Active"]) != bool(original_row["Active"])
        ):
            try:
                update_account(
                    conn, account_id, edited_row["Name"], edited_row["Kind"], edited_row["Profile"], bool(edited_row["Active"])
                )
                changed += 1
            except ValueError as exc:
                errors.append(str(exc))
    for err in errors:
        st.error(err)
    if changed:
        _flash(f"Saved {changed} account change(s).")
    st.session_state["accounts_generation"] = accounts_generation + 1
    st.rerun()

with st.expander("Add an account"):
    with st.form("add_account_form", clear_on_submit=True):
        new_name = st.text_input("Name")
        new_kind = st.selectbox("Kind", options=list(ACCOUNT_KINDS))
        new_profile = st.selectbox(
            "Bank profile", options=profile_ids,
            format_func=lambda pid: next((p["display_name"] for p in profile_choices if p["id"] == pid), pid),
        )
        submitted = st.form_submit_button("Add account")
        if submitted:
            try:
                create_account(conn, new_name, new_kind, new_profile)
                _flash(f"Added account '{new_name}'.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

st.divider()

# ---------------------------------------------------------------------------
# Bank profiles — self-service "teach the app a new bank's CSV format"
# ---------------------------------------------------------------------------
st.subheader("Bank profiles")
st.caption(
    "Teaches the Import page how to read one bank's CSV export — which column is the date, "
    "which is the amount, and so on. Scotia Chequing and Rogers Mastercard are built in; add "
    "any other bank here, no code or file editing needed."
)

custom_profiles = list_custom_profiles(conn)
if custom_profiles:
    st.table([{"Name": p["display_name"]} for p in custom_profiles])
    dpcol1, dpcol2 = st.columns([3, 1])
    with dpcol1:
        delete_profile_choice = st.selectbox(
            "Delete a custom profile", options=[p["id"] for p in custom_profiles],
            format_func=lambda pid: next(p["display_name"] for p in custom_profiles if p["id"] == pid),
            key="delete_profile_choice",
        )
    with dpcol2:
        st.write("")
        st.write("")
        if st.button("Delete profile"):
            try:
                delete_custom_profile(conn, delete_profile_choice)
                _flash("Deleted bank profile.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
else:
    st.caption("No custom profiles yet.")

with st.expander("Add a bank profile"):
    st.caption(
        "Upload one export file from the bank you want to add. It's parsed right here on your "
        "device to build the profile — nothing is uploaded anywhere."
    )
    sample_file = st.file_uploader("Sample CSV export", type=["csv"], key="profile_wizard_upload")

    if sample_file is not None:
        file_bytes = sample_file.getvalue()

        wizard_gen = st.session_state.get("profile_wizard_generation", 0)
        if st.session_state.get("profile_wizard_file_id") != sample_file.file_id:
            st.session_state["profile_wizard_file_id"] = sample_file.file_id
            wizard_gen += 1
            st.session_state["profile_wizard_generation"] = wizard_gen

        text = None
        used_encoding = None
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = file_bytes.decode(enc)
                used_encoding = enc
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            st.error("Couldn't decode this file as text — is it really a CSV?")
        else:
            raw_lines = text.splitlines()
            with st.expander("Raw file preview (first 10 lines)"):
                st.code("\n".join(raw_lines[:10]))

            skip_rows = st.number_input(
                "Header row is line # (0 = first line)", min_value=0,
                max_value=max(len(raw_lines) - 1, 0), value=0, step=1,
                key=f"profile_skip_rows_{wizard_gen}",
                help="Some exports have a title or account-summary line before the real column "
                "headers. Increase this until the columns/preview below look right.",
            )

            reader = csv_module.DictReader(raw_lines[skip_rows:])
            sample_rows = []
            for row in reader:
                if any((v or "").strip() for v in row.values()):
                    sample_rows.append(row)
                if len(sample_rows) >= 30:
                    break
            headers = list(reader.fieldnames or [])

            if not headers or not sample_rows:
                st.error("No data rows found with this header-row setting — try a different line number.")
            else:
                st.dataframe(pd.DataFrame(sample_rows[:5]), use_container_width=True)

                guess_key = f"profile_guess_{wizard_gen}_{skip_rows}"
                if guess_key not in st.session_state:
                    date_col_guess, date_fmt_guess = guess_date_column(sample_rows, headers)
                    claimed = {date_col_guess} if date_col_guess else set()
                    amount_guess = guess_amount_config(sample_rows, headers, exclude=claimed)
                    if amount_guess["mode"] == "single" and amount_guess.get("column"):
                        claimed.add(amount_guess["column"])
                    elif amount_guess["mode"] == "debit_credit":
                        claimed.update(
                            c for c in (amount_guess.get("debit_column"), amount_guess.get("credit_column")) if c
                        )
                    st.session_state[guess_key] = {
                        "date_col": date_col_guess,
                        "date_fmt": date_fmt_guess or "%Y-%m-%d",
                        "amount": amount_guess,
                        "desc": guess_description_columns(headers, claimed),
                    }
                guess = st.session_state[guess_key]

                st.markdown("**Date**")
                dcol1, dcol2 = st.columns(2)
                date_column = dcol1.selectbox(
                    "Date column", options=headers,
                    index=headers.index(guess["date_col"]) if guess["date_col"] in headers else 0,
                    key=f"profile_date_col_{wizard_gen}",
                )
                date_format = dcol2.text_input(
                    "Date format", value=guess["date_fmt"], key=f"profile_date_fmt_{wizard_gen}",
                    help="Python date-format codes — e.g. %Y-%m-%d for 2025-08-17, "
                    "%m/%d/%Y for 08/17/2025, %d-%b-%Y for 17-Aug-2025.",
                )
                sample_date_values = [r.get(date_column, "").strip() for r in sample_rows[:5] if r.get(date_column, "").strip()]
                if sample_date_values:
                    parsed_ok = sum(1 for v in sample_date_values if _try_parse_date(v, date_format))
                    if parsed_ok == len(sample_date_values):
                        st.success(f"Parsed all {parsed_ok} sample date(s): {', '.join(sample_date_values)}")
                    else:
                        st.warning(
                            f"Only parsed {parsed_ok}/{len(sample_date_values)} sample dates with this "
                            f"format. Example that failed: '{sample_date_values[0]}'"
                        )

                posted_options = ["(none)"] + headers
                posted_date_column = st.selectbox(
                    "Posted date column (optional)", options=posted_options, key=f"profile_posted_{wizard_gen}"
                )

                st.markdown("**Description**")
                description_columns = st.multiselect(
                    "Description column(s), in order", options=headers,
                    default=[c for c in guess["desc"] if c in headers], key=f"profile_desc_{wizard_gen}",
                    help="If you pick more than one, they're joined with ' | ' into a single description.",
                )

                st.markdown("**Amount**")
                amount_mode = st.radio(
                    "This bank's export has...", options=["single", "debit_credit"],
                    index=0 if guess["amount"]["mode"] == "single" else 1,
                    format_func=lambda m: "One amount column" if m == "single" else "Separate debit/credit columns",
                    horizontal=True, key=f"profile_amount_mode_{wizard_gen}",
                )
                if amount_mode == "single":
                    acol1, acol2 = st.columns(2)
                    amount_column = acol1.selectbox(
                        "Amount column", options=headers,
                        index=headers.index(guess["amount"]["column"]) if guess["amount"].get("column") in headers else 0,
                        key=f"profile_amount_col_{wizard_gen}",
                    )
                    invert = acol2.checkbox(
                        "Flip the sign", value=False, key=f"profile_invert_{wizard_gen}",
                        help="Check this if a purchase shows as a POSITIVE number in this column. "
                        "Leave unchecked if purchases are already negative. Check the full preview "
                        "below to be sure.",
                    )
                    amount_cfg = {"mode": "single", "column": amount_column, "invert": invert}
                else:
                    acol1, acol2 = st.columns(2)
                    debit_column = acol1.selectbox(
                        "Debit / withdrawal column", options=headers,
                        index=headers.index(guess["amount"]["debit_column"]) if guess["amount"].get("debit_column") in headers else 0,
                        key=f"profile_debit_{wizard_gen}",
                    )
                    credit_column = acol2.selectbox(
                        "Credit / deposit column", options=headers,
                        index=headers.index(guess["amount"]["credit_column"]) if guess["amount"].get("credit_column") in headers else 0,
                        key=f"profile_credit_{wizard_gen}",
                    )
                    amount_cfg = {"mode": "debit_credit", "debit_column": debit_column, "credit_column": credit_column}

                with st.expander("Advanced (optional)"):
                    currency_choice = st.selectbox(
                        "Currency column", options=["(none)"] + headers, key=f"profile_currency_{wizard_gen}"
                    )
                    encoding_options = ["utf-8-sig", "utf-8", "latin-1"]
                    encoding_choice = st.selectbox(
                        "File encoding", options=encoding_options,
                        index=encoding_options.index(used_encoding) if used_encoding in encoding_options else 0,
                        key=f"profile_encoding_{wizard_gen}",
                    )

                    st.caption("Skip rows where a column equals a specific value (e.g. skip 'Pending' rows).")
                    skip_draft_key = f"profile_skipif_{wizard_gen}"
                    if skip_draft_key not in st.session_state:
                        st.session_state[skip_draft_key] = []
                    remove_idx = None
                    for i, cond in enumerate(st.session_state[skip_draft_key]):
                        sc1, sc2, sc3, sc4 = st.columns([3, 1, 3, 1])
                        cond["column"] = sc1.selectbox(
                            "Column", options=headers,
                            index=headers.index(cond["column"]) if cond["column"] in headers else 0,
                            key=f"{skip_draft_key}_col_{cond['_id']}", label_visibility="collapsed",
                        )
                        sc2.write("equals")
                        cond["equals"] = sc3.text_input(
                            "Value", value=cond["equals"], key=f"{skip_draft_key}_val_{cond['_id']}",
                            label_visibility="collapsed",
                        )
                        if sc4.button("✕", key=f"{skip_draft_key}_rm_{cond['_id']}"):
                            remove_idx = i
                    if remove_idx is not None:
                        st.session_state[skip_draft_key].pop(remove_idx)
                        st.rerun()
                    if st.button("Add a skip condition", key=f"{skip_draft_key}_add"):
                        st.session_state[skip_draft_key].append({"_id": uuid.uuid4().hex, "column": headers[0], "equals": ""})
                        st.rerun()
                    skip_if = [
                        {"column": c["column"], "equals": c["equals"]}
                        for c in st.session_state[skip_draft_key] if c["equals"]
                    ]

                parse_cfg = {
                    "encoding": encoding_choice,
                    "skip_rows": int(skip_rows),
                    "date_column": date_column,
                    "date_format": date_format,
                    "description_columns": description_columns,
                    "amount": amount_cfg,
                    "currency_column": None if currency_choice == "(none)" else currency_choice,
                    "skip_if": skip_if,
                }
                if posted_date_column != "(none)":
                    parse_cfg["posted_date_column"] = posted_date_column

                draft_profile = {
                    "id": "_preview", "display_name": "(preview)",
                    "detect": {"headers": headers}, "parse": parse_cfg,
                }

                st.markdown("**Full preview** — exactly what Import would create with these settings")
                preview_records = []
                try:
                    preview_raw_rows, _ = parse_csv(file_bytes, draft_profile)
                    for raw in preview_raw_rows[:10]:
                        norm = normalize_row(raw, draft_profile)
                        if norm:
                            preview_records.append(
                                {"Date": norm["txn_date"], "Description": norm["description"], "Amount": f"{norm['amount_cents'] / 100:.2f}"}
                            )
                    if preview_records:
                        st.dataframe(preview_records, use_container_width=True)
                    else:
                        st.warning("No rows parsed with the current settings — check the date format and column choices above.")
                except Exception as exc:
                    st.error(f"Couldn't preview with these settings: {exc}")

                st.markdown("**Save**")
                display_name_input = st.text_input(
                    "Bank / account type name", placeholder="e.g. TD Chequing", key=f"profile_name_{wizard_gen}"
                )
                if st.button("Save this bank profile", type="primary", disabled=not preview_records):
                    if not display_name_input.strip():
                        st.error("Give this profile a name first.")
                    else:
                        builtin_ids = {p["id"] for p in load_profiles()}
                        new_id = unique_profile_id(conn, display_name_input, builtin_ids)
                        save_custom_profile(conn, new_id, display_name_input.strip(), headers, parse_cfg)
                        _flash(f"Saved bank profile '{display_name_input.strip()}'. It's available now when adding an account.")
                        st.session_state.pop("profile_wizard_file_id", None)
                        st.session_state.pop("profile_wizard_generation", None)
                        st.session_state.pop(guess_key, None)
                        st.session_state.pop(skip_draft_key, None)
                        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
st.subheader("Categories")
st.caption(
    "Renaming or merging a category won't update any Rule that already refers to its old name "
    "by text — check the Rules page's 'Rule-split failures' queue (or just re-open the rule) "
    "after a rename/merge if it was used in one."
)

show_inactive = st.checkbox("Show inactive categories too", value=False, key="settings_show_inactive_cats")
categories = list_categories(conn, include_inactive=show_inactive)
category_id_by_label = {c["label"]: c["id"] for c in categories}
all_active_categories = list_categories(conn)  # for parent/merge-target pickers, always active-only

active_flags = {
    r["id"]: bool(r["active"]) for r in conn.execute("SELECT id, active FROM categories").fetchall()
}
st.table(
    [{"Category": c["label"], "Active": "Yes" if active_flags.get(c["id"], True) else "No"} for c in categories]
)

with st.expander("Add a category"):
    with st.form("add_category_form", clear_on_submit=True):
        new_cat_name = st.text_input("Name")
        parent_choice = st.selectbox("Parent (optional)", options=["(top-level)"] + [c["label"] for c in all_active_categories])
        submitted = st.form_submit_button("Add category")
        if submitted:
            try:
                parent_id = None if parent_choice == "(top-level)" else category_id_by_label[parent_choice]
                create_category(conn, new_cat_name, parent_id)
                _flash(f"Added category '{new_cat_name}'.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

with st.expander("Rename a category"):
    rename_choice = st.selectbox("Category", options=[c["label"] for c in categories], key="rename_cat_choice")
    new_name_input = st.text_input("New name", key="rename_cat_new_name")
    if st.button("Rename"):
        try:
            rename_category(conn, category_id_by_label[rename_choice], new_name_input)
            _flash(f"Renamed to '{new_name_input}'.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

with st.expander("Deactivate / reactivate a category"):
    toggle_choice = st.selectbox("Category", options=[c["label"] for c in categories], key="toggle_cat_choice")
    toggle_id = category_id_by_label[toggle_choice]
    is_active = active_flags.get(toggle_id, True)
    st.caption(f"Currently {'active' if is_active else 'inactive'}.")
    if st.button("Deactivate" if is_active else "Reactivate"):
        try:
            set_category_active(conn, toggle_id, not is_active)
            _flash(f"{'Deactivated' if is_active else 'Reactivated'} '{toggle_choice}'.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

with st.expander("Merge two categories"):
    st.caption("Every split on the source category moves to the target, then the source is deactivated.")
    mcol1, mcol2 = st.columns(2)
    source_choice = mcol1.selectbox("Merge this category...", options=[c["label"] for c in all_active_categories], key="merge_source")
    target_choice = mcol2.selectbox("...into this one", options=[c["label"] for c in all_active_categories], key="merge_target")
    if st.button("Merge"):
        try:
            reassigned = merge_categories(conn, category_id_by_label[source_choice], category_id_by_label[target_choice])
            _flash(f"Merged '{source_choice}' into '{target_choice}' — {reassigned} split(s) reassigned.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

st.divider()

# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
st.subheader("People")

show_inactive_people = st.checkbox("Show inactive people too", value=False, key="settings_show_inactive_people")
people_active_flags = {r["id"]: bool(r["active"]) for r in conn.execute("SELECT id, active FROM people").fetchall()}
people_rows = conn.execute("SELECT id, name, active FROM people ORDER BY name").fetchall()
people_rows = [p for p in people_rows if show_inactive_people or p["active"]]
people_by_label = {p["name"]: p["id"] for p in people_rows}

st.table([{"Name": p["name"], "Active": "Yes" if p["active"] else "No"} for p in people_rows])

with st.expander("Add a person"):
    with st.form("add_person_form_settings", clear_on_submit=True):
        new_person_name = st.text_input("Name")
        submitted = st.form_submit_button("Add person")
        if submitted and new_person_name.strip():
            create_person(conn, new_person_name.strip())
            _flash(f"Added {new_person_name.strip()}.")
            st.rerun()

if people_rows:
    with st.expander("Rename a person"):
        rename_person_choice = st.selectbox("Person", options=list(people_by_label.keys()), key="rename_person_choice")
        new_person_name_input = st.text_input("New name", key="rename_person_new_name")
        if st.button("Rename person"):
            try:
                rename_person(conn, people_by_label[rename_person_choice], new_person_name_input)
                _flash(f"Renamed to '{new_person_name_input}'.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    with st.expander("Deactivate a person"):
        st.caption("Refused if they still have an open balance — settle up on Owed to Me first.")
        deactivate_person_choice = st.selectbox("Person", options=list(people_by_label.keys()), key="deactivate_person_choice")
        if st.button("Deactivate person"):
            try:
                deactivate_person(conn, people_by_label[deactivate_person_choice])
                _flash(f"Deactivated {deactivate_person_choice}.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

st.divider()

# ---------------------------------------------------------------------------
# Transfer matching
# ---------------------------------------------------------------------------
st.subheader("Transfer matching")
st.caption(
    "How auto-pairing decides two transactions are the same transfer (spec 8.1): opposite "
    "accounts, opposite signs, identical amount, dates within this many days of each other. "
    "A match containing one of these keywords in its description is applied automatically; "
    "otherwise it's only suggested for you to confirm."
)

current_window = get_transfer_window_days(conn)
current_keywords = get_transfer_keywords(conn)

window_input = st.number_input("Matching window (days)", min_value=1, max_value=60, value=current_window, step=1)
keywords_input = st.text_input("Keywords (comma-separated)", value=", ".join(current_keywords))

if st.button("Save transfer matching settings"):
    keywords = [k.strip().upper() for k in keywords_input.split(",") if k.strip()]
    if not keywords:
        st.error("At least one keyword is required.")
    else:
        set_transfer_matching(conn, int(window_input), keywords)
        _flash("Saved transfer matching settings.")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------
st.subheader("Backup & restore")
backups_dir = get_data_dir() / "backups"
existing_backups = list_backups(backups_dir)

bcol1, bcol2 = st.columns([1, 2])
with bcol1:
    keep_last = st.number_input("Keep last N backups", min_value=1, max_value=100, value=DEFAULT_KEEP_LAST, step=1)
    if st.button("Back up now", type="primary"):
        path = create_backup(conn, backups_dir, keep_last=int(keep_last))
        _flash(f"Backed up to {path.name}.")
        st.rerun()
with bcol2:
    if existing_backups:
        st.caption(f"{len(existing_backups)} backup(s) on disk, in {backups_dir}.")
        st.table([{"File": p.name, "Size (KB)": round(p.stat().st_size / 1024, 1)} for p in existing_backups])
    else:
        st.caption("No backups yet.")

if existing_backups:
    with st.expander("Restore from a backup"):
        st.warning(
            "This OVERWRITES your entire current database with the selected backup's contents. "
            "Anything added or changed since that backup was taken will be lost. "
            "**Restart the app after restoring** (kill and re-run `streamlit run app/Home.py`) — "
            "this page's own connection won't reflect the restored data until it does."
        )
        restore_choice = st.selectbox("Backup file", options=[p.name for p in existing_backups], key="restore_choice")
        if st.button("Restore this backup"):
            st.session_state["confirm_restore"] = restore_choice
        if st.session_state.get("confirm_restore") == restore_choice:
            st.error(f"Really overwrite your current data with '{restore_choice}'? This cannot be undone.")
            rcol1, rcol2 = st.columns(2)
            if rcol1.button("Yes, overwrite my current data", key="confirm_restore_yes"):
                restore_backup(backups_dir / restore_choice, get_db_path())
                del st.session_state["confirm_restore"]
                st.success("Restored. Please restart the app now (see warning above) before continuing.")
                st.stop()
            if rcol2.button("Cancel", key="confirm_restore_no"):
                del st.session_state["confirm_restore"]
                st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
st.subheader("Export")
st.caption("Builds a file in memory and hands it to your browser's own download — nothing is uploaded anywhere.")

months = list_available_months(conn)
if not months:
    st.caption("No transactions imported yet.")
else:
    ecol1, ecol2 = st.columns(2)
    with ecol1:
        st.markdown("**Transactions**")
        export_scope = st.radio("Range", options=["All time", "One month"], key="export_scope", horizontal=True)
        if export_scope == "One month":
            month_labels = [f"{calendar.month_name[m]} {y}" for y, m in months]
            picked = st.selectbox("Month", options=month_labels, key="export_month")
            y, m = months[month_labels.index(picked)]
            start, end = month_bounds(y, m)
        else:
            start, end = None, None
        txn_df = transactions_dataframe(conn, start, end)
        dl1, dl2 = st.columns(2)
        dl1.download_button(
            "Download CSV", data=to_csv_bytes(txn_df), file_name="transactions.csv", mime="text/csv",
            disabled=txn_df.empty,
        )
        dl2.download_button(
            "Download Excel", data=to_excel_bytes(txn_df, "Transactions"), file_name="transactions.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", disabled=txn_df.empty,
        )

    with ecol2:
        st.markdown("**Spending by category**")
        month_labels2 = [f"{calendar.month_name[m]} {y}" for y, m in months]
        picked2 = st.selectbox("Month", options=month_labels2, key="export_report_month")
        y2, m2 = months[month_labels2.index(picked2)]
        start2, end2 = month_bounds(y2, m2)
        report_df = spending_by_category_dataframe(conn, start2, end2)
        dl3, dl4 = st.columns(2)
        dl3.download_button(
            "Download CSV", data=to_csv_bytes(report_df), file_name=f"spending-{y2}-{m2:02d}.csv", mime="text/csv",
            disabled=report_df.empty, key="report_csv",
        )
        dl4.download_button(
            "Download Excel", data=to_excel_bytes(report_df, "Spending"), file_name=f"spending-{y2}-{m2:02d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", disabled=report_df.empty,
            key="report_excel",
        )
