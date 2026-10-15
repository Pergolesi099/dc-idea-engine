"""Shared selection logic: setup tags, eligibility, normalised features, scoring weights.

Used identically by today's scan and by the learner's historical backfill, so the model is
trained on exactly the rules it ranks with.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd

from features import DIRECTIONAL, NONDIRECTIONAL, gate, regime, side_features

TAGS = ["S1", "S2", "S3", "MOM", "LEAD", "SECT", "BO", "MACD"]
CONTINUOUS = DIRECTIONAL + NONDIRECTIONAL
FEATURES = CONTINUOUS + [f"tag_{t}" for t in TAGS]

# Starting weights before any learning (side-adjusted: positive = more of it is better).
PRIOR = {
    "rs_vs_ma": .08, "rs_cross": .06, "rs_hl": .05, "rs_lead": .06,
    "rssec_vs_ma": .06, "rssec_cross": .04, "rssec_hl": .04, "rssec_1m": .03, "rssec_3m": .04,
    "sec_ratio": .04, "sec_mom": .03, "sec_quad": .04, "sec_turn": .04,
    "w_d40": .02, "w_slope40": .04, "w_10gt40": .02,
    "p1w": 0.0, "p1m": .02, "p3m": .03, "p6m": .02, "d21": 0.0, "d50": .01, "d200": .01,
    "breakout": .06, "macd_sig": .05, "headroom": .02,
    "r2_63": .06, "er_63": .04, "x21_63": -.04, "vol63": -.02, "ext21": -.04, "base_tight": .02,
    **{f"tag_{t}": .01 for t in TAGS},
}


def normalise(w: dict) -> dict:
    s = sum(abs(v) for v in w.values()) or 1.0
    return {k: round(v / s, 5) for k, v in w.items()}


def load_weights(model_dir: pathlib.Path) -> dict:
    p = model_dir / "weights.json"
    prior = normalise(PRIOR)
    if not p.exists():
        return {"long": dict(prior), "short": dict(prior), "source": "prior"}
    w = json.loads(p.read_text())
    for side in ("long", "short"):                      # new features start at their prior
        w[side] = {f: w[side].get(f, prior.get(f, 0.0)) for f in FEATURES}
    return w


# ----------------------------------------------------------------- tags
def tag_frame(df: pd.DataFrame, side: str, cfg: dict) -> pd.DataFrame:
    """Boolean column per setup/trigger for this side. df = one date's snapshot with `supersector`."""
    s = cfg["strategies"]
    L = side == "long"
    sg = 1 if L else -1
    pct = lambda col: df.groupby("supersector")[col].rank(pct=True)
    t = pd.DataFrame(index=df.index)
    d21, d50, d200 = df.d21 * sg, df.d50 * sg, df.d200 * sg
    t["S1"] = (d21 > 0) & (d50 > 0) & (d200 > 0) & (d21 > np.maximum(d50, d200))
    p = pct(s["s2"]["perf_field"])
    t["S2"] = ((p >= s["s2"]["leader_pct"]) if L else (p <= s["s2"]["laggard_pct"])) & (d21 < 0) & (d50 > 0)
    t["S3"] = (df.d200.abs() <= s["s3"]["band_pct"]) & (df.p1m * sg > 0) & (df.r2_63 >= s["s3"]["min_r2"])
    comp = (pct("p1w") + pct("p1m") + pct("p3m")) / 3
    t["MOM"] = ((comp >= s["mom"]["leader_pct"]) if L else (comp <= s["mom"]["laggard_pct"])) & (d50 > 0)
    t["LEAD"] = df.rs_lead * sg == 1
    t["SECT"] = (df.sec_turn * sg == 1) & (df.rssec_vs_ma * sg > 0)   # daily turn agrees with weekly sector RS
    t["BO"] = df.breakout * sg > 0
    t["MACD"] = df.macd_sig * sg > 0
    r = cfg.get("risk") or {}
    if r.get("enabled") and not L:
        for tg in r.get("short_tags_barred", []):        # e.g. MOM: momentum-laggard shorts = falling knives
            t[tg] = False
    return t.fillna(False)


