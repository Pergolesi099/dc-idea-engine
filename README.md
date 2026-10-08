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

**Manual run:** Actions tab → weekly-scan → Run workflow.
**Optional secret:** `POLYGON_API_KEY` (free key from polygon.io) for history gap-filling and fallback.
**Local test without network:** `python tests/test_offline.py`
