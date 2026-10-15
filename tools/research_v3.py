#!/usr/bin/env python3
"""Research harness for engine v3: multi-horizon learning (point 2) and a pair model (point 3).

Usage: python tools/research_v3.py <out.json> [history_sessions]

Everything is walk-forward: models are refit every REFIT weeks on rows whose labels were already
known at that date (no look-ahead), then used to trade the following weeks. Strategies compared, each
10 pairs a week, max 2 per supersector, unique symbols, entry next open:
  A  v2.2 as live: prior leg weights, mechanical pairing, 1W hold
  B  learned leg weights (walk-forward ridge, 50/50 with prior), mechanical pairing, 1W hold
  C  pair model (gradient boosting on simulated same-sector spreads, 1W target), 1W hold
  D  v2.2 mechanical pairing held 1 month (rebalanced every 4 weeks)
  E  pair model with 1M target, held 1 month
  F  pair model ranked on the blend of 1W and 1M predictions, 1W hold
Plus: calibration of the pair model (out-of-sample quintiles -> realised spread and hit rate), which is
what a measured conviction would be built on, and per-feature importance.
"""
import copy
import datetime as dt
import json
import logging
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
import scan  # noqa: E402
from features import compute_panels, snapshot, to_weekly  # noqa: E402
from model import FEATURES, PRIOR, normalise, score, select, side_table  # noqa: E402

log = logging.getLogger("research")
N_PAIRS, PER_SECTOR, K = 10, 2, 8
REFIT, BURN_IN = 4, 52
H = (5, 10, 21)
REG = {"Uptrend": 2, "Turning up": 1, "Range": 0, "Turning down": -1, "Downtrend": -2}
PAIR_X = ["lsc", "ssc", "same_ind", "quad_l", "quad_s", "quad_gap", "reg_l", "reg_s", "corr60", "lvr",
          "beta_l", "beta_s", "net_beta", "rs_gap", "rssec_gap", "p1m_l", "p1m_s", "d21_l", "d21_s",
          "vol_l", "vol_s", "bo_l", "bo_s", "macd_l", "macd_s", "lead_l", "lead_s"]


def load(sessions, meta=None, history_fn=None):
    cfg = copy.deepcopy(scan.CFG)
    if meta is None:
        meta = pd.read_csv(ROOT / "data" / "universe.csv").drop_duplicates("symbol").set_index("symbol")
    etfs = list(scan.SECTOR_ETF.values())
    hist = (history_fn or scan.get_history)(list(meta.index) + etfs + [scan.BENCH, "SPY"], sessions)
    daily, spx, sb, _ = scan.build_panels(hist, meta)
    meta = meta.loc[daily["Close"].columns]
    panels = compute_panels(daily, spx, sb, cfg)
    panels["sessions"] = daily["Close"].notna().cumsum().astype("float32")
    C, O = daily["Close"].copy(), daily["Open"].copy()
    for e in etfs:
        if e in hist:
            C[e] = hist[e].Close.reindex(C.index)
            O[e] = hist[e].Open.reindex(C.index)
    return cfg, meta, hist, C, O, spx, panels


