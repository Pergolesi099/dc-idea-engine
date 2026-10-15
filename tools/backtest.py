#!/usr/bin/env python3
"""Full-pipeline backtest: select -> pair -> size -> one-week P&L, every week of the price history.

Usage: python tools/backtest.py <out.json>

Uses the live selection code (model.side_table / score / select) so a variant is exactly what the scan
would have shown that Saturday. Mechanical pairing stands in for the review: in each supersector the
i-th best long is paired with the i-th best short; the book is the 10 best pairs by combined score,
at most 2 per sector. Entry = next session's open, exit = close 5 sessions later (Monday open to
Friday close). Vol-balanced split as on the desk; beta-neutral split reported alongside.

Weights: the PRIOR weights for every variant, so differences come from the rules, not from weights
learned on the same history. Not testable here: the earnings gate (no historical calendar), borrow.
Survivorship: today's universe, so delisted names are missing and results are flattering for longs.
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
from model import PRIOR, normalise, score, select, side_table  # noqa: E402

log = logging.getLogger("backtest")
N_PAIRS, PER_SECTOR, HOLD = 10, 2, 5


def variants(base: dict) -> dict:
    def v(**risk):
        c = copy.deepcopy(base)
        c["risk"] = {**base.get("risk", {}), **risk}
        return c
    return {
        "v2.1": v(enabled=False),
        "v2.2": v(enabled=True),
        "v2.1 + short fix only": v(enabled=True, stretch_max_sigma=None),
        "v2.1 + stretch cap only": v(enabled=True, short_knife_max_below21=None, short_require_bounce=False,
                                     short_tags_barred=[]),
    }


def book(snap, cfg, w, quota):
    tabs = []
    for side in ("long", "short"):
        t = side_table(snap, side, cfg)
        if t.empty:
            continue
        t["final_score"] = score(t, w).groupby(t.supersector).rank(pct=True)
        tabs.append(t)
    if not tabs:
        return []
    allc = pd.concat(tabs).reset_index()
    best = allc.sort_values("final_score", ascending=False).drop_duplicates("symbol")
    sel = select(best, quota)
    pairs = []
    for ss, g in sel.groupby("supersector"):
        L = g[g.side == "long"].sort_values("final_score", ascending=False)
        S = g[g.side == "short"].sort_values("final_score", ascending=False)
        for i in range(min(len(L), len(S))):
            pairs.append({"ss": ss, "long": L.symbol.iloc[i], "short": S.symbol.iloc[i],
                          "score": (L.final_score.iloc[i] + S.final_score.iloc[i]) / 2, "rank": i})
    pairs = sorted(pairs, key=lambda p: -p["score"])
    out, per = [], {}
    for p in pairs:
        if per.get(p["ss"], 0) < PER_SECTOR:
            out.append(p)
            per[p["ss"]] = per.get(p["ss"], 0) + 1
        if len(out) == N_PAIRS:
            break
    return out


def stats(weeks: pd.DataFrame, col: str) -> dict:
    r = weeks[col].dropna()
    eq = (1 + r / 100).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    return {"weeks": int(len(r)), "mean_week_pct": round(r.mean(), 3), "stdev_week_pct": round(r.std(), 3),
            "sharpe_ann": round(r.mean() / r.std() * np.sqrt(52), 2) if r.std() > 0 else None,
            "hit_rate_weeks": round((r > 0).mean(), 3), "worst_week_pct": round(r.min(), 2),
            "best_week_pct": round(r.max(), 2), "max_drawdown_pct": round(dd, 2),
            "cumulative_pct": round((eq.iloc[-1] - 1) * 100, 1) if len(eq) else None}


def main(out_path: pathlib.Path, meta=None, history_fn=None):
    cfg0 = scan.CFG
    if meta is None:
        meta = pd.read_csv(ROOT / "data" / "universe.csv").drop_duplicates("symbol").set_index("symbol")
    etfs = list(scan.SECTOR_ETF.values())
    hist = (history_fn or scan.get_history)(list(meta.index) + etfs + [scan.BENCH, "SPY"], cfg0["data"]["history_sessions"])
    daily, spx, sector_bench, _ = scan.build_panels(hist, meta)
    meta = meta.loc[daily["Close"].columns]
    panels = compute_panels(daily, spx, sector_bench, cfg0)
    panels["sessions"] = daily["Close"].notna().cumsum().astype("float32")
    C, O = daily["Close"], daily["Open"]
    idx = C.index
    lr, lspx = np.log(C).diff(), np.log(spx).diff()
    week_ends = to_weekly({"Close": C[[C.columns[0]]]})["Close"].index
    dates = [d for d in week_ends if idx[300] <= d and idx.get_loc(d) + HOLD < len(idx)]
    spy_open, spy_close = hist.get("SPY", hist.get(scan.BENCH)).Open.reindex(idx), spx
    w = normalise(PRIOR)
    quota = cfg0["selection"]["quota_per_side_per_sector"]
    snaps = {d: snapshot(panels, d).join(meta, how="inner") for d in dates}
    log.info("Backtest over %d weeks (%s to %s), %d names", len(dates), dates[0].date(), dates[-1].date(), C.shape[1])

    results = {}
    for name, cfg in variants(cfg0).items():
        rows, prs = [], []
        for d in dates:
            i0 = idx.get_loc(d)
            e, x = idx[i0 + 1], idx[i0 + HOLD]
            ps = book(snaps[d], cfg, w, quota)
            pr_v, pr_b, lg, sh, nb = [], [], [], [], []
            for p in ps:
                a, b = p["long"], p["short"]
                rl = C.at[x, a] / O.at[e, a] - 1
                rs = C.at[x, b] / O.at[e, b] - 1
                if not (np.isfinite(rl) and np.isfinite(rs)):
                    continue
                win = lr.loc[:d].iloc[-60:]
                va, vb = win[a].std(), win[b].std()
                sl = vb / (va + vb) if va > 0 and vb > 0 else 0.5            # vol-balanced (= 1 / (1 + vL/vS))
                j = pd.concat([win[a], win[b], lspx.loc[:d].iloc[-60:]], axis=1).dropna()
                ba = j.iloc[:, 0].cov(j.iloc[:, 2]) / j.iloc[:, 2].var() if len(j) > 20 else 1
                bb = j.iloc[:, 1].cov(j.iloc[:, 2]) / j.iloc[:, 2].var() if len(j) > 20 else 1
                sb = bb / (ba + bb) if ba > 0.2 and bb > 0.2 else sl                # beta-neutral when betas sane
                pr_v.append((sl * rl - (1 - sl) * rs) * 100)
                pr_b.append((sb * rl - (1 - sb) * rs) * 100)
                lg.append(rl * 100)
                sh.append(rs * 100)
                nb.append(sl * ba - (1 - sl) * bb)
                prs.append({"date": str(d.date()), "long": a, "short": b, "pair_vol": round(pr_v[-1], 3)})
            rows.append({"date": d, "n": len(pr_v), "port_vol": np.mean(pr_v) if pr_v else np.nan,
                         "port_beta": np.mean(pr_b) if pr_b else np.nan,
                         "long_leg": np.mean(lg) if lg else np.nan, "short_leg": np.mean(sh) if sh else np.nan,
                         "net_beta": np.mean(nb) if nb else np.nan,
                         "spy": (spy_close.iloc[i0 + HOLD] / spy_open.iloc[i0 + 1] - 1) * 100})
        wk = pd.DataFrame(rows).set_index("date")
        pairs_df = pd.DataFrame(prs)
        res = {"vol_balanced": stats(wk, "port_vol"), "beta_neutral": stats(wk, "port_beta"),
               "avg_pairs_per_week": round(wk.n.mean(), 1),
               "pair_hit_rate": round((pairs_df.pair_vol > 0).mean(), 3) if len(pairs_df) else None,
               "long_leg_avg_pct": round(wk.long_leg.mean(), 3), "short_leg_avg_pct": round(wk.short_leg.mean(), 3),
               "avg_net_beta_vol_split": round(wk.net_beta.mean(), 3),
               "corr_to_spy": round(wk.port_vol.corr(wk.spy), 2),
               "spy_mean_week_pct": round(wk.spy.mean(), 3),
               "last_12_weeks": [{"week": str(k.date()), "port": round(v, 2)} for k, v in wk.port_vol.tail(12).items()]}
        log.info("%s: %s", name, res["vol_balanced"])
        results[name] = res
    out = {"computed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "engine_code": scan.ENGINE, "weeks": len(dates), "from": str(dates[0].date()), "to": str(dates[-1].date()),
           "universe": int(C.shape[1]), "book": f"{N_PAIRS} pairs, max {PER_SECTOR} per sector, Mon open -> Fri close",
           "weights": "prior (same for every variant)",
           "not_tested": ["earnings gate (no historical calendar)", "borrow availability/cost", "commissions"],
           "survivorship": "today's universe: delisted names missing",
           "variants": results}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "variants"}, indent=1))
    for k, v in results.items():
        print(k, v["vol_balanced"], "short leg", v["short_leg_avg_pct"], "long leg", v["long_leg_avg_pct"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out" / "backtest.json")
