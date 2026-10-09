#!/usr/bin/env python3
"""Stage 3 of the weekly review: assemble the run document the DC Idea Desk reads, and archive
the review (grades, reads, pairs) in the repo so the learner can use it next week.

Usage: python review/build_run.py <scan_output_dir> <work_dir>

Reads from work_dir (written during the review):
  verdicts.json   {"SYM|side": [grade, one-line read]} for every candidate
  pairs.json      {"market_note": str, "pairs": [{long, short, conviction, type, thesis}]}
  pair_matrix.tsv from prepare.py
  uploads.txt     "SYM_side <asset id>" per uploaded chart
Writes:
  work_dir/run_doc.json                      one document for the desk's `runs` collection
  <repo>/history/review/<run_id>/verdicts.json, pairs.json   archive for the learner / track record
Exits non-zero with a list of problems if the inputs disagree, so a bad run is never published.
"""
import datetime as dt
import json
import pathlib
import sys

import pandas as pd

HOLD_DAYS = 14
REPO = pathlib.Path(__file__).resolve().parent.parent

src, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
res = json.loads((src / "results.json").read_text())
verdicts = json.loads((work / "verdicts.json").read_text())
pj = json.loads((work / "pairs.json").read_text())
pm = pd.read_csv(work / "pair_matrix.tsv", sep="\t")
uploads = dict(line.split() for line in (work / "uploads.txt").read_text().split("\n") if line.strip())

run_ts = dt.datetime.fromisoformat(res["run_utc"])
run_id = res.get("run_id") or run_ts.date().isoformat()
run_date = dt.date.fromisoformat(run_id[:10])
engine = res.get("engine") or "2.0"
hold_until = run_date + dt.timedelta(days=HOLD_DAYS)
problems = []


def r1(v, d=2):
    return None if v is None else round(float(v), d)


cands, by_key = [], {}
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
        "industry": c["industry"], "tags": c["tags"], "family": c.get("family"), "regime": c.get("regime"),
        "sector_state": c.get("sector_state"), "sector_turn": c.get("sector_turn"),
        "rs_lead": c.get("rs_lead"), "breakout": c.get("breakout"), "macd_sig": c.get("macd_sig"),
        "grade": grade, "read": read, "chart_url": f"/_blob/{upl}" if upl else None, "chart_id": upl,
        "next_earnings": ne, "earnings_in_window": bool(ne and dt.date.fromisoformat(ne) <= hold_until),
        "stats": {"d21": r1(c.get("d21"), 1), "d50": r1(c.get("d50"), 1), "d200": r1(c.get("d200"), 1),
                  "p1w": r1(c.get("p1w"), 1), "p1m": r1(c.get("p1m"), 1), "p3m": r1(c.get("p3m"), 1),
                  "p6m": r1(c.get("p6m"), 1), "rs_ma": r1(c.get("rs_vs_ma"), 1), "rssec_ma": r1(c.get("rssec_vs_ma"), 1),
                  "vol": r1(c.get("vol63"), 0), "r2": r1(c.get("r2_63")), "x21": int(c.get("x21_63") or 0)},
        "final_score": r1(c.get("final_score"), 3),
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
    if "C" in (lo["grade"], sh["grade"]):
        problems.append(f"pair {p['long']}/{p['short']}: uses a C-graded leg")
    for s in (p["long"], p["short"]):
        if s in used:
            problems.append(f"{s} used in more than one pair")
        used.add(s)
    m = pm[(pm.long == p["long"]) & (pm.short == p["short"])]
    corr = vr = None
    if len(m):
        corr, vr = float(m.corr60.iloc[0]), float(m.vol_ratio.iloc[0])
    split_long = round(100 / (1 + vr)) if vr else 50
    pairs.append({
        "rank": i, "supersector": lo["supersector"], "type": p.get("type", "sector"),
        "conviction": int(p["conviction"]), "thesis": p["thesis"], "long": lo, "short": sh,
        "corr60": r1(corr), "vol_ratio": r1(vr), "split_long": split_long, "split_short": 100 - split_long,
    })

if problems:
    print("NOT BUILT. Fix these first:\n  " + "\n  ".join(problems))
    sys.exit(1)

model = res.get("model", {})
doc = {
    "run_utc": res["run_utc"], "run_id": run_id, "label": run_date.strftime("%a %-d %b %Y") + (f" · run {run_id.rsplit('-r', 1)[1]}" if "-r" in run_id[10:] else ""),
    "run_date": run_date.isoformat(), "engine": engine, "trigger": res.get("trigger", "schedule"),
    "cycle_id": res.get("cycle_id"),
    "data_through": res.get("data_through"), "source": res["source"], "universe_count": res["universe_count"],
    "eligible": res.get("eligible"), "regime_counts": res.get("regime_counts"),
    "hold_until": hold_until.isoformat(), "market_note": pj.get("market_note", ""),
    "model": {"weights_source": model.get("weights_source"), "top_weights": model.get("top_weights"),
              "sides": model.get("sides"), "preference": (model.get("report") or {}).get("preference"),
              "trained_at": (model.get("report") or {}).get("trained_at"),
              "note": (model.get("report") or {}).get("survivorship_note")},
    "track_record": res.get("track_record", []),
    "candidates": cands, "pairs": pairs,
}
out = work / "run_doc.json"
out.write_text(json.dumps(doc, separators=(",", ":")))

arch = REPO / "history" / "review" / run_id
arch.mkdir(parents=True, exist_ok=True)
(arch / "verdicts.json").write_text(json.dumps(verdicts, indent=1))
(arch / "pairs.json").write_text(json.dumps({"market_note": pj.get("market_note", ""), "pairs": [
    {"long": p["long"]["symbol"], "short": p["short"]["symbol"], "conviction": p["conviction"],
     "type": p["type"], "split_long": p["split_long"], "thesis": p["thesis"]} for p in pairs]}, indent=1))

size = out.stat().st_size / 1024
print(f"run {run_id}: {len(cands)} candidates, {len(pairs)} pairs, "
      f"{sum(c['earnings_in_window'] for c in cands)} legs with earnings by {hold_until}, {size:.0f} KB -> {out}")
if size > 240:
    print("WARNING: run doc close to the 256 KB document limit")
print("doc_id", run_id)
print("archived", arch)
