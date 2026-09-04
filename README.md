# Sierra Chart studies

Copy each `.cpp` into Sierra Chart `ACS_Source`. **Analysis → Build Custom Studies DLL → File → Select Files → Build → Remote Build**. Sierra Chart does not load DLLs built outside Remote Build / Visual C++.

Add from a chart: **Analysis → Studies → Add Custom Study**. Expand the DLL name, select the study, **Add**. They are not in the built-in **Add Study** list.

## Orion

Stacked bid/ask absorption at a lookback swing (setup arrow), then a max/min-delta climax with rebound on bar ask−bid (trigger point).

One file: `Orion.cpp`. Remote Build ignores extra headers unless you also select them, so the helpers are in this file.

1. Copy `Orion.cpp` into `ACS_Source` (for example `C:\SierraChart\ACS_Source\`).
2. Analysis → Build Custom Studies DLL → File → Select Files → `Orion.cpp` only → Build → Remote Build.
3. Analysis → Studies → Add Custom Study → expand **Orion** → **Orion - Absorption Climax**.
4. Point **Max delta** and **Min delta** at Numbers Bars Calculated Values subgraphs for maximum and minimum ask-bid difference.
5. Recalculate. After an update that changes input order, remove the study and add it again.

Alerts: 1 setup short, 2 setup long, 3 trigger. Optional arm-status text and absorption zone are on by default; turn them off under **Display**. After this update, remove and re-add the study so the new inputs appear.

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
