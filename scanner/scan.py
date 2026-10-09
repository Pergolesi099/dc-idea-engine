#!/usr/bin/env python3
"""DC idea engine — weekly top-down technical sweep of US stocks > $2bn.

Pipeline
  1. Universe from TradingView's screener (symbols, sectors, market caps). Fallback: cached universe.
  2. ~3 years of daily history for the whole universe (Yahoo; Polygon for gaps), S&P 500, sector ETFs.
  3. Feature panels for every name and date (features.py): weekly trend + RS Line - Blue Dot,
     sector rotation, daily setups (S1/S2/S3/MOM), 6M/3M breakouts, MACD histogram.
  4. Learning (learn.py): outcome model on years of weekly history + Dean's desk feedback.
  5. Today: weekly gate -> setups/triggers -> learned score -> quota per sector/side -> charts.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
import sys
import time

import numpy as np
import pandas as pd
import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
OUT = ROOT / "out"
ENGINE = str((CFG.get("engine") or {}).get("version", "2.0"))
CACHE = ROOT / "data" / "universe.csv"
log = logging.getLogger("scan")

# ---------------------------------------------------------------- sector mapping
SECTOR_ETF = {
    "Technology": "XLK", "Communication": "XLC", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Health Care": "XLV", "Financials": "XLF",
    "Real Estate": "XLRE", "Industrials": "XLI", "Materials": "XLB",
    "Energy": "XLE", "Utilities": "XLU",
}
TV_SECTOR = {
    "Electronic Technology": "Technology", "Technology Services": "Technology",
    "Communications": "Communication",
    "Retail Trade": "Consumer Discretionary", "Consumer Durables": "Consumer Discretionary",
    "Consumer Services": "Consumer Discretionary",
    "Consumer Non-Durables": "Consumer Staples",
    "Health Technology": "Health Care", "Health Services": "Health Care",
    "Finance": "Financials",
    "Producer Manufacturing": "Industrials", "Industrial Services": "Industrials",
    "Transportation": "Industrials", "Commercial Services": "Industrials",
    "Distribution Services": "Industrials",
    "Non-Energy Minerals": "Materials", "Process Industries": "Materials",
    "Energy Minerals": "Energy", "Utilities": "Utilities",
}
# Industry overrides to get closer to GICS where TradingView's taxonomy diverges.
TV_INDUSTRY = {
    "Internet Software/Services": "Communication",
    "Cable/Satellite TV": "Communication", "Movies/Entertainment": "Communication",
    "Broadcasting": "Communication", "Publishing: Newspapers": "Communication",
    "Real Estate Investment Trusts": "Real Estate", "Real Estate Development": "Real Estate",
    "Drugstore Chains": "Health Care", "Food Retail": "Consumer Staples",
    "Apparel/Footwear": "Consumer Discretionary",
    "Food Distributors": "Consumer Staples",
    "Medical Distributors": "Health Care",
    "Electronics Distributors": "Technology",
    "Oilfield Services/Equipment": "Energy", "Oil Refining/Marketing": "Energy",
    "Contract Drilling": "Energy", "Oil & Gas Pipelines": "Energy",
}


def supersector(sector: str, industry: str) -> str | None:
    return TV_INDUSTRY.get(industry) or TV_SECTOR.get(sector)


def yahoo_symbol(s: str) -> str:
    return s.replace(".", "-").replace("/", "-")


# ---------------------------------------------------------------- data sources
def tv_universe() -> pd.DataFrame:
    """Whole US market in one call: price, SMA20/50/200, perf W/1M/3M, mcap, sector."""
    u = CFG["universe"]
    cols = ["name", "description", "close", "SMA20", "SMA50", "SMA200",
            "Perf.W", "Perf.1M", "Perf.3M", "market_cap_basic", "sector",
            "industry", "average_volume_30d_calc", "exchange"]
    base_filter = [
        {"left": "market_cap_basic", "operation": "greater", "right": u["min_market_cap"]},
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "exchange", "operation": "in_range", "right": u["exchanges"]},
    ]
    for extra in ([{"left": "is_primary", "operation": "equal", "right": True}], []):
        body = {"markets": ["america"], "options": {"lang": "en"},
                "filter": base_filter + extra, "columns": cols,
                "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
                "range": [0, 6000]}
        r = requests.post("https://scanner.tradingview.com/america/scan", json=body, timeout=30,
                          headers={"User-Agent": "Mozilla/5.0", "Origin": "https://www.tradingview.com"})
        if r.ok:
            break
        log.warning("TradingView %s: %s", r.status_code, r.text[:200])
    r.raise_for_status()
    rows = [dict(zip(cols, x["d"])) for x in r.json()["data"]]
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "name": "symbol", "description": "company", "Perf.W": "p1w", "Perf.1M": "p1m",
        "Perf.3M": "p3m", "market_cap_basic": "mcap", "average_volume_30d_calc": "avg_vol"})
    df["d21"] = (df.close / df.SMA20 - 1) * 100      # TradingView only offers 20D; refined to 21D later
    df["d50"] = (df.close / df.SMA50 - 1) * 100
    df["d200"] = (df.close / df.SMA200 - 1) * 100
    df["supersector"] = [supersector(s, i) for s, i in zip(df.sector, df.industry)]
    df = df[df.supersector.notna() & (df.avg_vol >= u["min_avg_volume"])]
    df = df.dropna(subset=["d21", "d50", "d200", "p3m"]).drop_duplicates("symbol")
    return df[["symbol", "company", "exchange", "sector", "industry", "supersector", "mcap",
               "avg_vol", "close", "d21", "d50", "d200", "p1w", "p1m", "p3m"]].reset_index(drop=True)


def yahoo_history(symbols: list[str], sessions: int) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=int(sessions * 1.5) + 10)
    out: dict[str, pd.DataFrame] = {}
    ysyms = {yahoo_symbol(s): s for s in symbols}
    keys = list(ysyms)
    for i in range(0, len(keys), 150):
        chunk = keys[i:i + 150]
        for attempt in range(3):
            try:
                raw = yf.download(chunk, start=start, end=end, auto_adjust=True, group_by="ticker",
                                  threads=True, progress=False)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("yfinance chunk %d attempt %d: %s", i, attempt, e)
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for ys in chunk:
            try:
                sub = raw[ys] if isinstance(raw.columns, pd.MultiIndex) else raw
                sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(sub) > 60:
                    out[ysyms[ys]] = sub
            except KeyError:
                pass
    return out


def polygon_history(symbols: list[str], sessions: int, max_calls: int = 40) -> dict[str, pd.DataFrame]:
    """Gap-filler. Free tier = 5 calls/min, so capped."""
    key = os.environ.get("POLYGON_API_KEY")
    if not key or not symbols:
        return {}
    end = dt.date.today()
    start = end - dt.timedelta(days=int(sessions * 1.5) + 10)
    out = {}
    for n, s in enumerate(symbols[:max_calls]):
        if n and n % 5 == 0:
            time.sleep(61)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{s}/range/1/day/{start}/{end}"
               f"?adjusted=true&sort=asc&limit=50000&apiKey={key}")
        try:
            res = requests.get(url, timeout=30).json().get("results") or []
        except Exception as e:  # noqa: BLE001
            log.warning("polygon %s: %s", s, e)
            continue
        if len(res) > 60:
            df = pd.DataFrame(res)
            df.index = pd.to_datetime(df.t, unit="ms").dt.normalize()
            out[s] = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})[
                ["Open", "High", "Low", "Close", "Volume"]]
    return out


def next_earnings(symbols: list[str]) -> dict[str, str | None]:
    """Next earnings date per symbol (Yahoo). Best effort; None when unknown."""
    out: dict[str, str | None] = {}
    try:
        import yfinance as yf
    except ImportError:
        return out
    today = dt.date.today()
    for s in symbols:
        d = None
        try:
            cal = yf.Ticker(yahoo_symbol(s)).calendar or {}
            dates = cal.get("Earnings Date") or []
            future = sorted(x for x in dates if isinstance(x, dt.date) and x >= today)
            d = future[0].isoformat() if future else None
        except Exception as e:  # noqa: BLE001
            log.debug("earnings %s: %s", s, e)
        out[s] = d
    log.info("Earnings dates: %d/%d known", sum(v is not None for v in out.values()), len(symbols))
    return out


def get_history(symbols: list[str], sessions: int) -> dict[str, pd.DataFrame]:
    hist = yahoo_history(symbols, sessions)
    missing = [s for s in symbols if s not in hist]
    if missing:
        log.info("Yahoo missed %d symbols; trying Polygon", len(missing))
        hist.update(polygon_history(missing, sessions))
    return hist



# ---------------------------------------------------------------- panels
BENCH = "^GSPC"


def build_panels(hist: dict[str, pd.DataFrame], meta: pd.DataFrame):
    """Wide date x symbol panels, S&P 500 close, each symbol's sector-ETF close panel."""
    spx = hist.get(BENCH, hist.get("SPY"))
    if spx is None:
        raise SystemExit("No S&P 500 history (neither ^GSPC nor SPY) — cannot compute RS.")
    idx = spx.index
    syms = [s for s in meta.index if s in hist]
    daily = {k: pd.DataFrame({s: hist[s][k] for s in syms}).reindex(idx).astype("float64")
             for k in ("Open", "High", "Low", "Close", "Volume")}
    etf_close = {e: hist[e].Close.reindex(idx).ffill() for e in SECTOR_ETF.values() if e in hist}
    sector_bench = pd.DataFrame({s: etf_close.get(SECTOR_ETF[meta.at[s, "supersector"]]) for s in syms},
                                index=idx)
    return daily, spx.Close, sector_bench, etf_close


