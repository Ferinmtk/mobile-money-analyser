"""Streamlit dashboard for the mobile money analyser.

    streamlit run app.py

Opens on a provider chooser, then themes the rest of the session to match.
Uploaded files are parsed entirely in memory and never written to disk.

Note on branding: the colours below are approximations of each provider's
brand palette, used to make the two flows visually distinct. No provider logos
or trademarks are reproduced. See the README before changing this.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mobile_money import analysis  # noqa: E402
from mobile_money.categories import USER_RULES_FILE, load_user_rules  # noqa: E402
from mobile_money.parsers import StatementError, parse_many  # noqa: E402

st.set_page_config(
    page_title="Mobile Money Analyser",
    page_icon="◔",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- theming ---------------------------------------------------------------

THEMES = {
    "mpesa": {
        "label": "M-Pesa",
        "operator": "Safaricom",
        "accent": "#22B14C",        # Safaricom green
        "accent_deep": "#0F7A32",
        "rgb": "34, 177, 76",
        "blurb": "Safaricom M-Pesa statement",
    },
    "airtel": {
        "label": "Airtel Money",
        "operator": "Airtel",
        "accent": "#E4002B",        # Airtel red
        "accent_deep": "#9E0020",
        "rgb": "228, 0, 43",
        "blurb": "Airtel Money statement",
    },
    "both": {
        "label": "Both wallets",
        "operator": "Combined",
        "accent": "#6C74C9",
        "accent_deep": "#3F47A0",
        "rgb": "108, 116, 201",
        "blurb": "M-Pesa and Airtel Money together",
    },
}


ORDER = ("mpesa", "airtel", "both")


def theme() -> dict:
    return THEMES[st.session_state.get("provider", "both")]


def inject_css(config: dict, landing_mode: bool = False) -> None:
    """Theme the page. Alpha tints are used throughout so the same rules read
    correctly against both the light and dark Streamlit themes."""
    accent = config["accent"]
    deep = config["accent_deep"]
    rgb = config["rgb"]

    # On the landing screen each column gets its own provider colour.
    per_column = ""
    if landing_mode:
        for index, key in enumerate(ORDER, start=1):
            card = THEMES[key]
            per_column += f"""
              div[data-testid="stHorizontalBlock"] > div:nth-child({index})
              div.stButton > button {{
                background: {card['accent']};
                border-color: {card['accent']};
                color: #FFFFFF;
              }}
              div[data-testid="stHorizontalBlock"] > div:nth-child({index})
              div.stButton > button:hover {{
                background: {card['accent_deep']};
                border-color: {card['accent_deep']};
                color: #FFFFFF;
              }}
            """

    st.markdown(
        f"""
        <style>
          :root {{
            --accent: {accent};
            --accent-deep: {deep};
            --accent-rgb: {rgb};
          }}

          /* brand band and a tint that fades out, readable on either theme */
          .stApp::before {{
            content: "";
            position: fixed; top: 0; left: 0; right: 0; height: 5px;
            background: linear-gradient(90deg, {deep}, {accent} 55%, {deep});
            z-index: 999;
          }}
          .stApp {{
            background-image: linear-gradient(
              180deg, rgba({rgb}, .13) 0px, rgba({rgb}, 0) 380px);
          }}

          section[data-testid="stSidebar"] {{
            background-image: linear-gradient(
              180deg, rgba({rgb}, .14), rgba({rgb}, 0) 60%);
            border-right: 1px solid rgba({rgb}, .40);
          }}
          section[data-testid="stSidebar"] h2 {{ color: {accent}; }}

          h1, h2, h3 {{ letter-spacing: -0.02em; }}
          .rule {{
            height: 4px; width: 72px; border-radius: 2px;
            background: linear-gradient(90deg, {accent}, rgba({rgb}, 0));
            margin: 6px 0 20px 0;
          }}

          [data-testid="stMetric"] {{
            background: rgba({rgb}, .10);
            border: 1px solid rgba({rgb}, .35);
            border-left: 4px solid {accent};
            border-radius: 12px;
            padding: 14px 16px;
          }}
          [data-testid="stMetricValue"] {{ color: {accent}; font-weight: 700; }}

          div.stButton > button {{
            width: 100%;
            background: {accent};
            border: 1px solid {accent};
            color: #FFFFFF;
            font-weight: 700;
            border-radius: 10px;
            transition: background .15s ease, transform .15s ease;
          }}
          div.stButton > button:hover {{
            background: {deep};
            border-color: {deep};
            color: #FFFFFF;
            transform: translateY(-1px);
          }}
          div.stButton > button p {{ color: #FFFFFF !important; font-weight: 700; }}

          div.stDownloadButton > button {{
            background: transparent;
            border: 1.5px solid {accent};
            color: {accent};
            border-radius: 10px;
            font-weight: 600;
          }}
          div.stDownloadButton > button:hover {{
            background: {accent};
            color: #FFFFFF;
          }}

          .stTabs [aria-selected="true"] {{
            color: {accent} !important;
            border-bottom-color: {accent} !important;
          }}

          /* alerts: keep Streamlit's own text colour, restyle the frame only */
          div[data-testid="stAlert"] {{
            background: rgba({rgb}, .12);
            border: 1px solid rgba({rgb}, .38);
            border-left: 4px solid {accent};
            border-radius: 10px;
          }}

          .provider-card {{
            border: 1.5px solid rgba(var(--card-rgb), .55);
            border-radius: 16px;
            padding: 26px 22px 22px 22px;
            background: linear-gradient(
              158deg, rgba(var(--card-rgb), .20), rgba(var(--card-rgb), .04));
            min-height: 214px;
            transition: transform .16s ease, box-shadow .16s ease;
          }}
          .provider-card:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 30px rgba(0, 0, 0, .18);
          }}
          .provider-card h3 {{ margin: 12px 0 2px 0; font-size: 1.4rem; }}
          .provider-card .brand-logo {{ display: block; margin-bottom: 6px; }}
          .provider-card .operator {{
            font-size: .76rem; letter-spacing: .11em; text-transform: uppercase;
            font-weight: 800;
          }}
          .provider-card p {{ font-size: .92rem; opacity: .85; margin-top: 10px; }}

          .brand-chip {{
            display: inline-block; padding: 5px 14px; border-radius: 999px;
            font-size: .72rem; font-weight: 800; letter-spacing: .1em;
            text-transform: uppercase;
            border: 1.5px solid rgba({rgb}, .55);
            background: rgba({rgb}, .14);
            color: {accent};
          }}
          {per_column}
        </style>
        """,
        unsafe_allow_html=True,
    )


ASSETS = Path(__file__).parent / "assets"

# Optional brand logos. Drop files here and they replace the fallback icons:
#   assets/mpesa.png    assets/airtel.png    assets/both.png
# These are the operators' trademarks. Keep them local — assets/ is gitignored
# so they are not redistributed with the source.
LOGO_NAMES = {
    "mpesa": ("mpesa", "safaricom"),
    "airtel": ("airtel", "airtel-money"),
    "both": ("both", "combined"),
}


@st.cache_data(show_spinner=False)
def load_logo(key: str) -> str | None:
    """Return a data URI for a logo in assets/, or None if absent."""
    for stem in LOGO_NAMES.get(key, ()):
        for suffix in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
            candidate = ASSETS / f"{stem}{suffix}"
            if candidate.exists():
                mime = mimetypes.guess_type(candidate.name)[0] or "image/png"
                encoded = base64.b64encode(candidate.read_bytes()).decode()
                return f"data:{mime};base64,{encoded}"
    return None


def brand_mark(colour: str, variant: str) -> str:
    """A provider logo if one has been supplied, otherwise the fallback icon."""
    logo = load_logo(variant)
    if logo:
        return (
            f'<img src="{logo}" alt="" class="brand-logo" '
            f'style="height:58px;width:auto;border-radius:12px;" />'
        )
    return wallet_icon(colour, variant)


def wallet_icon(colour: str, variant: str) -> str:
    """Original iconography, used when no logo file is present."""
    if variant == "both":
        inner = (
            f'<circle cx="19" cy="24" r="11" fill="none" stroke="{colour}" '
            f'stroke-width="2.5"/>'
            f'<circle cx="29" cy="24" r="11" fill="none" stroke="{colour}" '
            f'stroke-width="2.5" opacity="0.55"/>'
        )
    elif variant == "mpesa":
        inner = (
            f'<rect x="8" y="13" width="32" height="22" rx="5" fill="none" '
            f'stroke="{colour}" stroke-width="2.5"/>'
            f'<circle cx="31" cy="24" r="4" fill="{colour}"/>'
        )
    else:
        inner = (
            f'<path d="M10 32 L24 12 L38 32" fill="none" stroke="{colour}" '
            f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="24" cy="30" r="3.4" fill="{colour}"/>'
        )
    return f'<svg width="48" height="48" viewBox="0 0 48 48">{inner}</svg>'


# --- landing ---------------------------------------------------------------

def landing() -> None:
    inject_css(THEMES["both"], landing_mode=True)

    st.title("Mobile Money Analyser")
    st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
    st.caption(
        "See where your money actually goes. Choose the wallet you want to "
        "analyse — everything runs on this machine and nothing is uploaded "
        "anywhere."
    )
    st.write("")

    columns = st.columns(3, gap="medium")
    for column, key in zip(columns, ORDER):
        config = THEMES[key]
        with column:
            st.markdown(
                f"""
                <div class="provider-card" style="--card-rgb: {config['rgb']};">
                  {brand_mark(config['accent'], key)}
                  <div class="operator" style="color:{config['accent']}">
                    {config['operator']}
                  </div>
                  <h3>{config['label']}</h3>
                  <p>{config['blurb']}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.write("")
            if st.button(f"Analyse {config['label']}", key=f"pick_{key}"):
                st.session_state["provider"] = key
                st.rerun()

    st.write("")
    st.caption(
        "No statement to hand? Generate a fictional one with "
        "`python scripts/make_sample_statement.py` or "
        "`python scripts/make_sample_airtel_statement.py split`."
    )


