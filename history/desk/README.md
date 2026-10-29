# Desk backup

Durable copy of Dean's inputs to the Idea Conveyor belt (desk artifact database), written by
`tools/snapshot_desk.py` at REVIEW step 7 every cycle. Git history is the version log.

- `feedback.jsonl`: chart reviews: grade, mark-ups (x/y as 0-1 fractions of the chart), notes, digest status
- `charts/<chart_id>.webp`: the chart each review was made on (kept even after desk housekeeping)
- `index.csv`: one row per review, for browsing
- `decisions.jsonl`, `chart_feedback.jsonl`, `book*.jsonl`, `cycles.jsonl`: the other desk collections
- `MANIFEST.json`: counts at the last snapshot

Restore: each line is a document; `_id` is the doc id, the rest its fields. Write back with
ArtifactData `batch` `set` (50 per call) into the same collection, and re-upload charts as assets if needed.
