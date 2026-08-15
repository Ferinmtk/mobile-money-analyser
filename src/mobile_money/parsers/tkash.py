"""Telkom T-Kash statement parser.

T-Kash is the third Kenyan mobile money wallet. Public statement samples are
scarce, so this parser reads any layout whose column headers appear in
HEADER_ALIASES — the same adaptive path Airtel Money uses. Run it against a
real statement and expect to add an alias or two in base.py.
"""

from __future__ import annotations

import re

from .base import Statement, header_mapped_parse

NAME = "T-Kash"

BRAND = re.compile(r"t-?kash|telkom\s*(kenya|money)", re.I)

TITLE = re.compile(r"t-?kash.{0,40}statement", re.I | re.S)


def detect(text: str) -> bool:
    """True if this looks like a T-Kash statement.

    Like Airtel, the brand is only trusted in the statement header — other
    providers' narrations can mention Telkom or T-Kash in a transfer.
    """
    head = "\n".join(text.splitlines()[:15])
    return bool(TITLE.search(text)) or bool(BRAND.search(head))


def parse(path, password: str | None = None) -> Statement:
    return header_mapped_parse(path, password, NAME)
