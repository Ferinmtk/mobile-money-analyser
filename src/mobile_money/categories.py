"""Categorise mobile money transactions from their details string.

Covers both vocabularies. M-Pesa writes "Merchant Payment Online to 5401 -
NAIVAS SUPERMARKET"; Airtel writes something terser like "Merchant Payment
NAIVAS". The patterns are deliberately loose enough for both.

Rules are ordered: the first pattern that matches wins, so charges and
reversals are checked before the broader transfer patterns.
"""

from __future__ import annotations

import re

import pandas as pd

# (category, compiled pattern). Order matters.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Charges & Fees", re.compile(r"\b(charge|fee|excise duty|levy)\b", re.I)),
    ("Reversal", re.compile(r"\breversal\b", re.I)),
    ("Airtime & Bundles", re.compile(r"\b(airtime|bundle|data|top ?up|recharge|credit purchase)\b", re.I)),
    ("Savings & Loans", re.compile(r"\b(m-?shwari|kcb m-?pesa|fuliza|loan|lock savings|overdraft|od |sacco|kopa)", re.I)),
    ("Cash Withdrawal", re.compile(r"\b(withdraw\w*|atm|cash out)\b", re.I)),
    ("Cash Deposit", re.compile(r"\b(deposit of funds at agent|cash in|agent deposit)\b", re.I)),
    ("Utilities", re.compile(r"\b(kplc|kenya power|token|nairobi water|water|dstv|gotv|zuku|safaricom home|internet)\b", re.I)),
    ("Transport & Fuel", re.compile(r"\b(shell|total|rubis|petrol|fuel|uber|bolt|little cab|sgr|matatu)\b", re.I)),
    ("Groceries & Shopping", re.compile(r"\b(naivas|quickmart|carrefour|chandarana|tuskys|supermarket|jumia|kilimall|shop|store|mart)\b", re.I)),
    ("Food & Drink", re.compile(r"\b(java|artcaffe|kfc|pizza|restaurant|cafe|hotel|eatery|chicken inn|galitos)\b", re.I)),
    ("Health", re.compile(r"\b(hospital|clinic|pharmacy|chemist|medical|nhif|sha )", re.I)),
    ("Education", re.compile(r"\b(school|college|university|tuition|fees account|kca|jkuat)\b", re.I)),
    ("Bank Transfer", re.compile(r"\b(equity|kcb|co-?op|absa|ncba|dtb|stanbic|family bank|bank)\b", re.I)),
    ("Bills & Paybill", re.compile(r"\bpay ?bill\b", re.I)),
    ("Merchant Payment", re.compile(r"\b(merchant payment|buy goods|till)\b", re.I)),
    ("Salary", re.compile(r"\b(salary|payroll|wages|net pay)\b", re.I)),
    ("Cross-network Transfer", re.compile(r"\b(to other network|other network|airtel to m-?pesa|m-?pesa to airtel|cross ?network)\b", re.I)),
    ("Money Received", re.compile(r"\b(funds received|received from|business payment from|money received)\b", re.I)),
    ("Money Sent", re.compile(r"\b(customer transfer|send money|sent to|transfer to)\b", re.I)),
]

UNCATEGORISED = "Other"


def categorise_one(details: str) -> str:
    """Return the category for a single Details string."""
    text = details or ""
    for label, pattern in RULES:
        if pattern.search(text):
            return label
    return UNCATEGORISED


def categorise(frame: pd.DataFrame, column: str = "details") -> pd.DataFrame:
    """Add a `category` column to a transactions DataFrame."""
    result = frame.copy()
    result["category"] = result[column].fillna("").map(categorise_one)
    return result


def counterparty(details: str) -> str:
    """Pull the merchant or person name out of a Details string.

    'Pay Bill to 888880 - KPLC PREPAID Acc. 12345' -> 'KPLC PREPAID'
    'Funds received from 254712345678 - JANE DOE'  -> 'JANE DOE'
    """
    text = details or ""
    if " - " in text:
        text = text.split(" - ", 1)[1]
    text = re.split(r"\bAcc\.?\b", text, flags=re.I)[0]
    text = re.sub(r"\b\d{6,}\b", "", text)
    return re.sub(r"\s+", " ", text).strip(" -") or "Unknown"
