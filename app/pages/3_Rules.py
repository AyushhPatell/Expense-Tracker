import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from core.categories import list_categories, list_people
from core.db import init_db
from core.rules import (
    FIELDS,
    NUMERIC_FIELDS,
    SHARE_TYPES,
    TEXT_FIELDS,
    apply_rules_to_all,
    create_rule,
    delete_rule,
    get_rule,
    get_rule_split_failures,
    list_rules,
    preview_rules_run,
    set_rule_priority_and_enabled,
    test_conditions_against_existing,
    update_rule,
)

st.set_page_config(page_title="Rules", layout="wide")
st.title("Rules")

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
    "Rules run automatically on every import (spec 6.4) and can be re-run against everything "
    "at any point below. Manual edits always win: a locked type or split is never touched by a rule."
)

TYPES = ["unreviewed", "expense", "income", "transfer", "reimbursement", "refund", "ignore"]
NO_CHANGE = "— don't set —"
TEXT_OPS = ["contains", "not_contains", "starts_with", "equals", "regex", "in"]
NUMERIC_OPS = ["equals", "between", "gt", "lt", "in"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

categories = list_categories(conn)
people = list_people(conn)
category_labels = [c["label"] for c in categories]
person_labels = [p["label"] for p in people]


# ---------------------------------------------------------------------------
# Human-readable summaries (rules list + JSON preview)
# ---------------------------------------------------------------------------


def _display_value(field: str, op: str, value):
    if field == "amount_abs":
        if op == "between":
            return f"${value[0] / 100:.2f}-${value[1] / 100:.2f}"
        if op == "in":
            return ", ".join(f"${v / 100:.2f}" for v in value)
        return f"${value / 100:.2f}"
    return value


def _describe_leaf(leaf: dict) -> str:
    return f"{leaf['field']} {leaf['op']} {_display_value(leaf['field'], leaf['op'], leaf.get('value'))}"


def _describe_conditions(node: dict) -> str:
    if "all" in node:
        return " AND ".join(_describe_conditions(c) for c in node["all"])
    if "any" in node:
        return " OR ".join(f"({_describe_conditions(c)})" for c in node["any"])
    return _describe_leaf(node)


def _describe_actions(actions: dict) -> str:
    parts = []
    if "set_type" in actions:
        parts.append(f"type→{actions['set_type']}")
    if "split_template" in actions:
        parts.append(f"split into {len(actions['split_template'])} line(s)")
    elif "set_category" in actions:
        parts.append(f"category→{actions['set_category']}")
    if actions.get("add_tags"):
        parts.append(f"tags: {', '.join(actions['add_tags'])}")
    if "set_note" in actions:
        parts.append("sets note")
    if "mark_reviewed" in actions:
        parts.append(f"reviewed→{actions['mark_reviewed']}")
    return "; ".join(parts) if parts else "(no actions)"


def _is_flat_group(conditions: dict) -> bool:
    """True if this is a single all/any group of plain leaf conditions — the
    only shape the builder below can edit. A rule with nested groups (which
    the engine supports, spec 7.1) has to be edited via the JSON box instead;
    the builder never attempts a lossy reconstruction of one."""
    if not conditions or ("all" not in conditions and "any" not in conditions):
        return False
    key = "all" if "all" in conditions else "any"
    return all("field" in c for c in conditions[key])


# ---------------------------------------------------------------------------
# Rules list: reorder / enable / stop-processing / delete
# ---------------------------------------------------------------------------

rules = list_rules(conn)

st.subheader(f"All rules ({len(rules)})")

if not rules:
    st.info("No rules yet — add one below.")
else:
    rules_generation = st.session_state.get("rules_generation", 0)
    df = pd.DataFrame(
        [
            {
                "id": r["id"],
                "Priority": r["priority"],
                "Enabled": r["enabled"],
                "Name": r["name"],
                "Conditions": _describe_conditions(r["conditions"]),
                "Actions": _describe_actions(r["actions"]),
                "Stop processing": r["stop_processing"],
                "Times applied": r["times_applied"],
            }
            for r in rules
        ]
    ).set_index("id")
    original_df = df.copy()

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Priority": st.column_config.NumberColumn(help="Lower runs first", step=1),
            "Enabled": st.column_config.CheckboxColumn(),
            "Name": st.column_config.TextColumn(disabled=True),
            "Conditions": st.column_config.TextColumn(disabled=True),
            "Actions": st.column_config.TextColumn(disabled=True),
            "Stop processing": st.column_config.CheckboxColumn(help="Skip lower-priority rules once this one matches"),
            "Times applied": st.column_config.NumberColumn(disabled=True),
        },
        key=f"rules_grid_{rules_generation}",
    )

    if st.button("Save order / enabled / stop-processing changes", type="primary"):
        changed = 0
        for rule_id, edited_row in edited_df.iterrows():
            original_row = original_df.loc[rule_id]
            if (
                edited_row["Priority"] != original_row["Priority"]
                or bool(edited_row["Enabled"]) != bool(original_row["Enabled"])
                or bool(edited_row["Stop processing"]) != bool(original_row["Stop processing"])
            ):
                set_rule_priority_and_enabled(
                    conn, rule_id, int(edited_row["Priority"]), bool(edited_row["Enabled"]), bool(edited_row["Stop processing"])
                )
                changed += 1
        _flash(f"Updated {changed} rule(s).")
        st.session_state["rules_generation"] = rules_generation + 1
        st.rerun()

    del_col1, del_col2 = st.columns([3, 1])
    with del_col1:
        delete_choice = st.selectbox(
            "Delete a rule", options=[r["id"] for r in rules],
            format_func=lambda rid: next(r["name"] for r in rules if r["id"] == rid),
            key="delete_rule_choice",
        )
    with del_col2:
        st.write("")
        st.write("")
        if st.button("Delete"):
            st.session_state["confirm_delete_rule"] = delete_choice
    if st.session_state.get("confirm_delete_rule") == delete_choice:
        st.warning(f"Delete rule '{next(r['name'] for r in rules if r['id'] == delete_choice)}'? This cannot be undone.")
        c1, c2 = st.columns(2)
        if c1.button("Yes, delete", key="confirm_delete_rule_yes"):
            delete_rule(conn, delete_choice)
            del st.session_state["confirm_delete_rule"]
            _flash("Rule deleted.")
            st.rerun()
        if c2.button("Cancel", key="confirm_delete_rule_no"):
            del st.session_state["confirm_delete_rule"]
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Re-run rules + rule-split failures queue
# ---------------------------------------------------------------------------
st.subheader("Re-run rules")
st.caption(
    "Applies every enabled rule, in priority order, to every transaction — not just new imports. "
    "Already-locked types and splits are always skipped, so this is safe to run any time."
)

