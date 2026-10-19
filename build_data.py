"""Refresh the local data files the screener reads.

Usage:
    python build_data.py                # default universe
    python build_data.py AAPL MSFT NVDA # custom tickers
"""
import os
import sys

import pandas as pd

from data_sources import company_fundamentals, download_prices, ticker_to_cik

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DEFAULT_UNIVERSE = """
AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA ORCL CRM ADBE AMD INTC QCOM TXN MU AMAT LRCX KLAC CSCO
IBM NOW INTU PANW SNPS CDNS ADI MRVL NFLX DIS CMCSA T VZ TMUS COST WMT HD LOW TGT NKE SBUX MCD
CMG KO PEP PG CL MDLZ PM MO UNH JNJ LLY ABBV MRK PFE TMO ABT DHR AMGN GILD ISRG VRTX REGN CAT DE
HON GE UPS FDX LMT RTX BA MMM XOM CVX COP SLB NEE DUK LIN SHW ECL
""".split()


def main(tickers):
    os.makedirs(DATA_DIR, exist_ok=True)
    ciks = ticker_to_cik()
    companies, fundamentals = [], []
    for i, ticker in enumerate(tickers, 1):
        if ticker not in ciks:
            print(f"[{i}/{len(tickers)}] {ticker}: not found in SEC ticker list, skipped")
            continue
        cik, name = ciks[ticker]
        try:
            rows = company_fundamentals(cik)
        except Exception as exc:  # one bad company should not stop the refresh
            print(f"[{i}/{len(tickers)}] {ticker}: failed ({exc})")
            continue
        for r in rows:
            r["ticker"] = ticker
        fundamentals.extend(rows)
        companies.append({"ticker": ticker, "name": name, "cik": cik})
        print(f"[{i}/{len(tickers)}] {ticker}: {len(rows)} data points")

    pd.DataFrame(companies).to_csv(os.path.join(DATA_DIR, "companies.csv"), index=False)
    pd.DataFrame(fundamentals)[["ticker", "metric", "freq", "end", "value"]].to_csv(
        os.path.join(DATA_DIR, "fundamentals.csv"), index=False
    )
    prices = download_prices([c["ticker"] for c in companies])
    prices.to_csv(os.path.join(DATA_DIR, "prices.csv"))
    print(f"Saved {len(companies)} companies, {len(fundamentals)} fundamentals rows, {prices.shape[0]} price days")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT_UNIVERSE)
