import json
import os

import pandas as pd
import streamlit as st

from data_sources import BASE_METRICS
from engine import (
    BASE_METRIC_NAMES,
    BASES,
    DEFAULT_CUSTOM_METRICS,
    PRICE_CONDITIONS,
    TESTS,
    CompanyData,
    change_series,
    combine,
    evaluate_price,
    evaluate_rule,
    period_label,
    validate_formula,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

st.set_page_config(page_title="Growth Stock Screener", page_icon="📈", layout="wide")

DEFAULT_RULES = [
    {"On": True, "Metric": "Revenue", "Basis": "Quarterly YoY", "Test": "Latest change ≥", "Threshold": 10.0, "k": 0, "m": 0, "Group size": 2, "Seasonal years": 3},
    {"On": True, "Metric": "Revenue", "Basis": "Quarterly QoQ", "Test": "Consistency: k of last m positive", "Threshold": 0.0, "k": 3, "m": 4, "Group size": 2, "Seasonal years": 3},
    {"On": False, "Metric": "Revenue", "Basis": "Quarterly YoY", "Test": "Repeated acceleration: k of last m", "Threshold": 0.0, "k": 2, "m": 3, "Group size": 2, "Seasonal years": 3},
    {"On": True, "Metric": "Revenue", "Basis": "Quarterly YoY", "Test": "Group improvement ≥ (pp)", "Threshold": 0.0, "k": 0, "m": 0, "Group size": 2, "Seasonal years": 3},
    {"On": False, "Metric": "Revenue", "Basis": "Seasonal QoQ", "Test": "Latest change ≥", "Threshold": 0.0, "k": 0, "m": 0, "Group size": 2, "Seasonal years": 3},
    {"On": False, "Metric": "FCF", "Basis": "TTM YoY", "Test": "Latest change ≥", "Threshold": 0.0, "k": 0, "m": 0, "Group size": 2, "Seasonal years": 3},
    {"On": False, "Metric": "GrossMargin", "Basis": "TTM rolling", "Test": "Latest change ≥", "Threshold": 0.0, "k": 0, "m": 0, "Group size": 2, "Seasonal years": 3},
    {"On": False, "Metric": "Revenue", "Basis": "Annual YoY", "Test": "CAGR ≥ % over m years", "Threshold": 10.0, "k": 0, "m": 5, "Group size": 2, "Seasonal years": 3},
]
DEFAULT_PRICE_RULES = [
    {"On": True, "Condition": "Below 52-week high by ≥ %", "Threshold": 10.0, "Years": 0},
    {"On": False, "Condition": "1-year return ≤ %", "Threshold": -10.0, "Years": 0},
    {"On": False, "Condition": "Below 200-day SMA by ≥ %", "Threshold": 20.0, "Years": 0},
    {"On": False, "Condition": "Within % above 52-week low", "Threshold": 10.0, "Years": 0},
    {"On": False, "Condition": "Below N-year high by ≥ %", "Threshold": 30.0, "Years": 3},
]


@st.cache_data
def load_data():
    companies = pd.read_csv(os.path.join(DATA_DIR, "companies.csv"))
    fundamentals = pd.read_csv(os.path.join(DATA_DIR, "fundamentals.csv"), parse_dates=["end"])
    prices = pd.read_csv(os.path.join(DATA_DIR, "prices.csv"), index_col=0, parse_dates=True)
    return companies, fundamentals, prices


@st.cache_resource
def build_companies(custom_json):
    _, fundamentals, _ = load_data()
    custom = json.loads(custom_json)
    return {t: CompanyData(rows, custom) for t, rows in fundamentals.groupby("ticker")}


def init_state(reset=False):
    defaults = {
        "custom_df": pd.DataFrame(DEFAULT_CUSTOM_METRICS),
        "rules_df": pd.DataFrame(DEFAULT_RULES),
        "price_df": pd.DataFrame(DEFAULT_PRICE_RULES),
        "f_mode": "AND",
        "p_mode": "ANY",
        "combine_mode": "AND",
    }
    for key, val in defaults.items():
        if reset or key not in st.session_state:
            st.session_state[key] = val
    if reset:
        for key in ["custom_editor", "rules_editor", "price_editor"]:
            st.session_state.pop(key, None)


def rule_name(i, r):
    test = r["Test"]
    if test.startswith("Consistency") or test.startswith("Repeated"):
        detail = f"{int(r['k'])} of {int(r['m'])}"
    elif test.startswith("Group"):
        detail = f"{int(r['Group size'])} vs {int(r['Group size'])} ≥ {r['Threshold']:g} pp"
    elif test.startswith("CAGR"):
        detail = f"≥ {r['Threshold']:g}% over {int(r['m'])}y"
    else:
        detail = f"{r['Threshold']:g}"
    short_test = test.split(":")[0].replace(" ≥", "").replace(" ≤", "").replace(" (pp)", "")
    op = "≤" if "≤" in test else "≥"
    if test.startswith(("Latest", "Acceleration")):
        detail = f"{op} {detail}"
    return f"F{i}: {r['Metric']} · {r['Basis']} · {short_test} {detail}"


def price_name(i, r):
    name = r["Condition"].replace("N-year", f"{int(r['Years'])}-year")
    return f"P{i}: {name.replace(' %', '')} {r['Threshold']:g}%"


init_state()
companies, fundamentals, prices = load_data()
names = dict(zip(companies.ticker, companies.name))

st.title("📈 Growth Stock Screener")
st.caption(
    f"{len(companies)} US companies · fundamentals from SEC EDGAR (latest filing period {fundamentals.end.max():%b %d, %Y}) · "
    f"prices from Yahoo Finance (to {prices.index.max():%b %d, %Y}) · free data, no subscription"
)

tab_screen, tab_detail, tab_limits = st.tabs(["Screener", "Company detail", "Data sources & limitations"])

with tab_screen:
    # ---------- custom metrics ----------
    with st.expander("① Metrics & your own formulas", expanded=False):
        st.markdown(
            "Base metrics come straight from SEC filings: " + ", ".join(f"`{m}`" for m in BASE_METRIC_NAMES)
            + ". Add or edit formulas below (e.g. your own FCF or ROIC definition). "
            "**ratio** metrics are shown in % and their changes are measured in percentage points; "
            "TTM ratios are always TTM numerator ÷ TTM denominator."
        )
        custom_edit = st.data_editor(
            st.session_state.custom_df,
            key="custom_editor",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "name": st.column_config.TextColumn("Name", required=True),
                "formula": st.column_config.TextColumn("Formula", required=True, width="large"),
                "type": st.column_config.SelectboxColumn("Type", options=["amount", "ratio"], required=True),
            },
        )
    valid_custom, errors = [], []
    for row in custom_edit.dropna(subset=["name", "formula"]).to_dict("records"):
        err = validate_formula(row["formula"])
        if err:
            errors.append(f"**{row['name']}**: {err}")
        else:
            valid_custom.append({"name": row["name"], "formula": row["formula"], "type": row.get("type") or "amount"})
    for e in errors:
        st.error(e)
    metric_options = BASE_METRIC_NAMES + [m["name"] for m in valid_custom]
    data = build_companies(json.dumps(valid_custom, sort_keys=True))

    # ---------- fundamental rules ----------
    with st.expander("② Fundamental filters", expanded=True):
        st.markdown(
            "Each row is one filter. **Basis** picks the comparison (QoQ, YoY, seasonal, TTM rolling/YoY, annual). "
            "**Test** picks what must be true. `k`/`m` are used by Consistency, Repeated acceleration (k of m) "
            "and CAGR (m years); **Group size** by Group improvement; **Seasonal years** by Seasonal QoQ. "
            "Latest value thresholds are in $ millions (or % for ratios)."
        )
        rules_edit = st.data_editor(
            st.session_state.rules_df,
            key="rules_editor",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "On": st.column_config.CheckboxColumn("On", default=True, width="small"),
                "Metric": st.column_config.SelectboxColumn("Metric", options=metric_options, required=True),
                "Basis": st.column_config.SelectboxColumn("Basis", options=BASES, required=True),
                "Test": st.column_config.SelectboxColumn("Test", options=TESTS, required=True, width="large"),
                "Threshold": st.column_config.NumberColumn("Threshold", default=0.0, format="%.1f"),
                "k": st.column_config.NumberColumn("k", default=3, min_value=0, step=1, width="small"),
                "m": st.column_config.NumberColumn("m", default=4, min_value=0, step=1, width="small"),
                "Group size": st.column_config.NumberColumn("Group size", default=2, min_value=1, step=1, width="small"),
                "Seasonal years": st.column_config.NumberColumn("Seasonal years", default=3, min_value=1, max_value=10, step=1, width="small"),
            },
        )
        st.radio("Combine fundamental filters with", ["AND", "OR"], key="f_mode", horizontal=True)

    # ---------- price rules ----------
    with st.expander("③ Stock price filters", expanded=True):
        price_edit = st.data_editor(
            st.session_state.price_df,
            key="price_editor",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "On": st.column_config.CheckboxColumn("On", default=True, width="small"),
                "Condition": st.column_config.SelectboxColumn("Condition", options=PRICE_CONDITIONS, required=True, width="large"),
                "Threshold": st.column_config.NumberColumn("Threshold %", default=10.0, format="%.1f"),
                "Years": st.column_config.NumberColumn("Years (N-year high)", default=3, min_value=0, step=1),
            },
        )
        c1, c2 = st.columns(2)
        c1.radio("Match price conditions", ["ANY", "ALL"], key="p_mode", horizontal=True)
        c2.radio("Combine fundamentals with price block", ["AND", "OR"], key="combine_mode", horizontal=True)

    # ---------- save / load ----------
    with st.expander("Save or load a screen"):
        settings = {
            "custom_metrics": valid_custom,
            "fundamental_rules": rules_edit.to_dict("records"),
            "price_rules": price_edit.to_dict("records"),
            "fundamental_mode": st.session_state.f_mode,
            "price_mode": st.session_state.p_mode,
            "combine_mode": st.session_state.combine_mode,
        }
        st.download_button("Download this screen (.json)", json.dumps(settings, indent=2, default=str), "screen.json", "application/json")
        uploaded = st.file_uploader("Load a saved screen", type="json")
        c1, c2 = st.columns(2)
        if uploaded is not None and c1.button("Apply loaded screen"):
            loaded = json.load(uploaded)
            init_state(reset=True)
            st.session_state.custom_df = pd.DataFrame(loaded["custom_metrics"])
            st.session_state.rules_df = pd.DataFrame(loaded["fundamental_rules"])
            st.session_state.price_df = pd.DataFrame(loaded["price_rules"])
            st.session_state.f_mode = loaded.get("fundamental_mode", "AND")
            st.session_state.p_mode = loaded.get("price_mode", "ANY")
            st.session_state.combine_mode = loaded.get("combine_mode", "AND")
            st.rerun()
        if c2.button("Reset to example screen"):
            init_state(reset=True)
            st.rerun()

    # ---------- run ----------
    active_rules = [(i, r) for i, r in enumerate(rules_edit.dropna(subset=["Metric", "Basis", "Test"]).to_dict("records"), 1) if r.get("On")]
    active_price = [(i, r) for i, r in enumerate(price_edit.dropna(subset=["Condition"]).to_dict("records"), 1) if r.get("On")]

    rows, reasons = [], {}
    for ticker in companies.ticker:
        company = data.get(ticker)
        row = {"Ticker": ticker, "Company": names[ticker]}
        f_results, p_results, why = [], [], []
        for i, r in active_rules:
            rule = {"metric": r["Metric"], "basis": r["Basis"], "test": r["Test"], "threshold": r["Threshold"],
                    "k": r["k"], "m": r["m"], "group_size": r["Group size"], "years": r["Seasonal years"]}
            ok, text = evaluate_rule(company, rule) if company is not None else (False, "no SEC data")
            f_results.append(bool(ok))
            row[f"F{i}"] = f"{'✅' if ok else '❌'} {text}"
            why.append((rule_name(i, r), bool(ok), text))
        for i, r in active_price:
            ok, text = evaluate_price(prices[ticker], {"condition": r["Condition"], "threshold": r["Threshold"], "years": r["Years"]}) \
                if ticker in prices.columns else (False, "no price data")
            p_results.append(bool(ok))
            row[f"P{i}"] = f"{'✅' if ok else '❌'} {text}"
            why.append((price_name(i, r), bool(ok), text))
        f_pass = combine(f_results, st.session_state.f_mode)
        p_pass = combine(p_results, "AND" if st.session_state.p_mode == "ALL" else "OR")
        parts = [x for x in (f_pass, p_pass) if x is not None]
        final = combine(parts, st.session_state.combine_mode) if parts else False
        row["Result"] = "✅ PASS" if final else "❌ fail"
        row["Passed"] = f"{sum(f_results) + sum(p_results)}/{len(f_results) + len(p_results)}"
        rows.append(row)
        reasons[ticker] = why

    results = pd.DataFrame(rows)
    st.session_state.reasons = reasons
    n_pass = int((results.Result == "✅ PASS").sum())

    st.subheader(f"Results: {n_pass} of {len(results)} companies pass")
    if active_rules or active_price:
        st.markdown("  \n".join([f"`{rule_name(i, r)}`" for i, r in active_rules] + [f"`{price_name(i, r)}`" for i, r in active_price]))
    only_pass = st.toggle("Show passing companies only", value=True)
    view = results[results.Result == "✅ PASS"] if only_pass else results.sort_values("Result", ascending=False)
    cols = ["Ticker", "Company", "Result", "Passed"] + [c for c in results.columns if c[0] in "FP" and c[1:].isdigit()]
    st.dataframe(view[cols], width="stretch", hide_index=True, height=min(38 * (len(view) + 1), 600))
    st.caption("Each filter cell shows the actual value and the periods compared. Open **Company detail** for the full history.")

