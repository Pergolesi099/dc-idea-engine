#!/usr/bin/env python3
"""Stage 1 of the weekly review: turn the scanner output into material Claude can review fast.

Usage: python review/prepare.py <scan_output_dir> <work_dir>
  scan_output_dir: checkout of the `output` branch (results.json, charts/, returns.csv)

Writes to work_dir:
  candidates.tsv      one line per candidate, the numbers that matter for a chart read
  sheets/*.png        contact sheets, one per supersector+side (up to 6 charts, 2x3 grid)
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

cols = ["key", "supersector", "industry", "tags", "d21", "d50", "d200", "p1w", "p1m", "p3m",
        "r2_63", "er_63", "sma21_crosses_63", "vol_63_ann", "rs_1m", "final_score"]
cands[cols].round(2).to_csv(work / "candidates.tsv", sep="\t", index=False)

# contact sheets
W, H = 1100, 700
for (ss, side), g in cands.groupby(["supersector", "side"]):
    imgs = [Image.open(src / c).convert("RGB") for c in g.chart]
    for n in range(0, len(imgs), 6):
        batch = imgs[n:n + 6]
        sheet = Image.new("RGB", (W, H // 2 * 3), "white")
        for i, im in enumerate(batch):
            im = im.resize((W // 2, H // 2), Image.LANCZOS)
            sheet.paste(im, ((i % 2) * W // 2, (i // 2) * H // 2))
        name = f"{ss.replace(' ', '_')}_{side}_{n // 6 + 1}.png"
        sheet.save(work / "sheets" / name, optimize=True)

# pair matrix
rets = pd.read_csv(src / "returns.csv", index_col=0, parse_dates=True).iloc[-60:]
rows = []
for ss, g in cands.groupby("supersector"):
    longs, shorts = g[g.side == "long"].symbol, g[g.side == "short"].symbol
    for lo in longs:
        for sh in shorts:
            if lo == sh or lo not in rets or sh not in rets:
                continue
            a, b = rets[lo].dropna(), rets[sh].dropna()
            j = a.index.intersection(b.index)
            if len(j) < 40:
                continue
            a, b = a[j], b[j]
            rows.append({"supersector": ss, "long": lo, "short": sh,
                         "corr60": round(float(a.corr(b)), 2),
                         "vol_ratio": round(float(a.std() / b.std()), 2),        # long vol / short vol
                         "beta_long_on_short": round(float(np.cov(a, b)[0, 1] / b.var()), 2)})
pm = pd.DataFrame(rows)
pm.to_csv(work / "pair_matrix.tsv", sep="\t", index=False)
print(f"{len(cands)} candidates, {len(list((work / 'sheets').glob('*.png')))} sheets, {len(pm)} pair combos")
