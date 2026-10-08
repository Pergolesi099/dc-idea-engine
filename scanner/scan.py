#!/usr/bin/env python3
"""DC idea engine — weekly technical sweep of US stocks > $2bn.

Pipeline
  1. Universe + coarse technicals from TradingView's screener (one request).
     Fallback: cached universe (data/universe.csv) + metrics computed from price history.
  2. Signals S1/S2/S3 scored within each supersector, long and short side.
  3. Top pool per sector/side -> full daily history (Yahoo; Polygon for gaps if key set).
  4. Exact 21/50/200D distances, smoothness (R2, efficiency ratio, 21D crosses), RS vs sector ETF.
  5. Final rank = signal + smoothness; quota per sector/side; render charts; write out/results.json.
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
    "Drugstore Chains": "Consumer Staples", "Food Retail": "Consumer Staples",
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


def get_history(symbols: list[str], sessions: int) -> dict[str, pd.DataFrame]:
    hist = yahoo_history(symbols, sessions)
    missing = [s for s in symbols if s not in hist]
    if missing:
        log.info("Yahoo missed %d symbols; trying Polygon", len(missing))
        hist.update(polygon_history(missing, sessions))
    return hist


# ---------------------------------------------------------------- metrics
def metrics_from_history(h: pd.DataFrame, etf: pd.Series | None) -> dict:
    c = h.Close.astype(float)
    if len(c) < 64:
        return {}
    m = {
        "close": c.iloc[-1],
        "d21": (c.iloc[-1] / c.rolling(21).mean().iloc[-1] - 1) * 100,
        "d50": (c.iloc[-1] / c.rolling(50).mean().iloc[-1] - 1) * 100,
        "d200": (c.iloc[-1] / c.rolling(200).mean().iloc[-1] - 1) * 100 if len(c) >= 200 else np.nan,
        "p1w": (c.iloc[-1] / c.iloc[-6] - 1) * 100,
        "p1m": (c.iloc[-1] / c.iloc[-22] - 1) * 100,
        "p3m": (c.iloc[-1] / c.iloc[-64] - 1) * 100,
    }
    w = c.iloc[-63:]
    y = np.log(w.values)
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    m["r2_63"] = float(1 - resid.var() / y.var()) if y.var() > 0 else 0.0
    m["slope_63_ann"] = float(slope * 252 * 100)
    m["er_63"] = float(abs(w.iloc[-1] - w.iloc[0]) / w.diff().abs().sum())
    sma21 = c.rolling(21).mean().iloc[-63:]
    side = np.sign(w - sma21).replace(0, np.nan).ffill()
    m["sma21_crosses_63"] = int((side.diff().abs() > 0).sum())
    if etf is not None:
        rs = (c / etf.reindex(c.index).ffill()).dropna()
        if len(rs) > 22:
            m["rs_1m"] = (rs.iloc[-1] / rs.iloc[-22] - 1) * 100
            m["rs_3m"] = (rs.iloc[-1] / rs.iloc[-64] - 1) * 100 if len(rs) > 64 else np.nan
    return {k: (round(float(v), 3) if pd.notna(v) else None) for k, v in m.items()}


# ---------------------------------------------------------------- signals
def _pct_in_sector(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby("supersector")[col].rank(pct=True)


def score_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Adds long_score/short_score in [0,1] (within-sector percentile of best strategy hit) and tags."""
    s = CFG["strategies"]
    df = df.copy()
    for side in ("long", "short"):
        df[f"{side}_tags"] = [[] for _ in range(len(df))]
    scores = {"long": [], "short": []}

    def add(name, side, mask, raw):
        raw = raw.where(mask)
        if raw.notna().sum() == 0:
            return
        pct = raw.groupby(df.supersector).rank(pct=True)
        scores[side].append(pct.rename(name))
        for i in df.index[mask.fillna(False)]:
            df.at[i, f"{side}_tags"].append(name)

    hi_lo = df[["d50", "d200"]]
    if s["s1"]["enabled"]:
        up = (df.d21 > 0) & (df.d50 > 0) & (df.d200 > 0) & (df.d21 > hi_lo.max(axis=1))
        dn = (df.d21 < 0) & (df.d50 < 0) & (df.d200 < 0) & (df.d21 < hi_lo.min(axis=1))
        up_raw = df.d21 - hi_lo.max(axis=1)
        dn_raw = hi_lo.min(axis=1) - df.d21
        if s["s1"]["bonus_200_over_50"]:
            up_raw = up_raw + 0.5 * (df.d200 > df.d50)
            dn_raw = dn_raw + 0.5 * (df.d200 < df.d50)
        long_m, long_r, short_m, short_r = (up, up_raw, dn, dn_raw)
        if s["s1"]["mode"] == "fade":
            long_m, long_r, short_m, short_r = dn, dn_raw, up, up_raw
        add("S1", "long", long_m, long_r)
        add("S1", "short", short_m, short_r)
    if s["s2"]["enabled"]:
        p = _pct_in_sector(df, s["s2"]["perf_field"])
        add("S2", "long", (p >= s["s2"]["leader_pct"]) & (df.d21 < 0) & (df.d50 > 0), p)
        add("S2", "short", (p <= s["s2"]["laggard_pct"]) & (df.d21 > 0) & (df.d50 < 0), 1 - p)
    if s["s3"]["enabled"]:
        band = s["s3"]["band_pct"]
        near = df.d200.abs() <= band
        closeness = 1 - df.d200.abs() / band
        add("S3", "long", near & (df.p1m > 0), closeness)
        if s["s3"]["short_breakdowns"]:
            add("S3", "short", near & (df.p1m < 0), closeness)

    for side in ("long", "short"):
        df[f"{side}_score"] = pd.concat(scores[side], axis=1).max(axis=1) if scores[side] else np.nan
    return df


