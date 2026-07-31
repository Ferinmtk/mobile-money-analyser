"""Summaries and simple forecasting over one or more parsed statements.

Every function reads only the normalised frame, so it does not care whether
the rows came from M-Pesa, Airtel Money, or both merged together.
"""

from __future__ import annotations

import pandas as pd

from .categories import categorise, counterparty


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Add category, counterparty and period columns used by every summary."""
    result = categorise(frame)
    result["counterparty"] = result["details"].fillna("").map(counterparty)
    result["month"] = result["completion_time"].dt.to_period("M").astype(str)
    result["day"] = result["completion_time"].dt.date
    result["weekday"] = result["completion_time"].dt.day_name()
    result["hour"] = result["completion_time"].dt.hour
    if "provider" not in result.columns:
        result["provider"] = "Unknown"
    return result


def _closing_balance(frame: pd.DataFrame) -> float | None:
    """Last known balance, or None when rows span several providers."""
    if frame.empty or "balance" not in frame.columns:
        return None
    if frame["provider"].nunique() > 1:
        return None
    balance = frame["balance"].dropna()
    return float(balance.iloc[-1]) if len(balance) else None


def by_provider(frame: pd.DataFrame) -> pd.DataFrame:
    """Money in, money out and transaction count per provider."""
    grouped = (
        frame.groupby("provider")
        .agg(
            transactions=("receipt_no", "count"),
            money_in=("paid_in", "sum"),
            money_out=("withdrawn", "sum"),
        )
        .reset_index()
    )
    grouped["net"] = grouped["money_in"] - grouped["money_out"]
    return grouped.sort_values("money_out", ascending=False).reset_index(drop=True)


def overview(frame: pd.DataFrame) -> dict[str, float | int | str | None]:
    """Headline numbers for the whole statement."""
    completed = frame[frame["status"] == "COMPLETED"]
    money_in = float(completed["paid_in"].sum())
    money_out = float(completed["withdrawn"].sum())
    charges = float(
        completed.loc[completed["category"] == "Charges & Fees", "withdrawn"].sum()
    )
    days = max((frame["completion_time"].max() - frame["completion_time"].min()).days, 1)

    return {
        "transactions": int(len(frame)),
        "money_in": money_in,
        "money_out": money_out,
        "net": money_in - money_out,
        "charges": charges,
        "charges_pct_of_spend": (charges / money_out * 100) if money_out else 0.0,
        "closing_balance": _closing_balance(frame),
        "avg_daily_spend": money_out / days,
        "days_covered": days,
        "first": str(frame["completion_time"].min()),
        "last": str(frame["completion_time"].max()),
    }


def by_category(frame: pd.DataFrame) -> pd.DataFrame:
    """Spend and income per category, biggest spend first."""
    grouped = (
        frame.groupby("category")
        .agg(
            transactions=("receipt_no", "count"),
            spent=("withdrawn", "sum"),
            received=("paid_in", "sum"),
        )
        .reset_index()
    )
    total = float(grouped["spent"].sum())
    if total:
        grouped["share_of_spend"] = (grouped["spent"] / total * 100).round(1)
    else:
        grouped["share_of_spend"] = 0.0
    return grouped.sort_values("spent", ascending=False).reset_index(drop=True)


def monthly(frame: pd.DataFrame) -> pd.DataFrame:
    """Money in, money out and net position per calendar month."""
    grouped = (
        frame.groupby("month")
        .agg(
            transactions=("receipt_no", "count"),
            money_in=("paid_in", "sum"),
            money_out=("withdrawn", "sum"),
        )
        .reset_index()
    )
    grouped["net"] = grouped["money_in"] - grouped["money_out"]
    return grouped.sort_values("month").reset_index(drop=True)


def top_counterparties(frame: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    """Who you send the most money to."""
    outgoing = frame[frame["withdrawn"] > 0]
    grouped = (
        outgoing.groupby("counterparty")
        .agg(transactions=("receipt_no", "count"), spent=("withdrawn", "sum"))
        .reset_index()
        .sort_values("spent", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )
    return grouped


def forecast_next_month(frame: pd.DataFrame) -> dict[str, float | str]:
    """Project next month's spend from the trend so far.

    Uses a simple linear fit when there are three or more complete months,
    otherwise falls back to the mean. Deliberately simple: with a handful of
    months, a heavier model would imply more confidence than the data holds.
    """
    months = monthly(frame)
    if months.empty:
        return {"method": "none", "projected_spend": 0.0, "confidence": "none"}

    spend = months["money_out"].to_numpy(dtype=float)

    if len(spend) < 3:
        return {
            "method": "mean",
            "projected_spend": float(spend.mean()),
            "confidence": "low",
            "months_used": int(len(spend)),
        }

    # Least squares fit on month index, then evaluate one step ahead.
    x = list(range(len(spend)))
    n = len(x)
    mean_x = sum(x) / n
    mean_y = float(spend.mean())
    denominator = sum((xi - mean_x) ** 2 for xi in x)
    slope = (
        sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, spend)) / denominator
        if denominator
        else 0.0
    )
    intercept = mean_y - slope * mean_x
    projection = max(intercept + slope * n, 0.0)

    return {
        "method": "linear trend",
        "projected_spend": float(projection),
        "monthly_change": float(slope),
        "confidence": "medium" if len(spend) >= 6 else "low",
        "months_used": int(len(spend)),
    }


def insights(frame: pd.DataFrame) -> list[str]:
    """Plain-language observations worth putting in front of a user."""
    notes: list[str] = []
    stats = overview(frame)
    categories = by_category(frame)

    if stats["charges"] > 0:
        notes.append(
            f"You paid KES {stats['charges']:,.0f} in M-Pesa charges, "
            f"{stats['charges_pct_of_spend']:.1f}% of everything you spent."
        )

    spending = categories[categories["spent"] > 0]
    if not spending.empty:
        top = spending.iloc[0]
        notes.append(
            f"Biggest category: {top['category']} at KES {top['spent']:,.0f} "
            f"across {int(top['transactions'])} transactions."
        )

    months = monthly(frame)
    if len(months) >= 2:
        last, previous = months.iloc[-1], months.iloc[-2]
        if previous["money_out"] > 0:
            change = (last["money_out"] - previous["money_out"]) / previous["money_out"] * 100
            direction = "up" if change > 0 else "down"
            notes.append(
                f"Spending in {last['month']} was {direction} "
                f"{abs(change):.0f}% on {previous['month']}."
            )

    if stats["net"] < 0:
        notes.append(
            f"Over this period you spent KES {abs(stats['net']):,.0f} more than you received."
        )

    if frame["provider"].nunique() > 1:
        split = by_provider(frame)
        biggest = split.iloc[0]
        notes.append(
            f"Most of your spending runs through {biggest['provider']}: "
            f"KES {biggest['money_out']:,.0f} of KES {stats['money_out']:,.0f}."
        )

    projection = forecast_next_month(frame)
    if projection["method"] != "none":
        notes.append(
            f"Projected spend next month: KES {projection['projected_spend']:,.0f} "
            f"({projection['method']}, {projection['confidence']} confidence)."
        )

    return notes
