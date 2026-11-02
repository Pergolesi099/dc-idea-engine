#!/usr/bin/env python3
"""Engine v2.4 candidate lab: how well does the engine match Dean's eye, and does his read make money?

Usage: python tools/lab_v24.py <out.json> [history_sessions]

1. Marks (B-006). labels/marks.jsonl (tools/harvest_marks.py) is resolved to dates on the real trading
   calendar: a chart's last bar is the last session before its run date; a daily mark sits
   `bars_back` sessions before it, a weekly mark that many weeks before. For Dean's long/short entry
   lines we measure, per signal, the share of his marks with that signal firing in the same direction
   within +-3 sessions (hit), the share of all days in the same charts' window with it (base), and
   lift = hit / base. Lift > 1 means the signal fires where he enters more than by chance.
   Engine view: is the name eligible (passes the weekly gate + a setup, same side) and is it picked
   (in the scan's quota) on the first Friday 0-5 sessions after his mark, v2.3 vs v2.4 variants.
2. Event study (B-005). For every Friday and every gated candidate, forward return of the leg vs its
   sector ETF, side-adjusted, entry next open, over 1W / 1M / 2M, by setup tag and by half.
3. Backtest. The live book replay of tools/backtest.py (10 pairs, max 2 per sector, Mon open -> Fri
   close, prior weights) for v2.3 and the v2.4 variants, plus a 1-month hold for v2.3 vs v2.4.
Selection bias: variants are chosen on the same history they are scored on. Ship only a variant that
beats v2.3 in both halves and matches Dean's marks better.
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
sys.path.insert(0, str(ROOT / "tools"))
import scan  # noqa: E402
from backtest import book, stats  # noqa: E402
from features import bars_since, compute_panels, crossover, crossunder, sma, snapshot, to_weekly  # noqa: E402
from model import PRIOR, PRIOR_V24, normalise, select, side_table, score  # noqa: E402

log = logging.getLogger("lab")
WIN = 3
LIMIT = 0


def variants(base: dict) -> dict:
    def v(chop=None, tags=(), nconf=0, w=PRIOR):
        c = copy.deepcopy(base)
        c["risk"] = {**base["risk"], "chop": chop}
        c["v24"] = {**base.get("v24", {}), "tags": list(tags), "trigger_min_confluence": nconf}
        return c, normalise(w)
    CH = {"max_x21": 6, "min_r2": 0.1}
    T = ("TURN", "B200", "XBRK")
    return {
        "v2.3": v(),
        "+chop (B-003)": v(chop=CH),
        "+tags (B-002, B-001b/c)": v(tags=T),
        "+score (confluence, sma_turn)": v(w=PRIOR_V24),
        "v2.4 all": v(chop=CH, tags=T, w=PRIOR_V24),
        "v2.4 all + trigger needs 3 crosses": v(chop=CH, tags=T, nconf=3, w=PRIOR_V24),
        "v2.4 all + trigger needs 2 crosses": v(chop=CH, tags=T, nconf=2, w=PRIOR_V24),
    }


def load(sessions: int, extra: list[str]):
    cfg = copy.deepcopy(scan.CFG)
    meta = pd.read_csv(ROOT / "data" / "universe.csv").drop_duplicates("symbol").set_index("symbol")
    if LIMIT:                                            # offline smoke test only
        meta = meta[meta.index.isin(extra) | (np.arange(len(meta)) < LIMIT)]
    etfs = list(scan.SECTOR_ETF.values())
    hist = scan.get_history(list(meta.index) + [s for s in extra if s not in meta.index] + etfs + [scan.BENCH, "SPY"], sessions)
    daily, spx, sb, _ = scan.build_panels(hist, meta)
    meta = meta.loc[daily["Close"].columns]
    panels = compute_panels(daily, spx, sb, cfg)
    panels["sessions"] = daily["Close"].notna().cumsum().astype("float32")
    C, O = daily["Close"].copy(), daily["Open"].copy()
    for e in etfs:
        if e in hist:
            C[e] = hist[e].Close.reindex(C.index)
            O[e] = hist[e].Open.reindex(C.index)
    return cfg, meta, hist, daily, spx, sb, panels, C, O


def events(daily, spx, sb, panels, cfg) -> dict:
    """Per-direction daily event panels (bool), v2.3 signals and Dean's crosses."""
    c, h, l = daily["Close"], daily["High"], daily["Low"]
    s21, s50, s200 = sma(c, 21), sma(c, 50), sma(c, 200)
    m = c.ewm(span=12, adjust=False, min_periods=12).mean() - c.ewm(span=26, adjust=False, min_periods=26).mean()
    hist = m - m.ewm(span=9, adjust=False, min_periods=9).mean()
    rs_d = c.div(spx.reindex(c.index), axis=0)
    rsd = c / sb
    rsd_ma = sma(rsd, cfg["sector_rotation"]["trend_len"])
    rs_ma = sma(rs_d, 200)
    w = to_weekly({"Close": c})["Close"]
    w40 = sma(w, 40)
    spx_w = spx.resample("W-FRI").last().reindex(w.index, method="ffill")
    rsw = w.div(spx_w, axis=0)
    rsw_ma = sma(rsw, 40)

    def wk(e):                                            # weekly event -> the week's last session
        return e.astype("float32").reindex(c.index).fillna(0) > 0
    P = panels
    bo, ms, st = P["breakout"], P["macd_sig"], P["sec_turn"]
    E = {}
    for d, sg in (("long", 1), ("short", -1)):
        X, U = (crossover, crossunder) if sg == 1 else (crossunder, crossover)
        E[d] = {
            # v2.3 triggers / tags as they fire (first day)
            "v2.3 MACD flip": X(hist, hist * 0),
            "v2.3 breakout": (bo * sg > 0) & ~(bo.shift(1) * sg > 0),
            "v2.3 sector turn": (st * sg > 0) & ~(st.shift(1) * sg > 0),
            "v2.3 weekly RS cross 40W": wk(X(rsw, rsw_ma)),
            # Dean's crosses
            "price x 21D": X(c, s21), "price x 50D": X(c, s50), "21D x 50D": X(s21, s50), "price x 200D": X(c, s200),
            "daily RS x 200D MA": X(rs_d, rs_ma), "vs sector x 50D MA": X(rsd, rsd_ma), "weekly price x 40W": wk(X(w, w40)),
            # v2.4 composites (first day the panel turns on)
            "v2.4 confluence>=2": (P["confluence"] * sg >= 2) & ~(P["confluence"].shift(1) * sg >= 2),
            "v2.4 confluence>=3": (P["confluence"] * sg >= 3) & ~(P["confluence"].shift(1) * sg >= 3),
            "v2.4 sma_turn": (P["sma_turn"] * sg > 0) & ~(P["sma_turn"].shift(1) * sg > 0),
            "v2.4 TURN": (P["turn_setup"] * sg > 0) & ~(P["turn_setup"].shift(1) * sg > 0),
            "v2.4 B200": (P["b200"] * sg > 0) & ~(P["b200"].shift(1) * sg > 0),
            "v2.4 XBRK": (P["xbrk"] * sg > 0) & ~(P["xbrk"].shift(1) * sg > 0),
        }
    return E


