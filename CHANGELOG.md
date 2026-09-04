# Changelog

## 2026-09-04

### Orion

- `Orion.cpp` is self-contained for Remote Build (helpers inlined; extra headers are not required).
- Setup: stacked bid/ask absorption at a lookback swing. Grouping tries scales 1–4; POC is taken from the winning scale.
- One armed direction at a time. Trigger is not allowed on the arm bar.
- Trigger uses Numbers Bars max/min delta for climax and bar ask−bid for rebound. Marker offset is arrow offset + 3 ticks.
- Persistents reset on a full recalc, not only at bar 0.
- VAP collection caps at 1024 levels and logs once if truncated.
- Volume-MA gate is off by default. Setup arrow size and trigger marker size are inputs, applied each bar.