def build(cfg, meta, C, O, spx, panels):
    idx = C.index
    lr, lspx = np.log(C).diff(), np.log(spx).diff()
    we = to_weekly({"Close": C[[C.columns[0]]]})["Close"].index
    dates = [d for d in we if idx[300] <= d and idx.get_loc(d) + max(H) < len(idx)]
    prior = normalise(PRIOR)
    legs, pairs = [], []
    for wi, d in enumerate(dates):
        i0 = idx.get_loc(d)
        snap = snapshot(panels, d).join(meta, how="inner")
        win = lr.iloc[i0 - 59:i0 + 1]
        wsp = lspx.iloc[i0 - 59:i0 + 1]
        fr = {h: C.iloc[i0 + h] / O.iloc[i0 + 1] - 1 for h in H}          # raw leg returns, entry next open
        tabs = {}
        for side in ("long", "short"):
            t = side_table(snap, side, cfg)
            if t.empty:
                continue
            t["s0"] = score(t, prior[side] if isinstance(prior.get(side), dict) else prior)
            t["s0p"] = t.groupby("supersector").s0.rank(pct=True)
            sg = 1 if side == "long" else -1
            etf = t.supersector.map(scan.SECTOR_ETF)
            for h in H:
                r = fr[h].reindex(t.index).to_numpy()
                re = fr[h].reindex(etf.to_numpy()).to_numpy()
                t[f"r{h}"] = r
                t[f"y{h}"] = (r - re) * sg
            t["wi"], t["date"] = wi, d
            tabs[side] = t
            legs.append(t.reset_index())
        if len(tabs) < 2:
            continue
        L, S = tabs["long"], tabs["short"]
        for ss in set(L.supersector) & set(S.supersector):
            l = L[L.supersector == ss].nlargest(K, "s0")
            s = S[S.supersector == ss].nlargest(K, "s0")
            names = sorted(set(l.index) | set(s.index))
            w = win[names]
            cm = w.corr()
            vol = w.std()
            beta = w.apply(lambda c: c.cov(wsp) / wsp.var())
            for a in l.index:
                for b in s.index:
                    if a == b:
                        continue
                    ra, rb = snap.loc[a], snap.loc[b]
                    va, vb = vol[a], vol[b]
                    sl = vb / (va + vb) if va > 0 and vb > 0 else 0.5
                    row = {"wi": wi, "date": d, "ss": ss, "long": a, "short": b,
                           "lsc": l.at[a, "s0p"], "ssc": s.at[b, "s0p"],
                           "same_ind": float(meta.at[a, "industry"] == meta.at[b, "industry"]),
                           "quad_l": ra.sec_quad, "quad_s": rb.sec_quad, "quad_gap": ra.sec_quad - rb.sec_quad,
                           "reg_l": REG.get(l.at[a, "regime"], 0), "reg_s": REG.get(s.at[b, "regime"], 0),
                           "corr60": cm.at[a, b], "lvr": abs(np.log(va / vb)) if va > 0 and vb > 0 else np.nan,
                           "beta_l": beta[a], "beta_s": beta[b], "net_beta": sl * beta[a] - (1 - sl) * beta[b],
                           "rs_gap": ra.rs_vs_ma - rb.rs_vs_ma, "rssec_gap": ra.rssec_vs_ma - rb.rssec_vs_ma,
                           "p1m_l": ra.p1m, "p1m_s": rb.p1m, "d21_l": ra.d21, "d21_s": rb.d21,
                           "vol_l": ra.vol63, "vol_s": rb.vol63, "bo_l": ra.breakout, "bo_s": -rb.breakout,
                           "macd_l": ra.macd_sig, "macd_s": -rb.macd_sig, "lead_l": ra.rs_lead, "lead_s": -rb.rs_lead,
                           "sl": sl}
                    for h in H:
                        row[f"p{h}"] = (sl * fr[h].get(a, np.nan) - (1 - sl) * fr[h].get(b, np.nan)) * 100
                    pairs.append(row)
        if wi % 20 == 0:
            log.info("week %d/%d %s: %d pair rows so far", wi, len(dates), d.date(), len(pairs))
    legs = pd.concat(legs, ignore_index=True)
    pairs = pd.DataFrame(pairs)
    return dates, legs, pairs


def choose(cands, score_col):
    out, per, used = [], {}, set()
    for r in cands.sort_values(score_col, ascending=False).itertuples():
        if per.get(r.ss, 0) >= PER_SECTOR or r.long in used or r.short in used:
            continue
        out.append(r)
        per[r.ss] = per.get(r.ss, 0) + 1
        used |= {r.long, r.short}
        if len(out) == N_PAIRS:
            break
    return out


def mechanical(pw, col="lsc_ssc"):
    """v2.2-style: rank pairs by the average of the two legs' within-sector score percentiles."""
    pw = pw.assign(lsc_ssc=(pw.lsc + pw.ssc) / 2)
    return choose(pw, col)


