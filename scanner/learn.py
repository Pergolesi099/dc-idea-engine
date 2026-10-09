"""The learning loop.

Two teachers, both refreshed every weekly run:
  1. The market (outcome model): every Friday in the price history becomes a training date.
     Each eligible stock's features are labelled with its forward return vs its sector ETF
     (5 sessions = the holding period). A ridge model on cross-sectional ranks learns which
     signals actually lead to sector-relative outperformance. New weights are adopted only if
     they beat the current ones on the most recent 30% of dates (out of sample), and each
     weight moves at most `max_step` per week.
  2. Dean (preference model): take / pass decisions and chart thumbs from the DC Idea Desk,
     joined to the features each candidate had when it was shown. A logistic model learns his
     eye; its say in the final ranking grows with the number of labels (beta = n / (n + k)).

Outputs (committed to the repo by the workflow):
  model/weights.json        weights used by the next scan (per side)
  model/report.json         what the model knows: ICs, per-feature evidence, sample sizes
  model/feedback_summary.md plain-English summary the Saturday chart review reads before grading
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import pathlib

import numpy as np
import pandas as pd

from features import snapshot
from model import CONTINUOUS, FEATURES, PRIOR, TAGS, load_weights, normalise, score, side_table

log = logging.getLogger("learn")


# ----------------------------------------------------------------- outcome dataset
def build_dataset(panels: dict[str, pd.DataFrame], meta: pd.DataFrame, fwd: dict[str, pd.DataFrame],
                  dates: list[pd.Timestamp], cfg: dict) -> pd.DataFrame:
    rows = []
    for d in dates:
        snap = snapshot(panels, d).join(meta, how="inner")
        for side in ("long", "short"):
            tab = side_table(snap, side, cfg)
            if len(tab) < 20:
                continue
            sg = 1.0 if side == "long" else -1.0
            for h, f in fwd.items():
                tab[f"y_{h}"] = f.loc[d].reindex(tab.index).to_numpy() * sg
            tab["date"] = d
            rows.append(tab.reset_index())
    if not rows:
        return pd.DataFrame()
    ds = pd.concat(rows, ignore_index=True)
    return ds.dropna(subset=[c for c in ds.columns if c.startswith("y_")])


def _ic(ds: pd.DataFrame, pred: np.ndarray, target: str) -> float:
    """Mean per-date Spearman rank correlation between prediction and target."""
    t = ds[[target, "date"]].copy()
    t["p"] = pred
    with np.errstate(invalid="ignore", divide="ignore"):
        ics = t.groupby("date").apply(lambda g: g["p"].rank().corr(g[target].rank()) if len(g) > 10 else np.nan,
                                  include_groups=False)
    return float(ics.mean()) if ics.notna().any() else float("nan")


def fit_outcome(ds: pd.DataFrame, current: dict, cfg: dict) -> tuple[dict, dict]:
    from sklearn.linear_model import Ridge

    lc = cfg["learning"]
    target = f"y_{lc['horizon']}"
    report = {"horizon_sessions": lc["horizon"], "sides": {}}
    new = {"long": dict(current["long"]), "short": dict(current["short"])}
    for side in ("long", "short"):
        d = ds[ds.side == side].copy()
        if d.empty:
            continue
        d["y"] = d.groupby(["date", "supersector"])[target].rank(pct=True) - 0.5
        dates = sorted(d.date.unique())
        cut = dates[int(len(dates) * (1 - lc["holdout_frac"]))]
        tr, ho = d[d.date < cut], d[d.date >= cut]
        X = [f"z_{f}" for f in FEATURES]
        m = Ridge(alpha=lc["ridge_alpha"], fit_intercept=False).fit(tr[X].to_numpy(), tr["y"].to_numpy())
        fitted = normalise(dict(zip(FEATURES, m.coef_.tolist())))
        cur = current[side]
        ic_cur = _ic(ho, score(ho, cur).to_numpy(), target)
        ic_fit = _ic(ho, score(ho, fitted).to_numpy(), target)
        ic_prior = _ic(ho, score(ho, normalise(PRIOR)).to_numpy(), target)
        adopt = np.isfinite(ic_fit) and ic_fit > (ic_cur if np.isfinite(ic_cur) else -1) + lc["min_ic_gain"]
        if adopt:
            step = lc["max_step"]
            moved = {f: cur.get(f, 0.0) + float(np.clip(lc["blend"] * (fitted[f] - cur.get(f, 0.0)), -step, step))
                     for f in FEATURES}
            new[side] = normalise(moved)
        ic_new = _ic(ho, score(ho, new[side]).to_numpy(), target)
        uni = {f: round(_ic(d, d[f"z_{f}"].to_numpy(), target), 4) for f in FEATURES}
        report["sides"][side] = {
            "n_rows": int(len(d)), "n_dates": len(dates),
            "train_dates": [str(dates[0].date()), str(cut.date())], "holdout_from": str(cut.date()),
            "holdout_ic": {"prior": round(ic_prior, 4), "current": round(ic_cur, 4),
                           "fitted": round(ic_fit, 4), "adopted": round(ic_new, 4)},
            "adopted": bool(adopt),
            "feature_ic": dict(sorted(uni.items(), key=lambda kv: -abs(kv[1] if np.isfinite(kv[1]) else 0))),
            "changes": {f: round(new[side][f] - cur.get(f, 0.0), 4) for f in FEATURES
                        if abs(new[side][f] - cur.get(f, 0.0)) >= 0.003},
        }
    return new, report


# ----------------------------------------------------------------- Dean's feedback
REASON_LEG = {   # pass reason -> which leg(s) the "no" applies to, and how strongly
    "Long leg weak": {"long": 1.0}, "Short leg weak": {"short": 1.0},
    "Both charts weak": {"long": 1.0, "short": 1.0}, "Chart not clean": {"long": .5, "short": .5},
    "Already extended": {"long": .5, "short": .5},
}


def load_labels(root: pathlib.Path) -> pd.DataFrame:
    """One row per (run, symbol, side) with label 1/0 and a weight."""
    rows = []
    for name in ("decisions", "chart_feedback"):
        p = root / "labels" / f"{name}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            run = r.get("run_id")
            if name == "chart_feedback":
                if r.get("verdict") in ("good", "bad"):
                    rows.append({"run": run, "symbol": r["symbol"], "side": r["side"],
                                 "label": 1 if r["verdict"] == "good" else 0, "w": 1.0, "src": "chart"})
                continue
            act = r.get("action")
            if act == "take":
                for side in ("long", "short"):
                    rows.append({"run": run, "symbol": r[side], "side": side, "label": 1, "w": 1.0, "src": "take"})
            elif act == "pass":
                for side, wgt in REASON_LEG.get(r.get("reason", ""), {}).items():
                    rows.append({"run": run, "symbol": r[side], "side": side, "label": 0, "w": wgt, "src": "pass"})
    lab = pd.DataFrame(rows)
    if lab.empty:
        return lab
    # latest label wins per (run, symbol, side, src); chart thumbs and pair decisions both kept
    return lab.drop_duplicates(["run", "symbol", "side", "src"], keep="last")


def fit_preference(root: pathlib.Path, cfg: dict) -> tuple[object | None, dict]:
    from sklearn.linear_model import LogisticRegression

    lab = load_labels(root)
    info = {"n_labels": int(len(lab)), "beta": 0.0}
    if lab.empty:
        return None, info
    feats = []
    for run in lab.run.dropna().unique():
        p = root / "history" / "features" / f"{run}.csv.gz"
        if p.exists():
            f = pd.read_csv(p)
            f["run"] = run
            feats.append(f)
    if not feats:
        return None, info
    F = pd.concat(feats, ignore_index=True)
    d = lab.merge(F, on=["run", "symbol", "side"], how="inner")
    X = [f"z_{f}" for f in FEATURES if f"z_{f}" in d.columns]
    n = len(d)
    info.update({"n_matched": int(n), "n_pos": int(d.label.sum()), "n_neg": int((1 - d.label).sum())})
    lc = cfg["learning"]
    if n < lc["pref_min_labels"] or d.label.nunique() < 2:
        return None, info
    m = LogisticRegression(C=lc["pref_C"], class_weight="balanced", max_iter=2000)
    m.fit(d[X].to_numpy(), d.label.to_numpy(), sample_weight=d.w.to_numpy())
    m.feature_names_ = X
    info["beta"] = round(min(lc["pref_beta_max"], n / (n + lc["pref_beta_k"])), 3)
    coefs = dict(zip([x[2:] for x in X], m.coef_[0].round(3).tolist()))
    info["top_likes"] = dict(sorted(coefs.items(), key=lambda kv: -kv[1])[:6])
    info["top_dislikes"] = dict(sorted(coefs.items(), key=lambda kv: kv[1])[:6])
    return m, info


def preference_score(m, tab: pd.DataFrame) -> pd.Series:
    X = tab[m.feature_names_].to_numpy(dtype=float)
    return pd.Series(m.decision_function(X), index=tab.index)


# ----------------------------------------------------------------- feedback summary for the chart review
def feedback_summary(root: pathlib.Path, report: dict, pref: dict) -> str:
    lines = [f"# What the desk has learned (updated {dt.date.today()})", ""]
    lab = load_labels(root)
    reviews = sorted((root / "history" / "review").glob("*/verdicts.json"))
    if not lab.empty and reviews:
        g = []
        for p in reviews:
            run = p.parent.name
            for k, (grade, _read) in json.loads(p.read_text()).items():
                sym, side = k.split("|")
                g.append({"run": run, "symbol": sym, "side": side, "grade": grade})
        g = pd.DataFrame(g).merge(lab, on=["run", "symbol", "side"], how="inner")
        if len(g):
            lines.append("## Your grades vs Dean's decisions")
            for grade, sub in g.groupby("grade"):
                lines.append(f"- Grade {grade}: {int(sub.label.sum())}/{len(sub)} liked ({sub.label.mean():.0%})")
            lines.append("")
    dec = root / "labels" / "decisions.jsonl"
    if dec.exists():
        d = pd.DataFrame([json.loads(x) for x in dec.read_text().splitlines() if x.strip()])
        if len(d):
            lines.append("## Pair decisions so far")
            lines.append(f"- {len(d)} decisions: " + ", ".join(f"{k} {v}" for k, v in d.action.value_counts().items()))
            pr = d[d.action == "pass"].reason.replace("", "No reason").value_counts()
            if len(pr):
                lines.append("- Pass reasons: " + ", ".join(f"{k} ({v})" for k, v in pr.items()))
            lines.append("")
    if pref.get("beta", 0) > 0:
        lines.append("## Dean's eye (preference model)")
        lines.append(f"- Labels: {pref['n_matched']}; weight in ranking: {pref['beta']:.0%}")
        lines.append("- Leans towards: " + ", ".join(pref["top_likes"]))
        lines.append("- Leans against: " + ", ".join(pref["top_dislikes"]))
        lines.append("")
    lines.append("## What the market rewarded (outcome model, out of sample)")
    for side, r in report.get("sides", {}).items():
        best = [f"{k} ({v:+.3f})" for k, v in list(r["feature_ic"].items())[:5]]
        lines.append(f"- {side}: holdout IC adopted {r['holdout_ic']['adopted']:+.3f} "
                     f"(prior {r['holdout_ic']['prior']:+.3f}); strongest: {', '.join(best)}")
    lines += ["", "Use this when grading: lean towards what Dean takes and what has worked; "
              "say in the market note when you go against it."]
    return "\n".join(lines) + "\n"


def run(root: pathlib.Path, panels, meta, fwd, dates, cfg) -> tuple[dict, object | None, dict]:
    model_dir = root / "model"
    model_dir.mkdir(exist_ok=True)
    current = load_weights(model_dir)
    ds = build_dataset(panels, meta, fwd, dates, cfg)
    log.info("Training rows: %d over %d dates", len(ds), ds.date.nunique() if len(ds) else 0)
    if len(ds) < cfg["learning"]["min_rows"]:
        log.warning("Not enough history to learn; keeping current weights")
        new, report = current, {"sides": {}, "note": "insufficient history"}
    else:
        new, report = fit_outcome(ds, current, cfg)
    new["source"] = "learned" if any(r.get("adopted") for r in report["sides"].values()) else current.get("source", "prior")
    new["updated"] = str(dt.date.today())
    pref_model, pref = fit_preference(root, cfg)
    report.update({"trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "preference": pref, "survivorship_note":
                   "Backfill uses today's universe, so delisted names are missing; ICs are optimistic."})
    (model_dir / "weights.json").write_text(json.dumps(new, indent=1))
    (model_dir / "report.json").write_text(json.dumps(report, indent=1, default=float))
    (model_dir / "feedback_summary.md").write_text(feedback_summary(root, report, pref))
    return new, pref_model, report
