#!/usr/bin/env python3
"""Stage 3 of the weekly review: assemble the run document the DC Idea Desk reads.

Usage: python review/build_run.py <scan_output_dir> <work_dir>

Reads from work_dir (written during the review):
  verdicts.json   {"SYM|side": [grade, one-line read]} for every candidate
  pairs.json      {"market_note": str, "pairs": [{long, short, conviction, type, thesis}]}
  pair_matrix.tsv from prepare.py
  uploads.txt     "SYM_side <asset id>" per uploaded chart
Writes work_dir/run_doc.json: one document for the desk's `runs` collection (doc id = run date).
Exits non-zero with a list of problems if the inputs disagree, so a bad run is never published.
"""
import datetime as dt
import json
import pathlib
import sys

import pandas as pd

HOLD_DAYS = 14   # earnings on or before run date + 14 days are flagged "in hold window"

src, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
res = json.loads((src / "results.json").read_text())
verdicts = json.loads((work / "verdicts.json").read_text())
pj = json.loads((work / "pairs.json").read_text())
pm = pd.read_csv(work / "pair_matrix.tsv", sep="\t")
uploads = dict(line.split() for line in (work / "uploads.txt").read_text().split("\n") if line.strip())

run_ts = dt.datetime.fromisoformat(res["run_utc"])
run_date = run_ts.date()
hold_until = run_date + dt.timedelta(days=HOLD_DAYS)
problems = []


def r1(v, d=2):
    return None if v is None else round(float(v), d)


cands = []
by_key = {}
for c in res["candidates"]:
    key = f"{c['symbol']}|{c['side']}"
    grade, read = verdicts.get(key, [None, None])
    if grade is None:
        problems.append(f"no verdict for {key}")
    upl = uploads.get(f"{c['symbol'].replace('.', '-')}_{c['side']}")
    if upl is None:
        problems.append(f"no uploaded chart for {key}")
    ne = c.get("next_earnings")
    row = {
        "symbol": c["symbol"], "company": c["company"], "side": c["side"], "supersector": c["supersector"],
        "industry": c["industry"], "tags": c["tags"], "grade": grade, "read": read,
        "chart_url": f"/_blob/{upl}" if upl else None, "chart_id": upl,
        "next_earnings": ne,
        "earnings_in_window": bool(ne and dt.date.fromisoformat(ne) <= hold_until),
        "stats": {"d21": r1(c["d21"], 1), "d50": r1(c["d50"], 1), "d200": r1(c["d200"], 1),
                  "p1w": r1(c["p1w"], 1), "p1m": r1(c["p1m"], 1), "p3m": r1(c["p3m"], 1),
                  "vol": r1(c["vol_63_ann"], 0), "r2": r1(c["r2_63"]), "x21": int(c["sma21_crosses_63"] or 0)},
        "final_score": r1(c["final_score"], 3),
    }
    cands.append(row)
    by_key[key] = row

pairs, used = [], set()
for i, p in enumerate(pj["pairs"], 1):
    lo, sh = by_key.get(f"{p['long']}|long"), by_key.get(f"{p['short']}|short")
    if lo is None or sh is None:
        problems.append(f"pair {p['long']}/{p['short']}: leg missing from this scan")
        continue
    if lo["supersector"] != sh["supersector"]:
        problems.append(f"pair {p['long']}/{p['short']}: legs in different supersectors")
    for s in (p["long"], p["short"]):
        if s in used:
            problems.append(f"{s} used in more than one pair")
        used.add(s)
    m = pm[(pm.long == p["long"]) & (pm.short == p["short"])]
    corr = vr = None
    if len(m):
        corr, vr = float(m.corr60.iloc[0]), float(m.vol_ratio.iloc[0])
    split_long = round(100 / (1 + vr)) if vr else 50          # equal-risk: weight inversely to vol
    pairs.append({
        "rank": i, "supersector": lo["supersector"], "type": p.get("type", "sector"),
        "conviction": int(p["conviction"]), "thesis": p["thesis"],
        "long": lo, "short": sh,
        "corr60": r1(corr), "vol_ratio": r1(vr), "split_long": split_long, "split_short": 100 - split_long,
    })

if problems:
    print("NOT BUILT. Fix these first:\n  " + "\n  ".join(problems))
    sys.exit(1)

doc = {
    "run_utc": res["run_utc"], "label": run_ts.strftime("%a %-d %b %Y"),
    "source": res["source"], "universe_count": res["universe_count"],
    "hold_until": hold_until.isoformat(), "market_note": pj.get("market_note", ""),
    "candidates": cands, "pairs": pairs,
}
out = work / "run_doc.json"
out.write_text(json.dumps(doc, separators=(",", ":")))
print(f"run {run_date}: {len(cands)} candidates, {len(pairs)} pairs, "
      f"{sum(c['earnings_in_window'] for c in cands)} legs with earnings by {hold_until}, "
      f"{out.stat().st_size / 1024:.0f} KB -> {out}")
print("doc_id", run_date.isoformat())
