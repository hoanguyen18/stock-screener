"""Screening logic: metric series, change calculations, trend tests and price filters."""
import math
import re

import numpy as np
import pandas as pd

BASE_METRIC_NAMES = ["Revenue", "GrossProfit", "OperatingIncome", "NetIncome", "OperatingCashFlow", "CapEx"]

# Custom metrics are formulas over base metrics. "ratio" metrics are shown in % and
# their changes are measured in percentage points; "amount" metrics change in %.
DEFAULT_CUSTOM_METRICS = [
    {"name": "FCF", "formula": "OperatingCashFlow - CapEx", "type": "amount"},
    {"name": "GrossMargin", "formula": "GrossProfit / Revenue * 100", "type": "ratio"},
    {"name": "OperatingMargin", "formula": "OperatingIncome / Revenue * 100", "type": "ratio"},
    {"name": "NetMargin", "formula": "NetIncome / Revenue * 100", "type": "ratio"},
    {"name": "FCFMargin", "formula": "(OperatingCashFlow - CapEx) / Revenue * 100", "type": "ratio"},
]

BASES = ["Quarterly QoQ", "Quarterly YoY", "Seasonal QoQ", "TTM rolling", "TTM YoY", "Annual YoY"]
TESTS = [
    "Latest change ≥",
    "Latest change ≤",
    "Acceleration ≥ (pp)",
    "Consistency: k of last m positive",
    "Repeated acceleration: k of last m",
    "Group improvement ≥ (pp)",
    "Latest value ≥",
    "Latest value ≤",
    "CAGR ≥ % over m years",
]

PRICE_CONDITIONS = [
    "Below 52-week high by ≥ %",
    "1-year return ≤ %",
    "Below 200-day SMA by ≥ %",
    "Within % above 52-week low",
    "Below N-year high by ≥ %",
]

_ALLOWED_FORMULA = re.compile(r"^[A-Za-z0-9_+\-*/(). ]+$")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# ---------- metric series ----------

def validate_formula(formula):
    if not formula or not _ALLOWED_FORMULA.match(formula):
        return "Only metric names, numbers, + - * / and brackets are allowed"
    unknown = [n for n in _NAME.findall(formula) if n not in BASE_METRIC_NAMES]
    if unknown:
        return f"Unknown metric(s): {', '.join(sorted(set(unknown)))}"
    return None


def _evaluate(formula, frame):
    result = eval(formula, {"__builtins__": {}}, {c: frame[c] for c in frame.columns})  # formula is validated above
    return result.replace([np.inf, -np.inf], np.nan)


class CompanyData:
    """All series for one company, built from the long fundamentals table."""

    def __init__(self, rows, custom_metrics):
        self.custom = {m["name"]: m for m in custom_metrics}
        self.q = rows[rows.freq == "Q"].pivot_table(index="end", columns="metric", values="value").sort_index()
        self.fy = rows[rows.freq == "FY"].pivot_table(index="end", columns="metric", values="value").sort_index()

    def metric_type(self, metric):
        return self.custom[metric]["type"] if metric in self.custom else "amount"

    def _series(self, frame, metric):
        if metric in self.custom:
            needed = [n for n in _NAME.findall(self.custom[metric]["formula"]) if n in BASE_METRIC_NAMES]
            if any(n not in frame.columns for n in needed):
                return pd.Series(dtype=float)
            return _evaluate(self.custom[metric]["formula"], frame[needed].dropna()).dropna()
        return frame[metric].dropna() if metric in frame.columns else pd.Series(dtype=float)

    def quarterly(self, metric):
        return self._series(self.q, metric)

    def ttm(self, metric):
        # Sum the base metrics over 4 quarters first, then apply the formula, so
        # ratios such as margins are TTM numerator / TTM denominator (never summed %).
        ttm_base = self.q.rolling(4, min_periods=4).sum().dropna(how="all")
        return self._series(ttm_base, metric)

    def annual(self, metric):
        return self._series(self.fy, metric)


def period_label(ts, freq):
    ts = pd.Timestamp(ts)
    if freq == "FY":
        return f"FY{ts.year}"
    ts -= pd.Timedelta(days=45)  # label by the calendar quarter the fiscal quarter mostly falls in
    return f"Q{(ts.month - 1) // 3 + 1} {ts.year}"


def _change(values, lag, kind):
    prev = values.shift(lag)
    if kind == "ratio":
        return values - prev
    return (values - prev) / prev.abs() * 100


def change_series(company, metric, basis, seasonal_years=3):
    """Return (levels, changes, freq, lag) for one metric and comparison basis."""
    kind = company.metric_type(metric)
    if basis == "Annual YoY":
        levels, freq, lag = company.annual(metric), "FY", 1
    elif basis.startswith("TTM"):
        levels, freq, lag = company.ttm(metric), "Q", 1 if basis == "TTM rolling" else 4
    else:
        levels, freq, lag = company.quarterly(metric), "Q", 4 if basis == "Quarterly YoY" else 1
    changes = _change(levels, lag, kind)
    if basis == "Seasonal QoQ":
        # Compare each QoQ change with the average change for the same seasonal
        # transition over the previous `seasonal_years` years.
        normal = sum(changes.shift(4 * k) for k in range(1, seasonal_years + 1)) / seasonal_years
        changes = changes - normal
    return levels, changes.dropna(), freq, lag


# ---------- rule evaluation ----------

def _fmt_change(x, kind_or_pp):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.1f}{' pp' if kind_or_pp == 'pp' else '%'}"


def _fmt_level(x, kind):
    if kind == "ratio":
        return f"{x:.1f}%"
    for div, suffix in [(1e9, "B"), (1e6, "M")]:
        if abs(x) >= div:
            return f"${x / div:,.2f}{suffix}"
    return f"${x:,.0f}"


