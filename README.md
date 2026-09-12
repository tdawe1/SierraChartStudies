# Sierra Chart studies

Copy each `.cpp` into Sierra Chart `ACS_Source`. **Analysis → Build Custom Studies DLL → File → Select Files → Build → Remote Build**. Sierra Chart does not load DLLs built outside Remote Build / Visual C++.

One build for everything: `python3 bundle.py --install` bundles all studies into `AllStudies.cpp` and copies every source plus the bundle straight into Sierra's `ACS_Source` (Wine: `~/.wine/drive_c/SierraChart/ACS_Source/`, override with `SC_ACS_SOURCE`). Then select **only** `AllStudies.cpp` and Remote Build once — all studies land in one DLL with unchanged names. Re-run after editing any source.

Add from a chart: **Analysis → Studies → Add Custom Study**. Expand the DLL name, select the study, **Add**. They are not in the built-in **Add Study** list.

## Orion

Stacked bid/ask absorption at a lookback swing (setup arrow), then a max/min-delta climax with rebound on bar ask−bid (trigger point).

One file: `Orion.cpp`. Remote Build ignores extra headers unless you also select them, so the helpers are in this file.

1. Copy `Orion.cpp` into `ACS_Source` (for example `C:\SierraChart\ACS_Source\`).
2. Analysis → Build Custom Studies DLL → File → Select Files → `Orion.cpp` only → Build → Remote Build.
3. Analysis → Studies → Add Custom Study → expand **Orion** → **Orion - Absorption Climax**.
4. Point **Max delta** and **Min delta** at Numbers Bars Calculated Values subgraphs for maximum and minimum ask-bid difference.
5. Recalculate. After an update that changes input order, remove the study and add it again.

Alerts: setup short, setup long, and trigger, each with its own on/off toggle and alert number (defaults 1, 2, 3). The setup master toggle stays off by default. Optional arm-status text, data overlay, and absorption zone are on by default; turn them off under **Display**. New study **Orion - Account Balance (Live)** shows opening balance plus today's closed P/L plus open position P/L for accounts without EOD reconciliation. After this update, remove and re-add the study so the new inputs appear.

Core tests (no Sierra headers):

```
g++ -std=c++17 -O2 -o /tmp/orion_core_test orion_core_test.cpp && /tmp/orion_core_test
```

## Other studies

| Study | Source |
|---|---|
| The Flipper / Delta Colored Candles | `FlipperStudies.cpp` |
| Discord Alerts | `DiscordAlerts.cpp` |
| Initial Balance Statistics | `InitialBalanceStatistics.cpp` |
| Saty Pivot Ribbon | `SatyPivotRibbon.cpp` — Add Custom Study → **Saty Pivot Ribbon** |
| Prop Risk Overlay | `PropRiskOverlay.cpp` — Add Custom Study → **Prop Risk Overlay** |
| Orion Executor | `OrionExecutor.cpp` — Add Custom Study → **Orion Executor** (sim-first; see below) |

## Backtesting

Local assessment system for the studies: batch runs, results store, in/out-of-sample + walk-forward checks, one-command comparison report with equity curves. See `backtest/README.md`.

```
python3 backtest/bt.py demo --out demo-report.html
```

## Lockout (native enforcement + sim protocol)

Enforcement lives in Sierra, not in our studies: **Global Settings >>
Global Profit/Loss Management**. Configure both sides, driven by SC's own
Daily Net P/L (fills-based — the same numbers the Trade Window shows):

- Daily Net Loss >> Flatten When Loss Rules Met + Loss Trigger Value
- Daily Net Profit >> Flatten When Profit Rules Met + Profit Trigger Value,
  plus Trail Daily Net Profit (give-back guard)
- Lock After Positions Flattened + Lock for Day + Disable Auto Trading
  on Lock (no re-entry, automated entries dead while locked)
- Profit target behavior: flatten + lock (decided; not manage-the-runner)

Sierra disclaims reliability here (fill completeness), so abuse-test in
sim before trusting it — tick each off:

- [ ] Loss halt: drive a sim account past the loss trigger, confirm
  auto-flatten of every position, lock for the day, auto-trading disabled
- [ ] Profit halt: same past the profit trigger, including the trail
  (trigger, rise, give-back flatten)
- [ ] Late connect: connect mid-session with missing fills, confirm you
  know which numbers are wrong and by how much (broker compare is the
  detector — nothing compensates for incomplete fills)
- [ ] Fast replay + outside-Sierra orders: confirm the failure mode of
  each, recorded with numbers, before live use

Executor halt contract (v1, one position): enter only when the wired
"Halt state" reads 0 at the last bar (unwired/failed read = halted),
flat per `sc.GetTradePosition`, and the order call itself succeeds
(failures stand down — this is the native-lock detector; no ACSIL
auto-trade query exists).

## Orion Executor (v1, sim-first)

Fires market entries with attached stop/target brackets from Orion's
TriggerLong/TriggerShort subgraphs under the halt contract above.
Wiring on one chart: add **Orion Absorption Climax**, **Prop Risk Overlay**,
**Orion Executor** (all land in one DLL via `bundle.py --install`); note
the Study IDs (chart **Studies** list, `Study ID` column) and enter them in
the executor inputs (Orion ID + trigger subgraphs default 2/3; halt ID +
subgraph default 1). Keep Enabled at No until: sim account, sim mode on,
chart auto-trading on, brackets verified on 1-lots, halt read confirmed
via the executor's `Last action` subgraph (2 = halted, 4 = order error
with the reason in the Message Log).

Backtest parity: the offline twin is `signal_replay` — wire the same
Trigger subgraphs into the BacktestExporter's Signal inputs, export, and
run `bt.py run` with `exec_mode: next_open` and matching
`stop_ticks`/`target_ticks`.
