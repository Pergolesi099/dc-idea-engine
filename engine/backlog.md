# Engine backlog

Dean's feedback from the desk, digested by the Saturday review into candidate changes for the next engine
version. Newest first. An item is `open` until a release ships it (`shipped vX.Y`) or it is set aside
(`parked: reason`). Releases cite the item ids in `engines.json` → `changes`.

Format:

    ## B-001 · YYYY-MM-DD · open
    - From: feedback <id> · run <run_id> · <SYM side | pair | general>
    - Dean: what he wrote, close to his words; marks summarised ("1: base breakout here, 2: RS already crossed")
    - Reading: what this says about how he reads charts, in one or two lines
    - Change: the concrete rule, feature, weight or threshold to try, and how to test it on history

## TRAINER 2026-11-02 · blind labels (Chart Trainer artifact, MiZMrbynjbfVhDEBSEUJAk)
- Batch 1: 300 past charts (2020-03 to 2026-08), ticker/dates/prices hidden; 150 random, 150 eye-model top-2% picks
  (tools/blind_batch.py on branch v3-eye, seed 1). Outcomes in history/trainer/batch-1/charts.json; page in trainer/.
- Dean calls long/short/pass + A/B/C; calls in the trainer's `calls` collection, backed up Saturdays to history/trainer.
- Next, once >= 100 long/short calls: (1) does his blind read beat the average chart at 1M (t > 2)? (2) does it beat the
  model on the model's own picks? (3) if yes, retrain the eye model on blind calls (side + grade, outcome-weighted) and
  rebuild the weekly sector pairs from it; if no, stop modelling his eye and say so.

## LAB 2026-11-02 · v3 "eye" (branch `v3-eye`, results history/lab/2026-11-02-eye-v3.json)
Dean: scrap the gates; machine-learn his reviewed charts as pictures. Built scanner/eye.py (six chart panels, window-scaled,
short = flipped picture) + tools/eye_lab.py. Labels: 219 dated entry lines (+ jitter), 12,357 same-chart non-entry dates,
80 grades, 4,548 light universe negatives. History 2017-05 to 2026-11, 1,560 names.
- It learns his eye: on stocks it never saw (5-fold by stock), best model (regularised logistic on the raw picture) AUC 0.83;
  his entry date ranks in the top 10% of its own chart 54% of the time (10% by chance). PCA/kNN/GBM variants 0.63-0.78.
- It does not learn his grades: Spearman 0.07 with A/B/C on held-out charts (80 grades is too few).
- It does not make money out of sample (2020-02 to 2025-01, before any training picture): one pair per sector, 1W Sharpe
  -0.51 (random pairs 0.07), 1M -0.33, 2M -0.29. Long pictures that look most like his entries underperform the rest at 2M
  (-1.15%, t -2.6); short pictures help (+0.96%, t 2.3). In-sample 2025-26 is positive (0.5-1.5) but leaks: the training
  pictures sit inside that period.
- Why: his lines are drawn in hindsight on turns that worked. The picture at the turn looks the same for turns that fail,
  so a model of "where he would mark" learns the look of an early turn, which on average does not follow through.
- Next: blind labels. Show him past charts cut at a random date (future hidden), he grades side + A/B/C; outcomes are known at
  once, so (1) we learn whether his blind read predicts returns at all, and (2) the model trains on forward-looking labels.
  Not live: the eye model stays on the branch.

## LAB 2026-11-02 · v2.4 candidate tested (branch `v2.4-candidate`, results history/lab/2026-11-02-v2.4-candidate.json)
History 2021-06 to 2026-11, 217 weeks of book replay (split 2024-09-27), 1,560 names, prior weights. 402 of 410 marks dated.
- B-006 done: tools/harvest_marks.py -> labels/marks.jsonl. Dean's entry lines vs a permutation null (marks shifted together
  within the same chart, 300 shuffles). Hit = signal within +-3 sessions, same direction. Daily marks (n=165):
  sector turn 76% (lift 3.8) · vs-sector x 50D 66% (3.0) · price x 50D 65% (3.0) · daily RS x 200D 50% (3.9) ·
  confluence>=3 48% (4.0) · weekly RS x 40W 44% (5.7) · MACD flip 38% (1.5) · 21D x 50D 21% (2.7) · TURN 11% (6.5). All p<0.003.
  Caveat: hindsight marks on swing turns coincide with crosses by construction; this measures resemblance, not edge.