def evaluate_rule(company, rule):
    """Return (passed, explanation) for one fundamental rule on one company."""
    metric, basis, test = rule["metric"], rule["basis"], rule["test"]
    threshold = float(rule.get("threshold") or 0)
    k, m = int(rule.get("k") or 0), int(rule.get("m") or 0)
    kind = company.metric_type(metric)
    unit = "pp" if (kind == "ratio" or basis == "Seasonal QoQ") else "%"
    try:
        levels, ch, freq, lag = change_series(company, metric, basis, int(rule.get("years") or 3))
    except Exception as exc:
        return False, f"error: {exc}"
    if levels.empty:
        return False, f"no {metric} data"

    label = lambda ts: period_label(ts, freq)  # noqa: E731
    if test.startswith("Latest value"):
        val = levels.iloc[-1]
        target = threshold if kind == "ratio" else threshold * 1e6
        passed = val >= target if "≥" in test else val <= target
        return passed, f"{_fmt_level(val, kind)} ({label(levels.index[-1])})"

    if test.startswith("CAGR"):
        annual = company.annual(metric)
        years = m or 5
        if len(annual) <= years:
            return False, f"needs {years + 1} fiscal years, has {len(annual)}"
        start, end = annual.iloc[-1 - years], annual.iloc[-1]
        if start <= 0 or end <= 0:
            return False, "CAGR undefined for negative values"
        cagr = ((end / start) ** (1 / years) - 1) * 100
        return cagr >= threshold, f"{cagr:+.1f}%/yr ({label(annual.index[-1 - years])}→{label(annual.index[-1])})"

    if ch.empty:
        return False, f"not enough history for {basis}"
    last_ts = ch.index[-1]
    prev_ts = levels.index[levels.index.get_loc(last_ts) - lag]
    period = f"{label(last_ts)} vs {label(prev_ts)}" if basis != "Seasonal QoQ" else f"{label(last_ts)} vs normal"

    if test.startswith("Latest change"):
        val = ch.iloc[-1]
        passed = val >= threshold if "≥" in test else val <= threshold
        extra = ""
        if kind == "amount" and basis != "Seasonal QoQ":
            delta = levels.loc[last_ts] - levels.loc[prev_ts]
            extra = f", {'+' if delta >= 0 else '-'}{_fmt_level(abs(delta), kind)}"
        return passed, f"{_fmt_change(val, unit)}{extra} ({period})"

    if test.startswith("Acceleration"):
        if len(ch) < 2:
            return False, "not enough history"
        acc = ch.iloc[-1] - ch.iloc[-2]
        return acc >= threshold, f"{_fmt_change(ch.iloc[-2], unit)} → {_fmt_change(ch.iloc[-1], unit)} = {acc:+.1f} pp ({label(last_ts)})"

    if test.startswith("Consistency"):
        window = ch.iloc[-m:]
        if len(window) < m:
            return False, "not enough history"
        hits = int((window > 0).sum())
        seq = ", ".join(_fmt_change(v, unit) for v in window)
        return hits >= k, f"{hits} of {m} positive [{seq}]"

    if test.startswith("Repeated acceleration"):
        window = ch.iloc[-(m + 1):]
        if len(window) < m + 1:
            return False, "not enough history"
        hits = int((window.diff().dropna() > 0).sum())
        seq = ", ".join(_fmt_change(v, unit) for v in window)
        return hits >= k, f"accelerated in {hits} of {m} comparisons [{seq}]"

    if test.startswith("Group improvement"):
        n = int(rule.get("group_size") or 2)
        if len(ch) < 2 * n:
            return False, "not enough history"
        newer, older = ch.iloc[-n:].mean(), ch.iloc[-2 * n:-n].mean()
        diff = newer - older
        return diff >= threshold, f"newer {n} avg {_fmt_change(newer, unit)} vs older {n} avg {_fmt_change(older, unit)} = {diff:+.1f} pp"

    return False, f"unknown test: {test}"


def evaluate_price(prices, rule):
    p = prices.dropna()
    if len(p) < 260:
        return False, "not enough price history"
    last, thr = p.iloc[-1], float(rule.get("threshold") or 0)
    cond = rule["condition"]
    if cond == "Below 52-week high by ≥ %":
        hi = p.iloc[-252:].max()
        gap = (1 - last / hi) * 100
        return gap >= thr, f"{gap:.1f}% below 52w high (${hi:,.2f})"
    if cond == "1-year return ≤ %":
        ret = (last / p.iloc[-253] - 1) * 100
        return ret <= thr, f"1y return {ret:+.1f}%"
    if cond == "Below 200-day SMA by ≥ %":
        sma = p.iloc[-200:].mean()
        gap = (1 - last / sma) * 100
        return gap >= thr, f"{gap:+.1f}% vs 200d SMA (${sma:,.2f})"
    if cond == "Within % above 52-week low":
        lo = p.iloc[-252:].min()
        gap = (last / lo - 1) * 100
        return gap <= thr, f"{gap:.1f}% above 52w low (${lo:,.2f})"
    if cond == "Below N-year high by ≥ %":
        years = int(rule.get("years") or 3)
        window = p.iloc[-252 * years:]
        hi = window.max()
        gap = (1 - last / hi) * 100
        note = "" if len(p) >= 252 * years else f" (only {len(p) / 252:.1f}y of data)"
        return gap >= thr, f"{gap:.1f}% below {years}y high (${hi:,.2f}){note}"
    return False, f"unknown condition: {cond}"


def combine(results, mode):
    if not results:
        return None
    return all(results) if mode == "AND" else any(results)
