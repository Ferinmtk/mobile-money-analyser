"""Tests run against generated statements, never real financial data."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mobile_money import analysis, categories  # noqa: E402
from mobile_money.parsers import (  # noqa: E402
    StatementError,
    airtel,
    identify,
    mpesa,
    parse_many,
    parse_statement,
)
from mobile_money.parsers.base import (  # noqa: E402
    split_amount,
    to_datetime,
    to_number,
)

SCRIPTS = ROOT / "scripts"


def _run(script: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args], check=True, capture_output=True
    )


@pytest.fixture(scope="session")
def mpesa_pdf(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("data") / "mpesa.pdf"
    _run("make_sample_statement.py", str(out))
    return out


@pytest.fixture(scope="session", params=["split", "single"])
def airtel_pdf(request, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("data") / f"airtel_{request.param}.pdf"
    _run("make_sample_airtel_statement.py", request.param, str(out))
    return out


@pytest.fixture(scope="session")
def airtel_split_pdf(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("data") / "airtel_split.pdf"
    _run("make_sample_airtel_statement.py", "split", str(out))
    return out


@pytest.fixture(scope="session")
def frame(mpesa_pdf: Path) -> pd.DataFrame:
    return analysis.prepare(parse_statement(mpesa_pdf).transactions)


# --- detection -------------------------------------------------------------

def test_identifies_mpesa(mpesa_pdf: Path) -> None:
    assert identify(mpesa_pdf) == "mpesa"


def test_identifies_airtel(airtel_pdf: Path) -> None:
    assert identify(airtel_pdf) == "airtel"


def test_mpesa_detection_is_structural() -> None:
    """A mention of M-Pesa is not enough; the row shape is what identifies it."""
    assert not mpesa.detect("Send Money to Other Network MPESA 254712000111")
    assert mpesa.detect("QGH4XK9TL2 2026-01-14 09:12:33 Airtime Purchase")


def test_airtel_brand_must_be_in_the_header() -> None:
    """An M-Pesa statement mentioning Airtel in a narration must not match."""
    body = "\n".join(["Receipt No Completion Time Details"] * 20)
    assert not airtel.detect(body + "\nSend Money to Airtel Money 254730000987")
    assert airtel.detect("AIRTEL MONEY KENYA\nAIRTEL MONEY STATEMENT")


# --- parsing ---------------------------------------------------------------

def test_parses_mpesa(mpesa_pdf: Path) -> None:
    statement = parse_statement(mpesa_pdf)
    assert statement.provider == "M-Pesa"
    assert len(statement) > 100


def test_parses_airtel(airtel_pdf: Path) -> None:
    statement = parse_statement(airtel_pdf)
    assert statement.provider == "Airtel Money"
    assert len(statement) > 100
    assert statement.transactions["completion_time"].notna().all()


def test_airtel_layouts_agree(tmp_path_factory) -> None:
    """Same data, two layouts: separate columns versus one signed Amount.

    The single-amount layout has to infer direction from the description, so
    if that inference regressed these totals would diverge.
    """
    totals = []
    for layout in ("split", "single"):
        out = tmp_path_factory.mktemp(layout) / f"{layout}.pdf"
        _run("make_sample_airtel_statement.py", layout, str(out))
        transactions = parse_statement(out).transactions
        totals.append(
            (
                round(float(transactions["paid_in"].sum()), 2),
                round(float(transactions["withdrawn"].sum()), 2),
            )
        )
    assert len(set(totals)) == 1, f"layouts disagree: {totals}"


def test_no_row_is_both_directions(airtel_pdf: Path) -> None:
    transactions = parse_statement(airtel_pdf).transactions
    both = (transactions["paid_in"] > 0) & (transactions["withdrawn"] > 0)
    assert not both.any()


def test_account_is_masked(airtel_split_pdf: Path) -> None:
    statement = parse_statement(airtel_split_pdf)
    assert statement.account is not None
    assert statement.account.endswith("0987")
    assert statement.account.startswith("*")


def test_forcing_the_wrong_parser_fails(airtel_split_pdf: Path) -> None:
    with pytest.raises(StatementError):
        parse_statement(airtel_split_pdf, provider="mpesa")


def test_missing_file_raises() -> None:
    with pytest.raises(StatementError):
        parse_statement("does_not_exist.pdf")


def test_unknown_provider_raises(mpesa_pdf: Path) -> None:
    with pytest.raises(StatementError):
        parse_statement(mpesa_pdf, provider="tkash")


# --- merging ---------------------------------------------------------------

def test_merges_providers(mpesa_pdf: Path, airtel_split_pdf: Path) -> None:
    merged = parse_many([mpesa_pdf, airtel_split_pdf])
    assert merged.transactions["provider"].nunique() == 2
    assert merged.transactions["completion_time"].is_monotonic_increasing


def test_merged_balance_is_suppressed(mpesa_pdf: Path, airtel_split_pdf: Path) -> None:
    """A running balance across two wallets is meaningless, so it is dropped."""
    merged = parse_many([mpesa_pdf, airtel_split_pdf])
    assert merged.transactions["balance"].isna().all()
    prepared = analysis.prepare(merged.transactions)
    assert analysis.overview(prepared)["closing_balance"] is None


def test_single_provider_keeps_its_balance(mpesa_pdf: Path) -> None:
    prepared = analysis.prepare(parse_statement(mpesa_pdf).transactions)
    assert analysis.overview(prepared)["closing_balance"] is not None


def test_by_provider_totals(mpesa_pdf: Path, airtel_split_pdf: Path) -> None:
    prepared = analysis.prepare(parse_many([mpesa_pdf, airtel_split_pdf]).transactions)
    split = analysis.by_provider(prepared)
    assert len(split) == 2
    assert split["money_out"].sum() == pytest.approx(
        analysis.overview(prepared)["money_out"]
    )


def test_parse_many_rejects_empty_list() -> None:
    with pytest.raises(StatementError):
        parse_many([])


# --- money, dates and direction --------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,200.00", 1200.00),
        ("0.00", 0.0),
        ("(500.00)", -500.0),
        ("250.00 DR", -250.0),
        ("250.00 CR", 250.0),
        ("KES 3,400.50", 3400.50),
        ("", 0.0),
        ("-", 0.0),
        (None, 0.0),
    ],
)
def test_number_conversion(raw, expected) -> None:
    assert to_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "2026-01-14 09:12:33",
        "14/01/2026",
        "14/01/2026 09:12",
        "14-Jan-2026",
        "14 Jan 2026",
    ],
)
def test_date_formats(raw: str) -> None:
    assert to_datetime(raw) is not None


def test_unparseable_date_returns_none() -> None:
    assert to_datetime("not a date") is None


@pytest.mark.parametrize(
    "amount,details,expected",
    [
        (-500.0, "Airtime Recharge", (0.0, 500.0)),
        (500.0, "Money Received from 254701000333", (500.0, 0.0)),
        (500.0, "Cash In at Agent 55231", (500.0, 0.0)),
        (500.0, "Merchant Payment NAIVAS", (0.0, 500.0)),
        (0.0, "anything", (0.0, 0.0)),
    ],
)
def test_direction_inference(amount, details, expected) -> None:
    """A single unsigned Amount column has to be read from the description."""
    assert split_amount(amount, details) == expected


# --- categorisation --------------------------------------------------------

@pytest.mark.parametrize(
    "details,expected",
    [
        ("Pay Bill Online to 888880 - KPLC PREPAID Acc. 4471029", "Utilities"),
        ("Airtime Purchase", "Airtime & Bundles"),
        ("Customer Transfer Charge", "Charges & Fees"),
        ("Customer Withdrawal At Agent Till 402931 - MJENGO", "Cash Withdrawal"),
        ("M-Shwari Deposit", "Savings & Loans"),
        ("Something entirely unfamiliar", "Other"),
        # Airtel vocabulary
        ("Airtime Recharge", "Airtime & Bundles"),
        ("Data Bundle Purchase", "Airtime & Bundles"),
        ("Cash Out at Agent 55231 KAREN AGENCY", "Cash Withdrawal"),
        ("Transaction Fee", "Charges & Fees"),
        ("Send Money to Other Network 254712000111", "Cross-network Transfer"),
        ("Salary Payment NOXERATECH", "Salary"),
    ],
)
def test_categorisation(details: str, expected: str) -> None:
    assert categories.categorise_one(details) == expected


def test_charges_beat_transfers() -> None:
    """Rule order matters: a transfer charge is a charge, not a transfer."""
    assert categories.categorise_one("Customer Transfer Charge") == "Charges & Fees"


@pytest.mark.parametrize(
    "details,expected",
    [
        ("Pay Bill Online to 888880 - KPLC PREPAID Acc. 4471029", "KPLC PREPAID"),
        ("Funds received from 254701000333 - JAMES OTIENO", "JAMES OTIENO"),
        ("Airtime Purchase", "Airtime Purchase"),
    ],
)
def test_counterparty_extraction(details: str, expected: str) -> None:
    assert categories.counterparty(details) == expected


# --- analysis --------------------------------------------------------------

def test_overview_balances(frame: pd.DataFrame) -> None:
    stats = analysis.overview(frame)
    assert stats["transactions"] == len(frame)
    assert stats["net"] == pytest.approx(stats["money_in"] - stats["money_out"])


def test_category_shares_sum_to_100(frame: pd.DataFrame) -> None:
    table = analysis.by_category(frame)
    spending = table[table["spent"] > 0]
    assert spending["share_of_spend"].sum() == pytest.approx(100.0, abs=0.5)


def test_by_category_handles_no_spending() -> None:
    empty = analysis.prepare(
        pd.DataFrame(
            {
                "receipt_no": ["A"],
                "completion_time": pd.to_datetime(["2026-01-01"]),
                "details": ["Funds received from 254700000000 - X"],
                "status": ["COMPLETED"],
                "paid_in": [100.0],
                "withdrawn": [0.0],
                "balance": [100.0],
                "provider": ["M-Pesa"],
            }
        )
    )
    assert analysis.by_category(empty)["share_of_spend"].sum() == 0.0


def test_monthly_is_ordered(frame: pd.DataFrame) -> None:
    months = analysis.monthly(frame)
    assert list(months["month"]) == sorted(months["month"])


def test_top_counterparties_limit(frame: pd.DataFrame) -> None:
    assert len(analysis.top_counterparties(frame, limit=5)) == 5


def test_forecast_returns_projection(frame: pd.DataFrame) -> None:
    projection = analysis.forecast_next_month(frame)
    assert projection["projected_spend"] >= 0
    assert projection["method"] in {"linear trend", "mean", "none"}


def test_forecast_falls_back_with_few_months(frame: pd.DataFrame) -> None:
    two = frame[frame["month"].isin(sorted(frame["month"].unique())[:2])]
    assert analysis.forecast_next_month(two)["method"] == "mean"


def test_insights_are_readable(frame: pd.DataFrame) -> None:
    notes = analysis.insights(frame)
    assert notes and all(isinstance(note, str) and note for note in notes)


def test_merged_insights_mention_providers(
    mpesa_pdf: Path, airtel_split_pdf: Path
) -> None:
    prepared = analysis.prepare(parse_many([mpesa_pdf, airtel_split_pdf]).transactions)
    assert any("runs through" in note for note in analysis.insights(prepared))