def forward_relative(close: pd.DataFrame, bench: pd.DataFrame, n: int) -> pd.DataFrame:
    return ((close.shift(-n) / close) / (bench.shift(-n) / bench) - 1) * 100


def track_record(close: pd.DataFrame, today: pd.Timestamp) -> list[dict]:
    """Realised 5/10-session pair returns for every pair published on past runs."""
    out = []
    dec = {}
    p = ROOT / "labels" / "decisions.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                dec[(r.get("run_id"), r.get("long"), r.get("short"))] = r.get("action")
    for pf in sorted((ROOT / "history" / "review").glob("*/pairs.json")):
        run = pf.parent.name
        try:
            t0 = close.index[close.index <= pd.Timestamp(run[:10])][-1]
        except IndexError:
            continue
        i0 = close.index.get_loc(t0)
        for pr in json.loads(pf.read_text()).get("pairs", []):
            lo, sh = pr.get("long"), pr.get("short")
            if lo not in close or sh not in close:
                continue
            row = {"run": run, "long": lo, "short": sh, "conviction": pr.get("conviction"),
                   "decision": dec.get((run, lo, sh)) or "none"}
            sl = (pr.get("split_long") or 50) / 100
            for n in (5, 10):
                if i0 + n < len(close.index) and close.index[i0 + n] <= today:
                    rl = close[lo].iloc[i0 + n] / close[lo].iloc[i0] - 1
                    rsh = close[sh].iloc[i0 + n] / close[sh].iloc[i0] - 1
                    row[f"ret{n}"] = round(float((sl * rl - (1 - sl) * rsh) * 100), 2)
            out.append(row)
    return out


