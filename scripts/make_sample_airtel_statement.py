"""Generate fictional Airtel Money statement PDFs for testing and demos.

Two layouts, because Airtel exports are not consistent:

    split    separate Paid In / Withdrawn columns, ruled table
    single   one signed Amount column, no ruling lines

    python scripts/make_sample_airtel_statement.py split out.pdf
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

random.seed(23)

OUTGOING = [
    ("Merchant Payment NAIVAS SUPERMARKET", 300, 4200),
    ("Merchant Payment QUICKMART", 250, 3000),
    ("Pay Bill KPLC PREPAID", 200, 2200),
    ("Pay Bill NAIROBI WATER", 400, 1600),
    ("Airtime Recharge", 20, 500),
    ("Data Bundle Purchase", 50, 1500),
    ("Send Money to 254733000222 MARY WANJIKU", 200, 7000),
    ("Send Money to Other Network 254712000111", 300, 9000),
    ("Cash Out at Agent 55231 KAREN AGENCY", 500, 12000),
    ("Merchant Payment SHELL LANGATA", 500, 4500),
    ("Transaction Fee", 5, 90),
    ("Withdrawal Charge", 10, 180),
    ("Pay Bill KCA UNIVERSITY", 5000, 22000),
]

INCOMING = [
    ("Money Received from 254701000333 JAMES OTIENO", 400, 8000),
    ("Cash In at Agent 55231 KAREN AGENCY", 1000, 25000),
    ("Salary Payment NOXERATECH", 25000, 70000),
]

LAYOUTS = {
    "split": {
        "headers": [
            "Date",
            "Transaction ID",
            "Description",
            "Paid In",
            "Withdrawn",
            "Balance",
        ],
        "x": [28, 88, 190, 372, 434, 496],
        "order": ["date", "ref", "details", "in", "out", "balance"],
        "ruled": True,
        "date_fmt": "%d/%m/%Y %H:%M",
        "single": False,
    },
    "single": {
        "headers": ["Date", "Transaction ID", "Description", "Amount", "Balance"],
        "x": [28, 100, 200, 400, 480],
        "order": ["date", "ref", "details", "amount", "balance"],
        "ruled": False,
        "date_fmt": "%d-%b-%Y",
        "single": True,
    },
}


def txn_id() -> str:
    return (
        f"PP{random.randint(260101, 260630)}."
        f"{random.randint(1000, 9999)}."
        f"C{random.randint(10000, 99999)}"
    )


def build_rows(layout: dict, months: int = 5, per_month: int = 28) -> list[dict]:
    start = datetime(2026, 2, 4, 8, 30, 0)
    events: list[tuple[datetime, str, float, float]] = []

    for month in range(months):
        for _ in range(per_month + random.randint(-6, 6)):
            when = start + timedelta(
                days=month * 30 + random.randint(0, 28),
                hours=random.randint(0, 12),
                minutes=random.randint(0, 59),
            )
            if random.random() < 0.24:
                label, low, high = random.choice(INCOMING)
                amount = round(random.uniform(low, high), 2)
                paid_in, withdrawn = amount, 0.0
            else:
                label, low, high = random.choice(OUTGOING)
                amount = round(random.uniform(low, high), 2)
                paid_in, withdrawn = 0.0, amount

            events.append((when, label, paid_in, withdrawn))

    # Chain the running balance in chronological order so the statement
    # reconciles row by row.
    events.sort(key=lambda e: e[0])
    balance = 6_800.00
    rows: list[dict] = []

    def emit(when: datetime, label: str, paid_in: float, withdrawn: float) -> None:
        nonlocal balance
        balance = round(balance + paid_in - withdrawn, 2)
        signed = paid_in if paid_in else -withdrawn
        rows.append(
            {
                "date": when.strftime(layout["date_fmt"]),
                "ref": txn_id(),
                "details": label,
                "in": f"{paid_in:,.2f}" if paid_in else "",
                "out": f"{withdrawn:,.2f}" if withdrawn else "",
                "amount": f"{signed:,.2f}",
                "balance": f"{balance:,.2f}",
                "_sort": when,
            }
        )

    for when, label, paid_in, withdrawn in events:
        if balance + paid_in - withdrawn < 200:
            # An explicit top-up row keeps the balance chain honest.
            emit(when, "Cash In at Agent 55231 KAREN AGENCY", 20_000.00, 0.0)
        emit(when, label, paid_in, withdrawn)

    return rows


def write_pdf(path: str, layout: dict, rows: list[dict]) -> None:
    pdf = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    xs = layout["x"]
    line_height = 13

    def header() -> float:
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(28, height - 38, "AIRTEL MONEY KENYA")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(
            28, height - 52, "AIRTEL MONEY STATEMENT (SAMPLE — FICTIONAL DATA)"
        )
        pdf.drawString(28, height - 64, "Mobile Number: 254730000987")
        pdf.drawString(28, height - 76, "Customer Name: SAMPLE CUSTOMER")

        y = height - 100
        pdf.setFont("Helvetica-Bold", 7.5)
        for x, title in zip(xs, layout["headers"]):
            pdf.drawString(x, y, title)
        if layout["ruled"]:
            pdf.setStrokeColor(colors.black)
            pdf.setLineWidth(0.4)
            pdf.line(24, y - 4, width - 24, y - 4)
        pdf.setFont("Helvetica", 7)
        return y - 16

    y = header()

    for row in rows:
        if y < 50:
            pdf.showPage()
            y = header()
        for x, key in zip(xs, layout["order"]):
            pdf.drawString(x, y, str(row.get(key, ""))[:48])
        if layout["ruled"]:
            pdf.setLineWidth(0.15)
            pdf.setStrokeColor(colors.lightgrey)
            pdf.line(24, y - 3, width - 24, y - 3)
        y -= line_height

    pdf.save()


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "split"
    out = sys.argv[2] if len(sys.argv) > 2 else f"sample_airtel_{which}.pdf"

    if which not in LAYOUTS:
        print(f"Unknown layout '{which}'. Choose from: {', '.join(LAYOUTS)}")
        return 1

    layout = LAYOUTS[which]
    rows = build_rows(layout)
    write_pdf(out, layout, rows)
    print(f"Wrote {len(rows)} fictional Airtel Money transactions to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