def resolve_marks(marks: pd.DataFrame, C: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in marks.itertuples():
        if r.symbol not in C:
            continue
        ix = C[r.symbol].dropna().index
        last = ix[ix < pd.Timestamp(r.run_date)]
        if not len(last):
            continue
        if r.tf == "D":
            k = len(last) - 1 - int(round(r.bars_back))
            if k < 0:
                continue
            d0 = d1 = last[k]
        else:
            wk = last.to_series().resample("W-FRI").last().dropna()
            k = len(wk) - 1 - int(round(r.bars_back))
            if k < 0:
                continue
            d1 = wk.iloc[k]
            d0 = last[max(0, last.get_loc(d1) - 4)]
        rows.append({**r._asdict(), "d0": d0, "d1": d1})
    return pd.DataFrame(rows)


NPERM = 300


def _hits(groups, ev_of, shift=None, rng=None):
    """Hit (event within the mark's window) per mark. groups: per chart, (span lo, hi, [(key, a, b)]).
    With shift, every mark of a chart moves by the same random offset (wrapping inside the chart's
    span), which keeps Dean's spacing between marks: the null for "his marks sit on this signal"."""
    out = []
    for lo, hi, ms in groups:
        off = int(rng.integers(0, hi - lo)) if shift else 0
        for key, a, b in ms:
            e = ev_of(key)
            if shift:
                w = b - a
                a = lo + (a - lo + off) % max(1, (hi - lo - w))
                b = a + w
            out.append(bool(e[max(a, 0):b + 1].any()))
    return np.array(out, bool)


def score_marks(mk: pd.DataFrame, E: dict, C: pd.DataFrame) -> dict:
    """Signal hit rates on Dean's marks vs a permutation null (same charts, marks shifted together).
    lift = hit / null mean; p = share of shuffles with a hit rate >= Dean's."""
    idx = C.index
    rng = np.random.default_rng(7)
    ent = mk[mk.cls.isin(["entry_long", "entry_short"])].copy()
    ent["dir"] = np.where(ent.cls == "entry_long", "long", "short")
    bx = mk[mk.cls.isin(["cross", "flip", "turnaround"]) & (mk.tf == "D")].copy()
    bx["dir"] = "both"
    out = {"entry_marks": int(len(ent)), "by_tf": ent.groupby("tf").size().to_dict(),
           "box_marks": int(len(bx)), "permutations": NPERM, "signals": {}, "boxes": {}}

    def groups(df):
        g = []
        for (sym, run, tf), x in df.groupby(["symbol", "run_date", "tf"]):
            j = idx.get_loc(idx[idx < pd.Timestamp(run)][-1])
            lo = j - (5 * 104 if tf == "W" else 251)
            g.append((lo, j, [((r.dir, sym), idx.get_loc(r.d0) - WIN, idx.get_loc(r.d1) + WIN) for r in x.itertuples()]))
        return g
    cache = {}
    for name in E["long"]:
        def ev_of(key, name=name):
            d, sym = key
            k = (name, d, sym)
            if k not in cache:
                cache[k] = (E["long"][name][sym].to_numpy(bool) | E["short"][name][sym].to_numpy(bool)) if d == "both" \
                    else E[d][name][sym].to_numpy(bool)
            return cache[k]
        res = {}
        for label, df in (("D", ent[ent.tf == "D"]), ("W", ent[ent.tf == "W"]), ("boxes", bx)):
            df = df[df.symbol.isin(E["long"][name].columns)]
            if df.empty:
                res[label] = {"n": 0}
                continue
            G = groups(df)
            obs = _hits(G, ev_of).mean()
            null = np.array([_hits(G, ev_of, True, rng).mean() for _ in range(NPERM)])
            m = null.mean()
            res[label] = {"n": int(len(df)), "hit": round(float(obs), 3), "null": round(float(m), 3),
                          "lift": round(float(obs / m), 2) if m else None,
                          "null_lift_95": round(float(np.quantile(null, .95) / m), 2) if m else None,
                          "p": round(float((null >= obs).mean()), 3)}
        out["signals"][name] = {"D": res["D"], "W": res["W"]}
        out["boxes"][name] = res["boxes"]
    return out


def engine_view(mk, snaps_fn, V, meta, C) -> dict:
    """Is the marked name eligible / picked (same side) by each variant on the first Friday 0-5 sessions
    after Dean's entry mark? Baseline: the same symbol and side on every Friday of that chart's year."""
    idx = C.index
    ent = mk[mk.cls.isin(["entry_long", "entry_short"]) & (mk.tf == "D")].copy()
    ent["dir"] = np.where(ent.cls == "entry_long", "long", "short")
    fridays = to_weekly({"Close": C[[C.columns[0]]]})["Close"].index
    ent["fri"] = [next((f for f in fridays if f >= d and idx.get_loc(f) - idx.get_loc(d) <= 5), None) for d in ent.d1]
    ent = ent.dropna(subset=["fri"])
    first = idx[max(0, idx.get_loc(idx[idx < pd.Timestamp(ent.run_date.min())][-1]) - 256)]
    fris = [f for f in fridays if f >= first]
    res = {}
    for name, (cfg, w) in V.items():
        book_ = {}
        for f in fris:
            snap = snaps_fn(f)
            tabs = []
            for side in ("long", "short"):
                t = side_table(snap, side, cfg)
                if not t.empty:
                    t["final_score"] = score(t, w).groupby(t.supersector).rank(pct=True)
                    tabs.append(t)
            if not tabs:
                book_[f] = (set(), set())
                continue
            allc = pd.concat(tabs).reset_index()
            best = allc.sort_values("final_score", ascending=False).drop_duplicates("symbol")
            sel = select(best, cfg["selection"]["quota_per_side_per_sector"])
            book_[f] = (set(zip(allc.symbol, allc.side)), set(zip(sel.symbol, sel.side)))
        el, pk, bel, bpk = [], [], [], []
        for r in ent.itertuples():
            k = (r.symbol, r.dir)
            el.append(k in book_[r.fri][0])
            pk.append(k in book_[r.fri][1])
            end = pd.Timestamp(r.run_date)
            yr = [f for f in fris if end - pd.Timedelta(days=365) <= f < end]
            bel.append(np.mean([k in book_[f][0] for f in yr]))
            bpk.append(np.mean([k in book_[f][1] for f in yr]))
        e, p_, be, bp = (float(np.mean(x)) for x in (el, pk, bel, bpk))
        res[name] = {"n": len(el), "eligible": round(e, 3), "eligible_base": round(be, 3),
                     "eligible_lift": round(e / be, 2) if be else None,
                     "picked": round(p_, 3), "picked_base": round(bp, 3), "picked_lift": round(p_ / bp, 2) if bp else None}
        log.info("engine on marks %s: %s", name, res[name])
    return res


def event_study(snaps, dates, V, C, O, meta) -> dict:
    idx = C.index
    cfg, w = V["v2.4 all"]
    rows = []
    for d in dates:
        i0 = idx.get_loc(d)
        if i0 + 43 >= len(idx):
            continue
        snap = snaps[d]
        for side in ("long", "short"):
            t = side_table(snap, side, cfg)
            if t.empty:
                continue
            sg = 1 if side == "long" else -1
            etf = t.supersector.map(scan.SECTOR_ETF)
            for h in (5, 21, 42):
                leg = C.iloc[i0 + h][t.index].to_numpy() / O.iloc[i0 + 1][t.index].to_numpy() - 1
                eb = np.array([C.iloc[i0 + h].get(e, np.nan) / O.iloc[i0 + 1].get(e, np.nan) - 1 for e in etf])
                t[f"f{h}"] = sg * (leg - eb) * 100
            t["date"] = d
            rows.append(t[["date", "side", "tags", "f5", "f21", "f42"]])
    df = pd.concat(rows)
    half = sorted(df.date.unique())[len(df.date.unique()) // 2]
    out = {"half_split": str(pd.Timestamp(half).date()), "by_tag": {}}
    allm = {h: df[h].mean() for h in ("f5", "f21", "f42")}
    out["all_candidates"] = {h: round(v, 3) for h, v in allm.items()} | {"n": int(len(df))}
    for tg in ["S1", "S2", "S3", "MOM", "LEAD", "SECT", "BO", "MACD", "TURN", "B200", "XBRK"]:
        for side in ("long", "short"):
            g = df[(df.side == side) & df.tags.apply(lambda x: tg in x)]
            if len(g) < 30:
                continue
            r = {"n": int(len(g))}
            for hcol in ("f5", "f21", "f42"):
                x = g[hcol].dropna()
                r[hcol] = round(x.mean(), 3)
                r[hcol + "_t"] = round(x.mean() / x.std() * np.sqrt(len(x)), 1) if x.std() > 0 else None
                r[hcol + "_h1"] = round(g[g.date < half][hcol].mean(), 3)
                r[hcol + "_h2"] = round(g[g.date >= half][hcol].mean(), 3)
            out["by_tag"][f"{tg} {side}"] = r
    return out


def run_book(V, snaps, dates, C, O, hold, lr, step=1):
    idx = C.index
    res = {}
    for name, (cfg, w) in V.items():
        rows = []
        for d in dates[::step]:
            i0 = idx.get_loc(d)
            if i0 + hold >= len(idx):
                continue
            e, x = idx[i0 + 1], idx[i0 + hold]
            ps = book(snaps[d], cfg, w, cfg["selection"]["quota_per_side_per_sector"], scan.SECTOR_ETF)
            pr = []
            for p in ps:
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
        half = len(wk) // 2
        ann = np.sqrt(52 / step)
        st = lambda r: {**stats(r.to_frame("p"), "p"), "sharpe_ann": round(r.mean() / r.std() * ann, 2) if r.std() > 0 else None}
        res[name] = {"all": st(wk.port), "first_half": st(wk.port.iloc[:half]), "second_half": st(wk.port.iloc[half:]),
                     "split": str(wk.index[half].date()), "avg_pairs": round(wk.n.mean(), 1)}
        log.info("hold %d %s: %s", hold, name, res[name]["all"])
    return res


def main(out_path: pathlib.Path, sessions: int):
    marks = pd.read_json(ROOT / "labels" / "marks.jsonl", lines=True)
    cfg0, meta, hist, daily, spx, sb, panels, C, O = load(sessions, sorted(marks.symbol.unique()))
    idx = C.index
    lr = np.log(C).diff()
    V = variants(cfg0)
    E = events(daily, spx, sb, panels, cfg0)
    mk = resolve_marks(marks, daily["Close"])
    log.info("marks resolved: %d of %d", len(mk), len(marks))
    out = {"computed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "history": [str(idx[0].date()), str(idx[-1].date())], "universe": int(len(meta)),
           "marks_total": int(len(marks)), "marks_resolved": int(len(mk)),
           "marks_missing_symbols": sorted(set(marks.symbol) - set(daily["Close"].columns))}
    out["marks"] = score_marks(mk, E, daily["Close"])
    week_ends = to_weekly({"Close": C[[C.columns[0]]]})["Close"].index
    snap_cache = {}

    def snaps_fn(d):
        if d not in snap_cache:
            snap_cache[d] = snapshot(panels, d).join(meta, how="inner")
        return snap_cache[d]
    out["engine_on_marks"] = engine_view(mk, snaps_fn, V, meta, C)
    dates = [d for d in week_ends if idx[300] <= d and idx.get_loc(d) + 5 < len(idx)]
    snaps = {d: snaps_fn(d) for d in dates}
    out["event_study"] = event_study(snaps, dates, V, C, O, meta)
    out["backtest_1w"] = run_book(V, snaps, dates, C, O, 5, lr)
    V1m = {k: V[k] for k in ("v2.3", "v2.4 all", "v2.4 all + trigger needs 3 crosses")}
    out["backtest_1m"] = run_book(V1m, snaps, dates, C, O, 21, lr, step=4)
    out["weeks"] = len(dates)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1, default=str))
    (out_path.parent / "marks_resolved.csv").write_text(mk.to_csv(index=False))
    print(json.dumps({k: out[k] for k in ("history", "universe", "marks_resolved", "weeks")}))
    for k, v in out["backtest_1w"].items():
        print("1W", k, v["all"]["sharpe_ann"], v["first_half"]["sharpe_ann"], v["second_half"]["sharpe_ann"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "lab_v24.json",
         int(sys.argv[2]) if len(sys.argv) > 2 else 1300)
