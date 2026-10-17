# DC Autopilot — desk-fed chain (since 2026-10-16)

Authoritative procedure for the scheduled phases SCREEN, STAGE, FILLCHECK, VIXWATCH.
Supersedes the factor screener and the Drive ledger in the `weekly-dc-autopilot` skill.
That skill and `weekly-dc-portfolio-orders` / `-fill-monitor` still apply for **mechanics only**
(limit conventions, fill-check logic). Their safety rules stand.
**No live orders, ever.** Drafts only via `create_order_instruction`. Never cancel or modify a live order.

Unattended: never ask questions. Decide per these rules, log, notify on the events listed.

## Chain
Sat 08:52 REVIEW publishes pairs on the desk → Dean marks **Take** → Sun 19:53 SCREEN sizes the Takes
→ Mon 14:54 STAGE re-reads Takes (cut-off), re-sizes, stages drafts → Mon/Tue 16:15 FILLCHECK
→ VIXWATCH weekdays 15:51 / 20:51.

Dean's approval on the desk is the only selection step. Nothing is picked by Claude.

## Where state lives (desk database)
Desk: https://claude.ai/artifact/SDNEj77zxqT1ws5qMgjz8k — use the ArtifactData tool, pin `if_version` on existing docs.
- `book_control/control`: `{kill_switch, leverage, max_pairs, sizing, ...}`. Dean's dials. Read first, every run.
  `kill_switch: true` → log nothing, notify nothing, stop.
- `book/<week>`: the ledger. **Week = ISO week of the trading Monday** (`YYYY-Www`): a run on Saturday or Sunday uses the
  following Monday, so Sunday's SCREEN and Monday's STAGE write the same doc. Set the `week` field to the doc id. Schema:
```json
{"week": "2026-W43", "phase_done": [], "run_ids": [], "leverage": 2.0, "vix_fired": false,
 "nav_eur": 0, "eurusd": 0, "sizing": {}, "checks": {},
 "picks": [{"run_id": "", "supersector": "", "conviction": 0, "decision_id": "",
            "long": "", "long_co": "", "long_conid": 0, "long_px": 0, "long_qty": 0,
            "short": "", "short_co": "", "short_conid": 0, "short_px": 0, "short_qty": 0}],
 "dropped": [{"long": "", "short": "", "reason": ""}],
 "staged_instruction_ids": [], "log": [{"ts": "", "phase": "", "decision": "", "reason": ""}]}
```
- Previous weeks' `book/*` docs are the record of what the autopilot opened (used for closes).
- Idempotency: phase already in this week's `phase_done` → stop.

## Collecting the Takes (SCREEN and STAGE)
1. `runs`: query, newest first; this cycle = runs whose run_id date (first 10 chars) is ≥ the most recent
   Saturday (Paris) on or before today. Saturday scan plus any manual cycle since.
2. `decisions`: query `action == "take"` and `run_id in <cycle run ids>`.
3. Dedupe: a symbol may appear in one pair only. On a clash keep the later `decided_at`; log the other as dropped.
4. More than `max_pairs` (12): keep the highest conviction; ties → most recent run, then earliest `decided_at`.
5. Watch and pass are ignored.

## Phase: SCREEN (Sun 19:53 Paris)
1. Read control; create `book/<week>` if missing (copy `leverage` from control).
   The desk's Book tab reads this doc live, so keep `picks`, `sizing`, `checks`, `dropped` and `log` complete. Reset `vix_fired`.
2. Regime log only: SPX vs 200D, VIX. VIX > 30 → warning line in the notification. Never changes picks or leverage.
3. Collect Takes. **None** → write the ledger (picks empty), push "No Takes on the desk yet — cut-off Mon 14:54",
   stop. STAGE will still do the close-out.
4. For each leg: `search_contracts` (US listing, company name matches the desk ticket) and `get_price_snapshot`.
   Unresolvable contract → drop the pair, log it.
5. NAV EUR from `get_account_balances`, EUR/USD from IBKR. Run `python autopilot/size.py in.json out.json`
   (equal dollar per leg = NAV_USD × leverage / 2n, floored; drops zero-share pairs and re-sizes).
6. Write picks, sizing, checks to the ledger, add SCREEN to `phase_done`.
7. Push (≤3 lines): N pairs sized, leverage, gross × NAV, CHECK flags, legs with earnings in the hold window
   (from the run doc's `earnings_in_window`; flag only, Dean already saw it), desk link.

## Phase: STAGE (Mon 14:54 Paris, before the US open) — approval cut-off
1. Read control and this week's ledger. Leverage and max_pairs come from control **now** (Dean may change them on the
   desk until the cut-off); store them on the ledger. No ledger → create it and run SCREEN steps 3–5 inline (log "SCREEN missing").
2. **Re-collect Takes** (cut-off is now). Pairs added since Sunday are resolved and priced; pairs un-taken are removed.
   Log every change vs Sunday.
3. Fresh quotes on every leg. For pairs carried from Sunday, a leg that moved > 3% since the SCREEN quote → drop
   the pair, log it. Then re-size the survivors with `size.py` on fresh quotes and fresh NAV.
4. Shortable check on every short leg (IBKR). Not shortable → drop the pair, log it, re-size.
5. Clean up: delete unsubmitted instructions from earlier weeks in `get_order_instructions`.
6. Closes: every open position in `get_account_positions` that matches a prior week's `book/*` picks. Positions
   in no ledger are left alone and flagged. If a ticker is both closed and re-opened this week, net it: stage only
   the quantity difference, same side logic.
7. Entries: this week's picks. Limits 0.25% through the quote (buy ×1.0025, sell ×0.9975). Closes first, then entries.
8. Abort (stop, notify, stage nothing further): unresolved contract, a failed `create_order_instruction`, or > 50 instructions.
9. Zero Takes at cut-off → closes only.
10. Write instruction ids and STAGE to the ledger. Push: "N closes + M entries staged — submit in IBKR. Set stops manually (25% ref)."
    plus pairs dropped and why.

## Phase: FILLCHECK (Mon and Tue 16:15 Paris)
`weekly-dc-portfolio-fill-monitor` logic against this week's ledger picks. Re-stage remaining quantities.
Notify only if |skew| > 5%, a reprice gap > 2%, or a leg is unfilled; when re-staging, always warn to cancel the stale order first.
All filled and skew < 5% → write the ledger, no notification.

## Phase: VIXWATCH (weekdays 15:51 and 20:51 Paris)
VIX > 35, or +25% vs prior close, and this week's `vix_fired` false → stage flatten drafts for every open ledger position,
set `vix_fired: true`, urgent push. Otherwise write nothing. VIX 25 is not a trigger.

## Failure handling
- IBKR unreachable or auth expired: retry once, then push "IBKR reconnect needed" and stop. Never size or stage on web quotes.
- Desk database unreachable: do no staging; push the reason and stop.
- Anything not covered here: log, notify, stop that phase. Never improvise.

## Notifications
Push via SendUserMessage, max 3 lines, numbers first.
