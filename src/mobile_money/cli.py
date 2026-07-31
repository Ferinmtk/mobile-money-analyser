"""Command line interface.

    python -m mobile_money statement.pdf --password 12345678
    python -m mobile_money mpesa.pdf airtel.pdf --csv combined.csv
"""

from __future__ import annotations

import argparse
import sys

from .analysis import (
    by_provider,
    by_category,
    forecast_next_month,
    insights,
    monthly,
    overview,
    prepare,
    top_counterparties,
)
from .parsers import StatementError, parse_many, parse_statement


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"KES {value:,.2f}"


def _rule(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mobile-money",
        description=(
            "Turn M-Pesa and Airtel Money PDF statements into a spending "
            "summary. Pass several files to see them as one timeline."
        ),
    )
    parser.add_argument("statements", nargs="+", help="One or more statement PDFs")
    parser.add_argument(
        "-p",
        "--password",
        help="Statement password (usually the ID number on the line)",
    )
    parser.add_argument(
        "--provider",
        choices=["mpesa", "airtel"],
        help="Force a parser instead of detecting the provider",
    )
    parser.add_argument("--csv", help="Also write the parsed transactions to this CSV")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many counterparties to list (default: 10)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if len(args.statements) == 1:
            statement = parse_statement(
                args.statements[0], password=args.password, provider=args.provider
            )
        else:
            statement = parse_many(args.statements, password=args.password)
    except StatementError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    frame = prepare(statement.transactions)
    stats = overview(frame)

    _rule(f"{statement.provider} — {statement.source}")
    if statement.account:
        print(f"Account         {statement.account}")
    print(f"Period          {stats['first']} to {stats['last']}")
    print(f"Transactions    {stats['transactions']}")
    print(f"Money in        {_money(stats['money_in'])}")
    print(f"Money out       {_money(stats['money_out'])}")
    print(f"Net             {_money(stats['net'])}")
    print(f"Charges         {_money(stats['charges'])} "
          f"({stats['charges_pct_of_spend']:.1f}% of spend)")
    print(f"Closing balance {_money(stats['closing_balance'])}")
    print(f"Average daily   {_money(stats['avg_daily_spend'])}")

    for warning in statement.warnings:
        print(f"\nNote: {warning}")

    if frame["provider"].nunique() > 1:
        _rule("By provider")
        for _, row in by_provider(frame).iterrows():
            print(
                f"{row['provider']:<20} in {_money(row['money_in']):>16}  "
                f"out {_money(row['money_out']):>16}  ({int(row['transactions'])} txns)"
            )

    _rule("Spending by category")
    categories = by_category(frame)
    for _, row in categories.iterrows():
        if row["spent"] <= 0:
            continue
        print(
            f"{row['category']:<22} {_money(row['spent']):>16}  "
            f"{row['share_of_spend']:>5.1f}%  ({int(row['transactions'])} txns)"
        )

    _rule("By month")
    for _, row in monthly(frame).iterrows():
        print(
            f"{row['month']}  in {_money(row['money_in']):>16}  "
            f"out {_money(row['money_out']):>16}  net {_money(row['net']):>16}"
        )

    _rule(f"Top {args.top} counterparties")
    for _, row in top_counterparties(frame, args.top).iterrows():
        print(f"{row['counterparty']:<34} {_money(row['spent']):>16}  "
              f"({int(row['transactions'])} txns)")

    _rule("Insights")
    for note in insights(frame):
        print(f"- {note}")

    if args.csv:
        frame.to_csv(args.csv, index=False)
        print(f"\nWrote {len(frame)} transactions to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
