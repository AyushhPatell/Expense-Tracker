import calendar
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import altair as alt
import pandas as pd
import streamlit as st

from core.db import init_db
from core.reports import (
    coverage_warnings,
    list_available_months,
    month_bounds,
    spending_by_account,
    spending_by_category,
    spending_lines_for_category,
    splitwise_totals,
    top_merchants,
    total_income,
    total_spending,
    transfer_by_category,
)

st.set_page_config(page_title="Dashboard", layout="wide")
st.title("Dashboard")

conn = init_db()

months = list_available_months(conn)
if not months:
    st.info("No transactions imported yet. Go to Import first.")
    st.stop()

month_labels = [f"{calendar.month_name[m]} {y}" for y, m in months]
selected_label = st.selectbox("Period", options=month_labels)
year, month = months[month_labels.index(selected_label)]
start_date, end_date = month_bounds(year, month)

# ---------------------------------------------------------------------------
# Completeness warning
# ---------------------------------------------------------------------------
warnings = coverage_warnings(conn, start_date, end_date)
for w in warnings:
    st.warning(f"Incomplete: {w}")

# ---------------------------------------------------------------------------
# Key metrics
# ---------------------------------------------------------------------------
spending = total_spending(conn, start_date, end_date)
income = total_income(conn, start_date, end_date)
net = income + spending
savings_rate = (net / income * 100) if income > 0 else None

col1, col2, col3, col4 = st.columns(4)
col1.metric("Spending", f"${-spending / 100:,.2f}")
col2.metric("Income", f"${income / 100:,.2f}")
col3.metric("Net savings", f"${net / 100:,.2f}")
col4.metric("Savings rate", f"{savings_rate:.0f}%" if savings_rate is not None else "n/a")

# ---------------------------------------------------------------------------
# Via Splitwise
# ---------------------------------------------------------------------------
sw = splitwise_totals(conn, start_date, end_date)
if sw["sent"] or sw["received"]:
    st.subheader("Via Splitwise")
    swcol1, swcol2 = st.columns(2)
    swcol1.metric("Sent (fronted / settled up)", f"${-sw['sent'] / 100:,.2f}")
    swcol2.metric("Received (paid back to me)", f"${sw['received'] / 100:,.2f}")
    st.caption("Excluded from spending and income above — tracked here separately.")

# ---------------------------------------------------------------------------
# Other tracked (excluded from spending) — investments, tuition, anything
# large/irregular marked transfer + categorized, so it doesn't skew "my
# spending" but is still visible as its own number.
# ---------------------------------------------------------------------------
other_tracked = transfer_by_category(conn, start_date, end_date)
if other_tracked:
    st.subheader("Other tracked (excluded from spending)")
    st.table(
        [{"Category": r["category"], "Amount": f"${-r['amount'] / 100:,.2f}"} for r in other_tracked]
    )
    st.caption(
        "Transfer-typed transactions with a category — investments, tuition, etc. "
        "Excluded from spending above, but tracked here so the money's still visible."
    )

# ---------------------------------------------------------------------------
# Spending by category
# ---------------------------------------------------------------------------
st.subheader("Spending by category")
by_category = spending_by_category(conn, start_date, end_date)
spend_only = [c for c in by_category if c["amount"] < 0]
if spend_only:
    df_cat = pd.DataFrame(
        [{"Category": c["category"], "Amount": -c["amount"] / 100} for c in spend_only]
    ).sort_values("Amount", ascending=False)
    chart = (
        alt.Chart(df_cat)
        .mark_bar()
        .encode(
            x=alt.X("Amount:Q", title="Spent ($)"),
            y=alt.Y("Category:N", sort="-x", title=None),
            tooltip=["Category", "Amount"],
        )
    )
    st.altair_chart(chart, use_container_width=True)

    category_options = df_cat.sort_values("Amount", ascending=False)["Category"].tolist()
    drill_choice = st.selectbox("See what's in a category", options=category_options, key="category_drilldown")
    lines = spending_lines_for_category(conn, start_date, end_date, drill_choice)
    st.table(
        [
            {"Date": ln["txn_date"], "Description": ln["description"], "Account": ln["account"], "Amount": f"${-ln['amount'] / 100:,.2f}"}
            for ln in lines
        ]
    )
else:
    st.caption("No spending recorded for this period.")

# ---------------------------------------------------------------------------
# Account breakdown + Top merchants
# ---------------------------------------------------------------------------
acol, mcol = st.columns(2)
with acol:
    st.subheader("Account breakdown")
    by_account = spending_by_account(conn, start_date, end_date)
    if by_account:
        st.table(
            [{"Account": a["account"], "Spent": f"${-a['amount'] / 100:,.2f}"} for a in by_account if a["amount"] < 0]
        )
    else:
        st.caption("Nothing to show.")

with mcol:
    st.subheader("Top merchants")
    merchants = top_merchants(conn, start_date, end_date)
    if merchants:
        st.table(
            [
                {"Merchant": m["description"], "Spent": f"${-m['amount'] / 100:,.2f}", "Count": m["count"]}
                for m in merchants
                if m["amount"] < 0
            ]
        )
    else:
        st.caption("Nothing to show.")

# ---------------------------------------------------------------------------
# Month-over-month trend (last 6 months, per category)
# ---------------------------------------------------------------------------
st.subheader("Trend (last 6 months)")
trend_months = months[:6][::-1]  # oldest to newest, for a left-to-right chart
trend_rows = []
for y, m in trend_months:
    s, e = month_bounds(y, m)
    label = f"{calendar.month_abbr[m]} {y}"
    for row in spending_by_category(conn, s, e):
        if row["amount"] < 0:
            trend_rows.append({"Month": label, "Category": row["category"], "Amount": -row["amount"] / 100})

if trend_rows:
    df_trend = pd.DataFrame(trend_rows)
    trend_chart = (
        alt.Chart(df_trend)
        .mark_bar()
        .encode(
            x=alt.X("Month:N", sort=[f"{calendar.month_abbr[m]} {y}" for y, m in trend_months]),
            y=alt.Y("Amount:Q", title="Spent ($)"),
            color="Category:N",
            tooltip=["Month", "Category", "Amount"],
        )
    )
    st.altair_chart(trend_chart, use_container_width=True)
else:
    st.caption("Not enough history yet.")
