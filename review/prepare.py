#!/usr/bin/env python3
"""Stage 1 of the weekly review: turn the scanner output into material Claude can review fast.

Usage: python review/prepare.py <scan_output_dir> <work_dir>
  scan_output_dir: checkout of the `output` branch (results.json, charts/, returns.csv)

Writes to work_dir:
  candidates.tsv      one line per candidate: family, tags, weekly regime, sector state, key numbers
  sheets/*.png        contact sheets, 3 charts per sheet, grouped by supersector + side
  pair_matrix.tsv     every long x short within a supersector: 60D correlation, vol ratio, beta
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd
from PIL import Image

src, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
(work / "sheets").mkdir(parents=True, exist_ok=True)
res = json.loads((src / "results.json").read_text())
cands = pd.DataFrame(res["candidates"])
cands["key"] = cands.symbol + "|" + cands.side
cands["tags"] = cands.tags.apply(",".join)

cols = ["key", "supersector", "industry", "family", "tags", "regime", "sector_state", "rs_lead", "breakout",
        "macd_sig", "d21", "d50", "d200", "p1m", "p3m", "p6m", "rs_vs_ma", "rssec_vs_ma", "r2_63", "x21_63",
        "vol63", "final_score", "next_earnings"]
cands[[c for c in cols if c in cands]].round(2).to_csv(work / "candidates.tsv", sep="\t", index=False)

PER, W = 3, 1200
for (ss, side), g in cands.groupby(["supersector", "side"]):
    imgs = [Image.open(src / c).convert("RGB") for c in g.chart]
    for n in range(0, len(imgs), PER):
        batch = [im.resize((W, int(im.height * W / im.width)), Image.LANCZOS) for im in imgs[n:n + PER]]
        sheet = Image.new("RGB", (W, sum(im.height for im in batch)), "white")
        y = 0
        for im in batch:
            sheet.paste(im, (0, y))
            y += im.height
        sheet.save(work / "sheets" / f"{ss.replace(' ', '_')}_{side}_{n // PER + 1}.png", optimize=True)

rets = pd.read_csv(src / "returns.csv", index_col=0, parse_dates=True).iloc[-60:]
rows = []
for ss, g in cands.groupby("supersector"):
    for lo in g[g.side == "long"].symbol:
        for sh in g[g.side == "short"].symbol:
            if lo == sh or lo not in rets or sh not in rets:
                continue
            a, b = rets[lo].dropna(), rets[sh].dropna()
            j = a.index.intersection(b.index)
            if len(j) < 40:
                continue
            a, b = a[j], b[j]
            rows.append({"supersector": ss, "long": lo, "short": sh, "corr60": round(float(a.corr(b)), 2),
                         "vol_ratio": round(float(a.std() / b.std()), 2),
                         "beta_long_on_short": round(float(np.cov(a, b)[0, 1] / b.var()), 2)})
pm = pd.DataFrame(rows)
pm.to_csv(work / "pair_matrix.tsv", sep="\t", index=False)
print(f"{len(cands)} candidates, {len(list((work / 'sheets').glob('*.png')))} sheets, {len(pm)} pair combos")
m = res.get("model", {})
print("model:", m.get("weights_source"), {s: v.get("holdout_ic") for s, v in m.get("sides", {}).items()})
fb = pathlib.Path(__file__).resolve().parent.parent / "model" / "feedback_summary.md"
if fb.exists():
    print(f"read {fb} before grading")
