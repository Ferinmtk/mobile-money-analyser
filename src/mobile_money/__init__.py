"""Parse and analyse Kenyan mobile money statements: M-Pesa and Airtel Money."""

from .analysis import (
    by_category,
    by_provider,
    forecast_next_month,
    insights,
    monthly,
    overview,
    prepare,
    top_counterparties,
)
from .categories import categorise, categorise_one, counterparty
from .parsers import Statement, StatementError, identify, parse_many, parse_statement

__version__ = "0.2.0"
__all__ = [
    "parse_statement",
    "parse_many",
    "identify",
    "Statement",
    "StatementError",
    "prepare",
    "overview",
    "by_category",
    "by_provider",
    "monthly",
    "top_counterparties",
    "forecast_next_month",
    "insights",
    "categorise",
    "categorise_one",
    "counterparty",
]
