"""Airtel Money statement parser.

Airtel statements are looser than Safaricom's. In practice they vary by how
the statement was requested (app export, emailed PDF, agent printout), so this
parser leans on header mapping and word positions rather than a fixed layout.

Known variations handled:
  - separate Paid In / Withdrawn columns, or a single signed Amount column
  - transaction ids of the form PP260114.1432.C12345, or plain digits
  - dates with or without a time component

The layout is not as stable as M-Pesa's. Run it against a real statement and
expect to adjust HEADER_ALIASES in base.py if a column is named differently.
"""

from __future__ import annotations

import re

import pdfplumber

from .base import (
    Statement,
    StatementError,
    assign_column,
    clean,
    column_anchors,
    lines_with_positions,
    normalise,
    split_amount,
    to_datetime,
    to_number,
)

NAME = "Airtel Money"

BRAND = re.compile(r"airtel\s*(money|kenya|networks)", re.I)

# PP260114.1432.C12345 style, or a long digit run used as a txn id
TXN_ID = re.compile(r"\b([A-Z]{2}\d{6}\.\d{3,4}\.[A-Z]\d{4,6}|\d{10,14})\b")

TITLE = re.compile(r"airtel\s*money.{0,40}statement", re.I | re.S)


def detect(text: str) -> bool:
    """True if this looks like an Airtel Money statement.

    Requires the Airtel brand on the page. An M-Pesa statement can mention
    Airtel in a transfer narration, so the brand alone is checked against the
    statement header rather than anywhere in the body.
    """
    head = "\n".join(text.splitlines()[:15])
    return bool(TITLE.search(text)) or bool(BRAND.search(head))


def _rows_from_tables(page) -> list[dict]:
    """Read a ruled table, mapping columns by their header text."""
    rows: list[dict] = []
    mapping: dict[str, int] | None = None

    for table in page.extract_tables() or []:
        for raw in table:
            cells = [clean(c) for c in raw]
            if not any(cells):
                continue

            lowered = [c.lower() for c in cells]
            if any("balance" in c for c in lowered) and any(
                "date" in c or "time" in c for c in lowered
            ):
                mapping = {}
                from .base import HEADER_ALIASES

                used: set[int] = set()
                for logical, aliases in HEADER_ALIASES.items():
                    for alias in aliases:
                        for index, cell in enumerate(lowered):
                            if index in used or not cell:
                                continue
                            if alias in cell:
                                mapping[logical] = index
                                used.add(index)
                                break
                        if logical in mapping:
                            break
                continue

            if not mapping:
                continue

            def cell(key: str) -> str:
                index = mapping.get(key)
                if index is None or index >= len(cells):
                    return ""
                return cells[index]

            when = to_datetime(cell("completion_time"))
            if when is None:
                continue

            details = cell("details")
            paid_in = to_number(cell("paid_in"))
            withdrawn = abs(to_number(cell("withdrawn")))

            if not paid_in and not withdrawn and "amount" in mapping:
                paid_in, withdrawn = split_amount(to_number(cell("amount")), details)

            rows.append(
                {
                    "receipt_no": cell("receipt_no"),
                    "completion_time": when,
                    "details": details,
                    "status": cell("status") or "COMPLETED",
                    "paid_in": paid_in,
                    "withdrawn": withdrawn,
                    "balance": to_number(cell("balance")),
                }
            )
    return rows


def _rows_from_positions(page) -> list[dict]:
    """Fallback for statements with no ruled table, using word x positions."""
    lines = lines_with_positions(page)
    anchors = column_anchors(lines)
    if not anchors:
        return []

    rows: list[dict] = []
    for line in lines:
        cells: dict[str, list[str]] = {}
        for word in line:
            column = assign_column(word, anchors)
            if column:
                cells.setdefault(column, []).append(word["text"])

        joined = {key: " ".join(value) for key, value in cells.items()}
        when = to_datetime(joined.get("completion_time", ""))
        if when is None:
            continue

        details = clean(joined.get("details", ""))
        paid_in = to_number(joined.get("paid_in", ""))
        withdrawn = abs(to_number(joined.get("withdrawn", "")))

        if not paid_in and not withdrawn and "amount" in joined:
            paid_in, withdrawn = split_amount(to_number(joined["amount"]), details)

        if not paid_in and not withdrawn:
            continue

        rows.append(
            {
                "receipt_no": joined.get("receipt_no", ""),
                "completion_time": when,
                "details": details,
                "status": joined.get("status", "COMPLETED"),
                "paid_in": paid_in,
                "withdrawn": withdrawn,
                "balance": to_number(joined.get("balance", "")),
            }
        )
    return rows


def parse(path, password: str | None = None) -> Statement:
    rows: list[dict] = []
    warnings: list[str] = []
    used_positions = False
    first_page_text = ""

    try:
        with pdfplumber.open(path, password=password) as pdf:
            pages = len(pdf.pages)
            for index, page in enumerate(pdf.pages):
                if index == 0:
                    first_page_text = page.extract_text() or ""
                found = _rows_from_tables(page)
                if not found:
                    found = _rows_from_positions(page)
                    if found:
                        used_positions = True
                rows.extend(found)
    except Exception as exc:
        raise StatementError(f"Could not read Airtel Money statement: {exc}") from exc

    if not rows:
        raise StatementError(
            "No Airtel Money transactions found. Airtel layouts vary — see the "
            "'Adding a layout' section of the README."
        )

    if used_positions:
        warnings.append(
            "This statement had no ruled table, so columns were read from the "
            "horizontal position of each value. Spot-check a few rows."
        )

    account = None
    found = re.search(r"\b(?:2547|07)\d{7,8}\b", first_page_text)
    if found:
        number = found.group(0)
        account = "*" * (len(number) - 4) + number[-4:]

    return Statement(
        transactions=normalise(rows, NAME),
        source=str(path),
        provider=NAME,
        pages=pages,
        account=account,
        warnings=warnings,
    )
