"""Offline smoke test: simulated universe + price paths, no network.

Run: python tests/test_offline.py
"""
import json
import pathlib
import shutil
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
import scan  # noqa: E402

rng = np.random.default_rng(7)
SECTORS = {v: k for k, v in scan.TV_SECTOR.items()}  # one TV sector per supersector
SECTORS["Real Estate"] = "Finance"
SECTORS["Communication"] = "Communications"
N_PER_SECTOR = 60
DAYS = 420
IDX = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=DAYS)


def path(drift, vol, shape=None):
    r = rng.normal(drift, vol, DAYS)
    if shape == "thrust_up":
        r[-15:] += 0.012
    if shape == "pullback":
        r[:-15] += 0.002
        r[-15:] -= 0.008
    if shape == "near200":
        r[:-60] -= 0.001
        r[-60:] += 0.0015
        r[-60:] = rng.normal(0.0015, vol * 0.35, 60)
    c = 50 * np.exp(np.cumsum(r))
    o = c * (1 + rng.normal(0, vol / 3, DAYS))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, vol / 2, DAYS)))
    lo = np.minimum(o, c) * (1 - abs(rng.normal(0, vol / 2, DAYS)))
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c,
                         "Volume": rng.integers(5e5, 5e6, DAYS)}, index=IDX)


HIST = {}
META = []
shapes = [None, "thrust_up", "pullback", "near200"]
for ss, tv in SECTORS.items():
    for i in range(N_PER_SECTOR):
        sym = "".join(w[0] for w in ss.split()) + ss[-2:].upper() + f"{i:02d}"
        HIST[sym] = path(rng.normal(0.0003, 0.0007), rng.uniform(0.012, 0.03), shapes[i % 4])
        industry = "Real Estate Investment Trusts" if ss == "Real Estate" else "Generic"
        META.append({"symbol": sym, "company": f"{ss} Co {i}", "exchange": "NYSE", "sector": tv,
                     "industry": industry, "mcap": float(rng.uniform(2.1e9, 3e11))})
for etf in scan.SECTOR_ETF.values():
    HIST[etf] = path(0.0003, 0.01)


def fake_tv():
    rows = []
    for m in META:
        c = HIST[m["symbol"]].Close
        rows.append({**m, "supersector": scan.supersector(m["sector"], m["industry"]),
                     "avg_vol": 1e6, "close": c.iloc[-1],
                     "d21": (c.iloc[-1] / c.rolling(20).mean().iloc[-1] - 1) * 100,
                     "d50": (c.iloc[-1] / c.rolling(50).mean().iloc[-1] - 1) * 100,
                     "d200": (c.iloc[-1] / c.rolling(200).mean().iloc[-1] - 1) * 100,
                     "p1w": (c.iloc[-1] / c.iloc[-6] - 1) * 100,
                     "p1m": (c.iloc[-1] / c.iloc[-22] - 1) * 100,
                     "p3m": (c.iloc[-1] / c.iloc[-64] - 1) * 100})
    return pd.DataFrame(rows)


def fake_hist(symbols, sessions):
    return {s: HIST[s] for s in symbols if s in HIST and not s.endswith("07")}  # simulate gaps


def broken_tv():
    raise ConnectionError("simulated TradingView block")


def check(res, label):
    c = res["candidates"]
    df = pd.DataFrame(c)
    per = df.groupby(["supersector", "side"]).size()
    assert per.max() <= scan.CFG["selection"]["quota_per_side_per_sector"], per
    assert df.supersector.nunique() == 11, df.supersector.unique()
    assert set(df.side) == {"long", "short"}
    assert all((ROOT / "out" / x).exists() for x in df.chart)
    assert not df.duplicated(["symbol", "side"]).any()
    json.loads((ROOT / "out" / "results.json").read_text())  # strict JSON
    tags = pd.Series([t for ts in df.tags for t in ts]).value_counts().to_dict()
    print(f"[{label}] source={res['source']} universe={res['universe_count']} pool={res['pool_count']} "
          f"charts={len(c)} tags={tags}")
    # rule sanity on exact metrics
    for r in c:
        if r["tags"] == ["S1"] and r["side"] == "long":
            assert r["d21"] > 0, r


if __name__ == "__main__":
    shutil.rmtree(ROOT / "out", ignore_errors=True)
    check(scan.run(fake_tv, fake_hist), "tradingview path")
    (ROOT / "data").mkdir(exist_ok=True)
    shutil.copy(ROOT / "out" / "universe.csv", ROOT / "data" / "universe.csv")
    check(scan.run(broken_tv, fake_hist), "fallback path")
    print("OK")
