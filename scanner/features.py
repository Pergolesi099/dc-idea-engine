"""Vectorised feature engine: every indicator is computed for the whole universe at once, as
date x symbol panels, so the same code serves today's scan and the historical backfill the
learner trains on.

Top-down order, matching Dean's manual read:
  1. Weekly (long timeframe): 10W/40W trend, RS Line - Blue Dot (vs S&P 500), RS vs sector ETF,
     RS MA crossovers that lead the price 40W cross, RS new highs/lows.
  2. Daily setups: S1/S2/S3/MOM distances and momentum (existing methodology).
  3. Daily triggers (6M / 3M window): support/resistance breakouts with volume, MACD histogram
     flips and turns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

F32 = np.float32


# ----------------------------------------------------------------- primitives
def sma(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def bars_since(cond: pd.DataFrame) -> pd.DataFrame:
    """Bars since `cond` was last True (0 = true on this bar); NaN if never."""
    c = cond.fillna(False).to_numpy(dtype=bool)
    idx = np.arange(c.shape[0], dtype=float)[:, None]
    last = pd.DataFrame(np.where(c, idx, np.nan)).ffill().to_numpy()
    return pd.DataFrame(idx - last, index=cond.index, columns=cond.columns)


def crossover(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Pine ta.crossover: a > b now and a[1] <= b[1]."""
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return (a < b) & (a.shift(1) >= b.shift(1))


