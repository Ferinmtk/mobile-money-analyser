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
    parse_csv,
    parse_many,
    parse_statement,
    tkash,
)
from mobile_money.parsers.base import (  # noqa: E402
    normalise,
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
        parse_statement(mpesa_pdf, provider="equitel")


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


# --- normalisation ----------------------------------------------------------

def _row(**overrides) -> dict:
    row = {
        "receipt_no": "QGH4XK9TL2",
        "completion_time": "2026-01-14 09:12:33",
        "details": "Customer Transfer to 254712000111 - PETER KAMAU",
        "status": "COMPLETED",
        "paid_in": "0.00",
        "withdrawn": "1,000.00",
        "balance": "5,000.00",
    }
    row.update(overrides)
    return row


def test_charge_row_sharing_a_receipt_survives_dedupe() -> None:
    """M-Pesa prints the charge as a separate row with its parent's receipt."""
    rows = [
        _row(),
        _row(details="Customer Transfer Charge", withdrawn="110.00", balance="4,890.00"),
    ]
    frame = normalise(rows, "M-Pesa")
    assert len(frame) == 2


def test_true_duplicate_rows_are_deduped() -> None:
    frame = normalise([_row(), _row()], "M-Pesa")
    assert len(frame) == 1


def test_status_synonyms_are_normalised() -> None:
    frame = normalise([_row(status="Success")], "Airtel Money")
    assert frame["status"].iloc[0] == "COMPLETED"


def test_dropped_rows_are_counted() -> None:
    warnings: list[str] = []
    rows = [_row(), _row(receipt_no="X", completion_time="not a date")]
    frame = normalise(rows, "M-Pesa", warnings)
    assert len(frame) == 1
    assert warnings and "1 row(s)" in warnings[0]


def test_missing_balance_is_na_not_zero() -> None:
    frame = normalise([_row(balance="")], "Airtel Money")
    assert pd.isna(frame["balance"].iloc[0])


def test_statement_order_kept_within_equal_timestamps() -> None:
    """Charge and parent share a second; printed order keeps the chain coherent."""
    rows = [
        _row(balance="5,000.00"),
        _row(details="Customer Transfer Charge", withdrawn="110.00", balance="4,890.00"),
    ]
    frame = normalise(rows, "M-Pesa")
    assert frame["balance"].iloc[-1] == 4890.00


def test_mpesa_detection_needs_a_letter_in_the_receipt() -> None:
    """A 10-digit id next to an ISO date is not an M-Pesa receipt."""
    assert not mpesa.detect("1234567890 2026-01-14 09:12 Airtel Money Transfer")


def test_payment_from_is_an_inflow() -> None:
    assert split_amount(500.0, "Business Payment from 600100 - ACME") == (500.0, 0.0)


def test_charges_plural_is_a_charge() -> None:
    assert categories.categorise_one("Withdrawal Charges") == "Charges & Fees"


def test_school_fees_are_education_not_charges() -> None:
    assert categories.categorise_one("Pay Bill to 300500 - SCHOOL FEES") == "Education"


# --- new analysis ----------------------------------------------------------

def test_uploading_the_same_statement_twice_counts_once(mpesa_pdf: Path) -> None:
    once = parse_statement(mpesa_pdf)
    twice = parse_many([mpesa_pdf, mpesa_pdf])
    assert len(twice) == len(once)
    assert any("counted once" in warning for warning in twice.warnings)


def test_reconcile_checks_the_balance_chain(frame: pd.DataFrame) -> None:
    check = analysis.reconcile(frame)
    assert check is not None
    assert check["checked"] > 0
    assert check["mismatched"] <= check["checked"]


def test_reconcile_skips_merged_providers(
    mpesa_pdf: Path, airtel_split_pdf: Path
) -> None:
    merged = analysis.prepare(parse_many([mpesa_pdf, airtel_split_pdf]).transactions)
    assert analysis.reconcile(merged) is None


def test_top_sources(frame: pd.DataFrame) -> None:
    sources = analysis.top_counterparties(frame, limit=5, direction="in")
    assert "received" in sources.columns
    assert (sources["received"] > 0).all()


def test_by_weekday_covers_the_week(frame: pd.DataFrame) -> None:
    week = analysis.by_weekday(frame)
    assert list(week["weekday"]) == analysis.WEEKDAYS


def test_by_hour_covers_the_day(frame: pd.DataFrame) -> None:
    hours = analysis.by_hour(frame)
    assert len(hours) == 24


def test_summaries_agree_on_totals(frame: pd.DataFrame) -> None:
    """Every summary sums the same settled population."""
    stats = analysis.overview(frame)
    assert analysis.by_category(frame)["spent"].sum() == pytest.approx(
        stats["money_out"]
    )
    assert analysis.monthly(frame)["money_out"].sum() == pytest.approx(
        stats["money_out"]
    )


# --- T-Kash, CSV and in-memory input ----------------------------------------

@pytest.fixture(scope="session")
def tkash_pdf(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("data") / "tkash.pdf"
    _run("make_sample_tkash_statement.py", str(out))
    return out


def test_identifies_and_parses_tkash(tkash_pdf: Path) -> None:
    assert identify(tkash_pdf) == "tkash"
    statement = parse_statement(tkash_pdf)
    assert statement.provider == "T-Kash"
    assert len(statement) > 80
    assert analysis.reconcile(analysis.prepare(statement.transactions)) is not None


def test_tkash_detection_needs_the_brand_in_the_header() -> None:
    body = "\n".join(["Receipt No Completion Time Details"] * 20)
    assert not tkash.detect(body + "\nSend Money to T-Kash 254770000111")
    assert tkash.detect("TELKOM KENYA\nT-KASH STATEMENT")


def test_csv_round_trip(mpesa_pdf: Path, tmp_path: Path) -> None:
    """A CSV exported from the app can be re-analysed with identical totals."""
    original = analysis.prepare(parse_statement(mpesa_pdf).transactions)
    out = tmp_path / "transactions.csv"
    original[
        ["completion_time", "provider", "details", "status",
         "paid_in", "withdrawn", "balance"]
    ].to_csv(out, index=False)

    reparsed = parse_csv(out)
    assert len(reparsed) == len(original)
    assert reparsed.transactions["paid_in"].sum() == pytest.approx(
        original["paid_in"].sum()
    )
    assert set(reparsed.transactions["provider"]) == {"M-Pesa"}


def test_csv_goes_through_parse_statement(mpesa_pdf: Path, tmp_path: Path) -> None:
    original = parse_statement(mpesa_pdf).transactions
    out = tmp_path / "export.csv"
    original.to_csv(out, index=False)
    assert len(parse_statement(out)) == len(original)


def test_unreadable_csv_raises(tmp_path: Path) -> None:
    out = tmp_path / "junk.csv"
    out.write_text("a,b\n1,2\n")
    with pytest.raises(StatementError):
        parse_csv(out)


def test_parses_in_memory_file(mpesa_pdf: Path) -> None:
    """The dashboard hands parsers a BytesIO — no temp files on disk."""
    import io

    buffer = io.BytesIO(mpesa_pdf.read_bytes())
    buffer.name = "statement.pdf"
    statement = parse_statement(buffer)
    assert statement.provider == "M-Pesa"
    assert statement.source == "statement.pdf"
    assert len(statement) > 100


# --- user rules -------------------------------------------------------------

def test_user_rules_override_builtins(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.json"
    rules_file.write_text('{"mama mboga": "Groceries & Shopping", "naivas": "Rent"}')
    extra = categories.load_user_rules(rules_file)
    assert categories.categorise_one("MAMA MBOGA KIBERA", extra) == "Groceries & Shopping"
    # User rules win over the builtin Naivas -> Groceries rule.
    assert categories.categorise_one("Merchant Payment NAIVAS", extra) == "Rent"
    # Without extras, builtins still apply.
    assert categories.categorise_one("Merchant Payment NAIVAS") == "Groceries & Shopping"


def test_missing_rules_file_means_no_rules(tmp_path: Path) -> None:
    assert categories.load_user_rules(tmp_path / "absent.json") == []


def test_broken_rules_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "rules.json"
    bad.write_text("not json")
    with pytest.raises(ValueError):
        categories.load_user_rules(bad)


# --- recurring, loans and self-transfers ------------------------------------

def _synthetic(rows: list[tuple[str, str, float, float, str]]) -> pd.DataFrame:
    """(time, details, paid_in, withdrawn, provider) -> prepared frame."""
    frame = pd.DataFrame(
        {
            "receipt_no": [f"R{i:04d}XXAA{i%7}Z" for i in range(len(rows))],
            "completion_time": pd.to_datetime([r[0] for r in rows]),
            "details": [r[1] for r in rows],
            "status": "COMPLETED",
            "paid_in": [r[2] for r in rows],
            "withdrawn": [r[3] for r in rows],
            "balance": pd.NA,
            "provider": [r[4] for r in rows],
        }
    )
    return analysis.prepare(frame)


def test_recurring_finds_steady_monthly_payments() -> None:
    rows = [
        (f"2026-0{month}-05 09:00", "Customer Transfer to 254700111222 - LANDLORD KAMAU",
         0.0, 25000.0, "M-Pesa")
        for month in range(1, 5)
    ] + [
        ("2026-01-20 12:00", "Merchant Payment Online to 5401 - NAIVAS", 0.0, 8000.0, "M-Pesa"),
    ]
    regular = analysis.recurring(_synthetic(rows))
    assert list(regular["counterparty"]) == ["LANDLORD KAMAU"]
    assert regular.iloc[0]["monthly_commitment"] == pytest.approx(25000.0)
    assert regular.iloc[0]["months"] == 4


def test_recurring_ignores_erratic_amounts() -> None:
    rows = [
        (f"2026-0{month}-05 09:00", "Customer Transfer to 254700111222 - LANDLORD KAMAU",
         0.0, amount, "M-Pesa")
        for month, amount in [(1, 2000.0), (2, 30000.0), (3, 500.0), (4, 12000.0)]
    ]
    assert analysis.recurring(_synthetic(rows)).empty


def test_loans_split_fuliza_fees_from_repayments() -> None:
    rows = [
        ("2026-01-05 09:00", "OD Loan Repayment to Fuliza M-Pesa", 0.0, 1000.0, "M-Pesa"),
        ("2026-01-04 09:00", "Fuliza M-Pesa amount borrowed", 950.0, 0.0, "M-Pesa"),
        ("2026-01-05 09:01", "Fuliza M-Pesa access fee", 0.0, 25.0, "M-Pesa"),
        ("2026-01-08 10:00", "M-Shwari Deposit", 0.0, 5000.0, "M-Pesa"),
    ]
    borrowing = analysis.loans(_synthetic(rows))
    fuliza = borrowing[borrowing["product"] == "Fuliza"].iloc[0]
    assert fuliza["to_you"] == pytest.approx(950.0)
    assert fuliza["from_you"] == pytest.approx(1000.0)
    assert fuliza["fees"] == pytest.approx(25.0)
    assert "M-Shwari" in set(borrowing["product"])


def test_self_transfers_are_excluded_from_totals() -> None:
    rows = [
        ("2026-01-05 09:00", "Send Money to Other Network 254730000987",
         0.0, 5000.0, "M-Pesa"),
        ("2026-01-05 09:03", "Money Received from 254712000111 SELF",
         5000.0, 0.0, "Airtel Money"),
        ("2026-01-06 11:00", "Merchant Payment NAIVAS", 0.0, 1200.0, "Airtel Money"),
    ]
    frame = _synthetic(rows)
    assert frame["self_transfer"].sum() == 2
    stats = analysis.overview(frame)
    assert stats["money_out"] == pytest.approx(1200.0)
    assert stats["money_in"] == pytest.approx(0.0)


def test_single_provider_never_marks_self_transfers() -> None:
    rows = [
        ("2026-01-05 09:00", "Send Money to Other Network 254730000987",
         0.0, 5000.0, "M-Pesa"),
        ("2026-01-05 09:03", "Money Received from 254712000111", 5000.0, 0.0, "M-Pesa"),
    ]
    assert not _synthetic(rows)["self_transfer"].any()


# --- CLI --------------------------------------------------------------------

def test_cli_json_output(mpesa_pdf: Path, capsys) -> None:
    import json

    from mobile_money.cli import main

    assert main([str(mpesa_pdf), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["statement"]["provider"] == "M-Pesa"
    assert payload["overview"]["transactions"] > 100
    assert isinstance(payload["by_category"], list)
    assert isinstance(payload["insights"], list)


def test_cli_missing_file_exits_nonzero() -> None:
    from mobile_money.cli import main

    assert main(["does_not_exist.pdf"]) == 1


def test_cli_writes_csv(mpesa_pdf: Path, tmp_path: Path) -> None:
    from mobile_money.cli import main

    out = tmp_path / "out.csv"
    assert main([str(mpesa_pdf), "--csv", str(out)]) == 0
    assert out.exists()
    header = out.read_text().splitlines()[0]
    assert "completion_time" in header and "withdrawn" in header