# ---------------------------------------------------------------- pipeline
def _clean(o):
    """JSON-safe: numpy scalars -> python, NaN -> None, floats rounded."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 4)
    return o


def run(universe_fn=tv_universe, history_fn=get_history, learn_enabled: bool = True) -> dict:
    import shutil

    import learn
    from charts import render
    from features import SECTOR_STATES, compute_panels, snapshot, to_weekly
    from model import load_weights, score, select, side_table

    shutil.rmtree(OUT / "charts", ignore_errors=True)
    (OUT / "charts").mkdir(parents=True)
    # run id = scan date + engine version (+ -r2, -r3 for repeat runs the same day; the workflow sets it)
    run_id = os.environ.get("DC_RUN_ID") or f"{dt.datetime.now(dt.timezone.utc).date().isoformat()}-v{ENGINE}"

    # 1. universe
    try:
        meta = universe_fn()
        source = "tradingview"
        OUT.mkdir(exist_ok=True)
        meta[["symbol", "company", "exchange", "sector", "industry", "supersector", "mcap"]].to_csv(
            OUT / "universe.csv", index=False)
    except Exception as e:  # noqa: BLE001
        log.warning("TradingView failed (%s); using cached universe", e)
        if not CACHE.exists():
            raise SystemExit("No TradingView and no cached universe — cannot run.") from e
        meta = pd.read_csv(CACHE)
        source = "cached-universe"
    meta = meta.drop_duplicates("symbol").set_index("symbol")[
        ["company", "exchange", "sector", "industry", "supersector", "mcap"]]
    log.info("Universe: %d names (%s)", len(meta), source)

    # 2. full-universe history
    etfs = list(SECTOR_ETF.values())
    hist = history_fn(list(meta.index) + etfs + [BENCH, "SPY"], CFG["data"]["history_sessions"])
    daily, spx, sector_bench, etf_close = build_panels(hist, meta)
    meta = meta.loc[daily["Close"].columns]
    log.info("History: %d/%d symbols, %d sessions", daily["Close"].shape[1], len(meta), len(daily["Close"]))

    # 3. features (all dates, all names)
    panels = compute_panels(daily, spx, sector_bench, CFG)
    panels["sessions"] = daily["Close"].notna().cumsum().astype("float32")
    today = daily["Close"].index[-1]

    # 4. learning
    weights, pref_model, report = load_weights(ROOT / "model"), None, {}
    if learn_enabled:
        fwd = {h: forward_relative(daily["Close"], sector_bench, h) for h in (5, 10)}
        week_ends = to_weekly({"Close": daily["Close"][[daily["Close"].columns[0]]]})["Close"].index
        warm = daily["Close"].index[min(len(daily["Close"]) - 1, 300)]
        last_ok = daily["Close"].index[-11] if len(daily["Close"]) > 11 else daily["Close"].index[0]
        dates = [d for d in week_ends if warm <= d <= last_ok]
        weights, pref_model, report = learn.run(ROOT, panels, meta, fwd, dates, CFG)

    # 4b. backfill feature snapshots for desk runs that predate v2 (so Dean's labels on them count)
    labelled = set()
    for name in ("decisions", "chart_feedback"):
        lp = ROOT / "labels" / f"{name}.jsonl"
        if lp.exists():
            labelled |= {json.loads(x).get("run_id") for x in lp.read_text().splitlines() if x.strip()}
    feat_dir = ROOT / "history" / "features"
    feat_dir.mkdir(parents=True, exist_ok=True)
    for rid in sorted(x for x in labelled if x and not (feat_dir / f"{x}.csv.gz").exists()):
        past = daily["Close"].index[daily["Close"].index <= pd.Timestamp(rid[:10])]
        if not len(past):
            continue
        ps = snapshot(panels, past[-1]).join(meta, how="inner")
        rows = [side_table(ps, s, CFG, gated=False).assign(side=s) for s in ("long", "short")]
        bf = pd.concat(rows).reset_index()
        bf[["symbol", "side", "supersector", "regime"] + [c for c in bf.columns if c.startswith("z_")]].to_csv(
            feat_dir / f"{rid}.csv.gz", index=False, float_format="%.4f")
        log.info("Backfilled features for desk run %s (as of %s)", rid, past[-1].date())
    if labelled and learn_enabled:
        pref_model, pref = learn.fit_preference(ROOT, CFG)   # refit now that backfills exist
        report["preference"] = pref
        (ROOT / "model" / "report.json").write_text(json.dumps(report, indent=1, default=float))
        (ROOT / "model" / "feedback_summary.md").write_text(learn.feedback_summary(ROOT, report, pref))

    # 5. today's ranking
    snap = snapshot(panels, today).join(meta, how="inner")
    beta = (report.get("preference") or {}).get("beta", 0.0) if pref_model is not None else 0.0
    tabs = []
    for side in ("long", "short"):
        tab = side_table(snap, side, CFG)
        if tab.empty:
            continue
        tab["outcome_score"] = score(tab, weights[side])
        tab["pref_score"] = learn.preference_score(pref_model, tab) if pref_model is not None else np.nan
        po = tab.groupby("supersector").outcome_score.rank(pct=True)
        pp = tab.groupby("supersector").pref_score.rank(pct=True) if pref_model is not None else po
        tab["final_score"] = (1 - beta) * po + beta * pp
        tabs.append(tab)
    allc = pd.concat(tabs).reset_index()
    # store every eligible row's features: the preference model joins Dean's labels to these later
    keep = ["symbol", "side", "supersector", "regime", "outcome_score", "pref_score", "final_score"] + \
        [c for c in allc.columns if c.startswith("z_")]
    fe = allc[keep].copy()
    fe.to_csv(feat_dir / f"{run_id}.csv.gz", index=False, float_format="%.4f")

    best = allc.sort_values("final_score", ascending=False).drop_duplicates("symbol")
    quota = CFG["selection"]["quota_per_side_per_sector"]
    final = select(best, quota).sort_values(["supersector", "side", "final_score"], ascending=[True, True, False])
    log.info("Eligible: %d long, %d short; selected %d",
             (allc.side == "long").sum(), (allc.side == "short").sum(), len(final))

    earn = next_earnings(sorted(set(final.symbol)))
    records = []
    for r in final.to_dict("records"):
        sym = r["symbol"]
        raw = snap.loc[sym].to_dict()
        etf = SECTOR_ETF[r["supersector"]]
        row = {**raw, **{k: r[k] for k in ("side", "regime", "tags", "final_score", "outcome_score", "pref_score")},
               "symbol": sym, "sector_etf": etf}
        fn = f"{sym.replace('.', '-')}_{r['side']}.png"
        try:
            d = pd.DataFrame({k: daily[k][sym] for k in ("Open", "High", "Low", "Close", "Volume")})
            render(sym, row, d, spx, etf_close.get(etf), OUT / "charts" / fn, CFG)
        except Exception as e:  # noqa: BLE001
            log.warning("chart %s failed: %s", sym, e)
            continue
        records.append({
            "symbol": sym, "company": raw["company"], "exchange": raw["exchange"], "supersector": r["supersector"],
            "industry": raw["industry"], "side": r["side"], "tags": r["tags"], "regime": r["regime"],
            "family": r["family"],
            "mcap": raw["mcap"], "close": float(daily["Close"][sym].iloc[-1]),
            "sector_state": SECTOR_STATES.get(raw.get("sec_quad")), "sector_turn": raw.get("sec_turn"),
            "rs_lead": raw.get("rs_lead"), "rs_cross": raw.get("rs_cross"), "rs_hl": raw.get("rs_hl"),
            "breakout": raw.get("breakout"), "macd_sig": raw.get("macd_sig"),
            **{k: raw.get(k) for k in ("d21", "d50", "d200", "p1w", "p1m", "p3m", "p6m", "r2_63", "er_63",
                                       "x21_63", "vol63", "rs_vs_ma", "rssec_vs_ma", "rssec_1m", "rssec_3m",
                                       "w_d40", "w_slope40", "sec_ratio", "sec_mom", "res126", "sup126",
                                       "headroom_long", "headroom_short", "sessions")},
            "next_earnings": earn.get(sym),
            "outcome_score": r["outcome_score"], "pref_score": r["pref_score"], "final_score": r["final_score"],
            "chart": f"charts/{fn}",
        })

    rets = {}
    for s in sorted(set(final.symbol)) + [e for e in etf_close]:
        src = daily["Close"][s] if s in daily["Close"] else etf_close[s]
        rets[s] = np.log(src).diff().iloc[-126:]
    pd.DataFrame(rets).round(6).to_csv(OUT / "returns.csv")

    w_sides = {s: dict(sorted(((k, v) for k, v in weights[s].items()), key=lambda kv: -abs(kv[1]))[:12])
               for s in ("long", "short")}
    result = {
        "run_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id, "engine": ENGINE,
        "trigger": os.environ.get("DC_TRIGGER", "schedule"), "cycle_id": os.environ.get("DC_CYCLE_ID") or None,
        "data_through": str(today.date()), "source": source,
        "universe_count": int(len(meta)),
        "eligible": {"long": int((allc.side == "long").sum()), "short": int((allc.side == "short").sum())},
        "candidate_count": len(records),
        "regime_counts": snap.assign(reg=__import__("features").regime(snap)).reg.value_counts().to_dict(),
        "model": {"weights_source": weights.get("source", "prior"), "top_weights": w_sides,
                  "report": {k: v for k, v in report.items() if k != "sides"},
                  "sides": {s: {k: v for k, v in r.items() if k != "feature_ic"}
                            | {"top_feature_ic": dict(list(r["feature_ic"].items())[:8])}
                            for s, r in report.get("sides", {}).items()}},
        "track_record": track_record(daily["Close"], today),
        "config": CFG,
        "candidates": records,
    }
    (OUT / "results.json").write_text(json.dumps(_clean(result), indent=1))
    log.info("Done: %d charts written", len(records))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    run(learn_enabled=os.environ.get("DC_LEARN", "1") != "0")
