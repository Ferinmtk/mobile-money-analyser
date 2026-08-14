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

from .base import Statement, header_mapped_parse

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


def parse(path, password: str | None = None) -> Statement:
    return header_mapped_parse(path, password, NAME)
