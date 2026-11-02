"""Engine v3 "eye": learn Dean's chart reading from the charts he reviewed, with no hand-written rules.

A chart is turned into a picture vector: the six panels of Dean's chart (scanner/charts.py), each
scaled to its own window the way the chart axes autoscale, so the model sees shapes, not levels:
  W1 weekly close, 10W, 40W (2Y)          W2 weekly RS line vs S&P and its 40W MA (2Y)
  D1 daily close, 21D, 50D, 200D (1Y)     D2 daily RS vs S&P and its 200D MA (1Y)
  D3 MACD histogram (1Y)                  D4 stock / sector ETF and its 50D MA (1Y)
  Z  the last 3 months of D1 at full resolution (the right edge, where the eye goes)
A short is read as the long picture flipped upside down (every panel y -> 1 - y, MACD sign
flipped), so one model learns "looks like one of Dean's entries" for both sides.

The model is deliberately small (standardise -> PCA -> regularised logistic regression): ~240
labelled entries cannot support more. Training data: tools/eye_lab.py / eye_train().
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from features import sma, to_weekly

WK, WK_N = 104, 52          # weekly bars on the chart, points kept
DY, DY_N = 252, 84          # daily bars on the chart, points kept
ZM = 63                     # zoom: last 3 months, full resolution
PANELS = {"W1": 3 * WK_N, "W2": 2 * WK_N, "D1": 4 * DY_N, "D2": 2 * DY_N, "D3": DY_N, "D4": 2 * DY_N, "Z": 3 * ZM}
DIM = sum(PANELS.values())


class Store:
    """Every series the chart draws, as date x symbol arrays, computed once for the whole universe."""

    def __init__(self, daily: dict[str, pd.DataFrame], spx: pd.Series, sector_bench: pd.DataFrame, trend_len: int = 50):
        c, h, l = daily["Close"], daily["High"], daily["Low"]
        self.idx, self.cols = c.index, c.columns
        f = lambda x: x.to_numpy(dtype=np.float32)
        self.c, self.h, self.l = f(c), f(h), f(l)
        self.s21, self.s50, self.s200 = f(sma(c, 21)), f(sma(c, 50)), f(sma(c, 200))
        rs = c.div(spx.reindex(c.index), axis=0)
        self.rs, self.rs200 = f(rs), f(sma(rs, 200))
        m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        self.hist = f(m - m.ewm(span=9, adjust=False).mean())
        ratio = c / sector_bench
        self.sec, self.sec50 = f(ratio), f(sma(ratio, trend_len))
        w = to_weekly({"Close": c, "High": h, "Low": l})
        self.widx = w["Close"].index
        spx_w = spx.resample("W-FRI").last().reindex(self.widx, method="ffill")
        rsw = w["Close"].div(spx_w, axis=0)
        self.wc, self.wh, self.wl = f(w["Close"]), f(w["High"]), f(w["Low"])
        self.w10, self.w40 = f(sma(w["Close"], 10)), f(sma(w["Close"], 40))
        self.rsw, self.rsw40 = f(rsw), f(sma(rsw, 40))

    def pos(self, date) -> tuple[int, int]:
        """Daily row of `date` and the last weekly row completed on or before it."""
        i = self.idx.get_loc(pd.Timestamp(date))
        j = int(self.widx.searchsorted(pd.Timestamp(date), side="right")) - 1
        return i, j


def _scale(arrs: list[np.ndarray], lo_hi: list[np.ndarray] | None = None) -> list[np.ndarray]:
    """Joint min-max scaling per symbol (columns) over the window (rows), like a chart's y-axis."""
    ref = lo_hi if lo_hi is not None else arrs
    lo = np.nanmin(np.stack([np.nanmin(a, axis=0) for a in ref]), axis=0)
    hi = np.nanmax(np.stack([np.nanmax(a, axis=0) for a in ref]), axis=0)
    rng = np.where(hi - lo > 0, hi - lo, np.nan)
    return [(a - lo) / rng for a in arrs]


