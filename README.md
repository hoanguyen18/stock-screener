# Growth Stock Screener

A flexible stock screener built on **free data only**: fundamentals from SEC EDGAR and prices from Yahoo Finance. Every metric can be screened by its actual value and by how it changes over time, and every filter is editable in the app.

## Features

**Change types for every metric**
- Latest change in % (or percentage points for ratios), with the dollar change
- Acceleration: growth rate now vs. the previous period, in pp
- Consistency: positive in at least *k* of the last *m* periods
- Repeated acceleration: growth improved in *k* of the last *m* comparisons
- Group improvement: average of the newest *N* periods vs. the *N* before
- CAGR over *m* years, and latest value thresholds

**Comparison bases**
- Quarterly QoQ and quarterly YoY
- Seasonal QoQ: current QoQ vs. the normal change for that same seasonal transition over the past 3 to 5 years
- TTM rolling (overlapping) and TTM YoY (non-overlapping)
- Annual YoY and CAGR

**Price filters**, each independently selectable: below 52-week high, 1-year return, below 200-day SMA, near 52-week low, below N-year high. They can be matched with ANY or ALL.

**Combining:** fundamental filters with AND/OR, then fundamentals with the price block using AND/OR.

**Your own formulas:** define metrics such as FCF, margins or adjusted ROIC from base metrics. Ratios are recalculated from TTM values, never by adding quarterly percentages.

**Transparency:** every result shows the actual value, the periods compared and the pass/fail reason. Screens can be saved and loaded as JSON.

## Run locally

```bash
pip install -r requirements.txt
python build_data.py          # refresh data (default universe), or: python build_data.py AAPL MSFT ...
streamlit run app.py
```

The SEC asks API users to identify themselves. Set your contact before refreshing data:

```bash
export SEC_USER_AGENT="Your Name your@email.com"
```

## Data sources and limitations

| Data | Source | Cost |
|---|---|---|
| Quarterly and annual financials | SEC EDGAR XBRL API | Free, official |
| Daily prices | Yahoo Finance (`yfinance`) | Free, unofficial |

- US SEC filers only. History starts around 2009 to 2011, when XBRL reporting began.
- Quarters reported only as year-to-date values (cash flow, Q4) are derived, e.g. Q4 = fiscal year − Q1 − Q2 − Q3.
- Some companies use their own XBRL tags, so a metric can be missing until a mapping is added. Missing data shows as "no data" and is never guessed.
- Yahoo Finance is unofficial and can change or rate-limit. Prices are split-adjusted.

## Project structure

| File | Purpose |
|---|---|
| `app.py` | Streamlit interface |
| `engine.py` | Metric series, change calculations, filters |
| `data_sources.py` | SEC EDGAR and Yahoo Finance download, quarter derivation |
| `build_data.py` | Refreshes the files in `data/` |
