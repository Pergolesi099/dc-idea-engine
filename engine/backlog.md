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

## B-001 · 2026-10-24 · open
- From: feedback f-20261018085348507-d9zy · run 2026-10-17-v2.3 · XOM long (graded A)
- Dean: "I have marked points of interests for long and short ideas. Hard to compute a rules based system around, its more about a feel and appreciation of the visual chart sometimes." 13 pins: (1-4) long entry where the weekly uptrend, RS vs S&P turn, MACD histogram cross and sector-vs-XLE line above its trend line all line up on the same bar; (5-7) short signal: daily crosses down through the 50D after a big extension, confirmed by a histogram flip and the vs-XLE line breaking its trend line; (8-9) long: bounce on the 200D, reclaim of the 50D, weekly cross back over the 40W; (10-13) long: bounce on the 200D, cross of the 21D heading to the 50D, vs-sector trend-line cross, histogram flip.
- Reading: he trades confluence, not single signals: an entry is the bar where price-MA, MACD histogram and relative-strength (vs S&P and vs sector) crosses cluster. He also uses the 200D as the pullback-buy level in an uptrend, and a 50D break after an extended run as a tactical short/exit, even inside a rising weekly.
- Change: (a) `confluence` feature = number of these firing within 3 sessions: price crosses 21D or 50D, MACD hist sign flip, RS-vs-sector crosses its MA, RS-vs-S&P crosses its 200D MA, weekly 40W cross; require >= 3 for the Trigger family and add it to the score. (b) new long setup "200D bounce": low within 2% of a rising 200D, close back over the 21D, hist flipping up. (c) short/exit flag "extension break": close under the 50D after > 2 sigma above it within 20 sessions, with hist flip. Test: event study on 2023-2026 history, 1W/1M forward spread vs current Trigger/Turn definitions, by half; ship only if it beats the v2.3 walk-forward Sharpe.

