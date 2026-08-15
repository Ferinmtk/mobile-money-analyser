"""Summaries and simple forecasting over one or more parsed statements.

Every function reads only the normalised frame, so it does not care whether
the rows came from M-Pesa, Airtel Money, or both merged together.
"""

from __future__ import annotations

import re

import pandas as pd

from .categories import Rules, categorise, counterparty


def prepare(frame: pd.DataFrame, extra_rules: Rules | None = None) -> pd.DataFrame:
    """Add category, counterparty and period columns used by every summary.

    `extra_rules` are user categorisation rules, checked before the builtins.
    """
    result = categorise(frame, extra_rules=extra_rules)
    result["counterparty"] = result["details"].fillna("").map(counterparty)
    result["month"] = result["completion_time"].dt.to_period("M").astype(str)
    result["day"] = result["completion_time"].dt.date
    result["weekday"] = result["completion_time"].dt.day_name()
    result["hour"] = result["completion_time"].dt.hour
    if "provider" not in result.columns:
        result["provider"] = "Unknown"
    return mark_self_transfers(result)


def mark_self_transfers(
    frame: pd.DataFrame, window_minutes: int = 10
) -> pd.DataFrame:
    """Flag transfers between the user's own wallets in a multi-wallet frame.

    Moving KES 5,000 from M-Pesa to your own Airtel Money is not income and
    not spending, but a naive sum counts it as both. A cross-network outgoing
    row is paired with an incoming row of the same amount on a different
    provider within a few minutes; both get `self_transfer = True` and every
    summary excludes them (see `settled`).
    """
    frame["self_transfer"] = False
    if frame["provider"].nunique() < 2:
        return frame

    window = pd.Timedelta(minutes=window_minutes)
    outgoing = frame[
        (frame["category"] == "Cross-network Transfer") & (frame["withdrawn"] > 0)
    ]
    incoming = frame[frame["paid_in"] > 0]

    claimed: set = set()
    for index, row in outgoing.iterrows():
        matches = incoming[
            (incoming["provider"] != row["provider"])
            & (~incoming.index.isin(claimed))
            & ((incoming["paid_in"] - row["withdrawn"]).abs() < 0.01)
            & ((incoming["completion_time"] - row["completion_time"]).abs() <= window)
        ]
        if matches.empty:
            continue
        partner = matches.index[0]
        claimed.add(partner)
        frame.loc[[index, partner], "self_transfer"] = True

    return frame


def settled(frame: pd.DataFrame) -> pd.DataFrame:
    """Only the rows where money actually moved in or out of the user's life.

    Excludes failed/pending/reversed rows and transfers between the user's
    own wallets. Every summary sums over this same population so the category
    table, the monthly trend and the overview all agree with each other.
    """
    rows = frame[frame["status"] == "COMPLETED"]
    if "self_transfer" in rows.columns:
        rows = rows[~rows["self_transfer"]]
    return rows


def reconcile(frame: pd.DataFrame) -> dict[str, int] | None:
    """Check the running balance chain against the amounts.

    For each consecutive pair of settled rows, balance should move by exactly
    the transaction amount. Mismatches mean rows were missed or misread —
    this catches most parsing bugs. Returns None when the check does not
    apply (multiple providers, or no balance column worth trusting).
    """
    if frame.empty or "balance" not in frame.columns:
        return None
    if frame["provider"].nunique() > 1:
        return None
    rows = settled(frame)
    balance = rows["balance"]
    if balance.isna().all() or (balance == 0).all():
        return None
    deltas = balance.diff()
    mismatched = int(((deltas - rows["amount"]).abs() > 0.01).sum())
    checked = int(deltas.notna().sum())
    return {"checked": checked, "mismatched": mismatched}


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
        settled(frame).groupby("provider")
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
    completed = settled(frame)
    money_in = float(completed["paid_in"].sum())
    money_out = float(completed["withdrawn"].sum())
    charges = float(
        completed.loc[completed["category"] == "Charges & Fees", "withdrawn"].sum()
    )
    days = max((frame["completion_time"].max() - frame["completion_time"].min()).days, 1)

    return {
        "transactions": int(len(frame)),
        "not_settled": int(len(frame) - len(completed)),
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
        settled(frame).groupby("category")
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
        settled(frame).groupby("month")
        .agg(
            transactions=("receipt_no", "count"),
            money_in=("paid_in", "sum"),
            money_out=("withdrawn", "sum"),
        )
        .reset_index()
    )
    grouped["net"] = grouped["money_in"] - grouped["money_out"]
    return grouped.sort_values("month").reset_index(drop=True)


