# Mobile Money Analyser

M-Pesa and Airtel Money both email you a PDF statement. Both are a wall of rows.
Neither tells you where your money actually goes.

This parses either one, categorises every transaction, and merges both into a
single timeline if you use both wallets.

```
M-Pesa + Airtel Money — mpesa.pdf, airtel.pdf
Transactions    406
Money in        KES 1,471,930.72
Money out       KES   928,627.47
Charges         KES     2,945.50 (0.3% of spend)

By provider
M-Pesa               in KES 748,620.88  out KES 629,533.55  (256 txns)
Airtel Money         in KES 723,309.84  out KES 299,093.92  (150 txns)

Spending by category
Education                KES 299,713.18   32.3%  (22 txns)
Cash Withdrawal          KES 167,397.00   18.0%  (25 txns)
Cross-network Transfer    KES 42,386.66    4.6%  ( 9 txns)
```

## Supported

| Provider | Detection | Notes |
|---|---|---|
| M-Pesa | Automatic | Password-protected statements supported |
| Airtel Money | Automatic | Both split-column and single-Amount layouts |

## Install

```bash
git clone https://github.com/Ferinmtk/mobile-money-analyzer.git
cd mobile-money-analyzer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Use

```bash
# one statement
python -m mobile_money mpesa.pdf --password 12345678

# both wallets, as one timeline
python -m mobile_money mpesa.pdf airtel.pdf --csv combined.csv

# force a parser if detection gets it wrong
python -m mobile_money statement.pdf --provider airtel
```

Web dashboard:

```bash
streamlit run app.py
```

No statement to hand? Generate fictional ones:

```bash
python scripts/make_sample_statement.py sample_statement.pdf
python scripts/make_sample_airtel_statement.py split sample_airtel_split.pdf
python -m mobile_money sample_statement.pdf sample_airtel_split.pdf
```

## How it works

```
statement.pdf
     |
     v
 parsers/            detect provider from the first page
     |__ mpesa.py    receipt number + ISO timestamp signature
     |__ airtel.py   header mapping, then word positions
     |
     v
 base.normalise()    one schema regardless of provider:
     |               receipt_no, completion_time, details, status,
     |               paid_in, withdrawn, balance, provider
     v
 categories.py       ordered regex rules -> category + counterparty
     |
     v
 analysis.py         overview, by category, by month, by provider,
     |               top counterparties, forecast, insights
     v
 cli.py / app.py
```

Everything downstream of `normalise()` is provider-agnostic. Adding a provider
means writing a parser, and nothing else changes.

### Four problems worth explaining

**Detecting M-Pesa by keyword does not work.** Airtel narrations say things like
`Send Money to Other Network MPESA 254712...`, so searching for the word sends
Airtel statements to the wrong parser. Detection is structural instead: a
10-character receipt followed by an ISO timestamp is a shape only M-Pesa makes.
Airtel is the reverse problem — its brand *is* the signal, so it is only trusted
when it appears in the statement header, not anywhere in the body.

**Airtel prints two different layouts.** Some exports have separate Paid In and
Withdrawn columns; others have one signed Amount column. A negative amount is
unambiguous, but an unsigned one is not, so direction is read from the
description (`Money Received`, `Cash In`, `Deposit` mean inflow). A test asserts
both layouts produce identical totals from identical data — that is the thing
that would break first if the inference regressed.

**Not every statement has a ruled table.** Without ruling lines `pdfplumber`
returns loose text, and a lone `4,500.00` on a line is ambiguous. The fallback
reads the x-coordinate of every word and assigns it to the column whose header
it sits under. Position is the only thing that disambiguates.

**Rule order matters.** `Customer Transfer Charge` contains both "transfer" and
"charge". Charges are checked first, which keeps a KES 23 fee out of the
money-sent total and off every percentage on the page.

## Privacy

Statements are financial records, so the design assumes they should never leave
the machine they are read on.

- Everything runs locally. No network calls, no external API.
- The dashboard parses uploads in memory and deletes the temporary file.
- Phone numbers are masked to the last four digits.
- `.gitignore` blocks `*.pdf` and `*.csv` so a real statement cannot be
  committed by accident.
- Tests and demos run on generated data, never a real statement.

## Adding a layout

Airtel exports vary more than Safaricom's. If a statement fails to parse, the
usual fix is a column name the mapper does not know yet. Add it to
`HEADER_ALIASES` in `parsers/base.py`:

```python
"withdrawn": ("withdrawn", "money out", "debit", "amount out", "your column name"),
```

Only a genuinely unusual layout needs new parsing code.

## Tests

```bash
pytest
```

65 tests covering detection, both Airtel layouts, merging, direction inference,
money-string edge cases (`(500.00)`, `250.00 DR`, `KES 3,400.50`), date formats,
category rule ordering and the analysis layer. All run against generated
statements.

## Limitations

- The M-Pesa parser is tuned to Safaricom's current layout and is stable. The
  Airtel parser is validated against generated statements only — run it against
  a real one and expect to add a header alias or two.
- Categorisation is rule-based, not learned. Unfamiliar merchants land in
  `Other`; adding one is a single line.
- The forecast is a linear trend, not a seasonal model. January school fees will
  pull the projection up.
- Merged views hide the running balance, because a balance across two separate
  wallets has no meaning.

## Licence

MIT