rerun_col1, rerun_col2 = st.columns([1, 3])
with rerun_col1:
    dry_run = st.checkbox("Dry run (preview only)", value=True)
    run_clicked = st.button("Run now", type="primary")

if run_clicked:
    if dry_run:
        preview = preview_rules_run(conn)
        st.session_state["rules_preview"] = preview
    else:
        result = apply_rules_to_all(conn)
        _flash(f"Applied rules: {result['matched']} match(es), {result['changed']} transaction(s) changed.")
        st.session_state.pop("rules_preview", None)
        st.rerun()

if dry_run and st.session_state.get("rules_preview"):
    preview = st.session_state["rules_preview"]
    st.caption(f"Would match {preview['matched']} time(s) across {len(preview['preview'])} transaction(s) with a real change.")
    if preview["preview"]:
        st.dataframe(
            [
                {
                    "Transaction": r["transaction_id"],
                    "Rule": r["rule_name"],
                    "Would change": ", ".join(f"{k}={v}" for k, v in r["changes"].items()),
                }
                for r in preview["preview"]
            ],
            use_container_width=True,
        )
    if preview["failures"]:
        st.warning(f"{len(preview['failures'])} split template(s) wouldn't reconcile — see below.")

failures = get_rule_split_failures(conn)
if failures:
    st.markdown("**Rule-split failures** — a matching rule's split template doesn't add up to the transaction amount")
    st.dataframe(
        [
            {
                "Date": f["txn_date"],
                "Description": f["description"],
                "Amount": f"${f['amount'] / 100:.2f}",
                "Rule": f["rule_name"],
                "Reason": f["reason"],
            }
            for f in failures
        ],
        use_container_width=True,
    )
    st.caption("Fix these by hand on the Review page, or adjust the rule's split template below.")

