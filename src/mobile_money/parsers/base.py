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
from typing import Callable

import pandas as pd
import pdfplumber

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
        return 0.0 if pd.isna(value) else float(value)

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


# --- shared parse scaffolding ----------------------------------------------


def open_pdf(path, password: str | None = None):
    """Open a PDF, translating encryption errors into a clear message.

    Used by every parser so a wrong or missing password reads the same
    whether the provider was detected or forced.
    """
    if hasattr(path, "seek"):
        path.seek(0)
    try:
        return pdfplumber.open(path, password=password)
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            hint = (
                "the password given did not work"
                if password
                else "pass the password"
            )
            raise StatementError(
                f"This statement is password protected — {hint}. For M-Pesa it "
                "is usually the ID number the line is registered to."
            ) from exc
        raise StatementError(f"Could not open this statement: {exc}") from exc


def source_name(path) -> str:
    """A display name for a path or an in-memory file object."""
    name = getattr(path, "name", None) or str(path)
    return str(name)


def parse_pdf(
    path,
    password: str | None,
    provider: str,
    page_rows: Callable,
    no_rows_message: str,
) -> Statement:
    """The parse loop every PDF provider shares.

    `page_rows(page, index)` returns one page's raw row dicts; providers keep
    any cross-page state (header mappings, anchors) in a closure.
    """
    rows: list[dict] = []
    first_page_text = ""

    try:
        with open_pdf(path, password) as pdf:
            pages = len(pdf.pages)
            for index, page in enumerate(pdf.pages):
                if index == 0:
                    first_page_text = page.extract_text() or ""
                rows.extend(page_rows(page, index))
    except StatementError:
        raise
    except Exception as exc:
        raise StatementError(
            f"Could not read {provider} statement: {exc}"
        ) from exc

    if not rows:
        raise StatementError(no_rows_message)

    warnings: list[str] = []
    return Statement(
        transactions=normalise(rows, provider, warnings),
        source=source_name(path),
        provider=provider,
        pages=pages,
        account=masked_account(first_page_text),
        warnings=warnings,
    )


def map_headers(cells: list[str]) -> dict[str, int]:
    """Map logical column names to cell indexes via HEADER_ALIASES."""
    lowered = [c.lower().replace("_", " ") for c in cells]
    mapping: dict[str, int] = {}
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
    return mapping


def rows_from_tables(
    page, mapping: dict[str, int] | None
) -> tuple[list[dict], dict[str, int] | None]:
    """Read a ruled table, mapping columns by their header text.

    `mapping` carries the last known header layout: continuation pages often
    print the table without repeating the header row. Provider-agnostic —
    any statement whose headers appear in HEADER_ALIASES parses through here.
    """
    rows: list[dict] = []

    for table in page.extract_tables() or []:
        for raw in table:
            cells = [clean(c) for c in raw]
            if not any(cells):
                continue

            lowered = [c.lower() for c in cells]
            if any("balance" in c for c in lowered) and any(
                "date" in c or "time" in c for c in lowered
            ):
                mapping = map_headers(cells)
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
                    "balance": cell("balance"),
                }
            )
    return rows, mapping


def rows_from_positions(
    page, anchors: dict[str, tuple[float, float]] | None
) -> tuple[list[dict], dict[str, tuple[float, float]] | None]:
    """Fallback for statements with no ruled table, using word x positions.

    `anchors` carries the last page's header positions so continuation pages
    that do not repeat the header still parse.
    """
    lines = lines_with_positions(page)
    anchors = column_anchors(lines) or anchors
    if not anchors:
        return [], None

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
                "balance": joined.get("balance", ""),
            }
        )
    return rows, anchors


POSITIONS_WARNING = (
    "This statement had no ruled table, so columns were read from the "
    "horizontal position of each value. Spot-check a few rows."
)


def header_mapped_parse(path, password: str | None, provider: str) -> Statement:
    """Full parse for any provider readable via header mapping.

    Tries ruled tables first, then the positional fallback, carrying header
    state across pages. Airtel Money and T-Kash both parse through here.
    """
    state: dict = {"mapping": None, "anchors": None, "positions": False}

    def page_rows(page, index: int) -> list[dict]:
        found, state["mapping"] = rows_from_tables(page, state["mapping"])
        if not found:
            found, state["anchors"] = rows_from_positions(page, state["anchors"])
            if found:
                state["positions"] = True
        return found

    statement = parse_pdf(
        path,
        password,
        provider,
        page_rows,
        f"No {provider} transactions found. Layouts vary — see the "
        "'Adding a layout' section of the README.",
    )
    if state["positions"]:
        statement.warnings.append(POSITIONS_WARNING)
    return statement


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
    if "provider" in frame.columns:
        # A CSV re-import can carry its own per-row provider; keep it.
        frame["provider"] = (
            frame["provider"].fillna(provider).replace("", provider)
        )
    else:
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
