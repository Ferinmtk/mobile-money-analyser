"""Safaricom M-Pesa statement parser.

M-Pesa is the most reliable of the mobile money statements to parse: every row
carries a 10-character receipt number and a full timestamp, and the details
string is far richer than Airtel's description field.
"""

from __future__ import annotations

import re

import pdfplumber

from .base import Statement, StatementError, clean, masked_account, normalise

NAME = "M-Pesa"

# A mention of "M-Pesa" is not enough for detection: other providers' and
# banks' narrations reference M-Pesa constantly. This is a structural signal.
# The receipt must contain at least one letter — a plain 10-digit run next to
# an ISO date could be another provider's numeric transaction id.
STRUCTURE = re.compile(
    r"\b(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}"
)
TITLE = re.compile(r"m-?pesa\s+(full\s+)?statement", re.I)

RECEIPT = re.compile(r"^[A-Z0-9]{10}$")

LINE = re.compile(
    r"^(?P<receipt>[A-Z0-9]{10})\s+"
    r"(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<details>.+?)\s+"
    r"(?P<status>COMPLETED|FAILED|PENDING|REVERSED)\s+"
    r"(?P<paid_in>-?[\d,]+\.\d{2})\s+"
    r"(?P<withdrawn>-?[\d,]+\.\d{2})\s+"
    r"(?P<balance>-?[\d,]+\.\d{2})\s*$",
    re.IGNORECASE,
)

FIELDS = [
    "receipt_no",
    "completion_time",
    "details",
    "status",
    "paid_in",
    "withdrawn",
    "balance",
]


def detect(text: str) -> bool:
    """True if this looks like an M-Pesa statement.

    Deliberately structural: a 10-character receipt followed by an ISO
    timestamp is a shape only M-Pesa produces.
    """
    return bool(STRUCTURE.search(text)) or bool(TITLE.search(text))


def _from_tables(page) -> list[dict]:
    rows: list[dict] = []
    for table in page.extract_tables() or []:
        for raw in table:
            cells = [clean(c) for c in raw]
            if len(cells) >= 7 and RECEIPT.match(cells[0]):
                rows.append(dict(zip(FIELDS, cells[:7])))
    return rows


def _from_text(page) -> list[dict]:
    rows: list[dict] = []
    for line in (page.extract_text() or "").splitlines():
        match = LINE.match(clean(line))
        if match:
            rows.append(
                {
                    "receipt_no": match["receipt"],
                    "completion_time": match["time"],
                    "details": clean(match["details"]),
                    "status": match["status"].upper(),
                    "paid_in": match["paid_in"],
                    "withdrawn": match["withdrawn"],
                    "balance": match["balance"],
                }
            )
    return rows


def parse(path, password: str | None = None) -> Statement:
    rows: list[dict] = []
    first_page_text = ""
    try:
        with pdfplumber.open(path, password=password) as pdf:
            pages = len(pdf.pages)
            for index, page in enumerate(pdf.pages):
                if index == 0:
                    first_page_text = page.extract_text() or ""
                rows.extend(_from_tables(page) or _from_text(page))
    except Exception as exc:
        raise StatementError(f"Could not read M-Pesa statement: {exc}") from exc

    if not rows:
        raise StatementError("No M-Pesa transactions found in this file.")

    warnings: list[str] = []
    return Statement(
        transactions=normalise(rows, NAME, warnings),
        source=str(path),
        provider=NAME,
        pages=pages,
        account=masked_account(first_page_text),
        warnings=warnings,
    )
