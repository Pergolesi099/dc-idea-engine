#!/usr/bin/env python3
"""B-006: turn Dean's chart mark-ups into labels the engine can be scored against.

Usage: python tools/harvest_marks.py [history/desk/feedback.jsonl] [labels/marks.jsonl]

Each desk mark is stored as fractions (0-1) of the 1400x920 chart image. The chart geometry is
recovered exactly by rendering a synthetic chart with the production code (scanner/charts.py) and
reading each panel's pixel box and x-limits, so a mark's x maps to a bar of that panel. A mark is
stored as "bars back from the chart's last bar" (0 = last bar) plus the panel's timeframe (W/D);
the date is resolved later against the real trading calendar (tools/lab_marks.py), because the
chart's last bar is the last session before the run date.

Mark classes (from the label Dean typed):
  entry_long / entry_short  vertical lines labelled long / short (also "long?", "...entry point long")
  cross / flip / turnaround  boxes or pins on a crossover, MACD flip, SMA turnaround
  chop                       "sideways", "noise", "inconclusive"
  gain                       boxes measuring a move ("gain", "potential gain ...")
  other                      anything else
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scanner"))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import charts  # noqa: E402

PANELS = ["weekly_price", "weekly_rs", "daily_price", "daily_rs", "macd", "sector"]
TF = {"weekly_price": "W", "weekly_rs": "W", "daily_price": "D", "daily_rs": "D", "macd": "D", "sector": "D"}


def layout(cfg: dict) -> dict:
    """Render a synthetic chart (enough history for every panel) and capture panel boxes + x-limits."""
    n = 900
    idx = pd.bdate_range("2022-01-03", periods=n)
    rng = np.random.default_rng(0)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    d = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=idx)
    spx = pd.Series(4000 * np.exp(np.cumsum(rng.normal(0, 0.008, n))), index=idx)
    etf = pd.Series(80 * np.exp(np.cumsum(rng.normal(0, 0.009, n))), index=idx)
    row = {"side": "long", "supersector": "Technology", "sector_etf": "XLK"}
    grabbed = {}
    real_close = plt.close

    def grab(fig=None):
        grabbed["fig"] = fig
    plt.close = grab
    try:
        charts.render("SYN", row, d, spx, etf, pathlib.Path("/dev/null"), cfg)
    finally:
        plt.close = real_close
    fig = grabbed["fig"]
    axes = fig.axes
    assert len(axes) == 6, f"expected 6 panels, got {len(axes)}"
    W, H = fig.get_size_inches() * fig.dpi
    out = {"size": [int(W), int(H)], "weekly_bars": cfg["charts"]["weekly_bars"], "daily_bars": cfg["charts"]["daily_bars"]}
    for name, ax in zip(PANELS, axes):
        b = ax.get_position()            # figure fractions, y from the bottom
        out[name] = {"x0": b.x0, "x1": b.x1, "top": 1 - b.y1, "bottom": 1 - b.y0, "xlim": list(ax.get_xlim())}
    real_close(fig)
    return out


def panel_at(L: dict, x: float, y: float) -> str | None:
    for p in PANELS:
        b = L[p]
        if b["x0"] - 0.01 <= x <= b["x1"] + 0.01 and b["top"] - 0.02 <= y <= b["bottom"] + 0.02:
            return p
    return None


def bars_back(L: dict, p: str, x: float) -> float:
    b = L[p]
    lo, hi = b["xlim"]
    bar = lo + (x - b["x0"]) / (b["x1"] - b["x0"]) * (hi - lo)
    n = L["weekly_bars"] if TF[p] == "W" else L["daily_bars"]
    return (n - 1) - bar


def classify(t: str) -> str:
    s = t.strip().lower()
    if re.search(r"sideways|noise|inconclusive", s):
        return "chop"
    if re.search(r"gain", s):
        return "gain"
    if re.fullmatch(r"long\??", s) or "entry point long" in s or s.startswith("long signal"):
        return "entry_long"
    if re.fullmatch(r"short\??", s) or s.startswith("short signal") or "classic short" in s or "short after" in s:
        return "entry_short"
    if "turnaround" in s:
        return "turnaround"
    if "flip" in s:
        return "flip"
    if "cross" in s:
        return "cross"
    return "other"


def main(src: pathlib.Path, dst: pathlib.Path) -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    L = layout(cfg)
    rows = []
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        fb = json.loads(line)
        for k, m in enumerate(fb.get("marks") or [], 1):
            xm = (m["x1"] + m["x2"]) / 2
            if m["t"] == "line":                         # vertical line: panel = where its top sits
                ys = sorted([m["y1"], m["y2"]])
                p = panel_at(L, xm, ys[0] + 0.01) or panel_at(L, xm, (ys[0] + ys[1]) / 2)
            else:
                p = panel_at(L, xm, (m["y1"] + m["y2"]) / 2)
            if p is None:
                continue
            bb = bars_back(L, p, xm)
            w = abs(bars_back(L, p, m["x1"]) - bars_back(L, p, m["x2"])) if m["t"] == "box" else 0.0
            rows.append({"fid": fb["_id"], "mark": k, "run_id": fb.get("run_id"), "run_date": str(fb.get("run_id", ""))[:10],
                         "symbol": fb.get("symbol"), "chart_side": fb.get("side"), "grade": fb.get("grade"),
                         "type": m["t"], "label": (m.get("txt") or "").strip(), "cls": classify(m.get("txt") or ""),
                         "panel": p, "tf": TF[p], "bars_back": round(bb, 2), "width_bars": round(w, 2)})
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    (dst.parent / "chart_layout.json").write_text(json.dumps(L, indent=1) + "\n")
    df = pd.DataFrame(rows)
    print(f"{len(rows)} marks from {df.fid.nunique()} reviews -> {dst}")
    print(df.groupby(["cls", "tf"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    a = sys.argv[1:]
    main(pathlib.Path(a[0]) if a else ROOT / "history" / "desk" / "feedback.jsonl",
         pathlib.Path(a[1]) if len(a) > 1 else ROOT / "labels" / "marks.jsonl")
