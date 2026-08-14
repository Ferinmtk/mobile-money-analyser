"""Shared building blocks for every mobile money parser.

M-Pesa and Airtel Money publish different statements but describe the same
thing: a timestamped transaction with a direction, an amount and a running
balance. Every parser returns this normalised frame:

    receipt_no  completion_time  details  status  paid_in  withdrawn  balance

Everything downstream — categorisation, analysis, the dashboard — reads only
that schema, so adding a provider never touches the analysis layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

COLUMNS = [
    "receipt_no",
    "completion_time",
    "details",
    "status",
    "paid_in",
    "withdrawn",
    "balance",
]

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y",
    "%d %b %Y %H:%M",
    "%d %b %Y",
)


class StatementError(Exception):
    """Raised when a statement cannot be opened, read or understood."""


@dataclass
class Statement:
    """A parsed statement plus the metadata worth keeping."""

    transactions: pd.DataFrame
    source: str
    provider: str
    pages: int = 0
    account: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def period(self) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        if self.transactions.empty:
            return None
        column = self.transactions["completion_time"]
        return column.min(), column.max()

    def __len__(self) -> int:
        return len(self.transactions)


def to_number(value: str | float | None) -> float:
    """Convert a money string to a float.

    Handles thousands separators, currency prefixes, trailing DR/CR markers
    and accounting negatives: '1,200.00', 'KES 3,400.50', '(500.00)', '250 DR'.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text in {"-", "--", "nil", "NIL", "N/A"}:
        return 0.0

    debit = bool(re.search(r"\bDR\b", text, re.I))
    text = re.sub(r"\b(DR|CR)\b", "", text, flags=re.I)
    text = re.sub(r"(KES|KSH|KSHS|/=)", "", text, flags=re.I)
    text = text.replace(",", "").replace(" ", "").strip()

    bracketed = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if not text or text == "-":
        return 0.0

    try:
        number = float(text)
    except ValueError:
        return 0.0

    return -abs(number) if (bracketed or debit) else number


def to_datetime(value: str) -> datetime | None:
    """Parse a date or datetime string using the formats providers actually use."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.to_pydatetime()


# Kenyan MSISDNs: 2547xx/2541xx + 8 digits, or 07xx/01xx + 8 digits.
PHONE = re.compile(r"\b(?:254[17]\d{8}|0[17]\d{8})\b")


def masked_account(text: str) -> str | None:
    """Find the account phone number in page text, masked to its last 4 digits."""
    found = PHONE.search(text)
    if not found:
        return None
    number = found.group(0)
    return "*" * (len(number) - 4) + number[-4:]


def clean(text: str | None) -> str:
    """Collapse whitespace and strip. Returns '' for None."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


# --- column mapping --------------------------------------------------------
#
# Providers name their columns differently and order them differently. Mapping
# by header text rather than position means one code path handles all of them.

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "completion_time": (
        "completion time",
        "transaction date",
        "date and time",
        "date & time",
        "value date",
        "timestamp",
        "date",
    ),
    "details": (
        "transaction details",
        "transaction type",
        "description",
        "narration",
        "particulars",
        "details",
    ),
    "paid_in": ("paid in", "money in", "credit", "amount in", "received"),
    "withdrawn": ("withdrawn", "money out", "debit", "amount out", "paid out"),
    "amount": ("amount",),
    "balance": ("running balance", "closing balance", "balance"),
    "receipt_no": (
        "receipt no",
        "transaction id",
        "reference no",
        "reference",
        "txn id",
        "receipt",
    ),
    "status": ("transaction status", "status"),
}


