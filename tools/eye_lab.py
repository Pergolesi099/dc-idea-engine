#!/usr/bin/env python3
"""Engine v3 "eye" lab: learn Dean's reviewed charts, validate, backtest, and pick this week's pairs.

Usage: python tools/eye_lab.py <out_dir> [history_sessions=2300]

Labels (all from the desk, backed up in history/desk and labels/):
  + his long/short entry lines (labels/marks.jsonl, dated on the real calendar), read in the line's
    direction (short = flipped picture); +-1 session copies in training only
  - every 3rd session of the same chart's year that is > 7 sessions from any of his entries in that
    direction (he looked at the whole chart and did not mark it)
  - a light sample of random universe pictures (weight 0.2): most charts on most days are not setups
  +/- his chart grade at the chart date in the chart's side: A positive, B weak positive, C negative
Validation: 5-fold, grouped by stock (the model never sees the held-out stock). Reported: AUC of his
entries vs the rest of the same charts, where his entry ranks within its own chart, and the grade
correlation. Backtest: one pair per sector per week (best long picture vs best short picture), no
gates; the clean out-of-sample period is everything before the earliest training picture.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
import eye  # noqa: E402
import scan  # noqa: E402
from features import to_weekly  # noqa: E402

log = logging.getLogger("eye")
RNG = np.random.default_rng(0)
MODELS = {"logit pca20": ("logit", 20, 0.05), "logit pca30": ("logit", 30, 0.05), "logit pca50": ("logit", 50, 0.05),
          "logit pca30 C0.01": ("logit", 30, 0.01), "logit pca30 C0.5": ("logit", 30, 0.5),
          "logit raw": ("logit_raw", 0, 0.05), "knn pca30": ("knn", 30, 0), "gbm pca30": ("gbm", 30, 0)}


# ---------------------------------------------------------------- data
def load(sessions: int, extra: list[str]):
    meta = pd.read_csv(ROOT / "data" / "universe.csv").drop_duplicates("symbol").set_index("symbol")
    if LIMIT:
        meta = meta[meta.index.isin(extra) | (np.arange(len(meta)) < LIMIT)]
    etfs = list(scan.SECTOR_ETF.values())
    hist = scan.get_history(list(meta.index) + etfs + [scan.BENCH, "SPY"], sessions)
    daily, spx, sb, _ = scan.build_panels(hist, meta)
    meta = meta.loc[daily["Close"].columns]
    C, O = daily["Close"].copy(), daily["Open"].copy()
    for e in etfs:
        if e in hist:
            C[e] = hist[e].Close.reindex(C.index)
            O[e] = hist[e].Open.reindex(C.index)
    return meta, hist, daily, spx, sb, C, O


LIMIT = 0


def last_session_before(idx, run_date):
    return idx[idx < pd.Timestamp(run_date)][-1]


def resolve_marks(marks: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in marks.itertuples():
        if r.symbol not in close:
            continue
        ix = close[r.symbol].dropna().index
        last = ix[ix < pd.Timestamp(r.run_date)]
        if not len(last):
            continue
        if r.tf == "D":
            k = len(last) - 1 - int(round(r.bars_back))
            if k < 0:
                continue
            d = last[k]
        else:
            wk = last.to_series().resample("W-FRI").last().dropna()
            k = len(wk) - 1 - int(round(r.bars_back))
            if k < 0:
                continue
            d = wk.iloc[k]
        rows.append({**r._asdict(), "date": d})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- training set
def build_samples(S: eye.Store, mk: pd.DataFrame, grades: pd.DataFrame, meta) -> pd.DataFrame:
    """One row per (symbol, date, side, label, weight, kind, chart)."""
    idx = S.idx
    rows = []
    ent = mk[mk.cls.isin(["entry_long", "entry_short"])].copy()
    ent["side"] = np.where(ent.cls == "entry_long", "long", "short")
    for r in ent.itertuples():
        i = idx.get_loc(r.date)
        for k in (-1, 0, 1):
            if 0 <= i + k < len(idx):
                rows.append({"symbol": r.symbol, "date": idx[i + k], "side": r.side, "y": 1, "w": 1.0 if k == 0 else 0.5,
                             "kind": "entry" if k == 0 else "entry_jit", "chart": f"{r.symbol}|{r.run_date}"})
    charts = pd.concat([ent[["symbol", "run_date"]], grades[["symbol", "run_date"]]]).drop_duplicates()
    for c in charts.itertuples():
        if c.symbol not in S.cols:
            continue
        end = idx.get_loc(last_session_before(idx, c.run_date))
        mine = ent[(ent.symbol == c.symbol) & (ent.run_date == c.run_date)]
        for side in ("long", "short"):
            near = [idx.get_loc(d) for d in mine[mine.side == side].date]
            for i in range(end - 249, end + 1, 3):
                if all(abs(i - p) > 7 for p in near):
                    rows.append({"symbol": c.symbol, "date": idx[i], "side": side, "y": 0, "w": 1.0, "kind": "chart_neg",
                                 "chart": f"{c.symbol}|{c.run_date}"})
    for g in grades.itertuples():
        if g.symbol not in S.cols or g.grade not in ("A", "B", "C"):
            continue
        rows.append({"symbol": g.symbol, "date": last_session_before(idx, g.run_date), "side": g.side,
                     "y": int(g.grade != "C"), "w": {"A": 1.0, "B": 0.3, "C": 1.0}[g.grade], "kind": f"grade_{g.grade}",
                     "chart": f"{g.symbol}|{g.run_date}"})
    # random universe pictures, light weight, inside the same period as the charts
    lo = min(r["date"] for r in rows)
    fr = [d for d in to_weekly({"Close": pd.DataFrame(index=idx, data={"x": 0.0})})["Close"].index if d >= lo]
    syms = [s for s in S.cols if s in meta.index]
    for d in RNG.choice(fr, size=min(40, len(fr)), replace=False):
        for s in RNG.choice(syms, size=min(60, len(syms)), replace=False):
            for side in ("long", "short"):
                rows.append({"symbol": s, "date": d, "side": side, "y": 0, "w": 0.2, "kind": "universe_neg", "chart": f"U|{s}"})
    return pd.DataFrame(rows)


def featurize(S: eye.Store, smp: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    X = np.zeros((len(smp), eye.DIM), np.float32)
    ok = np.zeros(len(smp), bool)
    col = {s: k for k, s in enumerate(S.cols)}
    smp = smp.reset_index(drop=True)
    for (d, side), g in smp.groupby(["date", "side"]):
        cols = np.array([col[s] for s in g.symbol])
        x, o = eye.pictures(S, d, cols, short=(side == "short"))
        X[g.index] = x
        ok[g.index] = o
    return X, ok


# ---------------------------------------------------------------- validation
def auc(y, s):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, s)) if len(set(y)) == 2 else np.nan


def validate(smp: pd.DataFrame, X: np.ndarray) -> dict:
    from scipy.stats import spearmanr
    from sklearn.model_selection import GroupKFold
    groups = smp.symbol.to_numpy()
    res = {}
    for name, (kind, npca, C) in MODELS.items():
        oof = np.full(len(smp), np.nan)
        for tr, te in GroupKFold(5).split(X, smp.y, groups):
            m = eye.fit(eye.eye_model(kind, npca, C), X[tr], smp.y.to_numpy()[tr], smp.w.to_numpy()[tr])
            oof[te] = eye.proba(m, X[te])
        smp_ = smp.assign(s=oof)
        ev = smp_[smp_.kind.isin(["entry", "chart_neg"])]
        # where does his entry rank inside its own chart (same side)?
        ranks = []
        for (ch, side), g in ev.groupby(["chart", "side"]):
            pos = g[g.kind == "entry"]
            for v in pos.s:
                ranks.append((g.s < v).mean())
        gr = smp_[smp_.kind.str.startswith("grade_")]
        gnum = gr.kind.map({"grade_A": 2, "grade_B": 1, "grade_C": 0})
        res[name] = {"auc_entries_vs_same_charts": round(auc(ev.y, ev.s), 3),
                     "auc_entries_vs_universe": round(auc(smp_[smp_.kind.isin(["entry", "universe_neg"])].y,
                                                         smp_[smp_.kind.isin(["entry", "universe_neg"])].s), 3),
                     "entry_rank_in_chart_mean": round(float(np.mean(ranks)), 3),
                     "entry_in_top10pct_of_chart": round(float(np.mean(np.array(ranks) >= 0.9)), 3),
                     "grade_spearman": round(float(spearmanr(gnum, gr.s).correlation), 3) if len(gr) > 5 else None,
                     "grade_mean_score": {k: round(float(v), 3) for k, v in gr.groupby("kind").s.mean().items()}}
        log.info("CV %s: %s", name, res[name])
    return res


# ---------------------------------------------------------------- history
_CACHE: dict = {}


def score_week(S, model, d, sym_ok):
    key = (id(model), pd.Timestamp(d))
    if key in _CACHE:
        return {k: v.copy() for k, v in _CACHE[key].items()}
    out = {}
    for side in ("long", "short"):
        X, ok = eye.pictures(S, d, short=(side == "short"))
        s = eye.proba(model, X)
        s[~(ok & sym_ok)] = np.nan
        out[side] = s
    _CACHE[key] = out
    return {k: v.copy() for k, v in out.items()}


def sector_pairs(scores, cols, meta, per_sector=1):
    df = pd.DataFrame({"long": scores["long"], "short": scores["short"]}, index=cols)
    df["ss"] = meta.supersector.reindex(df.index)
    df = df.dropna(subset=["ss"])
    pairs = []
    for ss, g in df.groupby("ss"):
        L = g.long.dropna().sort_values(ascending=False)
        Sh = g.short.dropna().sort_values(ascending=False)
        used = set()
        for k in range(per_sector):
            l = next((s for s in L.index if s not in used), None)
            if l is None:
                break
            used.add(l)
            sh = next((s for s in Sh.index if s not in used), None)
            if sh is None:
                break
            used.add(sh)
            pairs.append({"ss": ss, "long": l, "short": sh, "ls": float(L[l]), "ss_": float(Sh[sh])})
    return pairs


def backtest(S, model, meta, C, O, fridays, hold, step, oos_end, per_sector=1, random_pairs=False):
    idx = C.index
    lr = np.log(C).diff()
    cols = S.cols
    sym_ok = np.array([s in meta.index for s in cols])
    rows = []
    for d in fridays[::step]:
        i0 = idx.get_loc(d)
        if i0 + hold >= len(idx):
            continue
        sc = score_week(S, model, d, sym_ok)
        if random_pairs:
            sc = {k: np.where(np.isnan(v), np.nan, RNG.random(len(v))) for k, v in sc.items()}
        e, x = idx[i0 + 1], idx[i0 + hold]
        pr = []
        for p in sector_pairs(sc, cols, meta, per_sector):
            a, b = p["long"], p["short"]
            rl, rs = C.at[x, a] / O.at[e, a] - 1, C.at[x, b] / O.at[e, b] - 1
            if not (np.isfinite(rl) and np.isfinite(rs)):
                continue
            win = lr.loc[:d].iloc[-60:]
            va, vb = win[a].std(), win[b].std()
            sl = vb / (va + vb) if va > 0 and vb > 0 else 0.5
            pr.append((sl * rl - (1 - sl) * rs) * 100)
        rows.append({"date": d, "port": np.mean(pr) if pr else np.nan, "n": len(pr)})
    wk = pd.DataFrame(rows).set_index("date")

    def st(r):
        r = r.dropna()
        if len(r) < 4:
            return {"weeks": int(len(r))}
        eq = (1 + r / 100).cumprod()
        return {"periods": int(len(r)), "mean_pct": round(r.mean(), 3), "sharpe_ann": round(r.mean() / r.std() * np.sqrt(52 / step), 2),
                "hit": round((r > 0).mean(), 3), "max_dd_pct": round((eq / eq.cummax() - 1).min() * 100, 1),
                "cum_pct": round((eq.iloc[-1] - 1) * 100, 1)}
    oos = wk[wk.index < oos_end]
    ins = wk[wk.index >= oos_end]
    h = len(oos) // 2
    return {"oos": st(oos.port), "oos_first_half": st(oos.port.iloc[:h]), "oos_second_half": st(oos.port.iloc[h:]),
            "in_sample": st(ins.port), "avg_pairs": round(wk.n.mean(), 1)}


def event_study(S, model, meta, C, fridays, oos_end):
    """Forward return vs sector ETF, side-adjusted, by eye-score decile (out-of-sample weeks only)."""
    idx = C.index
    cols = S.cols
    sym_ok = np.array([s in meta.index for s in cols])
    etf = meta.supersector.reindex(cols).map(scan.SECTOR_ETF)
    rows = []
    for d in [f for f in fridays if f < oos_end][::2]:
        i0 = idx.get_loc(d)
        if i0 + 43 >= len(idx):
            continue
        sc = score_week(S, model, d, sym_ok)
        for side, sg in (("long", 1), ("short", -1)):
            s = pd.Series(sc[side], index=cols).dropna()
            if len(s) < 50:
                continue
            dec = pd.qcut(s.rank(method="first"), 10, labels=False)
            for h in (5, 21, 42):
                leg = C.iloc[i0 + h][s.index] / C.iloc[i0 + 1][s.index] - 1
                eb = np.array([C.iloc[i0 + h].get(e, np.nan) / C.iloc[i0 + 1].get(e, np.nan) - 1 if isinstance(e, str) else np.nan
                               for e in etf[s.index]])
                rel = sg * (leg.to_numpy() - eb) * 100
                for q, v in zip(dec.to_numpy(), rel):
                    rows.append((side, h, int(q), v))
    df = pd.DataFrame(rows, columns=["side", "h", "decile", "rel"]).dropna()
    out = {}
    for (side, h), g in df.groupby(["side", "h"]):
        m = g.groupby("decile").rel.mean()
        top, rest = g[g.decile == 9].rel, g[g.decile < 9].rel
        out[f"{side} {h}d"] = {"by_decile": [round(float(v), 3) for v in m.values],
                               "top_minus_rest": round(float(top.mean() - rest.mean()), 3),
                               "t": round(float((top.mean() - rest.mean()) / np.sqrt(top.var() / len(top) + rest.var() / len(rest))), 1),
                               "n_top": int(len(top))}
    return out


# ---------------------------------------------------------------- this week
def picks(S, model, meta, hist, spx, sb, smp, X, out_dir, cfg):
    import charts
    fr = to_weekly({"Close": pd.DataFrame(index=S.idx, data={"x": 0.0})})["Close"].index
    d = fr[-2] if (fr[-1] == S.idx[-1] and S.idx[-1].weekday() != 4) else fr[-1]   # last completed week
    sym_ok = np.array([s in meta.index for s in S.cols])
    sc = score_week(S, model, d, sym_ok)
    pairs = sector_pairs(sc, S.cols, meta, 1)
    # nearest of his entries in the model's own space (PCA step when there is one)
    pos = smp.kind.eq("entry").to_numpy()
    tr = model[:-1] if len(model.steps) > 1 else None
    Z = tr.transform(X[pos]) if tr is not None else X[pos]
    ref = smp[pos].reset_index(drop=True)
    col = {s: k for k, s in enumerate(S.cols)}
    cdir = out_dir / "charts"
    cdir.mkdir(parents=True, exist_ok=True)
    top = {}
    for side in ("long", "short"):
        s = pd.Series(sc[side], index=S.cols).dropna()
        s = s[s.index.isin(meta.index)]
        top[side] = s.groupby(meta.supersector.reindex(s.index)).nlargest(3)
    for p in pairs:
        for leg in ("long", "short"):
            sym = p[leg]
            x, _ = eye.pictures(S, d, np.array([col[sym]]), short=(leg == "short"))
            z = tr.transform(x) if tr is not None else x
            dist = np.linalg.norm(Z - z, axis=1)
            dist[ref.symbol.to_numpy() == sym] = np.inf            # a reference must be another stock
            k = int(np.argmin(dist))
            p[f"{leg}_ref"] = f"{ref.symbol[k]} {ref.side[k]} {pd.Timestamp(ref.date[k]).date()}"
            dd = hist[sym].loc[:d]
            etf = hist.get(scan.SECTOR_ETF[meta.at[sym, "supersector"]])
            row = {"side": leg, "supersector": p["ss"], "sector_etf": scan.SECTOR_ETF[meta.at[sym, "supersector"]],
                   "company": meta.at[sym, "company"] if "company" in meta else "", "tags": [f"eye {p['ls' if leg == 'long' else 'ss_']:.2f}"]}
            try:
                charts.render(sym, row, dd, spx.loc[:d], etf.Close.loc[:d] if etf is not None else None,
                              cdir / f"{sym}_{leg}.png", cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("chart %s: %s", sym, e)
    return {"asof": str(d.date()), "pairs": pairs,
            "top3": {side: {f"{a} {b}": round(float(v), 3) for (a, b), v in t.items()} for side, t in top.items()}}


def main(out_dir: pathlib.Path, sessions: int):
    import joblib
    out_dir.mkdir(parents=True, exist_ok=True)
    marks = pd.read_json(ROOT / "labels" / "marks.jsonl", lines=True)
    fb = pd.read_json(ROOT / "history" / "desk" / "feedback.jsonl", lines=True)
    fb["run_date"] = fb.run_id.str[:10]
    grades = fb.sort_values("created_at").drop_duplicates(["symbol", "run_id"], keep="last")[["symbol", "run_date", "side", "grade"]]
    meta, hist, daily, spx, sb, C, O = load(sessions, sorted(set(marks.symbol) | set(grades.symbol)))
    S = eye.Store(daily, spx, sb, scan.CFG["sector_rotation"]["trend_len"])
    mk = resolve_marks(marks, daily["Close"])
    smp = build_samples(S, mk, grades, meta)
    X, ok = featurize(S, smp)
    smp, X = smp[ok].reset_index(drop=True), X[ok]
    log.info("samples %s", smp.groupby(["kind", "y"]).size().to_dict())
    out = {"computed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "history": [str(S.idx[0].date()), str(S.idx[-1].date())], "universe": int(len(meta)),
           "marks_dated": int(len(mk)), "samples": {f"{k} y={y}": int(n) for (k, y), n in smp.groupby(["kind", "y"]).size().items()},
           "symbols_reviewed": int(smp[smp.kind.isin(["entry", "chart_neg"])].symbol.nunique())}
    out["cv"] = validate(smp, X)
    best = max(out["cv"], key=lambda k: (out["cv"][k]["auc_entries_vs_same_charts"] or 0))
    out["chosen_model"] = best
    kind, npca, Cc = MODELS[best]
    model = eye.fit(eye.eye_model(kind, npca, Cc), X, smp.y.to_numpy(), smp.w.to_numpy())
    joblib.dump({"model": model, "name": best, "trained_on": out["samples"], "asof": out["history"][1]}, out_dir / "eye_model.joblib")
    oos_end = pd.Timestamp(smp[smp.kind != "universe_neg"].date.min()) - pd.Timedelta(days=30)
    oos_end = min(oos_end, pd.Timestamp(smp.date.min()))
    out["oos_end"] = str(oos_end.date())
    fridays = [f for f in to_weekly({"Close": C[[C.columns[0]]]})["Close"].index
               if S.idx.get_loc(f) >= 470 and S.widx.searchsorted(f, side="right") - 1 >= eye.WK + 40]
    out["backtest_from"] = str(fridays[0].date())
    out["backtest"] = {
        "1W, 1 pair/sector": backtest(S, model, meta, C, O, fridays, 5, 1, oos_end, 1),
        "1W, 2 pairs/sector": backtest(S, model, meta, C, O, fridays, 5, 1, oos_end, 2),
        "1M, 1 pair/sector": backtest(S, model, meta, C, O, fridays, 21, 4, oos_end, 1),
        "2M, 1 pair/sector": backtest(S, model, meta, C, O, fridays, 42, 8, oos_end, 1),
        "random 1W, 1 pair/sector": backtest(S, model, meta, C, O, fridays, 5, 1, oos_end, 1, random_pairs=True),
        "random 1M, 1 pair/sector": backtest(S, model, meta, C, O, fridays, 21, 4, oos_end, 1, random_pairs=True),
    }
    for k, v in out["backtest"].items():
        log.info("BT %s: %s", k, v)
    out["event_study_oos"] = event_study(S, model, meta, C, fridays, oos_end)
    out["this_week"] = picks(S, model, meta, hist, spx, sb, smp, X, out_dir, scan.CFG)
    (out_dir / "eye_lab.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("history", "marks_dated", "samples", "chosen_model", "oos_end", "backtest_from")}, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "eye",
         int(sys.argv[2]) if len(sys.argv) > 2 else 2300)
