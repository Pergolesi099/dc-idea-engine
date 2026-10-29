#!/usr/bin/env python3
"""Back up Dean's desk inputs (Idea Conveyor belt database) into the repo.

Dean's chart reviews, mark-ups and decisions live only in the desk artifact's database. This
snapshot is the durable copy: plain JSONL in git, so every cycle is versioned and restorable.

Usage: python tools/snapshot_desk.py <raw_dir> [<images_dir>]
  raw_dir/<collection>/*.json   ArtifactData `list` saved with out_dir (doc id = file stem)
  images_dir/<chart_id>.webp     charts behind feedback items (Artifact read, path = chart_id)

Writes (all under history/desk/, rewritten in full, idempotent):
  <collection>.jsonl     one line per document, sorted by id: {"_id": ..., <fields>}
  charts/<chart_id>.*    every chart a feedback item points at (never deleted once archived)
  index.csv              one row per feedback item: date, symbol, side, grade, marks, text, chart file
  MANIFEST.json          snapshot time, document counts, feedback by status, charts missing
Refuses to shrink a collection (fewer docs than the committed snapshot) unless --allow-shrink:
a shrink means desk data was lost, and the committed copy is what restores it.
"""
import csv
import json
import pathlib
import shutil
import sys
from datetime import datetime, timezone

COLLECTIONS = ["feedback", "decisions", "chart_feedback", "book", "book_control", "cycles"]
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "history" / "desk"

args = [a for a in sys.argv[1:] if not a.startswith("--")]
allow_shrink = "--allow-shrink" in sys.argv
if not args:
    sys.exit(__doc__)
raw = pathlib.Path(args[0])
imgs = pathlib.Path(args[1]) if len(args) > 1 else None
(OUT / "charts").mkdir(parents=True, exist_ok=True)


def load(coll):
    d = raw / coll
    return [{"_id": f.stem, **json.loads(f.read_text())} for f in sorted(d.glob("*.json"))] if d.is_dir() else None


def committed_count(coll):
    p = OUT / f"{coll}.jsonl"
    return sum(1 for line in p.read_text().splitlines() if line.strip()) if p.exists() else 0


counts, problems = {}, []
docs = {}
for c in COLLECTIONS:
    rows = load(c)
    if rows is None:                       # not exported this time: keep the committed file as is
        counts[c] = committed_count(c)
        continue
    before = committed_count(c)
    if len(rows) < before and not allow_shrink:
        problems.append(f"{c}: {len(rows)} docs on the desk vs {before} in the snapshot")
        counts[c] = before
        continue
    docs[c] = rows
    counts[c] = len(rows)

if problems:
    sys.exit("REFUSED (desk has fewer documents than the backup; investigate before overwriting):\n  "
             + "\n  ".join(problems))

for c, rows in docs.items():
    (OUT / f"{c}.jsonl").write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows))

fb = docs.get("feedback")
if fb is None:
    p = OUT / "feedback.jsonl"
    fb = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

missing = []
for chart_id in sorted({d["chart_id"] for d in fb if d.get("chart_id")}):
    if any((OUT / "charts").glob(f"{chart_id}.*")):
        continue
    src = next((p for ext in (".webp", ".png", ".jpg") if imgs and (p := imgs / f"{chart_id}{ext}").exists()), None)
    if src:
        shutil.copy2(src, OUT / "charts" / src.name)
    else:
        missing.append(chart_id)


def chart_file(cid):
    hit = next((OUT / "charts").glob(f"{cid}.*"), None) if cid else None
    return f"charts/{hit.name}" if hit else ""


with open(OUT / "index.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["created_at", "id", "run_id", "engine", "symbol", "side", "grade", "marks", "text", "status", "backlog_id", "chart"])
    for d in sorted(fb, key=lambda d: d.get("created_at", "")):
        w.writerow([d.get("created_at", ""), d["_id"], d.get("run_id", ""), d.get("engine", ""), d.get("symbol", ""),
                    d.get("side", ""), d.get("grade", ""), len(d.get("marks") or []), (d.get("text") or "").replace("\n", " "),
                    d.get("status", ""), d.get("backlog_id", ""), chart_file(d.get("chart_id"))])

status = {}
for d in fb:
    status[d.get("status", "?")] = status.get(d.get("status", "?"), 0) + 1
manifest = {"snapshot_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts,
            "feedback_by_status": status, "charts_archived": len(list((OUT / "charts").iterdir())),
            "charts_missing": missing}
(OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
print(json.dumps(manifest, indent=1))
