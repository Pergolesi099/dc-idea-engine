# Weekly review procedure (Claude) — engine-versioned

Runs every Saturday after the GitHub scan. Goal: turn ~130 top-down charts into 15–25 pair tickets
on the DC Idea Desk, ready for Dean's take / watch / pass before Sunday's SCREEN, and keep the
learning loop fed.

Desk: https://claude.ai/artifact/SDNEj77zxqT1ws5qMgjz8k  (collections: `runs`, `decisions`, `chart_feedback`, `engines`, `cycles`)
Repo: Pergolesi099/dc-idea-engine — scan output on branch `output`; learning state on `main`
(`model/`, `history/`, `labels/`).

## 0. Mode, engine, preconditions
Two ways in:
- **Scheduled (Saturday):** the scan the GitHub schedule ran this morning. Fresh means `results.json` →
  `run_id[:10]` is today (UTC) and `engine` equals `engines.json` → `current`. If stale or missing,
  trigger it (`gh workflow run weekly-scan.yml -R Pergolesi099/dc-idea-engine -f engine=latest -f trigger=schedule`),
  wait for it (`gh run watch`), re-check once.
- **Manual cycle (desk button):** the prompt carries an extra line
  `MANUAL CYCLE cycle_id=<id> engine=<version|latest>`. Always run a NEW scan with that engine, even if
  today's scan exists: `ArtifactData update` on `cycles/<id>` → `{status: "scanning", updated_at}`, then
  `gh workflow run weekly-scan.yml -R Pergolesi099/dc-idea-engine -f engine=<engine> -f trigger=manual -f cycle_id=<id>`,
  find that run (`gh run list -w weekly-scan.yml -L 3 --json databaseId,createdAt,event`), `gh run watch` it, then check
  `results.json` → `cycle_id` is `<id>`. Set `cycles/<id>` → `{status: "reviewing", run_id, engine, updated_at}`.

Still stale, workflow failed, or wrong cycle_id → stop and report; on a manual cycle also set
`cycles/<id>` → `{status: "failed", message: "<one line>", updated_at}`. Never publish an old scan as new.

Work in a scratch dir: `git worktree add <scratch>/scan origin/output`.

Sync the engine list the desk's Engine dropdown shows: for every entry in `engines.json` → `versions`,
`ArtifactData batch` `set` on `engines/<version>` with the entry plus `current: true|false`
(read the collection first and pin `if_version` on docs that exist).

## 1. Export Dean's feedback (feeds next week's learning)
ArtifactData `list` on `decisions` and on `chart_feedback` with `out_dir=<scratch>/labels_raw`
(page with `query.cursor` until done), then `python review/export_labels.py <scratch>/labels_raw`.
This rewrites `labels/*.jsonl`. Commit them in step 7.

## 2. Prepare
`python review/prepare.py <scratch>/scan <scratch>/work`
→ `candidates.tsv`, `sheets/*.png` (3 charts per sheet), `pair_matrix.tsv`.
Read `model/feedback_summary.md`: how your grades matched Dean's calls, his pass reasons, what his
preference model and the outcome model currently reward. Let it shape your eye this week.

## 3. Read every chart, top-down
Each chart: LEFT = weekly 2Y (10W/40W, then Dean's RS Line – Blue Dot vs S&P: slope-coloured line,
black 40W MA, blue/red 52W dots, ▲▼ crossovers). RIGHT = daily 6M (21/50/200D, 6M dashed and 3M dotted
support/resistance, volume), MACD histogram, stock vs sector ETF with rotation shading
(green Leading, blue Improving, amber Weakening, red Lagging).

Read in this order and stop as soon as the chart fails:
1. **Weekly picture.** Does the long timeframe agree with the side? Longs: price above a rising 40W, or
   RS crossed up through its MA while price is still at/below the 40W (the early turn Dean values most).
   Blue dots = leadership. Shorts: the mirror, red dots.
2. **Sector.** Leading / just turned Leading for longs; Lagging / just turned Lagging for shorts.
   A long that is Weakening vs its own sector is a yellow flag.
3. **Daily trigger.** Clean break of 3M/6M resistance (support for shorts) ideally on volume, or a
   pullback holding the 21D/50D; MACD histogram flipping or turning in the trade's direction.
   Note how much room there is to the next 6M level.