st.divider()

# ---------------------------------------------------------------------------
# Add / edit a rule
# ---------------------------------------------------------------------------
st.subheader("Add or edit a rule")

rule_choice = st.selectbox(
    "Rule to edit",
    options=["_new"] + [r["id"] for r in rules],
    format_func=lambda rid: "New rule" if rid == "_new" else next(r["name"] for r in rules if r["id"] == rid),
    key="rule_edit_choice",
)

# Switching which rule we're editing discards any unsaved draft for the
# previous one, same as the Review page's split editor — revisiting later
# always starts from the true saved state, never a stale half-edit.
previous_choice = st.session_state.get("rules_editor_last_choice")
if previous_choice is not None and previous_choice != rule_choice:
    st.session_state.pop(f"rule_draft_{previous_choice}", None)
st.session_state["rules_editor_last_choice"] = rule_choice

draft_key = f"rule_draft_{rule_choice}"


def _new_condition_row(field="description", op="contains", value="", value2=0.0) -> dict:
    return {"_id": uuid.uuid4().hex, "field": field, "op": op, "value": value, "value2": value2}


def _new_split_row(share="fixed", value=0.0, category="", person="", splitwise=False) -> dict:
    return {"_id": uuid.uuid4().hex, "share": share, "value": value, "category": category, "person": person, "splitwise": splitwise}


def _default_draft() -> dict:
    return {
        "name": "",
        "priority": 100,
        "enabled": True,
        "stop_processing": False,
        "group_type": "all",
        "conditions": [_new_condition_row()],
        "flat_unsupported": False,
        "set_type": NO_CHANGE,
        "set_category": NO_CHANGE,
        "split_lines": [],
        "add_tags": "",
        "set_note": "",
        "mark_reviewed": "Don't set",
    }


def _draft_from_rule(rule: dict) -> dict:
    draft = _default_draft()
    draft["name"] = rule["name"]
    draft["priority"] = rule["priority"]
    draft["enabled"] = rule["enabled"]
    draft["stop_processing"] = rule["stop_processing"]

    conditions = rule["conditions"]
    if _is_flat_group(conditions):
        group_type = "all" if "all" in conditions else "any"
        draft["group_type"] = group_type
        rows = []
        for leaf in conditions[group_type]:
            field, op, value = leaf["field"], leaf["op"], leaf.get("value")
            row = _new_condition_row(field=field, op=op)
            if op == "between":
                lo, hi = value
                if field == "amount_abs":
                    lo, hi = lo / 100, hi / 100
                row["value"], row["value2"] = lo, hi
            elif op == "in":
                if field == "weekday":
                    parts = [WEEKDAYS[v] if isinstance(v, int) else str(v) for v in value]
                elif field == "amount_abs":
                    parts = [str(v / 100) for v in value]
                else:
                    parts = [str(v) for v in value]
                row["value"] = ", ".join(parts)
            elif field == "amount_abs":
                row["value"] = (value or 0) / 100
            else:
                row["value"] = value
            rows.append(row)
        draft["conditions"] = rows or [_new_condition_row()]
    else:
        draft["flat_unsupported"] = True

    actions = rule["actions"]
    if "set_type" in actions:
        draft["set_type"] = actions["set_type"]
    if "split_template" in actions:
        draft["split_lines"] = [
            _new_split_row(
                share=line.get("share", "fixed"),
                value=line.get("value", 0.0),
                category=line.get("category", ""),
                person=line.get("person", ""),
                splitwise=bool(line.get("is_splitwise")),
            )
            for line in actions["split_template"]
        ]
    elif "set_category" in actions:
        draft["set_category"] = actions["set_category"]
    if actions.get("add_tags"):
        draft["add_tags"] = ", ".join(actions["add_tags"])
    if "set_note" in actions:
        draft["set_note"] = actions["set_note"]
    if "mark_reviewed" in actions:
        draft["mark_reviewed"] = "Mark reviewed" if actions["mark_reviewed"] else "Mark unreviewed"

    return draft