# --- analysis view ---------------------------------------------------------

def money(value: float | None) -> str:
    return "n/a" if value is None else f"KES {value:,.0f}"


def analysis_view() -> None:
    config = theme()
    key = st.session_state["provider"]
    inject_css(config)

    header, back = st.columns([5, 1])
    with header:
        logo = load_logo(key)
        if logo:
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:14px;">'
                f'<img src="{logo}" style="height:46px;border-radius:9px;" alt="" />'
                f'<h1 style="margin:0;">{config["label"]}</h1></div>',
                unsafe_allow_html=True,
            )
        else:
            st.title(config["label"])
        st.markdown(
            f'<span class="brand-chip">{config["operator"]}</span>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
    with back:
        st.write("")
        if st.button("Change"):
            st.session_state.pop("provider", None)
            st.session_state.pop("uploads", None)
            st.rerun()

    accepts_many = key == "both"
    with st.sidebar:
        st.header("Statement")
        uploads = st.file_uploader(
            "Upload both statements" if accepts_many else f"Upload your {config['label']} statement",
            type=["pdf", "csv"],
            accept_multiple_files=accepts_many,
            help="Parsed entirely in memory — never written to disk. "
            "A CSV exported from this tool works too.",
        )
        password = st.text_input(
            "Password (if protected)",
            type="password",
            help="For M-Pesa this is usually the ID number the line is registered to.",
        )

    if not uploads:
        st.info(
            f"Upload your {config['blurb']} in the sidebar to begin. "
            "Open the sidebar with the arrow at the top left if it is hidden."
        )
        st.stop()

    if not isinstance(uploads, list):
        uploads = [uploads]

    # In-memory file objects go straight to the parsers; nothing hits disk.
    buffers = []
    for upload in uploads:
        buffer = io.BytesIO(upload.getvalue())
        buffer.name = upload.name
        buffers.append(buffer)

    try:
        statement = parse_many(buffers, password=password or None)
    except StatementError as exc:
        st.error(str(exc))
        st.stop()

    # Guard against uploading the wrong provider's statement.
    providers = set(statement.transactions["provider"].unique())
    expected = {"mpesa": "M-Pesa", "airtel": "Airtel Money"}.get(key)
    if expected and providers != {expected}:
        st.warning(
            f"You chose {config['label']}, but this looks like a "
            f"{', '.join(sorted(providers))} statement. Showing it anyway."
        )

    try:
        extra_rules = load_user_rules()
    except ValueError as exc:
        st.warning(str(exc))
        extra_rules = []
    frame = analysis.prepare(statement.transactions, extra_rules=extra_rules)

    # Period filter: the whole dashboard scopes to the chosen dates.
    first_day = frame["completion_time"].min().date()
    last_day = frame["completion_time"].max().date()
    with st.sidebar:
        st.divider()
        chosen_period = st.date_input(
            "Period",
            value=(first_day, last_day),
            min_value=first_day,
            max_value=last_day,
            help="Everything below — totals, charts, insights — covers only these dates.",
        )
    if isinstance(chosen_period, tuple) and len(chosen_period) == 2:
        start, end = chosen_period
        frame = frame[
            (frame["completion_time"].dt.date >= start)
            & (frame["completion_time"].dt.date <= end)
        ]
    if frame.empty:
        st.info("No transactions in the chosen period.")
        st.stop()

    stats = analysis.overview(frame)

    left, middle, right, far_right = st.columns(4)
    left.metric("Money in", money(stats["money_in"]))
    middle.metric("Money out", money(stats["money_out"]))
    right.metric("Net", money(stats["net"]))
    far_right.metric(
        "Charges",
        money(stats["charges"]),
        f"{stats['charges_pct_of_spend']:.1f}% of spend",
        delta_color="inverse",
    )

    left, middle, right, far_right = st.columns(4)
    left.metric("Closing balance", money(stats["closing_balance"]))
    middle.metric("Avg daily spend", money(stats["avg_daily_spend"]))
    right.metric("Days covered", f"{stats['days_covered']}")
    far_right.metric("Transactions", f"{stats['transactions']}")

    caption = f"From {stats['first'][:10]} to {stats['last'][:10]}"
    if stats["not_settled"]:
        caption += (
            f" · {stats['not_settled']} failed/pending/reversed transaction(s) "
            "excluded from totals"
        )
    st.caption(caption)

    for warning in statement.warnings:
        st.warning(warning)

    # Balance reconciliation: each row's balance should move by exactly the
    # transaction amount. Mismatches usually mean rows were missed or misread.
    check = analysis.reconcile(frame)
    if check and check["checked"]:
        if check["mismatched"] > check["checked"] * 0.05:
            st.warning(
                f"{check['mismatched']} of {check['checked']} rows do not line "
                "up with the running balance. Some transactions may be missing "
                "or misread — treat the totals as approximate."
            )
        else:
            good = check["checked"] - check["mismatched"]
            st.caption(f"Balance check: {good} of {check['checked']} rows reconcile.")

    st.subheader("What we noticed")
    for note in analysis.insights(frame):
        st.write(f"- {note}")

    names = ["Categories", "Monthly trend", "People", "When you spend", "Transactions"]
    multi = frame["provider"].nunique() > 1
    has_balance = (
        not multi
        and frame["balance"].notna().any()
        and (frame["balance"].fillna(0) != 0).any()
    )
    regular = analysis.recurring(frame)
    borrowing = analysis.loans(frame)
    if not borrowing.empty:
        names.insert(2, "Loans & savings")
    if not regular.empty:
        names.insert(2, "Regular payments")
    if has_balance:
        names.insert(2, "Balance")
    if multi:
        names.insert(0, "By provider")
    panels = dict(zip(names, st.tabs(names)))

    def show_table(table, filename: str) -> None:
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            data=table.to_csv(index=False).encode("utf-8"),
            file_name=filename,
            mime="text/csv",
            key=f"download_{filename}",
        )

    if multi:
        with panels["By provider"]:
            split = analysis.by_provider(frame)
            st.bar_chart(split.set_index("provider")[["money_in", "money_out"]])
            show_table(split, "by_provider.csv")

    with panels["Categories"]:
        table = analysis.by_category(frame)
        spending = table[table["spent"] > 0].set_index("category")
        st.bar_chart(spending["spent"], height=380, color=config["accent"])
        show_table(table, "by_category.csv")

        # When 'Other' dominates, show what is hiding in it and how to fix it.
        other = table[table["category"] == "Other"]
        if not other.empty and float(other.iloc[0]["share_of_spend"]) > 20:
            with st.expander(
                f"'Other' is {other.iloc[0]['share_of_spend']:.0f}% of your "
                "spend — what's in it?"
            ):
                unknown = frame[
                    (frame["category"] == "Other") & (frame["withdrawn"] > 0)
                ]
                top_unknown = (
                    unknown.groupby("counterparty")["withdrawn"]
                    .agg(["sum", "count"])
                    .rename(columns={"sum": "spent", "count": "transactions"})
                    .sort_values("spent", ascending=False)
                    .head(15)
                    .reset_index()
                )
                st.dataframe(top_unknown, use_container_width=True, hide_index=True)
                st.caption(
                    f"Teach the analyser: add rules to `{USER_RULES_FILE}` as "
                    'JSON, e.g. `{"mama mboga": "Groceries & Shopping", '
                    '"j\\\\. otieno": "Rent"}`. Patterns are regexes matched '
                    "against the details text."
                )

    with panels["Monthly trend"]:
        months = analysis.monthly(frame).set_index("month")
        st.line_chart(months[["money_in", "money_out"]], height=340)
        st.bar_chart(months["net"], height=260, color=config["accent"])
        projection = analysis.forecast_next_month(frame)
        if projection["method"] != "none":
            st.info(
                f"Projected spend next month: {money(projection['projected_spend'])} "
                f"— {projection['method']}, {projection['confidence']} confidence, "
                f"based on {projection.get('months_used', 0)} months."
            )
        show_table(months.reset_index(), "monthly.csv")

    if has_balance:
        with panels["Balance"]:
            balance = (
                frame.dropna(subset=["balance"])
                .set_index("completion_time")["balance"]
            )
            st.line_chart(balance, height=380, color=config["accent"])
            lowest = balance.idxmin()
            st.caption(
                f"Lowest point: {money(float(balance.min()))} "
                f"on {lowest.date()}. "
                f"Highest: {money(float(balance.max()))}."
            )

    if not regular.empty:
        with panels["Regular payments"]:
            commitment = float(regular["monthly_commitment"].sum())
            st.metric("Monthly commitment", money(commitment))
            st.caption(
                "Payments made in 3+ months with steady amounts — rent, "
                "school fees, subscriptions, chama contributions."
            )
            st.bar_chart(
                regular.set_index("counterparty")["monthly_commitment"],
                height=340,
                color=config["accent"],
            )
            show_table(regular, "regular_payments.csv")

    if not borrowing.empty:
        with panels["Loans & savings"]:
            st.caption(
                "Money moving between your wallet and credit or savings "
                "products. For Fuliza, 'to you' is borrowed and 'from you' "
                "repaid; for savings it reads the other way."
            )
            show_table(borrowing, "loans_savings.csv")
            fuliza = borrowing[borrowing["product"] == "Fuliza"]
            if not fuliza.empty and float(fuliza.iloc[0]["fees"]) > 0:
                row = fuliza.iloc[0]
                st.warning(
                    f"Fuliza fees: {money(float(row['fees']))} on "
                    f"{money(float(row['to_you']))} borrowed."
                )

    with panels["People"]:
        paying, receiving = st.columns(2)
        with paying:
            st.markdown("**Who you pay**")
            top = analysis.top_counterparties(frame, limit=15)
            st.bar_chart(
                top.set_index("counterparty")["spent"],
                height=340,
                color=config["accent"],
            )
            show_table(top, "who_you_pay.csv")
        with receiving:
            st.markdown("**Who pays you**")
            sources = analysis.top_counterparties(frame, limit=15, direction="in")
            st.bar_chart(
                sources.set_index("counterparty")["received"],
                height=340,
                color=config["accent_deep"],
            )
            show_table(sources, "who_pays_you.csv")

    with panels["When you spend"]:
        weekday_col, hour_col = st.columns(2)
        with weekday_col:
            st.markdown("**By day of the week**")
            week = analysis.by_weekday(frame)
            # Streamlit sorts a text axis alphabetically; the number prefix
            # keeps Monday first.
            week["weekday"] = [
                f"{i + 1}. {name[:3]}" for i, name in enumerate(week["weekday"])
            ]
            st.bar_chart(
                week.set_index("weekday")["spent"], height=320, color=config["accent"]
            )
        with hour_col:
            st.markdown("**By hour of the day**")
            hours = analysis.by_hour(frame).set_index("hour")
            st.bar_chart(hours["spent"], height=320, color=config["accent"])
        busiest = analysis.by_weekday(frame)
        if float(busiest["spent"].sum()) > 0:
            peak = busiest.loc[busiest["spent"].idxmax()]
            st.caption(
                f"Most spending happens on {peak['weekday']}s "
                f"({money(float(peak['spent']))} across the period)."
            )

    with panels["Transactions"]:
        columns = [
            "completion_time",
            "provider",
            "details",
            "category",
            "counterparty",
            "status",
            "paid_in",
            "withdrawn",
        ]
        if not multi:
            columns.append("balance")
        display = frame[columns]

        search_col, category_col, status_col = st.columns([2, 2, 1])
        with search_col:
            query = st.text_input(
                "Search", placeholder="Name, till, paybill, anything in the details…"
            )
        with category_col:
            chosen = st.multiselect(
                "Category", sorted(frame["category"].unique()), default=[]
            )
        with status_col:
            statuses = st.multiselect(
                "Status", sorted(frame["status"].unique()), default=[]
            )

        if query:
            hit = display["details"].str.contains(query, case=False, regex=False)
            hit |= display["counterparty"].str.contains(query, case=False, regex=False)
            display = display[hit]
        if chosen:
            display = display[display["category"].isin(chosen)]
        if statuses:
            display = display[display["status"].isin(statuses)]

        st.caption(f"{len(display)} of {len(frame)} transactions")
        show_table(display, "transactions.csv")


if "provider" not in st.session_state:
    landing()
else:
    analysis_view()