Write `work/verdicts.json`: `{"SYM|side": ["A"|"B"|"C", "one-line read, <= 14 words"]}` for EVERY candidate.
- **A**: all three layers agree and the structure is clean.
- **B**: weekly + sector agree but the daily is extended (>10% from 21D), late, gap-driven, or the
  trigger hasn't fired yet; or the trigger is great but the weekly is still only "Turning".
- **C**: reject: weekly fights the side, choppy (many 21D crosses), blow-off/exhaustion bar, event gap
  with no base, binary biotech event, deal-pinned, or straight into a 6M level.
Reads say what the chart shows in trader shorthand, weekly first ("RS crossed 40W, price reclaiming
40W; 6M breakout on volume"). No predictions, no targets.

## 4. Pair
Write `work/pairs.json`: `{"market_note": "...", "pairs": [{long, short, conviction 1-5, type, thesis}]}`.
- Both legs from the SAME supersector, graded A or B, never C. Each symbol in at most one pair.
- Prefer same-industry relative value (`type: "industry"`) where both charts qualify; else `"sector"`.
- Prefer a Leading-vs-Lagging sector-rotation contrast between the legs: it is the cleanest pair.
- Use `pair_matrix.tsv`: prefer corr60 > 0.2; flag vol_ratio outside 0.6–1.6 in the thesis.
- Conviction: 4 = A/A or A/B same-industry, corr > 0.3, rotation contrast; 3 = solid A/B;
  2 = tradeable but weak fit; never publish 1; 5 reserved for exceptional setups.
- Aim for 15–25 pairs covering as many supersectors as the charts allow. Fewer good pairs beats padding.
- Make sure the Lead family (RS leads price) is represented where its charts are A/B.
- market_note: 1–2 sentences on breadth (`regime_counts`), sector rotation themes, earnings season.

## 5. Upload charts
Convert `scan/charts/*.png` to WebP into `desk/_assets/` (quality 82), then Artifact publish with
`url` = desk, `asset: true`, `file_paths` in batches of ≤ 25. Record every result as
`SYM_side <asset id>` lines in `work/uploads.txt`.

    python3 -c "from PIL import Image;import glob,os;[Image.open(f).convert('RGB').save('desk/_assets/'+os.path.basename(f)[:-4]+'.webp','WEBP',quality=82,method=6) for f in glob.glob('<scratch>/scan/charts/*.png')]"

## 6. Build and publish the run
`python review/build_run.py <scratch>/scan <scratch>/work` → `work/run_doc.json` and the archive
`history/review/<run_id>/{verdicts,pairs}.json`. It refuses to build if any candidate lacks a verdict
or chart, a pair leg is missing or C-graded, legs are in different sectors, or a symbol is reused.
Fix and rerun; never hand-edit around it.
The run doc carries `engine`, `trigger` and `cycle_id` from the scan; the desk's Engine dropdown filters on them.
Then ArtifactData `set` on collection `runs`, doc id = run_id, `file_path` = run_doc.json
(if the doc already exists, `get` it first and pass `if_version`).

## 7. Commit the learning inputs
On `main`: `git add labels history/review && git commit -m "review <run_id>" && git pull --rebase && git push`.
Never touch `model/` by hand: the scan owns it.

## 8. Housekeeping
- Runs older than 26 weeks: delete their chart assets (Artifact delete with `path` = asset id) and
  `update` the run doc so their `chart_url` fields are null. Never delete `decisions` or `chart_feedback`.
- Clear `desk/_assets/` locally after upload.

## 9. Close the cycle and report
Manual cycle: `cycles/<id>` → `{status: "published", run_id, pairs: <count>, updated_at}`. Any failure after
step 0 → `{status: "failed", message, updated_at}` and stop.

Report (SendUserMessage), four lines; start with the engine version and whether it was the Saturday run
or a manual cycle: pairs published (count by conviction and by family), sectors with nothing tradeable,
what the learner changed this week (`model.sides.*.changes`, adopted or kept), and any anomaly
(scan fallback, missing earnings dates, sector mapping oddities). Link the desk.
