# dc-idea-engine

Weekly technical sweep for the DC long/short pairs book. Runs every Saturday on GitHub Actions.

**Flow:** TradingView screen of US stocks > $2bn → S1/S2/S3 signals ranked within each of 11 supersectors → full daily history for the top 25 per sector/side (Yahoo, Polygon for gaps) → exact 21/50/200D distances, smoothness, RS vs sector ETF → top 6 per sector/side → ~130 charts.

**Outputs**
- `output` branch: `results.json` + `charts/*.png` for the latest run (overwritten weekly).
- `main/history/YYYY-MM-DD.json`: archive of every run's shortlist.
- `main/data/universe.csv`: cached universe, used if TradingView is unavailable.

**Strategies** (distance = % from SMA; all ranked within supersector)
- **S1 thrust:** 21D distance > 50D and 200D distances, all same sign. Bonus when 200D > 50D (the "21 +++ / 50 + / 200 ++" shape).
- **S2 momentum pullback:** sector leaders on 3M perf with 21D < 0 and 50D > 0 (longs); laggards mirrored (shorts).
- **S3 200D breakout/breakdown:** within ±3% of the 200D, smooth approach (R² ≥ 0.55, ≤ 6 crosses of the 21D in 63 sessions).

All thresholds live in `config.yaml`.

**Manual run:** the desk's *Run new scan* button (scan + Claude review, desk updated), or Actions tab → weekly-scan → Run workflow (scan only; `engine` input = a version or `latest`).

## Engine versions
Small, incremental improvements ship as numbered engine versions so each change can be judged against the last.
- `config.yaml` → `engine.version` is the version the code on `main` runs; `engines.json` is the changelog the desk shows.
- **To ship a change:** edit `scanner/` and/or `config.yaml`, run `python tools/release_engine.py 2.1 "summary" "change 1" "change 2"`, commit everything together, push. The `release-tag` workflow tags it `engine-v2.1`.
- Every run is stamped with its engine: run id `YYYY-MM-DD-v2.1` (`-r2`, `-r3` for repeat runs the same day).
- **Older versions stay runnable:** `engine: 2.0` checks out tag `engine-v2.0` (code, config and learned weights frozen at release) and runs it on today's data. Only the current version learns and writes `model/`.
- Minor bump (2.0 → 2.1) for threshold or weight tweaks; major bump (2 → 3) when the steps or the features change.
**Optional secret:** `POLYGON_API_KEY` (free key from polygon.io) for history gap-filling and fallback.
**Local test without network:** `python tests/test_offline.py`
