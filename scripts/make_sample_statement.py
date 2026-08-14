"""Generate a realistic but entirely fictional M-Pesa statement PDF.

Real statements contain personal financial data, so the test suite and the
public demo run on generated data instead.

    python scripts/make_sample_statement.py sample_statement.pdf
"""

from __future__ import annotations

import random
import string
import sys
from datetime import datetime, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

random.seed(7)

MERCHANTS = [
    ("Merchant Payment Online to 5401 - NAIVAS SUPERMARKET", 400, 4500),
    ("Merchant Payment Online to 8812 - QUICKMART LTD", 300, 3200),
    ("Pay Bill Online to 888880 - KPLC PREPAID Acc. 4471029", 200, 2000),
    ("Pay Bill Online to 4141 - NAIROBI WATER Acc. 88213", 400, 1800),
    ("Pay Bill Online to 444900 - DSTV KENYA Acc. 7712", 1100, 2900),
    ("Merchant Payment Online to 3391 - SHELL WESTLANDS", 500, 5000),
    ("Merchant Payment Online to 7724 - JAVA HOUSE", 350, 2200),
    ("Merchant Payment Online to 2210 - CHICKEN INN", 300, 1400),
    ("Pay Bill Online to 522522 - KCB PAYBILL Acc. 0110293", 1000, 12000),
    ("Merchant Payment Online to 9902 - GOODLIFE PHARMACY", 300, 3500),
    ("Airtime Purchase", 50, 1000),
    ("Customer Transfer to 254712000111 - PETER KAMAU", 200, 6000),
    ("Customer Transfer to 254733000222 - MARY WANJIKU", 300, 8000),
    ("Customer Withdrawal At Agent Till 402931 - MJENGO AGENCY", 500, 10000),
    ("Pay Bill Online to 400200 - KCA UNIVERSITY Acc. 21030", 5000, 25000),
]

INCOMING = [
    ("Funds received from 254701000333 - JAMES OTIENO", 500, 9000),
    ("Business Payment from 600100 - SIMPLIFYBIZ LTD", 20000, 60000),
    ("Funds received from 254720000444 - GRACE MUTHONI", 300, 5000),
]

CHARGES = [
    ("Pay Bill Charge", 0, 60),
    ("Customer Transfer Charge", 0, 110),
    ("Withdrawal Charge", 0, 200),
]

HEADER = [
    "Receipt No.",
    "Completion Time",
    "Details",
    "Status",
    "Paid In",
    "Withdrawn",
    "Balance",
]


def receipt() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def build_rows(months: int = 6, per_month: int = 45) -> list[list[str]]:
    start = datetime(2026, 1, 3, 8, 15, 0)
    events: list[tuple[datetime, str, float, float]] = []

    for month in range(months):
        for _ in range(per_month + random.randint(-8, 8)):
            when = start + timedelta(
                days=month * 30 + random.randint(0, 29),
                hours=random.randint(0, 13),
                minutes=random.randint(0, 59),
            )
            roll = random.random()

            if roll < 0.16:
                label, low, high = random.choice(INCOMING)
                amount = round(random.uniform(low, high), 2)
                paid_in, withdrawn = amount, 0.0
            elif roll < 0.28:
                label, low, high = random.choice(CHARGES)
                amount = round(random.uniform(max(low, 5), high), 2)
                paid_in, withdrawn = 0.0, amount
            else:
                label, low, high = random.choice(MERCHANTS)
                amount = round(random.uniform(low, high), 2)
                paid_in, withdrawn = 0.0, amount

            events.append((when, label, paid_in, withdrawn))

    # The running balance is chained in chronological order so the statement
    # reconciles: balance[n] - balance[n-1] == paid_in - withdrawn, always.
    events.sort(key=lambda e: e[0])
    balance = 14_200.00
    rows: list[list[str]] = []

    def emit(when: datetime, label: str, paid_in: float, withdrawn: float) -> None:
        nonlocal balance
        balance = round(balance + paid_in - withdrawn, 2)
        rows.append(
            [
                receipt(),
                when.strftime("%Y-%m-%d %H:%M:%S"),
                label,
                "COMPLETED",
                f"{paid_in:,.2f}",
                f"{withdrawn:,.2f}",
                f"{balance:,.2f}",
            ]
        )

    for when, label, paid_in, withdrawn in events:
        if balance + paid_in - withdrawn < 0:
            # An explicit top-up row, so the balance chain stays honest.
            emit(when, INCOMING[0][0], 15_000.00, 0.0)
        emit(when, label, paid_in, withdrawn)

    return rows


def write_pdf(path: str, rows: list[list[str]]) -> None:
    pdf = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    x_positions = [28, 92, 186, 392, 436, 486, 534]
    line_height = 12
    y = height - 60

    def header() -> float:
        nonlocal y
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(30, height - 40, "M-PESA STATEMENT (SAMPLE — FICTIONAL DATA)")
        pdf.setFont("Helvetica-Bold", 7)
        for x, title in zip(x_positions, HEADER):
            pdf.drawString(x, height - 60, title)
        pdf.setFont("Helvetica", 6.5)
        return height - 74

    y = header()

    for row in rows:
        if y < 45:
            pdf.showPage()
            y = header()
        for x, cell in zip(x_positions, row):
            text = cell if len(cell) <= 58 else cell[:57]
            pdf.drawString(x, y, text)
        y -= line_height * 0.75

    pdf.save()


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_statement.pdf"
    rows = build_rows()
    write_pdf(out, rows)
    print(f"Wrote {len(rows)} fictional transactions to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
