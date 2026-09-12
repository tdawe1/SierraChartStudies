# Changelog

## 2026-09-11

### Orion

- Alerts are configurable per direction: separate on/off toggles and alert numbers for long setup, short setup, and trigger (defaults 2, 1, 3). The setup master toggle still gates both directions.
- Data overlay adds two lines under the arm status: bar delta with volume vs MA and gate flags, plus arm detail (entry, zone, climax, rebound target) or swing-scan state when flat.
- New study Orion - Account Balance (Live): opening balance plus today's closed P/L plus open position P/L, for accounts without EOD reconciliation. Manual opening-balance override, optional components and broker-reported balance lines.

## 2026-09-04

### Orion

- Setup: stacked bid/ask absorption at a lookback swing. Grouping tries scales 1–4 plus the user scale; the largest stack wins (tighter scale on a tie). POC is taken from the winning scale.
- One armed direction. Trigger is not allowed on the arm bar. Trigger runs before lifetime/break expiry and before a new setup on the same bar; a rebound can still fire on the invalidation bar.
- Trigger uses Numbers Bars max/min delta for climax and bar ask−bid for rebound. Marker offset is arrow offset + 3 ticks. Wire both max and min, or neither.
- Wide VAP bars keep high and low extremes. Full-bar delta and volume are used even when grouping is truncated.
- Persistents reset on a full recalc, not only at bar 0.
- Setup arrows default to mint / rose, 6 ticks off the high/low. Optional absorption zone and arm-status drawing. Inputs are grouped by section.
- Alerts include stack, scale, and climax. Setup alerts fire once per new arm.
- Zero bar-delta is not both long and short. Closed-bar mode skips full VAP grouping until the bar closes. A live trigger bar keeps its zone fill.
- Volume-MA gate is off by default. Absolute rebound scales with volume MA when that filter is on.
- `Orion.cpp` is self-contained for Remote Build.
