# AGENTS.md — SierraChartStudies

## Repos and directories

- `~/SierraChartStudies` — study sources. Origin `tdawe1/SierraChartStudies` (fork),
  upstream `TradesTrevor/SierraChartStudies`. Open PRs in the fork; a cross-fork
  PR to upstream was rejected ("no commits between"), so retarget from the PR page.
- `~/SierraChart` (separate checkout = live Sierra Chart data folder) — copy the
  built `.cpp` there so Remote Build can compile it. Do NOT commit build outputs
  there; it already carries many untracked DLLs/CHTs. `TraderOracle.cpp` there owns
  the unrelated **Olympus** study — Orion work never touches it (they were confused
  once; Olympus ≠ Orion).

## Orion (`Orion.cpp`)

- Self-contained single file for Remote Build: copy only `Orion.cpp` into
  `ACS_Source`, select it, Remote Build. Never add headers to the build.
- Pure logic lives in `namespace orion` and is mirrored in `orion_core.h`
  (verified in sync via diff). Tests: `orion_core_test.cpp`, run with
  `g++ -std=c++17 -O2 -o /tmp/orion_core_test orion_core_test.cpp && /tmp/orion_core_test`.
- **Input indices are append-only** (0–44 used). Never insert/reorder; after index
  changes the user must remove and re-add the study. Same for subgraphs (0–6),
  persistent ints (1–11) / floats (1–6) in the main study.
- Alerts use `sc.SetAlert(number, index, msg)` with per-direction toggles and
  numbers (long 2, short 1, trigger 3); gated on `index >= last_index - 1` and
  `!sc.IsFullRecalculation`, setups fire once per new arm.
- Chart overlay uses `s_UseTool` `DRAWING_TEXT` with `UTAM_ADD_OR_ADJUST` and fixed
  `LineNumber` keys (202609041–43); position via `BeginDateTime = 1`,
  `BeginValue` percent, `UseRelativeVerticalValues = 1`. Delete drawings when
  their toggle is off or lines linger.
- `scsf_OrionAccountBalance` (same file): LIVE = non-zero
  `m_AvailableFundsForNewPositions` from `GetTradeAccountData` (primary; correct
  intraday on Rithmic), falling back to opening (`m_AccountValue`, manual override
  available) + daily closed P/L (`GetTradeStatisticsForSymbolV2`, needs
  `MaintainTradeStatisticsAndTradesData = 1`) + open P/L (`GetTradePosition`,
  check `== 1`) as fallback/cross-check. **All figures must share the chart
  trade account and chart symbol** — never mix a configured account with
  chart-scoped P/L calls. Throttled studies need `sc.UpdateAlways = 1` or quiet
  charts go stale.
- No `sierrachart.h` locally: syntax-check via stub at `/tmp/orion_check/sierrachart.h`
  with `g++ -std=c++17 -fsyntax-only -I/tmp/orion_check Orion.cpp`. Stub must define
  `SCDLLName(x)` *with* trailing semicolon. Rebuild/refresh the stub if new ACSIL
  APIs are used.
- After edits: run core tests, syntax-check, copy `Orion.cpp` to `~/SierraChart/`,
  update README + CHANGELOG (dated `### Orion` section).

## Backtesting

`backtest/` holds the local assessment harness: `python3 backtest/bt.py demo --out demo-report.html`.

## Other studies

- `FlipperStudies.cpp` was a full-file merge conflict (theirs + HEAD
  concatenated, duplicate studies). Resolved theirs-first, then appended the
  HEAD-only `scsf_DynamicFlipper`. When merging duplicates, diff the shared
  studies first — here they differed by whitespace only.
- `SatyPivotRibbon.cpp` had two real bugs: `SCStudyGraphRef` (must be
  `SCStudyInterfaceRef`) and `DRAWSTYLE_COLORBAR` (official name is
  `DRAWSTYLE_COLOR_BAR`, verified against Sierra docs).
- `DiscordAlerts.cpp`: pass `SCString` to `const char*` params via `.GetChars()`,
  never rely on implicit conversion.
- Syntax-check loop for every study (stub is at `/tmp/orion_check/sierrachart.h`,
  extended as new ACSIL APIs appear):
  `for f in *.cpp; do g++ -std=c++17 -fsyntax-only -I/tmp/orion_check $f; done`
  All five studies must print no errors. Stub gaps (missing member/constant) vs
  real bugs (wrong type/name per Sierra docs) — verify the latter before
  touching source.
- `backtest/` is tracked (its own `.gitignore` excludes outputs). `*_64.dll`
  files are build artifacts: untracked, never commit them.