- Engine on his daily entry marks (first Friday <= 5 sessions after), picked vs the same name/side on any Friday of that year:
  v2.3 picked 4.2% (lift 0.54: v2.3 avoids his entries) · +score 6.1% (0.77) · v2.4 all 5.5% (1.28) · + trigger needs 3 crosses 6.1% (1.75).
  Only 15% of his entries are even eligible in v2.3 (weekly gate + risk rules): the gate is the main gap, not the score.
- 1W book Sharpe (all / H1 / H2): v2.3 -0.04 / -0.82 / 0.72 · +chop 0.21 / 0.54 / -0.22 · +tags 0.01 / -0.76 / 0.77 ·
  +score 0.10 / -0.73 / 0.84 · v2.4 all -0.01 / 0.40 / -0.56 · +3 crosses 0.25 / -0.02 / 0.48 · +2 crosses 0.17 / -0.12 / 0.40.
  Only +score beats v2.3 in both halves (marginally). v2.3 itself is flat over 2022-24: its 0.6-0.8 is a 2024-26 result.
- 1M hold, non-overlapping (54 periods): v2.3 0.82 (-0.13 / 1.60) · v2.4 all 0.98 (0.38 / 1.55) · +3 crosses 1.24 (-0.35 / 3.17).
- Event study vs sector ETF, all gated candidates: 1W -0.01%, 1M +0.29%, 2M +0.57%. The edge is at 1-2 months (B-005).
  TURN long n=177: 1W -0.68, 1M -0.91 (negative) · B200 long n=987: ~0 · XBRK / TURN short: < 30 events (blocked by the gate).
- Verdict: no v2.4 release as specified. Keep: confluence + sma_turn in the score (B-001a/B-002 as features). Park: TURN, B200
  tags (no edge). Chop filter: halves disagree, park. Open: trigger-needs-3-crosses (best eye match, best totals, fails a half),
  the weekly gate vs his entries, and a 1M-horizon book (B-005).

## B-007 · 2026-10-31 · open
- From: feedback f-20261028165902610-zyni · run 2026-10-17-v2.3 · MZTI short (graded C)
- Dean: no text; marks: short entry at a 50D cross with a histogram flip, then "long (weak against short mega trend)" at a later histogram flip.
- Reading: a daily long signal against a falling weekly trend is low quality for him; the weekly trend sets the side, the daily only times it.
- Change: penalise counter-trend daily triggers: Trigger/Turn longs with price < falling 40W (and shorts with price > rising 40W) get a score haircut instead of passing on daily signals. Test: split the 2023-2026 event study by weekly-trend agreement; ship the haircut only if disagreeing triggers underperform by > 0.5%/week.