def risk_mask(df: pd.DataFrame, side: str, cfg: dict) -> pd.Series:
    """v2.2 risk rules (all off when risk.enabled is false, which reproduces v2.1 selection).
    - stretch: no entry more than `stretch_max_sigma` monthly sigmas beyond the 21D in the trade's direction
    - shorts: no falling knives (price at most `short_knife_max_below21` % below its 21D) and a bounce
      required (1W or 2W return > 0): short into a bounce, not after a collapse."""
    r = cfg.get("risk") or {}
    ok = pd.Series(True, index=df.index)
    if not r.get("enabled"):
        return ok
    sg = 1 if side == "long" else -1
    sm = r.get("stretch_max_sigma")
    if sm:
        sigma_m = df.vol63 / np.sqrt(12)
        ok &= ~((df.d21 * sg) / sigma_m > sm)
    if side == "short":
        k = r.get("short_knife_max_below21")
        if k is not None:
            ok &= df.d21 >= -k
        if r.get("short_require_bounce"):
            ok &= (df.p1w > 0) | (df.p2w > 0)
    return ok.fillna(False)


def side_table(df: pd.DataFrame, side: str, cfg: dict, gated: bool = True) -> pd.DataFrame:
    """Eligible rows for one side with tags, regime and normalised features (z in [-0.5, 0.5]).
    gated=False keeps every name with a sector (used to backfill features for past desk runs)."""
    u = cfg["universe"]
    reg = regime(df)
    tags = tag_frame(df, side, cfg)
    ok = df.supersector.notna()
    if gated:
        ok &= (gate(reg, side, cfg) & tags[TAGS].any(axis=1)
               & (df.sessions >= u["min_history_sessions"])
               & df.vol63.between(u["min_ann_vol_pct"], u["max_ann_vol_pct"])
               & risk_mask(df, side, cfg))
    sub = df[ok].copy()
    if sub.empty:
        return sub
    x = side_features(sub, side)
    z = x.groupby(sub.supersector).rank(pct=True) - 0.5
    z = z.fillna(0.0)
    out = pd.DataFrame(index=sub.index)
    out["supersector"] = sub.supersector
    out["side"] = side
    out["regime"] = reg[ok]
    out["tags"] = tags[ok].apply(lambda r: [t for t in TAGS if r[t]], axis=1)
    for f in CONTINUOUS:
        out[f"z_{f}"] = z[f].astype("float32")
    for tg in TAGS:
        out[f"z_tag_{tg}"] = tags.loc[ok, tg].astype("float32")
    return out


FAMILIES = {"Lead": ["LEAD"], "Turn": ["SECT", "S3"], "Trigger": ["BO", "MACD"], "Trend": ["S1", "S2", "MOM"]}


def family(tags: list[str]) -> str:
    for fam, members in FAMILIES.items():          # priority: Lead > Turn > Trigger > Trend
        if any(t in members for t in tags):
            return fam
    return "Trend"


def select(cands: pd.DataFrame, quota: int) -> pd.DataFrame:
    """Per sector/side, fill `quota` slots round-robin across setup families by score, so early
    turns (RS leading price, sector turning) are not crowded out by strong trend names."""
    cands = cands.assign(family=cands.tags.apply(family))
    picked = []
    for _, g in cands.groupby(["supersector", "side"]):
        queues = {f: q.sort_values("final_score", ascending=False) for f, q in g.groupby("family")}
        order = sorted(queues, key=lambda f: -queues[f].final_score.iloc[0])
        taken, i = [], 0
        while len(taken) < min(quota, len(g)):
            f = order[i % len(order)]
            q = queues[f]
            if len(q):
                taken.append(q.iloc[0])
                queues[f] = q.iloc[1:]
            i += 1
            if all(len(q) == 0 for q in queues.values()):
                break
        picked.extend(taken)
    return pd.DataFrame(picked)


def score(tab: pd.DataFrame, weights: dict) -> pd.Series:
    wv = np.array([weights.get(f, 0.0) for f in FEATURES], dtype=float)
    X = tab[[f"z_{f}" for f in FEATURES]].to_numpy(dtype=float)
    return pd.Series(X @ wv, index=tab.index)
