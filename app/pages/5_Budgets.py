import calendar
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from core.budgets import budgets_for_period, list_category_budgets, set_category_budget
from core.db import init_db
from core.reports import list_available_months, month_bounds

st.set_page_config(page_title="Budgets", layout="wide")
st.title("Budgets")

conn = init_db()


def _flash(message: str, icon: str = "✅") -> None:
    """Queue a short toast for the next render — a toast fired in the same
    run as st.rerun() never reaches the browser, since the rerun cuts the
    run off first."""
    st.session_state["_flash_message"] = (message, icon)


if "_flash_message" in st.session_state:
    _msg, _icon = st.session_state.pop("_flash_message")
    st.toast(_msg, icon=_icon)

# ---------------------------------------------------------------------------
# Set budgets per category (a standing setting, not tied to one month)
# ---------------------------------------------------------------------------
st.subheader("Monthly budgets")
st.caption(
    "A budget set on a category with children (e.g. Housing) covers all of its children's "
    "spending too. Leave the amount at $0 to stop tracking a category. Rollover carries "
    "last month's unused amount forward as a bonus — it never carries a deficit."
)

budgets_generation = st.session_state.get("budgets_generation", 0)
rows = list_category_budgets(conn)
df = pd.DataFrame(
    [
        {
            "id": r["id"],
            "Category": r["label"],
            "Monthly budget ($)": (r["monthly_budget"] or 0) / 100,
            "Rollover": r["rollover"],
        }
        for r in rows
    ]
).set_index("id")
original_df = df.copy()

edited_df = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    height=420,
    column_config={
        "Category": st.column_config.TextColumn(disabled=True),
        "Monthly budget ($)": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.2f"),
        "Rollover": st.column_config.CheckboxColumn(help="Carry unused budget forward one month"),
    },
    key=f"budgets_grid_{budgets_generation}",
)

if st.button("Save budgets", type="primary"):
    changed = 0
    for category_id, edited_row in edited_df.iterrows():
        original_row = original_df.loc[category_id]
        if edited_row["Monthly budget ($)"] != original_row["Monthly budget ($)"] or bool(
            edited_row["Rollover"]
        ) != bool(original_row["Rollover"]):
            new_budget = round(edited_row["Monthly budget ($)"] * 100)
            set_category_budget(conn, category_id, new_budget if new_budget > 0 else None, bool(edited_row["Rollover"]))
            changed += 1
    _flash(f"Saved {changed} budget change(s).")
    st.session_state["budgets_generation"] = budgets_generation + 1
    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Actual vs budget for a selected period
# ---------------------------------------------------------------------------
st.subheader("Budget vs actual")

months = list_available_months(conn)
if not months:
    st.info("No transactions imported yet. Go to Import first.")
    st.stop()

month_labels = [f"{calendar.month_name[m]} {y}" for y, m in months]
selected_label = st.selectbox("Period", options=month_labels, key="budgets_period")
year, month = months[month_labels.index(selected_label)]

results = budgets_for_period(conn, year, month)
if not results:
    st.info("No categories have a budget set yet — add one above.")
else:
    for r in results:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"**{r['category']}**" + (" 🔁" if r["rollover"] else ""))
            pct = min(r["pct_used"], 1.0) if r["pct_used"] is not None else 0.0
            st.progress(pct)
        with col2:
            spent_label = f"${r['spent'] / 100:,.2f} / ${r['effective_budget'] / 100:,.2f}"
            if r["over_budget"]:
                st.error(spent_label)
            else:
                st.write(spent_label)
        if r["over_budget"]:
            st.caption(f"⚠️ ${-r['remaining'] / 100:,.2f} over budget")
        elif r["rollover"] and r["effective_budget"] != r["budget"]:
            st.caption(f"Includes ${(r['effective_budget'] - r['budget']) / 100:,.2f} rolled over from last month.")