with tab_detail:
    c1, c2 = st.columns([1, 1])
    ticker = c1.selectbox("Company", companies.ticker, format_func=lambda t: f"{t} · {names[t]}")
    metric = c2.selectbox("Metric", metric_options)
    company = data.get(ticker)

    why = st.session_state.get("reasons", {}).get(ticker, [])
    if why:
        st.markdown("**Why it passed or failed the current screen**")
        st.dataframe(pd.DataFrame([{"Filter": n, "Pass": "✅" if ok else "❌", "Actual": t} for n, ok, t in why]),
                     width="stretch", hide_index=True)

    if company is None:
        st.warning("No SEC data for this company.")
    else:
        kind = company.metric_type(metric)
        q = company.quarterly(metric)
        if q.empty:
            st.warning(f"No {metric} data reported for {ticker}. See Data sources & limitations.")
        else:
            table = pd.DataFrame({"Quarter": [period_label(e, "Q") for e in q.index], "Period end": q.index.date})
            scale = 1 if kind == "ratio" else 1e6
            table["Value" + (" (%)" if kind == "ratio" else " ($M)")] = (q.values / scale).round(1)
            for basis, col in [("Quarterly QoQ", "QoQ"), ("Quarterly YoY", "YoY"), ("Seasonal QoQ", "Seasonal vs normal (pp)"), ("TTM YoY", "TTM YoY")]:
                _, ch, _, _ = change_series(company, metric, basis)
                table[col + ("" if "pp" in col else (" (pp)" if kind == "ratio" else " (%)"))] = ch.reindex(q.index).round(1).values
            ttm = company.ttm(metric)
            table["TTM" + (" (%)" if kind == "ratio" else " ($M)")] = (ttm.reindex(q.index) / scale).round(1).values

            chart = pd.DataFrame({"Quarterly": q / scale, "TTM": ttm.reindex(q.index) / scale})
            chart.index = pd.to_datetime(chart.index)
            st.markdown(f"**{metric} {'(%)' if kind == 'ratio' else '($ millions)'}**")
            st.line_chart(chart.iloc[-40:], height=300)
            st.dataframe(table.iloc[::-1], width="stretch", hide_index=True, height=420)

        if ticker in prices.columns:
            p = prices[ticker].dropna().iloc[-252 * 3:]
            st.markdown("**Share price, last 3 years, with 200-day SMA**")
            st.line_chart(pd.DataFrame({"Price": p, "200-day SMA": prices[ticker].dropna().rolling(200).mean().reindex(p.index)}), height=260)

