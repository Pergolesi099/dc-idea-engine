# Weekly review procedure (Claude)

Runs every Saturday after the GitHub scan. Goal: turn ~100 charts into 15–25 pair tickets on the
DC Idea Desk, ready for Dean's take / watch / pass before Sunday's SCREEN.

Desk: https://claude.ai/artifact/SDNEj77zxqT1ws5qMgjz8k  (collections: `runs`, `decisions`)
Repo: Pergolesi099/dc-idea-engine — scan output on branch `output`.

## 0. Preconditions
- `git fetch origin output` and check `results.json` → `run_utc` is from today (UTC). If the scan is
  stale or missing, trigger it (`gh workflow run weekly-scan.yml -R Pergolesi099/dc-idea-engine`),
  wait for it, re-check once. Still stale → stop and report; never publish an old scan as new.
- Work in a scratch dir: `git worktree add <scratch>/scan origin/output`.

## 1. Prepare
`python review/prepare.py <scratch>/scan <scratch>/work`
→ `candidates.tsv`, `sheets/*.png` (one contact sheet per supersector+side), `pair_matrix.tsv`.

## 2. Read every chart
Read `candidates.tsv`, then every contact sheet (open full charts only to break a tie).
Write `work/verdicts.json`: `{"SYM|side": ["A"|"B"|"C", "one-line read, <= 12 words"]}` for EVERY candidate.

Grading — Dean's eye, not a formula:
- **A**: clean, readable structure. Longs: orderly uptrend, higher lows, pulling back to the 21D or
  basing above a rising 50D, RS vs sector ETF rising. Shorts: lower highs below a falling 50D,
  bouncing into the 21D/50D, or a clean break of the 200D after a rolling top.
- **B**: right direction but a flaw: extended (>10% from the 21D), gap-driven, still choppy,
  very high vol, or the trigger hasn't happened yet.
- **C**: reject. Choppy (many 21D crosses, no slope), blow-off or exhaustion bar, news/event gap
  with no base, binary biotech event, merger-arb pinned (near-zero vol), or indecisive at the 200D.
Reads describe what the chart shows, in trader shorthand. No predictions, no price targets.

## 3. Pair
Write `work/pairs.json`: `{"market_note": "...", "pairs": [{long, short, conviction 1-5, type, thesis}]}`.
- Both legs from the SAME supersector, graded A or B, never C. Each symbol in at most one pair.
- Prefer same-industry relative value (`type: "industry"`) where both charts qualify; otherwise `"sector"`.
- Use `pair_matrix.tsv`: prefer corr60 > 0.2; flag vol_ratio outside 0.6–1.6 in the thesis.
- Conviction: 4 = A/A or A/B same-industry with corr > 0.3; 3 = solid A/B; 2 = tradeable but weak
  fit (low corr, extended leg); never publish 1. 5 is reserved for exceptional setups.
- Aim for 15–25 pairs covering as many supersectors as the charts allow. Fewer good pairs beats padding.
- Thesis: one line, both legs, what each chart is doing. No narrative about fundamentals unless obvious.
- market_note: 1–2 sentences on breadth by sector and anything common to many legs (e.g. earnings season).
- Check `next_earnings`: legs reporting inside the hold window are flagged on the desk automatically;
  mention it in the thesis only if it changes the trade.

## 4. Upload charts
Convert `scan/charts/*.png` to WebP into `desk/_assets/` (quality 82; see the snippet below), then
Artifact publish with `url` = desk, `asset: true`, `file_paths` in batches of ≤ 25.
Record every result as `SYM_side <asset id>` lines in `work/uploads.txt`.

    python3 -c "from PIL import Image;import glob,os;[Image.open(f).convert('RGB').save('desk/_assets/'+os.path.basename(f)[:-4]+'.webp','WEBP',quality=82,method=6) for f in glob.glob('<scratch>/scan/charts/*.png')]"

## 5. Build and publish the run
`python review/build_run.py <scratch>/scan <scratch>/work` → `work/run_doc.json`, doc id printed.
It refuses to build if any candidate lacks a verdict or chart, a pair leg is missing, legs are in
different sectors, or a symbol is reused. Fix and rerun; never hand-edit around it.
Then ArtifactData `set` on collection `runs`, doc id = run date, `file_path` = run_doc.json
(if the doc already exists, `get` it first and pass `if_version`).

## 6. Housekeeping
- Runs older than 26 weeks: delete their chart assets (Artifact delete with `path` = asset id) and
  `update` the run doc so their `chart_url` fields are null. Never delete `decisions`.
- Clear `desk/_assets/` locally after upload.

## 7. Report (SendUserMessage)
Three lines: pairs published (count by conviction), sectors with nothing tradeable, and any
anomaly (scan fallback used, earnings dates missing, sector mapping oddities). Link the desk.
