"""Offline test of the v2 pipeline: synthetic market, no network, sandboxed outputs.

Run: python tests/test_offline.py
Checks: Pine port of RS Line - Blue Dot on a hand-checkable series, full scan + learning run,
quotas, one side per symbol, gate consistency, strict JSON, charts on disk, features archive.
"""
import json
import pathlib
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
import features as F  # noqa: E402
import scan  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="dcie2-"))
scan.ROOT, scan.OUT, scan.CACHE = TMP, TMP / "out", TMP / "universe.csv"


# ---------------------------------------------------------------- 1. Pine port, by hand
def test_rs_port():
    idx = pd.date_range("2024-01-05", periods=60, freq="W-FRI")
    stock = pd.DataFrame({"A": np.r_[np.full(45, 10.0), np.linspace(10, 14, 15)]}, index=idx)
    bench = pd.Series(np.full(60, 100.0), index=idx)
    r = F.rs_line_bluedot(stock, bench, scale=100, ma_len=40, hl_len=52)
    rs = r["rs"]["A"]
    assert abs(rs.iloc[0] - 10.0) < 1e-12                       # 10 / 100 * 100
    assert np.isnan(r["ma"]["A"].iloc[38]) and abs(r["ma"]["A"].iloc[39] - 10.0) < 1e-12
    # flat at 10, linspace starts at 10 too: first bar above the MA (week 46) is the one crossover
    cu = r["cross_up"]["A"]
    assert cu.sum() == 1 and cu.idxmax() == idx[46], cu[cu]
    # 52-bar high needs 52 bars of history; rising tail makes new highs after that
    assert not r["nh"]["A"].iloc[:51].any() and r["nh"]["A"].iloc[52:].all()
    print("[rs port] ok")


# ---------------------------------------------------------------- 2. synthetic market
rng = np.random.default_rng(11)
N = 820
IDX = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=N)
SECT = {"Technology": "Electronic Technology", "Financials": "Finance", "Energy": "Energy Minerals",
        "Health Care": "Health Technology", "Industrials": "Producer Manufacturing",
        "Utilities": "Utilities", "Materials": "Process Industries",
        "Consumer Staples": "Consumer Non-Durables", "Consumer Discretionary": "Retail Trade",
        "Communication": "Communications", "Real Estate": "Finance"}


def ohlc(r, vol):
    c = 50 * np.exp(np.cumsum(r))
    o = c * (1 + rng.normal(0, vol / 3, N))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, N)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, N)))
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": rng.integers(5e5, 5e6, N)},
                        index=IDX)


HIST, META = {}, []
mkt = rng.normal(0.0003, 0.009, N)
HIST["^GSPC"] = ohlc(mkt, 0.009)
for ss, etf in scan.SECTOR_ETF.items():
    sec = mkt + rng.normal(0, 0.005, N)
    HIST[etf] = ohlc(sec, 0.01)
    for i in range(45):
        drift = rng.normal(0, 0.0012)
        r = sec + drift + rng.normal(0, rng.uniform(0.008, 0.02), N)
        if i % 5 == 0:   # turning up: weak for 2y then strong last 4 months
            r[:-90] -= 0.0012
            r[-90:] += 0.004
        if i % 5 == 1:   # rolling over
            r[:-90] += 0.0012
            r[-90:] -= 0.004
        sym = "".join(w[0] for w in ss.split()) + f"X{i:02d}"
        HIST[sym] = ohlc(r, 0.015)
        industry = "Real Estate Investment Trusts" if ss == "Real Estate" else "Generic"
        META.append({"symbol": sym, "company": f"{ss} Co {i}", "exchange": "NYSE", "sector": SECT[ss],
                     "industry": industry, "supersector": scan.supersector(SECT[ss], industry),
                     "mcap": float(rng.uniform(2.1e9, 3e11))})


def fake_tv():
    return pd.DataFrame(META)


def fake_hist(symbols, sessions):
    return {s: HIST[s] for s in symbols if s in HIST and not s.endswith("07")}   # simulate gaps


def broken_tv():
    raise ConnectionError("simulated TradingView block")


def check(res, label):
    c = pd.DataFrame(res["candidates"])
    q = scan.CFG["selection"]["quota_per_side_per_sector"]
    assert c.groupby(["supersector", "side"]).size().max() <= q
    assert not c.symbol.duplicated().any(), "one side per symbol"
    assert set(c.side) == {"long", "short"}
    assert c[c.side == "long"].regime.isin(scan.CFG["gate"]["long"]).all()
    assert c[c.side == "short"].regime.isin(scan.CFG["gate"]["short"]).all()
    assert all((scan.OUT / x).exists() for x in c.chart)
    json.loads((scan.OUT / "results.json").read_text())
    assert (TMP / "history" / "features" / f"{res['run_id']}.csv.gz").exists()
    assert (TMP / "model" / "weights.json").exists()
    tags = pd.Series([t for ts in c.tags for t in ts]).value_counts().to_dict()
    m = res["model"]
    ho = {s: v["holdout_ic"] for s, v in m["sides"].items()}
    print(f"[{label}] universe={res['universe_count']} eligible={res['eligible']} charts={len(c)}")
    print(f"   regimes={res['regime_counts']}")
    print(f"   tags={tags}")
    print(f"   weights={m['weights_source']} holdout_ic={ho}")


if __name__ == "__main__":
    test_rs_port()
    res = scan.run(fake_tv, fake_hist)
    check(res, "tradingview path")
    shutil.copy(scan.OUT / "universe.csv", scan.CACHE)
    res = scan.run(broken_tv, fake_hist)
    check(res, "cached-universe path")
    print("OK", TMP)
