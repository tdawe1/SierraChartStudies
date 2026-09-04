# Sierra Chart studies

Copy the `.cpp` (and any matching `.h`) into Sierra Chart `ACS_Source`, then **Analysis → Build Custom Studies DLL → Remote Build**.

## Orion

Stacked bid/ask absorption at a lookback swing (setup arrow), then a max/min-delta climax with rebound on bar ask−bid (trigger point).

Files: `Orion.cpp`, `orion_core.h`.

1. Copy both into `ACS_Source` (for example `C:\SierraChart\ACS_Source\`).
2. Analysis → Build Custom Studies DLL → select `Orion.cpp` → Remote Build.
3. Add **Orion**. Point **Max delta** and **Min delta** at Numbers Bars Calculated Values subgraphs for maximum and minimum ask-bid difference.
4. Recalculate. After an update that changes input order, remove the study and add it again.

Alerts: 1 setup short, 2 setup long, 3 trigger.

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
| Saty Pivot Ribbon | `SatyPivotRibbon.cpp` |