def top_counterparties(
    frame: pd.DataFrame, limit: int = 10, direction: str = "out"
) -> pd.DataFrame:
    """Who you send the most money to — or, with direction='in', who pays you."""
    rows = settled(frame)
    column = "withdrawn" if direction == "out" else "paid_in"
    label = "spent" if direction == "out" else "received"
    grouped = (
        rows[rows[column] > 0]
        .groupby("counterparty")
        .agg(transactions=("receipt_no", "count"), **{label: (column, "sum")})
        .reset_index()
        .sort_values(label, ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )
    return grouped


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def by_weekday(frame: pd.DataFrame) -> pd.DataFrame:
    """Spend per day of the week, Monday first."""
    grouped = (
        settled(frame).groupby("weekday")
        .agg(transactions=("receipt_no", "count"), spent=("withdrawn", "sum"))
        .reindex(WEEKDAYS, fill_value=0)
        .reset_index()
    )
    return grouped


def by_hour(frame: pd.DataFrame) -> pd.DataFrame:
    """Spend per hour of the day, 0-23."""
    grouped = (
        settled(frame).groupby("hour")
        .agg(transactions=("receipt_no", "count"), spent=("withdrawn", "sum"))
        .reindex(range(24), fill_value=0)
        .reset_index()
    )
    return grouped


RECURRING_COLUMNS = [
    "counterparty",
    "months",
    "typical_amount",
    "monthly_commitment",
    "total",
    "transactions",
]


def recurring(
    frame: pd.DataFrame, min_months: int = 3, max_variation: float = 0.35
) -> pd.DataFrame:
    """Regular outgoing payments: rent, school fees, subscriptions, chama.

    A counterparty qualifies when it is paid in at least `min_months`
    distinct months and the amounts are steady (coefficient of variation at
    most `max_variation`). `monthly_commitment` is the median month's total —
    the number to reach for when asking "what am I committed to every month".
    """
    rows = settled(frame)
    out = rows[(rows["withdrawn"] > 0) & (rows["counterparty"] != "Unknown")]

    found: list[dict] = []
    for name, group in out.groupby("counterparty"):
        months = int(group["month"].nunique())
        if months < min_months:
            continue
        mean = float(group["withdrawn"].mean())
        deviation = float(group["withdrawn"].std(ddof=0))
        if mean <= 0 or deviation / mean > max_variation:
            continue
        per_month = group.groupby("month")["withdrawn"].sum()
        found.append(
            {
                "counterparty": name,
                "months": months,
                "typical_amount": float(group["withdrawn"].median()),
                "monthly_commitment": float(per_month.median()),
                "total": float(group["withdrawn"].sum()),
                "transactions": int(len(group)),
            }
        )

    result = pd.DataFrame(found, columns=RECURRING_COLUMNS)
    if result.empty:
        return result
    return result.sort_values(
        "monthly_commitment", ascending=False
    ).reset_index(drop=True)


# Product name -> pattern over the details string. Checked in order; first
# match wins, so the named products come before the generic loan words.
LOAN_PRODUCTS: list[tuple[str, re.Pattern[str]]] = [
    ("Fuliza", re.compile(r"fuliza", re.I)),
    ("M-Shwari", re.compile(r"m-?shwari", re.I)),
    ("KCB M-Pesa", re.compile(r"kcb\s*m-?pesa", re.I)),
    ("Other loans & savings", re.compile(r"\b(?:loan|overdraft|lock savings|kopa|sacco)\b", re.I)),
]

LOAN_FEE = re.compile(r"\b(?:charges?|fees?|interest|access)\b", re.I)


def loans(frame: pd.DataFrame) -> pd.DataFrame:
    """Borrowing and savings activity per product.

    For a credit product (Fuliza), `to_you` is what you borrowed and
    `from_you` what you repaid; for a savings product (M-Shwari lock), the
    directions read the other way. `fees` is the cost of using it — for
    Fuliza-heavy statements this number is the story.
    """
    rows = settled(frame)
    results: list[dict] = []
    matched = pd.Series(False, index=rows.index)

    for product, pattern in LOAN_PRODUCTS:
        hits = rows["details"].str.contains(pattern, regex=True) & ~matched
        matched |= hits
        group = rows[hits]
        if group.empty:
            continue
        fee_rows = group["details"].str.contains(LOAN_FEE, regex=True)
        results.append(
            {
                "product": product,
                "to_you": float(group["paid_in"].sum()),
                "from_you": float(group.loc[~fee_rows, "withdrawn"].sum()),
                "fees": float(group.loc[fee_rows, "withdrawn"].sum()),
                "transactions": int(len(group)),
            }
        )

    return pd.DataFrame(
        results, columns=["product", "to_you", "from_you", "fees", "transactions"]
    )


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
        providers = frame["provider"].dropna().unique()
        label = f"{providers[0]} charges" if len(providers) == 1 else "wallet charges"
        notes.append(
            f"You paid KES {stats['charges']:,.0f} in {label}, "
            f"{stats['charges_pct_of_spend']:.1f}% of everything you spent."
        )

    spending = categories[categories["spent"] > 0]
    if not spending.empty:
        top = spending.iloc[0]
        notes.append(
            f"Biggest category: {top['category']} at KES {top['spent']:,.0f} "
            f"across {int(top['transactions'])} transactions."
        )

    regular = recurring(frame)
    if not regular.empty:
        commitment = float(regular["monthly_commitment"].sum())
        notes.append(
            f"You are committed to about KES {commitment:,.0f} every month "
            f"across {len(regular)} regular payments "
            f"(biggest: {regular.iloc[0]['counterparty']})."
        )

    borrowing = loans(frame)
    fuliza = borrowing[borrowing["product"] == "Fuliza"]
    if not fuliza.empty and float(fuliza.iloc[0]["fees"]) > 0:
        row = fuliza.iloc[0]
        notes.append(
            f"Fuliza cost you KES {row['fees']:,.0f} in fees on "
            f"KES {row['to_you']:,.0f} borrowed."
        )

    if "self_transfer" in frame.columns and frame["self_transfer"].any():
        moved = float(
            frame.loc[frame["self_transfer"], "withdrawn"].sum()
        )
        notes.append(
            f"KES {moved:,.0f} moved between your own wallets and is "
            "excluded from income and spending totals."
        )

    week = by_weekday(frame)
    if float(week["spent"].sum()) > 0:
        busiest = week.loc[week["spent"].idxmax()]
        notes.append(
            f"{busiest['weekday']} is your biggest spending day: "
            f"KES {busiest['spent']:,.0f} across the period."
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