## B-006 · 2026-10-31 · done (harvest + scoring built; see LAB 2026-11-02)
- From: feedback f-20261027202924577-zw5u, f-20261028163735457-3rmf, f-20261028164022426-cymy, f-20261028164359788-6psm, f-20261028164619914-ei0u, f-20261028164907588-tujk, f-20261028165107614-a4b2, f-20261028165234069-8cc9, f-20261028165337393-2exy, f-20261028165634262-uv6v, f-20261028170927582-yclb, f-20261028171018404-lymf, f-20261028171223667-25vl, f-20261028171404168-0rcj, f-20261028171947655-ehe3, f-20261028172117304-w53o, f-20261028172339117-qs6k, f-20261028172758368-c623, f-20261028173101897-flkn, f-20261028173410791-swpn, f-20261028175145443-7dqu, f-20261028175358991-ptyq, f-20261030171121646-qi1a, f-20261030171244974-auh5, f-20261030171326149-ygqf, f-20261030171424522-mzhf, f-20261030172047758-nn7t, f-20261030172230992-pxf2, f-20261030172412184-m37o, f-20261030172457109-0d95, f-20261030172600442-x6cu, f-20261030172648968-fyza, f-20261030173441675-ra8z, f-20261030173546011-llf7, f-20261030173718000-m2jo, f-20261030173829260-iqol, f-20261030174403685-gbvo, f-20261030174550233-bex4, f-20261030174700892-cecc, f-20261030174743778-z1mq · runs 2026-10-17-v2.3 and 2026-10-24-v2.3 · 40 charts, mostly no text
- Dean: vertical lines labelled "long" / "short" (sometimes "long?" / "short?") across the 1Y daily and 2Y weekly panels, each on a past swing turn; plus an A/B/C grade on each chart.
- Reading: he is labelling where he would have entered and exited in hindsight, swing by swing: turns are taken near MA crosses after a run, not at extremes. These lines are a dense, free set of supervised entry labels in his own eye.
- Change: build `tools/harvest_marks.py`: map each line's x to a date (panel bounds from the chart layout), side from the label, and store (symbol, date, side, panel) in labels/marks.jsonl. Use them to (a) measure which engine features fire within 3 sessions of his marks (precision/recall of Trigger/Turn definitions vs his turns) and (b) add the chart grade as a preference label. Test: recall of the current trigger set on his marks; target >= 60% before tuning.

## B-005 · 2026-10-31 · open
- From: feedback f-20261028172724804-2tb3, f-20261028172942313-6gxf, f-20261028173802672-3xak, f-20261030172856254-haqf · run 2026-10-17-v2.3 · VCYT, ENSG, SMG, BROS (shorts)
- Dean: boxes "potential gain captured", "post gapup gain remaining on the table", "potential gain over next 2 months", "gain", next to his entry lines.
- Reading: he sizes a setup by the move that follows over weeks to two months, not by the next week; a signal is good if the swing it starts is large.
- Change: add a 1M and 2M forward-return label next to the 5-session one in the learner report (shadow only), and report the confluence/turnaround signals (B-001, B-002) on both horizons. Test: walk-forward, by half; v2.3 showed the 1-month hold of the current book at Sharpe 0.08, so only the new signals are tested at 1-2M.

## B-004 · 2026-10-31 · open (merged with B-001 c)
- From: feedback f-20261028171544296-taob, f-20261028173308793-35ln, f-20261028174041939-20ni (also MAC, INVH, SJM marks) · run 2026-10-17-v2.3
- Dean: "short after rally extension", "cross down after big rally", "big rally before short", "classic short" with cross/flip boxes on the 21D/50D, RS vs S&P and vs-sector panels.
- Reading: his favourite short is the first 21D/50D cross down after an extended run, confirmed the same bar by a histogram flip and RS lines crossing down; not a short of a long downtrend.
- Change: implements B-001 (c) "extension break" as a short setup: close under the 50D within 20 sessions of being > 2 sigma above it, hist flip down, RS-vs-sector crossing under its MA within 3 sessions. Note the v2.2 short rule (1M return > 0) already allows it. Test as in B-001.

## B-003 · 2026-10-31 · open
- From: feedback f-20261028164255152-sp05, f-20261028172535041-1l4x, f-20261028173550325-qm5d, f-20261028173609989-1ba7, f-20261028175323695-dive, f-20261030171709054-kgks · runs 2026-10-17-v2.3 and 2026-10-24-v2.3
- Dean: "inconclusive", "sideways noise", "sideways, inconclusive", "noise, sideways"; graded B/C.
- Reading: he rejects ranges outright; a sideways chart gives no trade however the indicators read.
- Change: chop filter: drop candidates with x21_63 (21D crosses in 63 sessions) >= 6 or R2_63 < 0.1 unless a 3M/6M breakout fired this week. Test: event study of graded C-for-chop names vs the rest; check how many of last 4 runs' candidates it removes (target 10-20%).