if draft_key not in st.session_state:
    if rule_choice == "_new":
        st.session_state[draft_key] = _default_draft()
    else:
        st.session_state[draft_key] = _draft_from_rule(get_rule(conn, rule_choice))

draft = st.session_state[draft_key]

if draft["flat_unsupported"]:
    st.warning(
        "This rule's conditions use nested groups the builder below can't display. "
        "Use 'Advanced: edit raw JSON' further down instead — editing the builder fields "
        "here and saving would replace those conditions."
    )

name_col, priority_col, enabled_col, stop_col = st.columns([3, 1, 1, 1])
draft["name"] = name_col.text_input("Rule name", value=draft["name"], key=f"{draft_key}_name")
draft["priority"] = priority_col.number_input("Priority", value=int(draft["priority"]), step=1, key=f"{draft_key}_priority")
draft["enabled"] = enabled_col.checkbox("Enabled", value=draft["enabled"], key=f"{draft_key}_enabled")
draft["stop_processing"] = stop_col.checkbox("Stop processing", value=draft["stop_processing"], key=f"{draft_key}_stop")

st.markdown("**Conditions**")
draft["group_type"] = st.radio(
    "Match", options=["all", "any"], index=0 if draft["group_type"] == "all" else 1,
    format_func=lambda v: "ALL of these" if v == "all" else "ANY of these",
    horizontal=True, key=f"{draft_key}_group_type",
)

remove_cond_index = None
for i, row in enumerate(draft["conditions"]):
    row_id = row["_id"]
    c1, c2, c3, c4 = st.columns([2, 2, 3, 1])
    row["field"] = c1.selectbox(
        "Field", options=list(FIELDS), index=list(FIELDS).index(row["field"]),
        key=f"{draft_key}_field_{row_id}", label_visibility="collapsed",
    )
    ops = TEXT_OPS if row["field"] in TEXT_FIELDS else NUMERIC_OPS
    if row["op"] not in ops:
        row["op"] = ops[0]
    row["op"] = c2.selectbox(
        "Op", options=ops, index=ops.index(row["op"]), key=f"{draft_key}_op_{row_id}", label_visibility="collapsed",
    )

    is_amount = row["field"] == "amount_abs"
    is_weekday = row["field"] == "weekday"
    with c3:
        if row["op"] == "between":
            b1, b2 = st.columns(2)
            label = "$" if is_amount else ""
            row["value"] = b1.number_input(f"From {label}", value=float(row["value"]), key=f"{draft_key}_val1_{row_id}", label_visibility="collapsed")
            row["value2"] = b2.number_input(f"To {label}", value=float(row["value2"]), key=f"{draft_key}_val2_{row_id}", label_visibility="collapsed")
        elif row["op"] == "in":
            placeholder = "comma-separated, e.g. Monday, Friday" if is_weekday else "comma-separated"
            row["value"] = st.text_input("Value", value=str(row["value"]), key=f"{draft_key}_val_{row_id}", label_visibility="collapsed", placeholder=placeholder)
        elif is_amount:
            row["value"] = st.number_input("Value ($)", value=float(row["value"] or 0), key=f"{draft_key}_val_{row_id}", label_visibility="collapsed")
        elif is_weekday and row["field"] in NUMERIC_FIELDS:
            current = int(row["value"]) if str(row["value"]).lstrip("-").isdigit() else 0
            weekday_choice = st.selectbox("Weekday", options=WEEKDAYS, index=min(max(current, 0), 6), key=f"{draft_key}_val_{row_id}", label_visibility="collapsed")
            row["value"] = WEEKDAYS.index(weekday_choice)
        elif row["field"] == "day_of_month":
            row["value"] = st.number_input("Day", min_value=1, max_value=31, value=int(row["value"]) if str(row["value"]).isdigit() and row["value"] else 1, key=f"{draft_key}_val_{row_id}", label_visibility="collapsed")
        else:
            row["value"] = st.text_input("Value", value=str(row["value"]), key=f"{draft_key}_val_{row_id}", label_visibility="collapsed")
    if c4.button("✕", key=f"{draft_key}_cond_remove_{row_id}"):
        remove_cond_index = i

if remove_cond_index is not None:
    draft["conditions"].pop(remove_cond_index)
    st.rerun()

