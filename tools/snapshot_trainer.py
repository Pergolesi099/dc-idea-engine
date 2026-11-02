#!/usr/bin/env python3
"""Back up Dean's Chart Trainer calls (blind long/short/pass + A/B/C) into history/trainer/calls.jsonl.

Usage: python tools/snapshot_trainer.py <raw_dir>
  raw_dir/calls/*.json   ArtifactData `list` of collection `calls` on the trainer
                         (https://claude.ai/artifact/MiZMrbynjbfVhDEBSEUJAk), saved with out_dir
Doc id = chart id; outcomes for each chart are in history/trainer/batch-<n>/charts.json.
Refuses to shrink (fewer calls than the committed file) unless --allow-shrink.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "history" / "trainer" / "calls.jsonl"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    sys.exit(__doc__)
rows = [{"_id": f.stem, **json.loads(f.read_text())} for f in sorted((pathlib.Path(args[0]) / "calls").glob("*.json"))]
before = sum(1 for line in OUT.read_text().splitlines() if line.strip()) if OUT.exists() else 0
if len(rows) < before and "--allow-shrink" not in sys.argv:
    sys.exit(f"REFUSED: trainer has {len(rows)} calls vs {before} in the backup; investigate before overwriting")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
calls = {}
for r in rows:
    calls[r.get("call")] = calls.get(r.get("call"), 0) + 1
print(json.dumps({"calls": len(rows), "by_call": calls}))
