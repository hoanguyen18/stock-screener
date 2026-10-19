"""Download fundamentals from SEC EDGAR and prices from Yahoo Finance (both free)."""
import os
import time
from collections import defaultdict
from datetime import date

import pandas as pd
import requests

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "StockScreenerDemo contact@example.com")
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
QUARTER_DAYS = (75, 120)

# Each base metric maps to the XBRL tags companies use for it, in order of preference.
# Companies switch tags over the years (e.g. revenue after ASC 606 in 2018), so all are merged.
BASE_METRICS = {
    "Revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "GrossProfit": ["GrossProfit"],
    "OperatingIncome": ["OperatingIncomeLoss"],
    "NetIncome": ["NetIncomeLoss"],
    "OperatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities"],
    "CapEx": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    ],
}


def _get(url):
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    time.sleep(0.15)  # stay well under SEC's 10 requests/second limit
    return resp.json()


def ticker_to_cik():
    data = _get("https://www.sec.gov/files/company_tickers.json")
    return {row["ticker"]: (str(row["cik_str"]).zfill(10), row["title"]) for row in data.values()}


def _tag_values(facts, tag):
    """Latest-filed USD value for every (start, end) period reported under one tag."""
    rows = facts.get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
    best = {}
    for r in rows:
        if "start" not in r or r.get("form") not in FORMS:
            continue
        key = (date.fromisoformat(r["start"]), date.fromisoformat(r["end"]))
        if key not in best or r["filed"] > best[key][1]:
            best[key] = (r["val"], r["filed"])
    return {k: v[0] for k, v in best.items()}


def _quarters_and_years(values):
    """Split period values into quarterly and fiscal-year values.

    Many filings only report year-to-date figures (cash flow statements, and Q4 which
    only appears inside the annual 10-K), so missing quarters are derived by
    differencing consecutive year-to-date values that share the same start date.
    """
    direct, derived, annual = {}, {}, {}
    by_start = defaultdict(list)
    for (start, end), val in values.items():
        days = (end - start).days
        if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            direct[end] = val
        elif 350 <= days <= 380:
            annual[end] = val
        by_start[start].append((end, val))
    for periods in by_start.values():
        periods.sort()
        for (e1, v1), (e2, v2) in zip(periods, periods[1:]):
            if QUARTER_DAYS[0] <= (e2 - e1).days <= QUARTER_DAYS[1]:
                derived.setdefault(e2, v2 - v1)
    quarterly = dict(derived)
    quarterly.update(direct)  # directly reported quarters win over derived ones

    # Q4 is often only in the 10-K: fiscal year minus the three quarters inside it.
    for (start, end), val in values.items():
        if not 350 <= (end - start).days <= 380 or end in quarterly:
            continue
        inside = [e for e in quarterly if start < e < end]
        if len(inside) == 3:
            quarterly[end] = val - sum(quarterly[e] for e in inside)
    return quarterly, annual


def _latest_contiguous(series_dict, min_gap, max_gap):
    """Keep the most recent unbroken run of periods so comparisons line up."""
    ends = sorted(series_dict)
    keep = ends[-1:] if ends else []
    for prev, cur in zip(reversed(ends[:-1]), reversed(ends[1:])):
        if min_gap <= (cur - prev).days <= max_gap:
            keep.insert(0, prev)
        else:
            break
    return {e: series_dict[e] for e in keep}


def company_fundamentals(cik):
    facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")["facts"]
    rows = []
    for metric, tags in BASE_METRICS.items():
        q_all, y_all = {}, {}
        for tag in tags:
            q, y = _quarters_and_years(_tag_values(facts, tag))
            for end, val in q.items():
                q_all.setdefault(end, val)
            for end, val in y.items():
                y_all.setdefault(end, val)
        for freq, values, gaps in [("Q", q_all, QUARTER_DAYS), ("FY", y_all, (350, 380))]:
            for end, val in _latest_contiguous(values, *gaps).items():
                rows.append({"metric": metric, "freq": freq, "end": end, "value": float(val)})
    return rows


def download_prices(tickers, years=12):
    import yfinance as yf

    # Split-adjusted closing prices (not dividend-adjusted), as most screeners show.
    px = yf.download(tickers, period=f"{years}y", interval="1d", auto_adjust=False, progress=False)["Close"]
    return px.dropna(how="all")
