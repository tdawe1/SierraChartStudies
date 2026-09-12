# Changelog

## 2026-09-11

### Orion

- Alerts are configurable per direction: separate on/off toggles and alert numbers for long setup, short setup, and trigger (defaults 2, 1, 3). The setup master toggle still gates both directions.
- Data overlay adds two lines under the arm status: bar delta with volume vs MA and gate flags, plus arm detail (entry, zone, climax, rebound target) or swing-scan state when flat.
- New study Orion - Account Balance (Live): opening balance plus today's closed P/L plus open position P/L, for accounts without EOD reconciliation. Manual opening-balance override, optional components and broker-reported balance lines.

### Executor

- New `OrionExecutor.cpp` (in the bundle: 8 sources, 11 studies): market
  entries with attached stop/target brackets from Orion's Trigger
  subgraphs, gated by the PropRiskOverlay halt state, flat-position check,
  and order-call success (fail-closed; sim-first). Offline twin is
  `signal_replay` over exported Trigger columns.
- Fixed `InitialBalanceStatistics.cpp` real-build errors: `SCDateTime`
  has no `DAYS()/MINUTES()/MICROSECONDS()` members (durations are
  file-scope day-fraction constants) — verified against Sierra's
  `scdatetime.h`. Stub aligned to the real API (conversion/double
  comparisons, `SetDateTime(int,int)`); all 8 sources pass the stub check.

### Prop risk

- `bundle.py`: merge all studies into one `AllStudies.cpp` (single
  SCDLLName, one namespace per study, global scsf_ forwarders) so one
  Remote Build produces a single DLL with all 11 studies. Study names on
  the chart are unchanged.
- `bundle.py --install`: deploys all sources + bundle to the live
  `ACS_Source` (`SC_ACS_SOURCE` overrides); refreshed the three stale
  copies there and added the missing PropRiskOverlay/BacktestExporter.
- Fixed real-build errors: `DateTimeToString` returns `SCString` and takes
  `(datetime, FLAG_DT_*)` (overlay + exporter), `SCInputRef::GetString()`
  returns `const char*` (exporter). Stub updated to match so local checks
  catch this class.
- `SCSFExport` is `extern "C"` (flat names, namespaces don't shield it),
  so the bundle demotes bundled copies to plain `void` and only the 9
  `scsf_` forwarders export. Stub now uses `extern "C"` too.

- New `PropRiskOverlay.cpp` (self-contained, Remote Build): live day P/L vs
  daily loss limit, trailing-drawdown room, profit-target progress, size
  calculator, session TRADE/STANDBY gate, red HALT lines. Reads the broker
  account (opening + daily closed + open P/L); needs Maintain Trade
  Statistics on the chart.
- Backtester rule pack: `profit_target` (first-hit stamp),
  `consistency_max_pct` (best-day share PASS/FAIL), `trade_windows`
  multi-session entries, `symbols` allowlist (optional Symbol column),
  `max_trades_per_day`. Intraday (`daily_loss_limit`) vs trailing
  (`max_drawdown_limit`) semantics documented in `backtest/README.md`.

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