def lines_with_positions(page) -> list[list[dict]]:
    """Group a page's words into lines, keeping each word's x position."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    lines: dict[int, list[dict]] = {}
    for word in words:
        key = round(word["top"] / 3)  # tolerate sub-pixel baseline drift
        lines.setdefault(key, []).append(word)
    return [sorted(group, key=lambda w: w["x0"]) for _, group in sorted(lines.items())]


def column_anchors(lines: list[list[dict]]) -> dict[str, tuple[float, float]] | None:
    """Find the header row and return each logical column's x span.

    Needed because a lone `4,500.00` on a line is ambiguous — money in or money
    out? Only its horizontal position says which.
    """
    for line in lines:
        text = " ".join(w["text"] for w in line).lower()
        matched: dict[str, tuple[float, float]] = {}

        for logical, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias not in text:
                    continue
                tokens = alias.split()
                for index, word in enumerate(line):
                    window = " ".join(
                        w["text"].lower() for w in line[index : index + len(tokens)]
                    )
                    if window == alias:
                        span = line[index : index + len(tokens)]
                        matched[logical] = (span[0]["x0"], span[-1]["x1"])
                        break
                if logical in matched:
                    break

        has_money = {"paid_in", "withdrawn", "amount"} & matched.keys()
        if "completion_time" in matched and "balance" in matched and has_money:
            return matched
    return None


def assign_column(
    word: dict, anchors: dict[str, tuple[float, float]]
) -> str | None:
    """Assign a word to the column whose header it sits under."""
    centre = (word["x0"] + word["x1"]) / 2
    best, best_distance = None, float("inf")
    for logical, (x0, x1) in anchors.items():
        if x0 - 6 <= centre <= x1 + 60:
            distance = abs(centre - x0)
            if distance < best_distance:
                best, best_distance = logical, distance
    return best


INFLOW_WORDS = re.compile(
    r"\b(received|deposit|credit|cash in|top ?up|refund|reversal|salary|bonus"
    r"|(payment|transfer|funds|money) from)\b",
    re.I,
)

# Providers spell settlement states differently; analysis only understands
# COMPLETED / FAILED / PENDING / REVERSED.
STATUS_SYNONYMS = {
    "SUCCESS": "COMPLETED",
    "SUCCESSFUL": "COMPLETED",
    "TS": "COMPLETED",
    "COMPLETED TRANSACTION": "COMPLETED",
    "PROCESSED": "COMPLETED",
    "PAID": "COMPLETED",
    "TF": "FAILED",
    "FAILURE": "FAILED",
    "DECLINED": "FAILED",
    "CANCELLED": "FAILED",
    "REVERSED TRANSACTION": "REVERSED",
    "IN PROGRESS": "PENDING",
}


def split_amount(amount: float, details: str) -> tuple[float, float]:
    """Turn a single signed or unsigned amount into (paid_in, withdrawn).

    Some statements print one Amount column. A negative value is unambiguous;
    an unsigned one has to be read from the description.
    """
    if amount < 0:
        return 0.0, abs(amount)
    if amount > 0 and INFLOW_WORDS.search(details or ""):
        return amount, 0.0
    return (0.0, amount) if amount else (0.0, 0.0)


def normalise(
    rows: list[dict], provider: str, warnings: list[str] | None = None
) -> pd.DataFrame:
    """Turn raw parser output into the normalised frame.

    When a `warnings` list is passed, rows that had to be discarded (no
    parseable date, or an amount that could not be read) are counted into it
    rather than vanishing silently.
    """
    if not rows:
        return pd.DataFrame(
            columns=COLUMNS + ["amount", "direction", "provider"]
        )

    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None

    frame["completion_time"] = pd.to_datetime(frame["completion_time"], errors="coerce")
    for column in ("paid_in", "withdrawn"):
        frame[column] = frame[column].map(to_number)

    # A balance cell the statement never printed is unknown, not zero —
    # otherwise the dashboard would report a closing balance of KES 0.
    def balance_or_na(value):
        if value is None or not clean(str(value)) or clean(str(value)) in {"-", "--"}:
            return None
        return to_number(value)

    frame["balance"] = pd.to_numeric(
        frame["balance"].map(balance_or_na), errors="coerce"
    )

    frame["withdrawn"] = frame["withdrawn"].abs()
    frame["details"] = frame["details"].map(clean)
    frame["status"] = (
        frame["status"]
        .fillna("COMPLETED")
        .astype(str)
        .map(clean)
        .str.upper()
        .replace(STATUS_SYNONYMS)
    )
    frame["receipt_no"] = frame["receipt_no"].fillna("").astype(str).map(clean)
    frame["provider"] = provider

    frame["amount"] = frame["paid_in"] - frame["withdrawn"]
    frame["direction"] = frame["amount"].map(lambda v: "in" if v >= 0 else "out")

    before = len(frame)
    frame = frame.dropna(subset=["completion_time"])
    frame = frame[(frame["paid_in"] != 0) | (frame["withdrawn"] != 0)]
    dropped = before - len(frame)
    if dropped and warnings is not None:
        warnings.append(
            f"{dropped} row(s) were discarded because the date or amount "
            "could not be read. Totals may be missing those transactions."
        )

    # Dedupe on the full row identity, not receipt number alone: on real
    # M-Pesa statements the transaction charge is a separate row that shares
    # its parent's receipt number, and must survive.
    with_receipt = frame[frame["receipt_no"] != ""].drop_duplicates(
        subset=["receipt_no", "details", "paid_in", "withdrawn"]
    )
    without_receipt = frame[frame["receipt_no"] == ""]
    frame = pd.concat([with_receipt, without_receipt]).sort_index()

    # Sort chronologically, but keep the statement's printed order within
    # equal timestamps (a charge and its parent usually share one second) so
    # the running balance chain stays coherent.
    times = frame["completion_time"]
    newest_first = len(frame) > 1 and times.iloc[0] > times.iloc[-1]
    frame = frame.reset_index(drop=True)
    frame["_order"] = -frame.index if newest_first else frame.index
    return (
        frame.sort_values(["completion_time", "_order"], kind="stable")
        .reset_index(drop=True)[COLUMNS + ["amount", "direction", "provider"]]
    )
