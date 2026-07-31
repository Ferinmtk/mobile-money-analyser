"""Streamlit dashboard for the mobile money analyser.

    streamlit run app.py

Uploads are written to a temporary file, parsed, and deleted immediately —
including when parsing fails. Nothing is persisted and nothing leaves the
machine.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mobile_money import analysis  # noqa: E402
from mobile_money.parsers import StatementError, parse_many  # noqa: E402

st.set_page_config(page_title="Mobile Money Analyser", layout="wide")


def money(value: float | None) -> str:
    return "n/a" if value is None else f"KES {value:,.0f}"


st.title("Mobile Money Analyser")
st.caption(
    "Upload M-Pesa and Airtel Money PDF statements and see where the money went. "
    "Upload both to view them as one timeline. Nothing is stored: files are "
    "parsed in memory and discarded."
)

with st.sidebar:
    st.header("Statement")
    uploads = st.file_uploader(
        "PDF statements",
        type=["pdf"],
        accept_multiple_files=True,
        help="M-Pesa or Airtel Money. Upload both for a combined view.",
    )
    password = st.text_input(
        "Password (if protected)",
        type="password",
        help="For M-Pesa this is usually the ID number the line is registered to.",
    )
    st.divider()
    st.caption(
        "No statement to hand? Run `python scripts/make_sample_statement.py` or "
        "`python scripts/make_sample_airtel_statement.py split` to generate a "
        "fictional one."
    )

if not uploads:
    st.info("Upload one or more statements in the sidebar to begin.")
    st.stop()

paths = []
for upload in uploads:
    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    handle.write(upload.getvalue())
    handle.flush()
    handle.close()
    paths.append(handle.name)

try:
    statement = parse_many(paths, password=password or None)
except StatementError as exc:
    st.error(str(exc))
    st.stop()
finally:
    for path in paths:
        Path(path).unlink(missing_ok=True)

st.subheader(statement.provider)

frame = analysis.prepare(statement.transactions)
stats = analysis.overview(frame)

left, middle, right, far_right = st.columns(4)
left.metric("Money in", money(stats["money_in"]))
middle.metric("Money out", money(stats["money_out"]))
right.metric("Net", money(stats["net"]))
far_right.metric(
    "Charges",
    money(stats["charges"]),
    f"{stats['charges_pct_of_spend']:.1f}% of spend",
    delta_color="inverse",
)

caption = f"{stats['transactions']} transactions from {stats['first']} to {stats['last']}"
if stats["closing_balance"] is not None:
    caption += f" · closing balance {money(stats['closing_balance'])}"
st.caption(caption)

for warning in statement.warnings:
    st.warning(warning)

st.subheader("What we noticed")
for note in analysis.insights(frame):
    st.write(f"- {note}")

names = ["Categories", "Monthly trend", "Who you pay", "Transactions"]
multi = frame["provider"].nunique() > 1
if multi:
    names.insert(0, "By provider")
panels = dict(zip(names, st.tabs(names)))

if multi:
    with panels["By provider"]:
        split = analysis.by_provider(frame)
        st.bar_chart(split.set_index("provider")[["money_in", "money_out"]])
        st.dataframe(split, use_container_width=True, hide_index=True)

with panels["Categories"]:
    table = analysis.by_category(frame)
    spending = table[table["spent"] > 0].set_index("category")
    st.bar_chart(spending["spent"], height=380)
    st.dataframe(table, use_container_width=True, hide_index=True)

with panels["Monthly trend"]:
    months = analysis.monthly(frame).set_index("month")
    st.line_chart(months[["money_in", "money_out"]], height=340)
    st.bar_chart(months["net"], height=260)
    projection = analysis.forecast_next_month(frame)
    if projection["method"] != "none":
        st.info(
            f"Projected spend next month: {money(projection['projected_spend'])} "
            f"— {projection['method']}, {projection['confidence']} confidence, "
            f"based on {projection.get('months_used', 0)} months."
        )

with panels["Who you pay"]:
    top = analysis.top_counterparties(frame, limit=15)
    st.bar_chart(top.set_index("counterparty")["spent"], height=380)
    st.dataframe(top, use_container_width=True, hide_index=True)

with panels["Transactions"]:
    columns = [
        "completion_time",
        "provider",
        "details",
        "category",
        "counterparty",
        "paid_in",
        "withdrawn",
    ]
    if not multi:
        columns.append("balance")
    display = frame[columns]
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.download_button(
        "Download as CSV",
        data=display.to_csv(index=False).encode("utf-8"),
        file_name="transactions.csv",
        mime="text/csv",
    )
