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
import fundamentals as FU  # noqa: E402
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


def fake_fetch(sym, ysym, extras, cfg):
    h = sum(map(ord, sym))
    if h % 9 == 0:
        return {"symbol": sym}                                   # no coverage
    e0 = 1 + h % 7
    e1 = e0 * (1 + (h % 11 - 3) / 20)
    out = {"symbol": sym, "ccy": "EUR" if h % 13 == 0 else "USD", "fy_end": 1735603200,
           "eps0": e0, "eps1": e1, "eps2": e1 * 1.1, "rev0": 1e9 * e0, "rev1": 1.05e9 * e0, "rev2": 1.1e9 * e0,
           "n_an": 12}
    if extras:
        out.update({"eps1_90d": e1 * 0.97, "epssurp": 4.2, "surp_q": "2026-06-30",
                    "news": [{"d": "2026-10-01", "t": f"{sym} raises outlook", "s": "", "p": "Test"}]})
    return out


class FakeSec:
    def guidance(self, sym, days):
        return {"date": "2026-08-01", "form": "8-K", "url": "https://example.invalid", "excerpt": "FY26 revenue $1.2-1.3 billion"} \
            if sum(map(ord, sym)) % 2 else None


def fake_fund(cands, meta, price, run_id, root, cfg, ysym=None):
    return FU.collect(cands, meta, price, run_id, root, cfg, fetch=fake_fetch, fmp=lambda s, k: {"revsurp": 1.5, "epssurp": 3.0, "surp_date": "2026-08-01"}, sec_fn=FakeSec())


def test_guidance_extract():
    txt = ("Revenue for the quarter was $1.1 billion, compared to $1.0 billion for the same period last year.\n"
           "Full-year 2026 outlook: the company now expects revenue of $4.4 to $4.6 billion and adjusted EBITDA of $900 million.\n"
           "Backlog at quarter end reached $7.2 billion, up 18% year over year.\n"
           "We thank our employees.\nForward-Looking Statements\nThis release expects revenue of $9 billion blah.")
    g = FU.extract_guidance(FU._html_text(txt.replace("\n", "<br>")))
    assert "4.4 to $4.6 billion" in g and "Backlog" in g and "$9 billion" not in g and "same period" not in g, g
    print("[guidance extract] ok:", g[:90])


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
    test_guidance_extract()
    res = scan.run(fake_tv, fake_hist, fund_fn=fake_fund)
    check(res, "tradingview path")
    shutil.copy(scan.OUT / "universe.csv", scan.CACHE)
    # Dean's feedback on an older desk run (no feature snapshot yet) -> must be backfilled and learned
    old_run = str((IDX[-30]).date()) + "-v2.0"   # versioned run ids must backfill too
    c1 = pd.DataFrame(res["candidates"])
    (TMP / "labels").mkdir(exist_ok=True)
    L = c1[c1.side == "long"].symbol.tolist()
    Sh = c1[c1.side == "short"].symbol.tolist()
    dec = [{"run_id": old_run, "long": L[i], "short": Sh[i], "action": "take" if i % 2 else "pass",
            "reason": "Long leg weak" if i % 2 == 0 else ""} for i in range(25)]
    fb = [{"run_id": old_run, "symbol": s, "side": "long", "verdict": "good" if i % 3 else "bad"}
          for i, s in enumerate(L[25:45])]
    (TMP / "labels" / "decisions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in dec))
    (TMP / "labels" / "chart_feedback.jsonl").write_text("".join(json.dumps(x) + "\n" for x in fb))
    # an older fundamentals snapshot (~13 weeks back) so sales revisions can be computed
    snap = pd.read_csv(next((TMP / "history" / "fundamentals").glob("*.csv.gz")))
    snap["rev1"] = snap.rev1 / 1.02
    old_snap = (pd.Timestamp(res["run_id"][:10]) - pd.Timedelta(days=91)).date().isoformat()
    snap.to_csv(TMP / "history" / "fundamentals" / f"{old_snap}-v2.0.csv.gz", index=False)
    res = scan.run(broken_tv, fake_hist, fund_fn=fake_fund)
    check(res, "cached-universe path + feedback")
    assert (TMP / "history" / "features" / f"{old_run}.csv.gz").exists(), "backfill missing"
    pref = res["model"]["report"]["preference"]
    print("   preference:", {k: pref.get(k) for k in ("n_labels", "n_matched", "beta")})
    assert pref["n_matched"] >= 30 and pref["beta"] > 0, pref
    # fundamentals attached to candidates, sector aggregates present, context written, size check
    c2 = pd.DataFrame(res["candidates"])
    have = c2.fund.notna().mean()
    assert have > 0.8, have
    f0 = next(x for x in c2.fund if x and x.get("pe1"))
    assert {"pe1", "epsg1", "revg1", "peg1", "epsrev3m", "revsurp"} <= set(f0), f0
    assert abs(f0["epsrev3m"] - 3.09) < 0.05, f0["epsrev3m"]           # 1/0.97 - 1
    f1 = next(x for sym, x in zip(c2.symbol, c2.fund) if x and sym in set(snap.symbol) and x.get("eps1"))
    assert abs(f1["revrev3m"] - 2.0) < 0.05, f1["revrev3m"]            # snapshot 91 days back at /1.02
    assert res["sector_fund"] and all("pe1" in v for v in res["sector_fund"].values())
    eur = [x for x in c2.fund if x and x.get("ccy") == "EUR"]
    assert all(x["pe1"] is None for x in eur)
    ctx = json.loads((scan.OUT / "context.json").read_text())
    assert any("guidance" in v for v in ctx.values()) and any("news" in v for v in ctx.values())
    print("   fundamentals:", f"{have:.0%} covered;", "sector", next(iter(res["sector_fund"].items())))
    # engine versioning: run id carries the version, older-style ids still parse, registry agrees
    assert res["engine"] == scan.ENGINE and res["run_id"].endswith(f"-v{scan.ENGINE}"), res["run_id"]
    reg = json.loads((ROOT / "engines.json").read_text())
    assert reg["current"] == scan.ENGINE, (reg["current"], scan.ENGINE)
    assert reg["versions"][0]["version"] == scan.ENGINE
    print("   engine:", res["engine"], "run_id:", res["run_id"])
    print("OK", TMP)