if st.button("Add condition", key=f"{draft_key}_add_cond"):
    draft["conditions"].append(_new_condition_row())
    st.rerun()

st.markdown("**Actions**")
a1, a2 = st.columns(2)
draft["set_type"] = a1.selectbox("Set type to", options=[NO_CHANGE] + TYPES, index=([NO_CHANGE] + TYPES).index(draft["set_type"]) if draft["set_type"] in TYPES else 0, key=f"{draft_key}_set_type")
draft["mark_reviewed"] = a2.selectbox("Mark reviewed?", options=["Don't set", "Mark reviewed", "Mark unreviewed"], index=["Don't set", "Mark reviewed", "Mark unreviewed"].index(draft["mark_reviewed"]), key=f"{draft_key}_mark_reviewed")

draft["add_tags"] = st.text_input("Add tags (comma-separated)", value=draft["add_tags"], key=f"{draft_key}_tags")
draft["set_note"] = st.text_input("Set note (only fills in an empty note)", value=draft["set_note"], key=f"{draft_key}_note")

st.markdown("**Category / split** — a split template (if any lines are added) takes priority over a plain category")
draft["set_category"] = st.selectbox(
    "Set category to (used only when there's no split template below)",
    options=[NO_CHANGE] + category_labels,
    index=([NO_CHANGE] + category_labels).index(draft["set_category"]) if draft["set_category"] in category_labels else 0,
    key=f"{draft_key}_set_category",
)

st.caption("Split template lines — 'fixed' is a dollar amount, 'percent' is 0-100, 'equal'/'remainder' share whatever's left evenly.")
remove_split_index = None
for i, line in enumerate(draft["split_lines"]):
    line_id = line["_id"]
    s1, s2, s3, s4, s5, s6 = st.columns([2, 2, 3, 3, 1, 1])
    line["share"] = s1.selectbox("Share", options=list(SHARE_TYPES), index=list(SHARE_TYPES).index(line["share"]), key=f"{draft_key}_share_{line_id}", label_visibility="collapsed")
    line["value"] = s2.number_input(
        "Value", value=float(line["value"]), key=f"{draft_key}_share_val_{line_id}", label_visibility="collapsed",
        disabled=line["share"] in ("equal", "remainder"),
    )
    line["category"] = s3.selectbox("Category", options=[""] + category_labels, index=([""] + category_labels).index(line["category"]) if line["category"] in category_labels else 0, key=f"{draft_key}_share_cat_{line_id}", label_visibility="collapsed")
    line["person"] = s4.selectbox("Person", options=[""] + person_labels, index=([""] + person_labels).index(line["person"]) if line["person"] in person_labels else 0, key=f"{draft_key}_share_person_{line_id}", label_visibility="collapsed")
    line["splitwise"] = s5.checkbox("SW", value=line["splitwise"], key=f"{draft_key}_share_sw_{line_id}", help="Splitwise")
    if s6.button("✕", key=f"{draft_key}_split_remove_{line_id}"):
        remove_split_index = i

if remove_split_index is not None:
    draft["split_lines"].pop(remove_split_index)
    st.rerun()

if st.button("Add split line", key=f"{draft_key}_add_split"):
    draft["split_lines"].append(_new_split_row())
    st.rerun()


def _build_conditions(draft: dict) -> dict:
    leaves = []
    for row in draft["conditions"]:
        field, op = row["field"], row["op"]
        if field in NUMERIC_FIELDS:
            if op == "between":
                lo, hi = float(row["value"]), float(row["value2"])
                value = [round(lo * 100), round(hi * 100)] if field == "amount_abs" else [int(lo), int(hi)]
            elif op == "in":
                parts = [p.strip() for p in str(row["value"]).split(",") if p.strip()]
                if field == "weekday":
                    nums = [WEEKDAYS.index(p) if p in WEEKDAYS else int(p) for p in parts]
                else:
                    nums = [float(p) for p in parts]
                    nums = [round(n * 100) for n in nums] if field == "amount_abs" else [int(n) for n in nums]
                value = nums
            else:
                v = float(row["value"] or 0)
                value = round(v * 100) if field == "amount_abs" else int(v)
        else:
            if op == "in":
                value = [p.strip() for p in str(row["value"]).split(",") if p.strip()]
            else:
                value = row["value"]
        leaves.append({"field": field, "op": op, "value": value})
    return {draft["group_type"]: leaves}