with tab_limits:
    st.markdown(
        f"""
### Data sources (all free)
| Data | Source | Cost |
|---|---|---|
| Financial statements (quarterly & annual) | SEC EDGAR XBRL API (`data.sec.gov`) | Free, official, no API key |
| Share prices | Yahoo Finance via `yfinance` | Free, unofficial |

### How the numbers are built
- Quarterly values come from 10-Q and 10-K filings. When a company only reports year-to-date figures
  (common for cash flow, and Q4 only appears in the 10-K), the quarter is derived: e.g. Q4 = fiscal year − Q1 − Q2 − Q3.
- TTM = sum of the last four quarters. Ratios are recalculated from TTM values, never by adding quarterly percentages.
- Quarters are labelled by the calendar quarter they mostly fall in, so companies with different fiscal years line up.
- Each metric is mapped to the XBRL tags companies use for it:
"""
        + "\n".join(f"  - **{m}**: " + ", ".join(f"`{t}`" for t in tags) for m, tags in BASE_METRICS.items())
        + """

### Limitations to know
- **US-listed companies that file with the SEC only.** Foreign companies filing 20-F/40-F have limited data.
- **History starts around 2009–2011**, when XBRL reporting began, so 10+ years are available for most companies.
- **Tag differences between companies.** Some companies report items under their own custom tags, so a metric can be
  missing or have gaps (e.g. banks have no "gross profit"). These show as "no data" instead of guessed values; the fix is
  adding company-specific tag mappings over time.
- **Restatements:** the latest filed value for each period is used.
- **Yahoo Finance is unofficial:** it can change or rate-limit without notice. Prices are split-adjusted, not dividend-adjusted.
- **Refresh:** run `python build_data.py` (daily or weekly). A full S&P 500 refresh takes a few minutes.
"""
    )