## B-002 · 2026-10-31 · open
- From: feedback f-20261028174157310-g5lw, f-20261028174303857-jtfr, f-20261028174543671-ma84, f-20261028174715203-m2wl, f-20261028174950068-aeef, f-20261028175430604-x7nu, f-20261030171540724-mc1i, f-20261030171638837-52ze, f-20261030171859660-mtvd, f-20261030171944825-37uj, f-20261030172147355-0h24, f-20261030172753784-7rxg, f-20261030173030554-bubm, f-20261030173150699-djcd, f-20261030173334990-5v86, f-20261030174103096-sbc4, f-20261030174309430-zzq2, f-20261030174904240-3dv7 · runs 2026-10-17-v2.3 and 2026-10-24-v2.3
- Dean: "sma turnaround story" (also "turnaround sma", "sma turnaround story on 21 and 50", "crossing 200", "post rally situation, sma turnaround story"), almost always boxed with "cross", "flip" / "clean flip", "cross and leading" / "cross and laggard".
- Reading: his core trigger is the daily moving-average turnaround: the 21D crossing the 50D (sometimes the 200D) in the trade's direction, on the same bars as a MACD-histogram flip and the RS vs S&P / vs-sector lines crossing their MAs. "Leading"/"laggard" on the sector panel is the confirmation he wants.
- Change: new feature `sma_turn` = 21D crosses 50D (or price crosses 200D) in the trade direction within the last 5 sessions, and a `turnaround` setup requiring sma_turn + hist sign flip + RS-vs-sector cross within 3 sessions of each other (feeds B-001's confluence count). Test: event study 2023-2026, 1W/1M forward pair spread vs current Trigger family; ship if it beats v2.3 walk-forward Sharpe in both halves.

## B-001 update · 2026-10-31
- Same idea re-marked on f-20261028170135117-g7kc, f-20261028170337527-lj5k, f-20261028170458784-s5iu, f-20261028170819512-07ym, f-20261028171150665-6th0, f-20261028171701883-z0hj, f-20261028171836694-cws2, f-20261028172309693-yklz, f-20261028173523759-uvfp, f-20261028173653147-zzwd, f-20261028173907055-97p1, f-20261030171022919-ft3g, f-20261030174524350-1iw2 (TSN, JBS, FRPT, CRK, AXS, INTR, COO, FBIN, RPM, AXTA, IDT, INGR, UNFI): boxes "cross", "flip", "cross over relative SP500", "crossover relative sector, turning to laggard", "cross and leading" stacked on one bar across price, RS vs S&P, MACD and vs-sector panels. Confirms the confluence count is the priority item for the next engine version.

## B-001 · 2026-10-24 · open
- From: feedback f-20261018085348507-d9zy · run 2026-10-17-v2.3 · XOM long (graded A)
- Dean: "I have marked points of interests for long and short ideas. Hard to compute a rules based system around, its more about a feel and appreciation of the visual chart sometimes." 13 pins: (1-4) long entry where the weekly uptrend, RS vs S&P turn, MACD histogram cross and sector-vs-XLE line above its trend line all line up on the same bar; (5-7) short signal: daily crosses down through the 50D after a big extension, confirmed by a histogram flip and the vs-XLE line breaking its trend line; (8-9) long: bounce on the 200D, reclaim of the 50D, weekly cross back over the 40W; (10-13) long: bounce on the 200D, cross of the 21D heading to the 50D, vs-sector trend-line cross, histogram flip.
- Reading: he trades confluence, not single signals: an entry is the bar where price-MA, MACD histogram and relative-strength (vs S&P and vs sector) crosses cluster. He also uses the 200D as the pullback-buy level in an uptrend, and a 50D break after an extended run as a tactical short/exit, even inside a rising weekly.
- Change: (a) `confluence` feature = number of these firing within 3 sessions: price crosses 21D or 50D, MACD hist sign flip, RS-vs-sector crosses its MA, RS-vs-S&P crosses its 200D MA, weekly 40W cross; require >= 3 for the Trigger family and add it to the score. (b) new long setup "200D bounce": low within 2% of a rising 200D, close back over the 21D, hist flipping up. (c) short/exit flag "extension break": close under the 50D after > 2 sigma above it within 20 sessions, with hist flip. Test: event study on 2023-2026 history, 1W/1M forward spread vs current Trigger/Turn definitions, by half; ship only if it beats the v2.3 walk-forward Sharpe.