def stats(r, per_year):
    r = pd.Series(r).dropna()
    if not len(r):
        return {}
    eq = (1 + r / 100).cumprod()
    return {"periods": int(len(r)), "mean_pct": round(r.mean(), 3), "stdev_pct": round(r.std(), 3),
            "sharpe_ann": round(r.mean() / r.std() * np.sqrt(per_year), 2) if r.std() > 0 else None,
            "hit_rate": round((r > 0).mean(), 3), "worst_pct": round(r.min(), 2),
            "max_dd_pct": round(((eq / eq.cummax()) - 1).min() * 100, 2), "cum_pct": round((eq.iloc[-1] - 1) * 100, 1)}


def halves(r, per_year):
    r = pd.Series(r).dropna()
    h = len(r) // 2
    return {"first": stats(r.iloc[:h], per_year), "second": stats(r.iloc[h:], per_year)}


def main(out, sessions, meta=None, history_fn=None):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge

    cfg, meta, hist, C, O, spx, panels = load(sessions, meta, history_fn)
    dates, legs, pairs = build(cfg, meta, C, O, spx, panels)
    nW = len(dates)
    log.info("weeks %d, leg rows %d, pair rows %d", nW, len(legs), len(pairs))
    pairs = pairs.replace([np.inf, -np.inf], np.nan)
    for h in H:   # cross-sectional rank target per week
        pairs[f"y{h}"] = pairs.groupby("wi")[f"p{h}"].rank(pct=True) - 0.5
        legs[f"q{h}"] = legs.groupby(["wi", "supersector"])[f"y{h}"].rank(pct=True) - 0.5
    Xl = [f"z_{f}" for f in FEATURES]
    prior = normalise(PRIOR)

    pred = {f"pm{h}": pd.Series(np.nan, index=pairs.index) for h in (5, 21)}
    legw = {}
    imp = {}
    eval_from = BURN_IN
    for t in range(eval_from, nW, REFIT):
        for h in (5, 21):
            lag = int(np.ceil(h / 5)) + 1                  # weeks until the label is known
            tr = pairs[(pairs.wi <= t - lag)].dropna(subset=[f"y{h}"])
            m = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=300,
                                              l2_regularization=1.0, random_state=0)
            m.fit(tr[PAIR_X], tr[f"y{h}"])
            sel = (pairs.wi >= t) & (pairs.wi < t + REFIT)
            pred[f"pm{h}"][sel] = m.predict(pairs.loc[sel, PAIR_X])
            if t + REFIT >= nW:
                from sklearn.inspection import permutation_importance
                s = tr.sample(min(20000, len(tr)), random_state=0)
                pi = permutation_importance(m, s[PAIR_X], s[f"y{h}"], n_repeats=3, random_state=0)
                imp[h] = dict(sorted(zip(PAIR_X, pi.importances_mean.round(5).tolist()), key=lambda kv: -kv[1]))
        lag = 2
        w = {}
        for side in ("long", "short"):
            tr = legs[(legs.wi <= t - lag) & (legs.side == side)].dropna(subset=["q5"])
            rm = Ridge(alpha=50, fit_intercept=False).fit(tr[Xl].to_numpy(), tr["q5"].to_numpy())
            fit = normalise(dict(zip(FEATURES, rm.coef_.tolist())))
            w[side] = {f: 0.5 * prior.get(f, 0) + 0.5 * fit.get(f, 0) for f in FEATURES}
        for wk in range(t, min(t + REFIT, nW)):
            legw[wk] = w
    for k, v in pred.items():
        pairs[k] = v
    pairs["pmix"] = pairs.groupby("wi").pm5.rank(pct=True) + pairs.groupby("wi").pm21.rank(pct=True)

    # learned leg scores -> mechanical pairing on learned percentiles (strategy B)
    lsc_learn = {}
    for wk, w in legw.items():
        lw = legs[legs.wi == wk]
        for side in ("long", "short"):
            t = lw[lw.side == side]
            if t.empty:
                continue
            s = pd.Series(t[Xl].to_numpy() @ np.array([w[side][f] for f in FEATURES]), index=t.index)
            pct = s.groupby(t.supersector).rank(pct=True)
            for i, sym in zip(t.index, t.symbol):
                lsc_learn[(wk, side, sym)] = pct[i]
    pairs["lsc_b"] = [lsc_learn.get((w_, "long", a), np.nan) for w_, a in zip(pairs.wi, pairs.long)]
    pairs["ssc_b"] = [lsc_learn.get((w_, "short", b), np.nan) for w_, b in zip(pairs.wi, pairs.short)]
    pairs["learn_mech"] = (pairs.lsc_b + pairs.ssc_b) / 2

    res = {k: [] for k in "ABCDEF"}
    dates_eval = []
    for wk in range(eval_from, nW):
        pw = pairs[pairs.wi == wk]
        if pw.empty:
            continue
        dates_eval.append(str(dates[wk].date()))
        res["A"].append(np.mean([r.p5 for r in mechanical(pw)]))
        res["B"].append(np.mean([r.p5 for r in choose(pw.dropna(subset=["learn_mech"]), "learn_mech")]) if pw.learn_mech.notna().any() else np.nan)
        res["C"].append(np.mean([r.p5 for r in choose(pw, "pm5")]))
        res["F"].append(np.mean([r.p5 for r in choose(pw, "pmix")]))
        if (wk - eval_from) % 4 == 0:
            res["D"].append(np.mean([r.p21 for r in mechanical(pw)]))
            res["E"].append(np.mean([r.p21 for r in choose(pw, "pm21")]))
    labels = {"A": "v2.2 as live (prior weights, mechanical pairing, 1W)",
              "B": "learned leg weights, mechanical pairing, 1W",
              "C": "pair model (1W target), 1W",
              "D": "v2.2 mechanical pairing held 1M (monthly)",
              "E": "pair model (1M target) held 1M (monthly)",
              "F": "pair model ranked on 1W+1M blend, 1W"}
    out_res = {}
    for k, v in res.items():
        py = 12 if k in "DE" else 52
        out_res[k] = {"label": labels[k], "all": stats(v, py), **halves(v, py)}
        log.info("%s %s %s", k, labels[k], out_res[k]["all"])

    # calibration: out-of-sample pair-model quintiles -> realised spread and hit rate (all candidate pairs)
    cal = {}
    for h, col in ((5, "pm5"), (21, "pm21")):
        e = pairs[(pairs.wi >= eval_from)].dropna(subset=[col, f"p{h}"]).copy()
        e["q"] = e.groupby("wi")[col].rank(pct=True).mul(5).clip(upper=4.999).astype(int) + 1
        g = e.groupby("q")[f"p{h}"]
        cal[h] = [{"quintile": int(q), "mean_spread_pct": round(v.mean(), 3), "hit_rate": round((v > 0).mean(), 3),
                   "n": int(len(v))} for q, v in g]
        e["mq"] = e.groupby("wi")[["lsc", "ssc"]].mean(axis=1) if False else (e.lsc + e.ssc) / 2
        e["mq"] = e.groupby("wi").mq.rank(pct=True).mul(5).clip(upper=4.999).astype(int) + 1
        cal[f"mechanical_{h}"] = [{"quintile": int(q), "mean_spread_pct": round(v.mean(), 3),
                                   "hit_rate": round((v > 0).mean(), 3)} for q, v in e.groupby("mq")[f"p{h}"]]

    out_doc = {"computed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "history": f"{dates[0].date()} -> {dates[-1].date()} ({nW} weeks); evaluation from week {eval_from} "
                          f"({dates[eval_from].date()})",
               "universe": int(C.shape[1]), "pair_rows": int(len(pairs)), "refit_weeks": REFIT,
               "strategies": out_res, "calibration": cal, "pair_model_importance": imp,
               "notes": ["walk-forward: every model only sees labels known at its refit date",
                         "today's universe: survivorship bias, more so further back",
                         "no costs, borrow or earnings gate; mechanical pairing stands in for the chart review"]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_doc, indent=1, default=str))
    print(json.dumps({k: v for k, v in out_doc.items() if k not in ("pair_model_importance",)}, indent=1, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "research_v3.json",
         int(sys.argv[2]) if len(sys.argv) > 2 else 1300)
