#!/usr/bin/env python3
"""Blind training charts for the Chart Trainer: past charts cut at a date, future hidden, outcome known.

Usage: python tools/blind_batch.py <out_dir> [history_sessions=2300] [n=300] [seed=1]

Each chart is a (stock, Friday) between 2020-03 and two months before today, drawn by the production
renderer in blind mode (no ticker, company, dates or price levels). Half are random stocks, half are
names the v3 eye model (trained on Dean's marks) rates in its top 2% that week, so the trainer also
tells us whether his judgement adds to the model's. Each chart carries its outcome: entry next open,
exit close after 5 / 21 / 42 sessions, the stock's return and its return minus its sector ETF.
Writes <out_dir>/charts/<id>.webp and <out_dir>/manifest.json.
"""
from __future__ import annotations

import json
import logging
import pathlib
import string
import sys

import numpy as np
import pandas as pd
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
sys.path.insert(0, str(ROOT / "tools"))
import charts  # noqa: E402
import eye  # noqa: E402
import eye_lab  # noqa: E402
import scan  # noqa: E402
from features import to_weekly  # noqa: E402

log = logging.getLogger("blind")


def main(out: pathlib.Path, n: int, sessions: int, seed: int):
    rng = np.random.default_rng(seed)
    (out / "charts").mkdir(parents=True, exist_ok=True)
    marks = pd.read_json(ROOT / "labels" / "marks.jsonl", lines=True)
    fb = pd.read_json(ROOT / "history" / "desk" / "feedback.jsonl", lines=True)
    fb["run_date"] = fb.run_id.str[:10]
    grades = fb.sort_values("created_at").drop_duplicates(["symbol", "run_id"], keep="last")[["symbol", "run_date", "side", "grade"]]
    meta, hist, daily, spx, sb, C, O = eye_lab.load(sessions, sorted(set(marks.symbol) | set(grades.symbol)))
    S = eye.Store(daily, spx, sb, scan.CFG["sector_rotation"]["trend_len"])
    mk = eye_lab.resolve_marks(marks, daily["Close"])
    smp = eye_lab.build_samples(S, mk, grades, meta)
    X, ok = eye_lab.featurize(S, smp)
    smp, X = smp[ok].reset_index(drop=True), X[ok]
    model = eye.fit(eye.eye_model("logit_raw", 0, 0.05), X, smp.y.to_numpy(), smp.w.to_numpy())
    idx = S.idx
    fr = [f for f in to_weekly({"Close": C[[C.columns[0]]]})["Close"].index
          if f >= pd.Timestamp("2020-03-01") and idx.get_loc(f) + 43 < len(idx)
          and S.widx.searchsorted(f, side="right") - 1 >= eye.WK + 40 and idx.get_loc(f) >= 470]
    dates = sorted(rng.choice(fr, size=n, replace=True))
    sym_ok = np.array([s in meta.index for s in S.cols])
    used, rows = set(), []
    alphabet = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    for k, d in enumerate(dates):
        src = "eye" if k % 2 else "random"
        sc = eye_lab.score_week(S, model, d, sym_ok)
        side_hint, sym = None, None
        for _ in range(20):
            if src == "eye":
                side_hint = "long" if rng.random() < 0.5 else "short"
                s = pd.Series(sc[side_hint], index=S.cols).dropna()
                top = s[s >= s.quantile(0.98)]
                cand = rng.choice(top.index)
            else:
                cand = rng.choice(S.cols[~np.isnan(sc["long"])])
            if (cand, d) not in used and cand in meta.index:
                sym = cand
                break
        if sym is None:
            continue
        used.add((sym, d))
        i0 = idx.get_loc(d)
        etf = scan.SECTOR_ETF[meta.at[sym, "supersector"]]
        fwd = {}
        for h, lab in ((5, "1w"), (21, "1m"), (42, "2m")):
            r = C.iloc[i0 + h][sym] / O.iloc[i0 + 1][sym] - 1
            e = C.iloc[i0 + h][etf] / O.iloc[i0 + 1][etf] - 1 if etf in C else np.nan
            fwd[f"ret_{lab}"] = round(float(r) * 100, 2) if np.isfinite(r) else None
            fwd[f"rel_{lab}"] = round(float(r - e) * 100, 2) if np.isfinite(r) and np.isfinite(e) else None
        if fwd["rel_1m"] is None:
            continue
        cid = "".join(rng.choice(list(alphabet), 4))
        row = {"side": "long", "supersector": meta.at[sym, "supersector"], "sector_etf": etf}
        png = out / "charts" / f"{cid}.png"
        try:
            charts.render(sym, row, hist[sym].loc[:d], spx.loc[:d], hist[etf].Close.loc[:d] if etf in hist else None,
                          png, scan.CFG, blind=f"#{cid}")
        except Exception as e:  # noqa: BLE001
            log.warning("render %s %s: %s", sym, d.date(), e)
            continue
        Image.open(png).convert("RGB").save(png.with_suffix(".webp"), "WEBP", quality=82, method=6)
        png.unlink()
        rows.append({"id": cid, "symbol": sym, "company": str(meta.at[sym, "company"]) if "company" in meta else "",
                     "date": str(d.date()), "supersector": meta.at[sym, "supersector"], "etf": etf, "source": src,
                     "eye_side": side_hint, "eye_long": round(float(sc["long"][S.cols.get_loc(sym)]), 3),
                     "eye_short": round(float(sc["short"][S.cols.get_loc(sym)]), 3), **fwd})
        if len(rows) % 25 == 0:
            log.info("%d charts", len(rows))
    order = rng.permutation(len(rows))                       # shown in random order, not by date
    rows = [rows[i] for i in order]
    (out / "manifest.json").write_text(json.dumps({"seed": seed, "n": len(rows), "charts": rows}, indent=1))
    df = pd.DataFrame(rows)
    print(df.groupby("source")[["rel_1w", "rel_1m", "rel_2m"]].describe().round(2).T.to_string())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    a = sys.argv[1:]
    main(pathlib.Path(a[0]) if a else ROOT / "out" / "blind", int(a[2]) if len(a) > 2 else 300,
         int(a[1]) if len(a) > 1 else 2300, int(a[3]) if len(a) > 3 else 1)
