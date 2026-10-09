"""Top-down chart: weekly (2Y) on the left, daily (1Y) on the right.

Left:  weekly candles + 10W/40W SMA; RS Line - Blue Dot vs S&P 500 (slope-coloured line, 40W MA,
       blue new-high / red new-low dots, crossover markers).
Right: daily candles + 21/50/200D SMA, 6M and 3M support/resistance, breakout markers (no volume);
       daily RS vs S&P 500 with its 200D MA (Thami Kabbaj's original setting), 52W-high/low dots and
       crossovers; MACD (12,26,9) histogram; stock vs sector ETF with 50D MA and sector-rotation
       shading (Leading / Improving / Weakening / Lagging).
"""
from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from features import SECTOR_STATES, rs_line_bluedot, sma, to_weekly  # noqa: E402

UP, DN = "#1a9e5a", "#d0453b"
QUAD_COL = {2.0: "#1a9e5a", 1.0: "#2f6fd1", -1.0: "#e0a100", -2.0: "#d0453b"}
plt.rcParams.update({"font.size": 8, "axes.titlesize": 9})


def _candles(ax, df: pd.DataFrame, width: float = 0.7) -> np.ndarray:
    x = np.arange(len(df))
    up = df.Close >= df.Open
    col = np.where(up, UP, DN)
    ax.vlines(x, df.Low, df.High, color=col, linewidth=0.7)
    body = (df.Close - df.Open).abs().clip(lower=df.Close * 0.0008)
    ax.bar(x, body, bottom=np.minimum(df.Open, df.Close), color=col, width=width)
    return x


def _dates(ax, idx: pd.DatetimeIndex, n: int = 6, fmt: str = "%b %y"):
    t = np.linspace(0, len(idx) - 1, n).astype(int)
    ax.set_xticks(t)
    ax.set_xticklabels([idx[i].strftime(fmt) for i in t])


