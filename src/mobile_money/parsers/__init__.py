"""Work out which mobile money provider a PDF came from, then parse it."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pdfplumber

from . import airtel, mpesa
from .base import Statement, StatementError, normalise

PROVIDERS = {
    "mpesa": mpesa,
    "airtel": airtel,
}


def _first_page_text(path: Path, password: str | None) -> str:
    try:
        with pdfplumber.open(path, password=password) as pdf:
            if not pdf.pages:
                raise StatementError("This PDF has no pages.")
            return pdf.pages[0].extract_text() or ""
    except StatementError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise StatementError(
                "This statement is password protected. Pass the password — for "
                "M-Pesa it is usually the ID number the line is registered to."
            ) from exc
        raise StatementError(f"Could not open {path.name}: {exc}") from exc


def identify(path: str | Path, password: str | None = None) -> str:
    """Return 'mpesa' or 'airtel' for a statement PDF.

    M-Pesa is checked first because its signature is structural and therefore
    unambiguous; Airtel detection relies on branding, which is weaker.
    """
    text = _first_page_text(Path(path), password)
    if mpesa.detect(text):
        return "mpesa"
    if airtel.detect(text):
        return "airtel"
    raise StatementError(
        "Could not tell which provider this statement is from. Pass "
        "--provider mpesa or --provider airtel to force one."
    )


def parse_statement(
    path: str | Path,
    password: str | None = None,
    provider: str | None = None,
) -> Statement:
    """Parse a statement, detecting the provider automatically.

    Args:
        path: Path to the PDF.
        password: Statement password, if it is protected.
        provider: Force a parser ('mpesa' or 'airtel') instead of detecting.
    """
    path = Path(path)
    if not path.exists():
        raise StatementError(f"No such file: {path}")

    if provider and provider not in PROVIDERS:
        raise StatementError(
            f"Unknown provider '{provider}'. Use one of: {', '.join(PROVIDERS)}."
        )

    kind = provider or identify(path, password)
    statement = PROVIDERS[kind].parse(path, password=password)
    statement.source = path.name
    return statement


def parse_many(paths: list[str | Path], password: str | None = None) -> Statement:
    """Parse several statements and merge them into one timeline.

    The case this exists for: someone using both M-Pesa and Airtel Money who
    wants a single view of their spending rather than two partial ones.
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

    combined = pd.concat(frames, ignore_index=True).sort_values("completion_time")

    if len(set(providers)) > 1:
        # A running balance across two wallets is meaningless.
        combined["balance"] = pd.NA
        warnings.append(
            "Balance is hidden when combining providers: a running balance "
            "across separate wallets has no meaning."
        )

    return Statement(
        transactions=combined.reset_index(drop=True),
        source=", ".join(Path(p).name for p in paths),
        provider=" + ".join(dict.fromkeys(providers)),
        pages=pages,
        warnings=warnings,
    )


__all__ = [
    "parse_statement",
    "parse_many",
    "identify",
    "Statement",
    "StatementError",
    "normalise",
    "PROVIDERS",
]
