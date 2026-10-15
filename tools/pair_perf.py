#!/usr/bin/env python3
"""Mark-to-market of a desk run's pairs, as if every pair had been actioned.

Usage: python tools/pair_perf.py <run_id> [out.json]
  run_id: e.g. 2026-10-10-v2.1 (reads history/review/<run_id>/pairs.json and labels/decisions.jsonl)

Conventions
  - Equal gross capital per pair; inside a pair the vol-balanced split published on the desk.
  - Entry A: first session open after the scan (Monday open = what STAGE would fill).
  - Entry B: scan's last close (data_through), the desk's own reference price.
  - Mark: latest available price (today's live price if the market is open).
  - Pair return on pair gross = split_long * r_long - split_short * r_short. No commissions, borrow or dividends.
"""
import datetime as dt
import json
import pathlib
import sys

import pandas as pd
import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
run_id = sys.argv[1]
out_path = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out" / f"perf-{run_id}.json"
pairs = json.loads((ROOT / "history" / "review" / run_id / "pairs.json").read_text())["pairs"]
res = json.loads((ROOT / "history" / f"{run_id}.json").read_text())
ref = pd.Timestamp(res.get("data_through") or run_id[:10])

dec = {}
lp = ROOT / "labels" / "decisions.jsonl"
if lp.exists():
    for line in lp.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("run_id") == run_id:
                dec[(r["long"], r["short"])] = r.get("action")

syms = sorted({p["long"] for p in pairs} | {p["short"] for p in pairs} | {"SPY"})
ysym = {s: s.replace(".", "-") for s in syms}
raw = yf.download(list(ysym.values()), start=ref - pd.Timedelta(days=7), end=dt.date.today() + dt.timedelta(days=1),
                  auto_adjust=False, group_by="ticker", progress=False, threads=True)
px = {s: raw[ysym[s]][["Open", "Close"]].dropna() for s in syms if ysym[s] in raw.columns.get_level_values(0)}

def leg(s):
    d = px.get(s)
    if d is None or d.empty:
        return None
    before = d[d.index <= ref]
    after = d[d.index > ref]
    if before.empty or after.empty:
        return None
    return {"ref_close": float(before.Close.iloc[-1]), "entry_open": float(after.Open.iloc[0]),
            "entry_date": str(after.index[0].date()), "mark": float(d.Close.iloc[-1]), "mark_date": str(d.index[-1].date())}

rows, missing = [], []
for p in pairs:
    L, S = leg(p["long"]), leg(p["short"])
    if L is None or S is None:
        missing.append(f"{p['long']}/{p['short']}")
        continue
    sl = (p.get("split_long") or 50) / 100
    out = {"long": p["long"], "short": p["short"], "conviction": p.get("conviction"), "type": p.get("type"),
           "split_long": round(sl * 100), "decision": dec.get((p["long"], p["short"]), "none")}
    for tag, k in (("open", "entry_open"), ("ref", "ref_close")):
        rl = L["mark"] / L[k] - 1
        rs = S["mark"] / S[k] - 1
        out[f"long_{tag}"] = round(rl * 100, 2)
        out[f"short_{tag}"] = round(rs * 100, 2)
        out[f"pair_{tag}"] = round((sl * rl - (1 - sl) * rs) * 100, 2)
    out.update({"entry_date": L["entry_date"], "mark_date": L["mark_date"]})
    rows.append(out)

df = pd.DataFrame(rows)
spy = leg("SPY")
summary = {"run_id": run_id, "scan_close": str(ref.date()), "computed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "n_pairs": len(df), "missing": missing}
for tag in ("open", "ref"):
    s = df[f"pair_{tag}"]
    summary[tag] = {"portfolio_on_gross": round(s.mean(), 2), "hit_rate": round((s > 0).mean(), 2),
                    "long_book": round(df[f"long_{tag}"].mean(), 2), "short_book": round(df[f"short_{tag}"].mean(), 2),
                    "best": df.loc[s.idxmax(), ["long", "short"]].tolist() + [float(s.max())],
                    "worst": df.loc[s.idxmin(), ["long", "short"]].tolist() + [float(s.min())],
                    "by_conviction": df.groupby("conviction")[f"pair_{tag}"].agg(["mean", "count"]).round(2).reset_index().to_dict("records"),
                    "spy": round((spy["mark"] / spy["entry_open" if tag == "open" else "ref_close"] - 1) * 100, 2) if spy else None}
summary["pairs"] = df.to_dict("records")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(summary, indent=1, default=str))
print(json.dumps({k: v for k, v in summary.items() if k != "pairs"}, indent=1, default=str))
