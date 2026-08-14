"""Work out which mobile money provider a statement came from, then parse it.

Accepts PDF paths, in-memory PDF file objects (the dashboard passes uploads
straight through without touching disk), and CSVs previously exported from
this tool or a provider portal.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import airtel, mpesa, tkash
from .base import (
    Statement,
    StatementError,
    map_headers,
    normalise,
    open_pdf,
    source_name,
)

PROVIDERS = {
    "mpesa": mpesa,
    "airtel": airtel,
    "tkash": tkash,
}


def _first_page_text(path, password: str | None) -> str:
    with open_pdf(path, password) as pdf:
        if not pdf.pages:
            raise StatementError("This PDF has no pages.")
        return pdf.pages[0].extract_text() or ""


def identify(path, password: str | None = None) -> str:
    """Return the provider key ('mpesa', 'airtel', 'tkash') for a statement.

    M-Pesa is checked first because its signature is structural and therefore
    unambiguous; Airtel and T-Kash detection rely on branding, which is weaker.
    """
    text = _first_page_text(path, password)
    if mpesa.detect(text):
        return "mpesa"
    if airtel.detect(text):
        return "airtel"
    if tkash.detect(text):
        return "tkash"
    raise StatementError(
        "Could not tell which provider this statement is from. Pass "
        f"--provider with one of: {', '.join(PROVIDERS)}."
    )


def _is_csv(path) -> bool:
    return source_name(path).lower().endswith(".csv")


def parse_csv(path) -> Statement:
    """Re-analyse a CSV export — this tool's own, or a provider portal's.

    Columns are matched by name through HEADER_ALIASES, so both
    'Completion Time' and 'completion_time' work.
    """
    try:
        frame = pd.read_csv(path, dtype=str)
    except Exception as exc:
        raise StatementError(f"Could not read this CSV: {exc}") from exc
    if frame.empty:
        raise StatementError("This CSV has no rows.")

    mapping = map_headers(list(frame.columns))
    if "completion_time" not in mapping or not (
        {"paid_in", "withdrawn", "amount"} & mapping.keys()
    ):
        raise StatementError(
            "Could not find date and amount columns in this CSV. Expected "
            "headers like 'Completion Time', 'Paid In', 'Withdrawn' or 'Amount'."
        )

    rows: list[dict] = [
        {logical: record.iloc[index] for logical, index in mapping.items()}
        for _, record in frame.iterrows()
    ]

    # Keep a per-row provider when the export carried one.
    provider_column = next(
        (c for c in frame.columns if c.strip().lower() == "provider"), None
    )
    if provider_column:
        for row, value in zip(rows, frame[provider_column]):
            row["provider"] = value

    warnings: list[str] = []
    transactions = normalise(rows, "CSV import", warnings)
    if transactions.empty:
        raise StatementError("No transactions with a readable date and amount in this CSV.")

    providers = transactions["provider"].dropna().unique()
    return Statement(
        transactions=transactions,
        source=Path(source_name(path)).name,
        provider=" + ".join(providers) if len(providers) else "CSV import",
        pages=0,
        warnings=warnings,
    )


def parse_statement(
    path,
    password: str | None = None,
    provider: str | None = None,
) -> Statement:
    """Parse a statement, detecting the provider automatically.

    Args:
        path: Path to a PDF or CSV, or an in-memory file object.
        password: Statement password, if it is protected.
        provider: Force a parser (a PROVIDERS key) instead of detecting.
    """
    filelike = hasattr(path, "read")
    if not filelike:
        path = Path(path)
        if not path.exists():
            raise StatementError(f"No such file: {path}")

    if _is_csv(path):
        return parse_csv(path)

    if provider and provider not in PROVIDERS:
        raise StatementError(
            f"Unknown provider '{provider}'. Use one of: {', '.join(PROVIDERS)}."
        )

    kind = provider or identify(path, password)
    statement = PROVIDERS[kind].parse(path, password=password)
    statement.source = Path(source_name(path)).name
    return statement


def parse_many(paths: list, password: str | None = None) -> Statement:
    """Parse several statements and merge them into one timeline.

    The case this exists for: someone using more than one wallet who wants a
    single view of their spending rather than partial ones.
    """
    if not paths:
        raise StatementError("No statements supplied.")

    frames: list[pd.DataFrame] = []
    providers: list[str] = []
    warnings: list[str] = []
    pages = 0

    for path in paths:
        statement = parse_statement(path, password=password)
        frames.append(statement.transactions)
        providers.append(statement.provider)
        warnings.extend(statement.warnings)
        pages += statement.pages

    combined = pd.concat(frames, ignore_index=True).sort_values(
        "completion_time", kind="stable"
    )

    # Statements with overlapping periods (Jan-Mar plus Mar-Jun) repeat the
    # shared transactions; keep one copy of each.
    if len(frames) > 1:
        before = len(combined)
        has_receipt = combined["receipt_no"] != ""
        deduped = combined[has_receipt].drop_duplicates(
            subset=["provider", "receipt_no", "details", "paid_in", "withdrawn"]
        )
        combined = pd.concat([deduped, combined[~has_receipt]]).sort_values(
            "completion_time", kind="stable"
        )
        removed = before - len(combined)
        if removed:
            warnings.append(
                f"{removed} transaction(s) appeared in more than one statement "
                "and were counted once."
            )

    if len(set(providers)) > 1:
        # A running balance across two wallets is meaningless.
        combined["balance"] = pd.NA
        warnings.append(
            "Balance is hidden when combining providers: a running balance "
            "across separate wallets has no meaning."
        )

    return Statement(
        transactions=combined.reset_index(drop=True),
        source=", ".join(Path(source_name(p)).name for p in paths),
        provider=" + ".join(dict.fromkeys(providers)),
        pages=pages,
        warnings=warnings,
    )


__all__ = [
    "parse_statement",
    "parse_many",
    "parse_csv",
    "identify",
    "Statement",
    "StatementError",
    "normalise",
    "PROVIDERS",
]