def smoothness_score(df: pd.DataFrame) -> pd.Series:
    raw = 0.5 * df.r2_63.fillna(0) + 0.5 * df.er_63.fillna(0) - 0.03 * df.sma21_crosses_63.fillna(10)
    return raw.groupby(df.supersector).rank(pct=True)


# ---------------------------------------------------------------- charts
def render_chart(sym: str, row: dict, h: pd.DataFrame, etf: pd.Series | None, path: pathlib.Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = CFG["charts"]["display_sessions"]
    c = h.Close
    smas = {21: c.rolling(21).mean(), 50: c.rolling(50).mean(), 200: c.rolling(200).mean()}
    v = h.iloc[-n:]
    x = np.arange(len(v))
    fig, (ax, axv, axr) = plt.subplots(3, 1, figsize=(11, 7), sharex=True,
                                      gridspec_kw={"height_ratios": [5, 1, 1.6]})
    up = v.Close >= v.Open
    col = np.where(up, "#1a9e5a", "#d0453b")
    ax.vlines(x, v.Low, v.High, color=col, linewidth=0.8)
    ax.bar(x, (v.Close - v.Open).abs().clip(lower=v.Close * 0.0008), bottom=np.minimum(v.Open, v.Close),
           color=col, width=0.7)
    for k, colr in ((21, "#1f6fd1"), (50, "#e08a00"), (200, "#7b3fbf")):
        ax.plot(x, smas[k].iloc[-n:].values, color=colr, linewidth=1.3, label=f"SMA{k}")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    tags = ", ".join(f"{t}" for t in row["tags"])
    ax.set_title(
        f"{sym}  {row['company'][:40]}  |  {row['supersector']}  |  {row['side'].upper()} [{tags}]\n"
        f"d21 {row['d21']:+.1f}%  d50 {row['d50']:+.1f}%  d200 {row['d200']:+.1f}%   "
        f"1W {row['p1w']:+.1f}%  1M {row['p1m']:+.1f}%  3M {row['p3m']:+.1f}%   "
        f"R² {row['r2_63']:.2f}  ER {row['er_63']:.2f}  x21 {int(row['sma21_crosses_63'])}",
        fontsize=9.5, loc="left")
    axv.bar(x, v.Volume, color=col, width=0.7, alpha=0.6)
    axv.set_yticks([])
    if etf is not None:
        rs = (c / etf.reindex(c.index).ffill()).iloc[-n:]
        rs = rs / rs.iloc[0] * 100
        axr.plot(x, rs.values, color="#333333", linewidth=1.2)
        axr.axhline(100, color="#999999", linewidth=0.6, linestyle="--")
        axr.set_ylabel(f"RS vs {SECTOR_ETF[row['supersector']]}", fontsize=8)
    axr.grid(alpha=0.25)
    ticks = np.linspace(0, len(v) - 1, 7).astype(int)
    axr.set_xticks(ticks)
    axr.set_xticklabels([v.index[i].strftime("%d %b") for i in ticks], fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


# ---------------------------------------------------------------- pipeline
def _clean(o):
    """JSON-safe: numpy scalars -> python, NaN -> None, floats rounded."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 4)
    return o


def run(universe_fn=tv_universe, history_fn=get_history) -> dict:
    import shutil
    shutil.rmtree(OUT / "charts", ignore_errors=True)
    (OUT / "charts").mkdir(parents=True)
    sel, ch = CFG["selection"], CFG["charts"]
    cache = ROOT / "data" / "universe.csv"
    etfs = list(SECTOR_ETF.values())

    try:
        uni = universe_fn()
        source = "tradingview"
        uni[["symbol", "company", "exchange", "sector", "industry", "supersector", "mcap"]].to_csv(
            OUT / "universe.csv", index=False)
        log.info("TradingView universe: %d names", len(uni))
    except Exception as e:  # noqa: BLE001
        log.warning("TradingView failed (%s); falling back to cached universe + history", e)
        if not cache.exists():
            raise SystemExit("No TradingView and no cached universe — cannot run.") from e
        base = pd.read_csv(cache)
        hist_all = history_fn(base.symbol.tolist() + etfs, ch["history_sessions"])
        rows = []
        for r in base.itertuples():
            h = hist_all.get(r.symbol)
            if h is not None:
                rows.append({**r._asdict(), **metrics_from_history(h, None)})
        uni = pd.DataFrame(rows).drop(columns=["Index"], errors="ignore").dropna(subset=["d200", "p3m"])
        source = "history-fallback"

    scored = score_signals(uni)

    pool = []
    for side in ("long", "short"):
        top = (scored.dropna(subset=[f"{side}_score"])
               .sort_values(f"{side}_score", ascending=False)
               .groupby("supersector").head(sel["pool_per_side_per_sector"]))
        pool.append(top.assign(side=side, signal_score=top[f"{side}_score"], tags=top[f"{side}_tags"]))
    pool = pd.concat(pool, ignore_index=True).drop_duplicates(["symbol", "side"])
    log.info("Candidate pool: %d (side-rows)", len(pool))

    hist = history_fn(sorted(set(pool.symbol)) + etfs, ch["history_sessions"])
    etf_close = {e: hist[e].Close for e in etfs if e in hist}

    exact = []
    for r in pool.to_dict("records"):
        h = hist.get(r["symbol"])
        if h is None:
            continue
        m = metrics_from_history(h, etf_close.get(SECTOR_ETF[r["supersector"]]))
        if not m:
            continue
        exact.append({**r, **m})   # history-based 21/50/200D overwrite TradingView's approximations
    cand = pd.DataFrame(exact)

    s3 = CFG["strategies"]["s3"]
    only_s3 = cand.tags.apply(lambda t: t == ["S3"])
    choppy = (cand.r2_63 < s3["min_r2"]) | (cand.sma21_crosses_63 > s3["max_sma21_crosses"])
    cand = cand[~(only_s3 & choppy)].copy()

    w = CFG["ranking"]
    cand["smooth_score"] = smoothness_score(cand)
    cand["final_score"] = w["weight_signal"] * cand.signal_score + w["weight_smoothness"] * cand.smooth_score
    final = (cand.sort_values("final_score", ascending=False)
             .groupby(["supersector", "side"]).head(sel["quota_per_side_per_sector"])
             .sort_values(["supersector", "side", "final_score"], ascending=[True, True, False]))

    records = []
    for r in final.to_dict("records"):
        sym = r["symbol"]
        fn = f"{sym.replace('.', '-')}_{r['side']}.png"
        try:
            render_chart(sym, r, hist[sym], etf_close.get(SECTOR_ETF[r["supersector"]]), OUT / "charts" / fn)
        except Exception as e:  # noqa: BLE001
            log.warning("chart %s failed: %s", sym, e)
            continue
        keep = ["symbol", "company", "exchange", "supersector", "industry", "side", "tags", "mcap", "close",
                "d21", "d50", "d200", "p1w", "p1m", "p3m", "r2_63", "er_63", "sma21_crosses_63",
                "slope_63_ann", "rs_1m", "rs_3m", "signal_score", "smooth_score", "final_score"]
        rec = {k: r.get(k) for k in keep}
        rec["chart"] = f"charts/{fn}"
        records.append(rec)

    result = {
        "run_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "universe_count": int(len(uni)),
        "pool_count": int(len(pool)),
        "candidate_count": len(records),
        "config": CFG,
        "candidates": records,
    }
    (OUT / "results.json").write_text(json.dumps(_clean(result), indent=1))
    log.info("Done: %d charts written", len(records))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run()