def _build_actions(draft: dict) -> dict:
    actions: dict = {}
    if draft["set_type"] != NO_CHANGE:
        actions["set_type"] = draft["set_type"]
    if draft["split_lines"]:
        template = []
        for line in draft["split_lines"]:
            entry: dict = {"share": line["share"]}
            if line["share"] in ("fixed", "percent"):
                entry["value"] = float(line["value"])
            if line["category"]:
                entry["category"] = line["category"]
            if line["person"]:
                entry["person"] = line["person"]
            if line["splitwise"]:
                entry["is_splitwise"] = True
            template.append(entry)
        actions["split_template"] = template
    elif draft["set_category"] != NO_CHANGE:
        actions["set_category"] = draft["set_category"]
    if draft["add_tags"].strip():
        actions["add_tags"] = [t.strip() for t in draft["add_tags"].split(",") if t.strip()]
    if draft["set_note"].strip():
        actions["set_note"] = draft["set_note"].strip()
    if draft["mark_reviewed"] == "Mark reviewed":
        actions["mark_reviewed"] = True
    elif draft["mark_reviewed"] == "Mark unreviewed":
        actions["mark_reviewed"] = False
    return actions


built_conditions = _build_conditions(draft)
built_actions = _build_actions(draft)

with st.expander("Preview JSON"):
    st.code(json.dumps({"conditions": built_conditions, "actions": built_actions}, indent=2), language="json")

test_col1, test_col2 = st.columns([1, 3])
with test_col1:
    if st.button("Test against existing transactions"):
        st.session_state[f"{draft_key}_test_result"] = test_conditions_against_existing(conn, built_conditions)

test_result = st.session_state.get(f"{draft_key}_test_result")
if test_result:
    st.caption(f"Matches {test_result['total']} existing transaction(s).")
    if test_result["sample"]:
        st.dataframe(
            [
                {"Date": m["txn_date"], "Account": m["account"], "Description": m["description"], "Amount": f"${m['amount'] / 100:.2f}"}
                for m in test_result["sample"]
            ],
            use_container_width=True,
        )

save_label = "Create rule" if rule_choice == "_new" else "Save changes"
if st.button(save_label, type="primary"):
    try:
        if rule_choice == "_new":
            create_rule(
                conn, draft["name"], built_conditions, built_actions,
                priority=int(draft["priority"]), enabled=draft["enabled"], stop_processing=draft["stop_processing"],
            )
            _flash(f"Created rule '{draft['name']}'.")
        else:
            update_rule(
                conn, rule_choice, draft["name"], built_conditions, built_actions,
                priority=int(draft["priority"]), enabled=draft["enabled"], stop_processing=draft["stop_processing"],
            )
            _flash(f"Saved '{draft['name']}'.")
        del st.session_state[draft_key]
        st.session_state.pop(f"{draft_key}_test_result", None)
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))

with st.expander("Advanced: edit raw JSON"):
    st.caption("Escape hatch for anything the builder above can't express, like nested any/all groups.")
    raw_conditions = st.text_area("Conditions JSON", value=json.dumps(built_conditions, indent=2), key=f"{draft_key}_raw_cond", height=150)
    raw_actions = st.text_area("Actions JSON", value=json.dumps(built_actions, indent=2), key=f"{draft_key}_raw_actions", height=150)
    if st.button("Save from JSON", key=f"{draft_key}_save_raw"):
        try:
            parsed_conditions = json.loads(raw_conditions)
            parsed_actions = json.loads(raw_actions)
            if rule_choice == "_new":
                create_rule(
                    conn, draft["name"], parsed_conditions, parsed_actions,
                    priority=int(draft["priority"]), enabled=draft["enabled"], stop_processing=draft["stop_processing"],
                )
            else:
                update_rule(
                    conn, rule_choice, draft["name"], parsed_conditions, parsed_actions,
                    priority=int(draft["priority"]), enabled=draft["enabled"], stop_processing=draft["stop_processing"],
                )
            _flash(f"Saved '{draft['name']}' from JSON.")
            del st.session_state[draft_key]
            st.rerun()
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))