def _ds(a: np.ndarray, n: int) -> np.ndarray:
    """Downsample rows to n by block means (a.shape[0] must be a multiple of n)."""
    k = a.shape[0] // n
    return np.nanmean(a[-n * k:].reshape(n, k, -1), axis=1)


def pictures(S: Store, date, cols: np.ndarray | None = None, short: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Picture vectors (n_symbols x DIM) as of `date` (data up to and including it).
    Returns (X, ok): ok is False where the history is too short or too gappy to draw the chart."""
    i, j = S.pos(date)
    cols = np.arange(len(S.cols)) if cols is None else cols
    if i < DY - 1 or j < WK - 1:
        return np.full((len(cols), DIM), np.nan, np.float32), np.zeros(len(cols), bool)
    d = slice(i - DY + 1, i + 1)
    w = slice(j - WK + 1, j + 1)
    g = lambda arr, s: arr[s][:, cols]
    w1 = _scale([g(S.wc, w), g(S.w10, w), g(S.w40, w)], [g(S.wh, w), g(S.wl, w), g(S.w10, w), g(S.w40, w)])
    w2 = _scale([g(S.rsw, w), g(S.rsw40, w)])
    d1 = _scale([g(S.c, d), g(S.s21, d), g(S.s50, d), g(S.s200, d)], [g(S.h, d), g(S.l, d), g(S.s21, d), g(S.s50, d), g(S.s200, d)])
    d2 = _scale([g(S.rs, d), g(S.rs200, d)])
    hs = g(S.hist, d)
    d3 = hs / np.nanmax(np.abs(hs), axis=0)
    d3 = (d3 + 1) / 2                                        # 0..1 with 0.5 = zero line
    d4 = _scale([g(S.sec, d), g(S.sec50, d)])
    z = slice(i - ZM + 1, i + 1)
    zz = _scale([g(S.c, z), g(S.s21, z), g(S.s50, z)], [g(S.h, z), g(S.l, z), g(S.s21, z), g(S.s50, z)])
    parts = ([_ds(a, WK_N) for a in w1] + [_ds(a, WK_N) for a in w2] + [_ds(a, DY_N) for a in d1]
             + [_ds(a, DY_N) for a in d2] + [_ds(d3, DY_N)] + [_ds(a, DY_N) for a in d4] + zz)
    X = np.concatenate(parts, axis=0).T.astype(np.float32)   # symbols x DIM
    if short:
        X = 1.0 - X                                          # flip every panel (MACD: 0.5 -> 0.5, sign flips)
    nan_share = np.isnan(X).mean(axis=1)
    ok = nan_share < 0.05
    X = np.where(np.isnan(X), 0.5, X)
    return X, ok


def eye_model(kind: str = "logit", n_pca: int = 30, C: float = 0.05):
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if kind == "logit":
        return make_pipeline(StandardScaler(), PCA(n_pca, whiten=True, random_state=0),
                             LogisticRegression(C=C, class_weight="balanced", max_iter=2000))
    if kind == "logit_raw":
        return make_pipeline(StandardScaler(), LogisticRegression(C=C / 50, class_weight="balanced", max_iter=4000))
    if kind == "knn":
        return make_pipeline(StandardScaler(), PCA(n_pca, whiten=True, random_state=0),
                             KNeighborsClassifier(25, weights="distance"))
    if kind == "gbm":
        from sklearn.ensemble import HistGradientBoostingClassifier
        return make_pipeline(PCA(n_pca, random_state=0),
                             HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                                            l2_regularization=1.0, class_weight="balanced"))
    raise ValueError(kind)


def fit(model, X, y, w):
    last = model.steps[-1][0]
    try:
        model.fit(X, y, **{f"{last}__sample_weight": w})
    except TypeError:                                         # kNN takes no weights
        model.fit(X, y)
    return model


def proba(model, X) -> np.ndarray:
    return model.predict_proba(X)[:, 1]