def render(sym: str, row: dict, d: pd.DataFrame, spx: pd.Series, etf: pd.Series | None,
           path: pathlib.Path, cfg: dict) -> None:
    rc, sr = cfg["rs"], cfg["sector_rotation"]
    d = d.dropna(subset=["Close"])
    fig = plt.figure(figsize=(14, 9.2), dpi=100)
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.2], left=0.045, right=0.985, top=0.905, bottom=0.04, wspace=0.12)
    gl = gs[0].subgridspec(2, 1, height_ratios=[3, 1.6], hspace=0.05)
    gr = gs[1].subgridspec(4, 1, height_ratios=[2.6, 1.15, 0.85, 0.95], hspace=0.06)

    # ---------- weekly (2Y)
    w = to_weekly({k: d[[k]].rename(columns={k: sym}) for k in ("Open", "High", "Low", "Close", "Volume")})
    wk = pd.DataFrame({k: v[sym] for k, v in w.items()})
    s10, s40 = wk.Close.rolling(10).mean(), wk.Close.rolling(40).mean()
    spx_w = spx.resample("W-FRI").last().reindex(wk.index, method="ffill")
    r = rs_line_bluedot(wk[["Close"]], spx_w, rc["scale"], rc["ma_len"], rc["ma_type"], rc["hl_len"])
    r = {k: v["Close"] for k, v in r.items()}
    nW = cfg["charts"]["weekly_bars"]
    vw = wk.iloc[-nW:]
    ax = fig.add_subplot(gl[0])
    x = _candles(ax, vw, 0.6)
    ax.plot(x, s10.iloc[-nW:].values, color="#1f6fd1", lw=1.2, label="10W")
    ax.plot(x, s40.iloc[-nW:].values, color="#7b3fbf", lw=1.4, label="40W")
    ax.legend(loc="upper left", frameon=False)
    ax.set_title(f"Weekly · {row.get('regime') or '–'}", loc="left")
    ax.grid(alpha=0.2)
    ax.tick_params(labelbottom=False)
    axr = fig.add_subplot(gl[1], sharex=ax)
    rs, ma = r["rs"].iloc[-nW:].values, r["ma"].iloc[-nW:].values
    for i in range(1, len(rs)):
        axr.plot([i - 1, i], rs[i - 1:i + 1], color=UP if rs[i] >= rs[i - 1] else DN, lw=1.6)
    axr.plot(x, ma, color="#222222", lw=1.4)
    nh, nl = r["nh"].iloc[-nW:].values, r["nl"].iloc[-nW:].values
    axr.scatter(x[nh], rs[nh], s=28, color="#2f6fd1", alpha=0.55, zorder=3)
    axr.scatter(x[nl], rs[nl], s=28, color=DN, alpha=0.55, zorder=3)
    cu, cd = r["cross_up"].iloc[-nW:].values, r["cross_dn"].iloc[-nW:].values
    axr.scatter(x[cu], rs[cu], marker="^", s=40, color=UP, zorder=4)
    axr.scatter(x[cd], rs[cd], marker="v", s=40, color=DN, zorder=4)
    axr.set_ylabel("RS vs S&P · 40W MA", fontsize=8)
    axr.grid(alpha=0.2)
    _dates(axr, vw.index)

    # ---------- daily (1Y)
    nD = cfg["charts"]["daily_bars"]
    vd = d.iloc[-nD:]
    axd = fig.add_subplot(gr[0])
    xd = _candles(axd, vd)
    for n, col in ((21, "#1f6fd1"), (50, "#e08a00"), (200, "#7b3fbf")):
        axd.plot(xd, d.Close.rolling(n).mean().iloc[-nD:].values, color=col, lw=1.1, label=f"{n}D")
    excl = cfg["triggers"]["breakout_exclude_days"]
    for n, ls, a in ((126, "--", 0.9), (63, ":", 0.8)):
        res = d.High.rolling(n).max().shift(excl).iloc[-1]
        sup = d.Low.rolling(n).min().shift(excl).iloc[-1]
        lab = "6M" if n == 126 else "3M"
        for lvl, c in ((res, DN), (sup, UP)):
            if np.isfinite(lvl):
                axd.axhline(lvl, color=c, ls=ls, lw=0.9, alpha=a)
                axd.text(len(vd) - 1, lvl, f" {lab} {lvl:.2f}", color=c, va="bottom", ha="right", fontsize=7)
    bo = row.get("breakout") or 0
    if bo:
        axd.scatter([len(vd) - 1], [vd.Close.iloc[-1]], marker="^" if bo > 0 else "v", s=80,
                    color=UP if bo > 0 else DN, zorder=5)
    lo, hi = vd.Low.min(), vd.High.max()
    axd.set_ylim(lo - (hi - lo) * 0.10, hi + (hi - lo) * 0.05)
    axd.legend(loc="lower left", frameon=False, ncol=3)
    axd.set_title("Daily · 1Y", loc="left")
    axd.grid(alpha=0.2)
    axd.tick_params(labelbottom=False)

    # daily RS vs S&P with its 200D MA (Kabbaj); 52W new-high / new-low dots; crossovers
    axq = fig.add_subplot(gr[1], sharex=axd)
    dl = cfg["charts"].get("daily_rs_ma", 200)
    rq = rs_line_bluedot(d[["Close"]], spx.reindex(d.index).ffill(), rc["scale"], dl, rc["ma_type"], 252)
    rq = {k: v["Close"].iloc[-nD:].values for k, v in rq.items()}
    rsq, maq = rq["rs"], rq["ma"]
    for i in range(1, len(rsq)):
        axq.plot([i - 1, i], rsq[i - 1:i + 1], color=UP if rsq[i] >= rsq[i - 1] else DN, lw=1.2)
    axq.plot(xd, maq, color="#222222", lw=1.3, label=f"{dl}D MA")
    hq, lq = rq["nh"].astype(bool), rq["nl"].astype(bool)
    axq.scatter(xd[hq], rsq[hq], s=16, color="#2f6fd1", alpha=0.55, zorder=3)
    axq.scatter(xd[lq], rsq[lq], s=16, color=DN, alpha=0.55, zorder=3)
    cuq, cdq = rq["cross_up"].astype(bool), rq["cross_dn"].astype(bool)
    axq.scatter(xd[cuq], rsq[cuq], marker="^", s=40, color=UP, zorder=4)
    axq.scatter(xd[cdq], rsq[cdq], marker="v", s=40, color=DN, zorder=4)
    axq.set_ylabel(f"RS vs S&P · {dl}D", fontsize=8)
    axq.legend(loc="upper left", frameon=False, fontsize=7)
    axq.grid(alpha=0.2)
    axq.tick_params(labelbottom=False)

    # MACD (12,26,9) histogram: dark = growing, light = fading
    axm = fig.add_subplot(gr[2], sharex=axd)
    m = d.Close.ewm(span=12, adjust=False).mean() - d.Close.ewm(span=26, adjust=False).mean()
    sig = m.ewm(span=9, adjust=False).mean()
    hst = (m - sig).iloc[-nD:].values
    rising = np.r_[False, hst[1:] > hst[:-1]]
    hc = np.where(hst >= 0, np.where(rising, UP, "#8fd3b0"), np.where(rising, "#f0a49c", DN))
    axm.bar(xd, hst, color=hc, width=0.8)
    axm.axhline(0, color="#999999", lw=0.5)
    axm.set_ylabel("MACD hist", fontsize=8)
    axm.grid(alpha=0.2)
    axm.tick_params(labelbottom=False)

    axs = fig.add_subplot(gr[3], sharex=axd)
    if etf is not None:
        rsd = d.Close / etf.reindex(d.index).ffill()
        ratio = (rsd.ewm(span=sr["smooth_len"], adjust=False, min_periods=sr["smooth_len"]).mean()
                 / rsd.rolling(sr["trend_len"]).mean() - 1) * 100
        mom = ratio - ratio.shift(sr["mom_len"])
        q = np.select([(ratio >= 0) & (mom >= 0), (ratio < 0) & (mom >= 0), (ratio >= 0) & (mom < 0),
                       (ratio < 0) & (mom < 0)], [2.0, 1.0, -1.0, -2.0], np.nan)
        q = pd.Series(q, index=d.index).iloc[-nD:].values
        base = rsd.iloc[-nD:]
        line = base / base.iloc[0] * 100
        lma = (rsd.rolling(sr["trend_len"]).mean().iloc[-nD:] / base.iloc[0] * 100)
        for i, qi in enumerate(q):
            if np.isfinite(qi):
                axs.axvspan(i - 0.5, i + 0.5, color=QUAD_COL[qi], alpha=0.13, lw=0)
        axs.plot(xd, line.values, color="#222222", lw=1.2)
        axs.plot(xd, lma.values, color="#7b3fbf", lw=0.9, ls="--")
        state = SECTOR_STATES.get(q[-1], "–") if len(q) else "–"
        axs.set_ylabel(f"vs {row.get('sector_etf', 'sector')}", fontsize=8)
        axs.text(0.005, 0.92, f"Sector: {state}", transform=axs.transAxes, va="top", fontsize=8,
                 color=QUAD_COL.get(q[-1], "#222222") if len(q) else "#222222", fontweight="bold")
    axs.grid(alpha=0.2)
    _dates(axs, vd.index, 7, "%d %b")

    # ---------- header
    tags = ", ".join(row.get("tags") or [])
    sec = SECTOR_STATES.get(row.get("sec_quad"), "–")
    turn = {1.0: " (new)", -1.0: " (new)"}.get(row.get("sec_turn"), "")
    lead = {1.0: "  ·  RS leads price ↑", -1.0: "  ·  RS leads price ↓"}.get(row.get("rs_lead"), "")
    def f(k, n=1, sign=True):
        v = row.get(k)
        return "–" if v is None or not np.isfinite(v) else (f"{v:+.{n}f}" if sign else f"{v:.{n}f}")
    fig.text(0.045, 0.972, f"{sym}  {str(row.get('company', ''))[:26]}  |  {row.get('supersector')}  |  "
             f"{row['side'].upper()} [{tags}]  |  Sector: {sec}{turn}{lead}", fontsize=10, fontweight="bold")
    fig.text(0.045, 0.946, f"d21 {f('d21')}%  d50 {f('d50')}%  d200 {f('d200')}%   1W {f('p1w')}%  1M {f('p1m')}%  "
             f"3M {f('p3m')}%  6M {f('p6m')}%   RS/MA {f('rs_vs_ma')}%  RSsec/MA {f('rssec_vs_ma')}%   "
             f"R² {f('r2_63', 2, False)}  vol {f('vol63', 0, False)}%   score {f('final_score', 2, False)}", fontsize=9)
    fig.savefig(path, dpi=100)
    plt.close(fig)
