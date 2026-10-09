#!/usr/bin/env python3
"""Turn the desk's feedback (ArtifactData reads saved with out_dir) into the learner's label files.

Usage: python review/export_labels.py <out_dir>
  <out_dir>/decisions/*.json       ArtifactData list of collection `decisions`
  <out_dir>/chart_feedback/*.json  ArtifactData list of collection `chart_feedback`
Rewrites labels/decisions.jsonl and labels/chart_feedback.jsonl in full (idempotent).
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
src = pathlib.Path(sys.argv[1])
(REPO / "labels").mkdir(exist_ok=True)
FIELDS = {"decisions": ["run_id", "long", "short", "supersector", "conviction", "action", "reason", "note", "decided_at"],
          "chart_feedback": ["run_id", "symbol", "side", "verdict", "decided_at"]}
for coll, fields in FIELDS.items():
    rows = []
    for f in sorted((src / coll).glob("*.json")):
        d = json.loads(f.read_text())
        d = d.get("data", d)
        if not isinstance(d, dict) or not d.get("run_id"):
            continue
        rows.append({k: d.get(k) for k in fields})
    rows.sort(key=lambda r: (r["run_id"], r.get("decided_at") or ""))
    (REPO / "labels" / f"{coll}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{coll}: {len(rows)} labels")
