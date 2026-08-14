"""Generate a fictional Telkom T-Kash statement PDF for testing and demos.

Public T-Kash samples are scarce, so this follows the common Kenyan wallet
export shape: a ruled table with Date / Transaction ID / Description /
Paid In / Withdrawn / Balance columns under a branded header.

    python scripts/make_sample_tkash_statement.py out.pdf
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

random.seed(41)

OUTGOING = [
    ("Merchant Payment NAIVAS SUPERMARKET", 300, 4200),
    ("Pay Bill KPLC PREPAID", 200, 2200),
    ("Airtime Purchase", 20, 500),
    ("Send Money to 254770000555 JOHN MWANGI", 200, 7000),
    ("Cash Out at Agent 88214 UMOJA AGENCY", 500, 12000),
    ("Transaction Fee", 5, 90),
]

INCOMING = [
    ("Money Received from 254770000111 ALICE NJERI", 400, 8000),
    ("Cash In at Agent 88214 UMOJA AGENCY", 1000, 25000),
]

HEADERS = ["Date", "Transaction ID", "Description", "Paid In", "Withdrawn", "Balance"]
XS = [28, 88, 190, 372, 434, 496]


def txn_id() -> str:
    return f"TK{random.randint(10 ** 9, 10 ** 10 - 1)}"


def build_rows(months: int = 4, per_month: int = 24) -> list[dict]:
    start = datetime(2026, 3, 2, 9, 0, 0)
    events: list[tuple[datetime, str, float, float]] = []

    for month in range(months):
        for _ in range(per_month + random.randint(-5, 5)):
            when = start + timedelta(
                days=month * 30 + random.randint(0, 28),
                hours=random.randint(0, 12),
                minutes=random.randint(0, 59),
            )
            if random.random() < 0.26:
                label, low, high = random.choice(INCOMING)
                amount = round(random.uniform(low, high), 2)
                paid_in, withdrawn = amount, 0.0
            else:
                label, low, high = random.choice(OUTGOING)
                amount = round(random.uniform(low, high), 2)
                paid_in, withdrawn = 0.0, amount
            events.append((when, label, paid_in, withdrawn))

    # Chain the running balance chronologically so the statement reconciles.
    events.sort(key=lambda e: e[0])
    balance = 4_500.00
    rows: list[dict] = []

    def emit(when: datetime, label: str, paid_in: float, withdrawn: float) -> None:
        nonlocal balance
        balance = round(balance + paid_in - withdrawn, 2)
        rows.append(
            {
                "date": when.strftime("%d/%m/%Y %H:%M"),
                "ref": txn_id(),
                "details": label,
                "in": f"{paid_in:,.2f}" if paid_in else "",
                "out": f"{withdrawn:,.2f}" if withdrawn else "",
                "balance": f"{balance:,.2f}",
            }
        )

    for when, label, paid_in, withdrawn in events:
        if balance + paid_in - withdrawn < 100:
            emit(when, "Cash In at Agent 88214 UMOJA AGENCY", 15_000.00, 0.0)
        emit(when, label, paid_in, withdrawn)

    return rows


def write_pdf(path: str, rows: list[dict]) -> None:
    pdf = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    line_height = 13

    def header() -> float:
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(28, height - 38, "TELKOM KENYA")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(28, height - 52, "T-KASH STATEMENT (SAMPLE — FICTIONAL DATA)")
        pdf.drawString(28, height - 64, "Mobile Number: 254770000321")

        y = height - 92
        pdf.setFont("Helvetica-Bold", 7.5)
        for x, title in zip(XS, HEADERS):
            pdf.drawString(x, y, title)
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
        for x, key in zip(XS, ["date", "ref", "details", "in", "out", "balance"]):
            pdf.drawString(x, y, str(row.get(key, ""))[:48])
        pdf.setLineWidth(0.15)
        pdf.setStrokeColor(colors.lightgrey)
        pdf.line(24, y - 3, width - 24, y - 3)
        y -= line_height

    pdf.save()


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_tkash_statement.pdf"
    rows = build_rows()
    write_pdf(out, rows)
    print(f"Wrote {len(rows)} fictional T-Kash transactions to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