def to_weekly(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Weekly bars ending Friday, indexed by the week's actual last trading date (no look-ahead)."""
    c = panel["Close"]
    last_day = c.index.to_series().resample("W-FRI").last().dropna()
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = {}
    for k, how in agg.items():
        if k in panel:
            w = panel[k].resample("W-FRI").agg(how).loc[last_day.index]
            w.index = pd.DatetimeIndex(last_day.values)
            out[k] = w
    return out


# ----------------------------------------------------------------- RS Line - Blue Dot (Pine port)
def rs_line_bluedot(close: pd.DataFrame, bench: pd.DataFrame | pd.Series, scale: float = 100.0,
                    ma_len: int = 40, ma_type: str = "SMA", hl_len: int = 52) -> dict[str, pd.DataFrame]:
    """Line-for-line port of Dean's "RS Line - Blue Dot" Pine v6 indicator.

    rs = close / bench * scale; ma = SMA|EMA(rs, maLen); nh = rs >= highest(rs, hlLen);
    nl = rs <= lowest(rs, hlLen); alerts on crossover/crossunder(rs, ma) and first bar of nh/nl.
    Defaults are the script's: SPX benchmark, 40-bar MA (40W ~ 200D on a weekly chart), 52-bar dots.
    """
    if isinstance(bench, pd.Series):
        rs = close.div(bench, axis=0) * scale
    else:
        rs = close / bench * scale
    ma = sma(rs, ma_len) if ma_type == "SMA" else ema(rs, ma_len)
    hi = rs.rolling(hl_len, min_periods=hl_len).max()
    lo = rs.rolling(hl_len, min_periods=hl_len).min()
    nh, nl = rs >= hi, rs <= lo
    return {
        "rs": rs, "ma": ma, "nh": nh, "nl": nl,
        "is_up": rs >= rs.shift(1),                      # colMode "Slope" (script default)
        "cross_up": crossover(rs, ma), "cross_dn": crossunder(rs, ma),
        "nh_alert": nh & ~nh.shift(1, fill_value=False), "nl_alert": nl & ~nl.shift(1, fill_value=False),
    }


# ----------------------------------------------------------------- feature panels
DIRECTIONAL = [   # sign flips for shorts (higher = better for a long)
    "w_d40", "w_slope40", "w_10gt40", "rs_vs_ma", "rs_cross", "rs_hl", "rs_lead",
    "rssec_vs_ma", "rssec_cross", "rssec_hl", "rssec_1m", "rssec_3m",
    "p1w", "p1m", "p3m", "p6m", "d21", "d50", "d200", "breakout", "macd_sig", "headroom",
    "sec_ratio", "sec_mom", "sec_quad", "sec_turn",
]
SECTOR_STATES = {2.0: "Leading", 1.0: "Improving", -1.0: "Weakening", -2.0: "Lagging"}
NONDIRECTIONAL = ["r2_63", "er_63", "x21_63", "vol63", "ext21", "base_tight"]


def compute_panels(daily: dict[str, pd.DataFrame], spx_close: pd.Series, sector_bench: pd.DataFrame,
                   cfg: dict) -> dict[str, pd.DataFrame]:
    """All features as daily-aligned panels (date x symbol, float32).

    daily: {"Open","High","Low","Close","Volume"} panels; spx_close: S&P 500 close;
    sector_bench: panel of each symbol's sector-ETF close (same shape as Close).
    Weekly features are forward-filled onto daily dates (a mid-week date sees last completed week).
    """
    rc = cfg["rs"]
    c, h, l, v = daily["Close"], daily["High"], daily["Low"], daily["Volume"]
    out: dict[str, pd.DataFrame] = {}

    # ---------- weekly layer
    w = to_weekly(daily)
    cw = w["Close"]
    spx_w = spx_close.resample("W-FRI").last().reindex(cw.index, method="ffill")
    sec_w = to_weekly({"Close": sector_bench})["Close"].reindex(cw.index)
    s10, s40 = sma(cw, 10), sma(cw, 40)
    wk = {
        "w_d10": (cw / s10 - 1) * 100,
        "w_d40": (cw / s40 - 1) * 100,
        "w_slope40": (s40 / s40.shift(8) - 1) * 100,
        "w_10gt40": (s10 > s40).astype(F32).where(s40.notna()),
    }
    lead_win, hl_win = rc["cross_lookback_weeks"], rc["dot_lookback_weeks"]
    near, far = rc["lead_price_near_pct"], rc["lead_price_far_pct"]
    for tag, bench in (("rs", spx_w), ("rssec", sec_w)):
        r = rs_line_bluedot(cw, bench, rc["scale"], rc["ma_len"], rc["ma_type"], rc["hl_len"])
        bs_up, bs_dn = bars_since(r["cross_up"]), bars_since(r["cross_dn"])
        above = r["rs"] > r["ma"]
        cross = pd.DataFrame(0.0, index=cw.index, columns=cw.columns)
        cross = cross.mask((bs_up <= lead_win) & above & ((bs_up < bs_dn) | bs_dn.isna()), 1.0)
        cross = cross.mask((bs_dn <= lead_win) & ~above & ((bs_dn < bs_up) | bs_up.isna()), -1.0)
        bnh, bnl = bars_since(r["nh"]), bars_since(r["nl"])
        hl = pd.DataFrame(0.0, index=cw.index, columns=cw.columns)
        hl = hl.mask(bnh <= hl_win, 1.0).mask((bnl <= hl_win) & ~(bnh <= hl_win), -1.0)
        wk[f"{tag}_vs_ma"] = (r["rs"] / r["ma"] - 1) * 100
        wk[f"{tag}_cross"] = cross.where(r["ma"].notna())
        wk[f"{tag}_cross_wk"] = pd.concat([bs_up, bs_dn]).groupby(level=0).min().reindex(cw.index)
        wk[f"{tag}_hl"] = hl.where(r["ma"].notna())
        wk[f"{tag}_nh_wk"], wk[f"{tag}_nl_wk"] = bnh, bnl
    # RS-leads-price: RS (vs S&P, Dean's script) crossed its 40W MA recently while price is still
    # approaching its own 40W MA (up to `far` % on the not-yet-crossed side) or only just through it
    # (up to `near` %). Note RS/MA ~ stock-vs-40W minus S&P-vs-40W: when the index is well above its
    # 40W, long-side leads are rare and short-side leads common, and vice versa.
    wd40 = wk["w_d40"]
    lead = pd.DataFrame(0.0, index=cw.index, columns=cw.columns)
    lead = lead.mask((wk["rs_cross"] == 1) & wd40.between(-far, near), 1.0)
    lead = lead.mask((wk["rs_cross"] == -1) & wd40.between(-near, far), -1.0)
    wk["rs_lead"] = lead.where(wk["rs_vs_ma"].notna())
    for k, df in wk.items():
        out[k] = df.reindex(c.index, method="ffill").astype(F32)

    # ---------- daily setups
    s21, s50, s200 = sma(c, 21), sma(c, 50), sma(c, 200)
    out["d21"] = ((c / s21 - 1) * 100).astype(F32)
    out["d50"] = ((c / s50 - 1) * 100).astype(F32)
    out["d200"] = ((c / s200 - 1) * 100).astype(F32)
    out["ext21"] = out["d21"].abs()
    for k, n in (("p1w", 5), ("p1m", 21), ("p3m", 63), ("p6m", 126)):
        out[k] = ((c / c.shift(n) - 1) * 100).astype(F32)
    lr = np.log(c)
    out["vol63"] = (lr.diff().rolling(63, min_periods=63).std() * np.sqrt(252) * 100).astype(F32)
    t = pd.Series(np.arange(len(c), dtype=float), index=c.index)
    out["r2_63"] = (lr.rolling(63, min_periods=63).corr(t) ** 2).astype(F32)
    out["er_63"] = ((c - c.shift(63)).abs() / c.diff().abs().rolling(63, min_periods=63).sum()).astype(F32)
    side = np.sign(c - s21)
    flips = (side != side.shift(1)) & side.shift(1).notna() & side.notna()
    out["x21_63"] = flips.astype(F32).rolling(63, min_periods=63).sum().where(s21.notna()).astype(F32)
    rsd = c / sector_bench
    out["rssec_1m"] = ((rsd / rsd.shift(21) - 1) * 100).astype(F32)
    out["rssec_3m"] = ((rsd / rsd.shift(63) - 1) * 100).astype(F32)

    # ---------- sector rotation (stock vs its own sector ETF), RRG-style, daily
    sr = cfg["sector_rotation"]
    ratio = (ema(rsd, sr["smooth_len"]) / sma(rsd, sr["trend_len"]) - 1) * 100   # relative trend
    mom = ratio - ratio.shift(sr["mom_len"])                      # relative momentum
    q = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    q = q.mask((ratio >= 0) & (mom >= 0), 2.0)    # Leading
    q = q.mask((ratio < 0) & (mom >= 0), 1.0)     # Improving
    q = q.mask((ratio >= 0) & (mom < 0), -1.0)    # Weakening
    q = q.mask((ratio < 0) & (mom < 0), -2.0)     # Lagging
    q = q.where(ratio.notna() & mom.notna())
    into_lead = (q == 2) & (q.shift(1) != 2) & q.shift(1).notna()
    into_lag = (q == -2) & (q.shift(1) != -2) & q.shift(1).notna()
    bl, bg = bars_since(into_lead), bars_since(into_lag)
    turn = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    turn = turn.mask((bl < sr["turn_recent_days"]) & (q == 2), 1.0)
    turn = turn.mask((bg < sr["turn_recent_days"]) & (q == -2), -1.0)
    out["sec_ratio"], out["sec_mom"] = ratio.astype(F32), mom.astype(F32)
    out["sec_quad"] = q.astype(F32)
    out["sec_turn"] = turn.where(q.notna()).astype(F32)

    # ---------- daily triggers (6M / 3M window)
    tc = cfg["triggers"]
    excl = tc["breakout_exclude_days"]
    volr = v / v.rolling(50, min_periods=20).mean().shift(1)
    volr_recent = volr.rolling(tc["breakout_recent_days"], min_periods=1).max()
    bo = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    for n, wgt in ((63, 1.0), (126, 1.5)):
        res = h.rolling(n, min_periods=n).max().shift(excl)
        sup = l.rolling(n, min_periods=n).min().shift(excl)
        up, dn = c > res, c < sup
        up_first = up & ~up.shift(1, fill_value=False)
        dn_first = dn & ~dn.shift(1, fill_value=False)
        rec_up = (bars_since(up_first) < tc["breakout_recent_days"]) & up
        rec_dn = (bars_since(dn_first) < tc["breakout_recent_days"]) & dn
        bo = bo.mask(rec_up & (bo.abs() < wgt), wgt).mask(rec_dn & (bo.abs() < wgt), -wgt)
        if n == 126:
            out["res126"] = res.astype(F32)
            out["sup126"] = sup.astype(F32)
            out["base_tight"] = (-(res / sup - 1) * 100).astype(F32)   # tighter 6M range = higher
            hr_long = (res / c - 1) * 100            # room to 6M resistance (long)
            hr_short = (c / sup - 1) * 100           # room to 6M support (short)
            out["headroom_long"], out["headroom_short"] = hr_long.astype(F32), hr_short.astype(F32)
    vol_ok = volr_recent >= tc["breakout_volume_ratio"]
    out["breakout"] = (bo * (1 + 0.5 * vol_ok)).where(c.notna()).astype(F32)
    out["breakout_volr"] = volr_recent.astype(F32)

    m = ema(c, 12) - ema(c, 26)
    hist = m - ema(m, 9)
    histn = hist / c * 100                         # price-normalised histogram
    flip_up, flip_dn = (hist > 0) & (hist.shift(1) <= 0), (hist < 0) & (hist.shift(1) >= 0)
    turn_up = (hist < 0) & (hist > hist.shift(1)) & (hist.shift(1) <= hist.shift(2))
    turn_dn = (hist > 0) & (hist < hist.shift(1)) & (hist.shift(1) >= hist.shift(2))
    win = tc["macd_recent_days"]
    bfu, bfd, btu, btd = (bars_since(x) for x in (flip_up, flip_dn, turn_up, turn_dn))
    sig = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    sig = sig.mask((btu < win) & (hist < 0), 0.5).mask((btd < win) & (hist > 0), -0.5)
    sig = sig.mask((bfu < win) & (hist > 0), 1.0).mask((bfd < win) & (hist < 0), -1.0)
    out["macd_sig"] = sig.where(hist.notna()).astype(F32)
    out["macd_histn"] = histn.astype(F32)
    return out


def snapshot(panels: dict[str, pd.DataFrame], date: pd.Timestamp) -> pd.DataFrame:
    """One row per symbol with every feature as of `date`."""
    df = pd.DataFrame({k: p.loc[date] for k, p in panels.items()})
    df.index.name = "symbol"
    return df


# ----------------------------------------------------------------- weekly regime (the long-timeframe read)
REGIMES = ("Uptrend", "Turning up", "Range", "Turning down", "Downtrend")


def regime(df: pd.DataFrame) -> pd.Series:
    up = (df.w_d40 > 0) & (df.w_slope40 > 0) & (df.rs_vs_ma > 0)
    dn = (df.w_d40 < 0) & (df.w_slope40 < 0) & (df.rs_vs_ma < 0)
    r = pd.Series("Range", index=df.index)
    r[df.rs_lead == 1] = "Turning up"
    r[df.rs_lead == -1] = "Turning down"
    r[up] = "Uptrend"
    r[dn] = "Downtrend"
    r[df.rs_vs_ma.isna() | df.w_d40.isna()] = None
    return r


def gate(reg: pd.Series, side: str, cfg: dict) -> pd.Series:
    allowed = cfg["gate"]["long" if side == "long" else "short"]
    return reg.isin(allowed)


def side_features(df: pd.DataFrame, side: str) -> pd.DataFrame:
    """Raw feature values oriented so that higher = better for this side."""
    x = pd.DataFrame(index=df.index)
    s = 1.0 if side == "long" else -1.0
    for f in DIRECTIONAL:
        if f == "headroom":
            x[f] = df["headroom_long"] if side == "long" else df["headroom_short"]
        else:
            x[f] = df[f] * s
    for f in NONDIRECTIONAL:
        x[f] = df[f]
    return x
