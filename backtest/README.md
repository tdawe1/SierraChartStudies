# Study assessment system: run, compare, decide

Stdlib-only Python (`backtest/`). No pip, no build, no server — the
remote needs just `python3`.

## First look (30 seconds)

```
cd backtest
python3 bt.py demo --out demo-report.html   # runs sample matrix, writes report
```

Open `demo-report.html` in a browser: overlaid equity curves + a
leaderboard. That is the whole system in miniature.

## Daily workflow

**1. Get data out of the chart.** Copy `exporter/BacktestExporter.cpp`
into `ACS_Source`, Remote Build, add to the chart wired to your
trigger/setup subgraphs (e.g. Orion *Trigger Long/Short*) plus Numbers
Bars Max/Min delta. Recalculate, copy the CSV over.

**2. Log runs.** Every run is stored in `results.db` with its artifacts
under `results/<run-id>/` — comparing is a query, not a pile of CSVs.

```
python3 bt.py run --data ES-aug.csv --params params.replay.json --out out/aug-base --tag "orion base"
python3 bt.py run --data ES-aug.csv --params params.risk.json --out out/aug-risk --tag "prop risk"
python3 bt.py sessions --journal journal.csv
python3 bt.py remote --host user@gpu-box --data ES-aug.csv --params params.replay.json --notify you@example.com
```

## Prop risk controls

Set in the `engine` block (`params.risk.json` is a ready example):
`account_size` + `risk_pct` size every trade off its stop distance
(`max_qty` caps it); `daily_loss_limit` flattens and blocks new entries
for the day; `max_drawdown_limit` stops the run. Reports show the sizing
rule and a `risk halts` count, and `trades.csv` records per-trade qty.

## Journal time-of-day

`sessions` reads `DateTime,Symbol,Side,Qty,Entry,Exit,PnL`
(`sample_journal.csv` shows the contract; PnL is recomputed from
Entry/Exit when blank). Default windows are overnight / morning / midday
/ afternoon / evening — override with `--session NAME=HH:MM-HH:MM`.
Take the winning window into the engine's `session_start/session_end`.

## Remote email notification

`remote --notify you@example.com` emails `report.txt` when results land
(sendmail, else SMTP localhost:25; needs a local MTA, nothing committed).
`--dry-run` shows the email step without sending. `--sender` sets From.

**3. Compare.**

```
python3 bt.py compare --dataset ES-aug --out report-aug.html
python3 bt.py compare --study orion_bar --out report-orion.html --top 10
```

Ranks by out-of-sample total when a split exists, else by total.
Buy-and-hold baselines are added per dataset automatically. `⚠` flags
runs that are profitable in-sample but flat/losing out-of-sample.

## What each command is for

| Command | Answers |
|---|---|
| `run` | How did this study+params do on this data? |
| `run --split frac:0.7` (or `--split "2026-07-01"`) | Does it hold up out-of-sample? |
| `run` with `params.risk.json` | Does it survive prop daily-loss / drawdown rules? |
| `sweep` | Which params are worth a closer look? (confirm winners with `run --split`) |
| `walkforward --train N --test M` | Does it survive rolling regimes, not just one lucky split? |
| `compare` | Which study wins, side by side, with equity curves? |
| `remote --host … [--notify me@x]` | Same `run`, on a bigger box; emailed report on completion. |
| `sessions --journal j.csv` | Which times of day suit you? |
| `guide` | Builds a params file interactively for a first run. |

## Honesty notes (what the numbers assume)

- Signals fire on **closed bars**, fills at the **next open** by default.
- Splits/walk-forward compute signals **once over full history** —
  valid because every strategy is causal (past bars only, never peeks).
  The portfolio is then simulated per segment starting flat.
- One position at a time, stop-before-target on ambiguous bars,
  per-side commission + tick slippage on every fill. ES defaults in
  `params.*.json` (`tick_size` 0.25 / `tick_value` 12.50; NQ = 0.25/5.00).
- `orion_bar` approximates the setup (stacked VAP absorption → bar-delta
  gate) for fast sweeps. `signal_replay` replays chart-exported signals
  exactly — assess any study, including ones with no offline port.

## Plug in another study

Exact mode needs no code: export its signals, replay them. For a native
offline strategy, add `my_study(bars, **kw) -> list[int]` in
`strategies.py` (+1/−1/0 per closed bar, past data only) and register it
in `STRATEGIES`.

## Output schemas (stable)

Every `run` writes the same four files, same columns/keys, so anything
can parse them (`pandas.read_csv`, `jq`, a phone browser):

| File | Shape | Human surface |
|---|---|---|
| `trades.csv` | `dir,entry_idx,entry_time,entry,exit_idx,exit_time,exit,reason,pnl,bars_held,qty` | spreadsheet (desktop) |
| `equity.csv` | `stamp,equity` | chart it anywhere |
| `summary.json` | `{params, metrics, is_metrics?, oos_metrics?}`; metrics keys: `trades,wins,losses,win_rate,total_pnl,avg_win,avg_loss,profit_factor,expectancy,max_drawdown,calmar,sharpe_trade,max_consec_losses,avg_bars_held,risk_halts` | `jq .metrics` |
| `report.txt` | same numbers, aligned text | terminal, email body |
| `report.html` (`compare`) | top-5 cards + SVG equity + full leaderboard; viewport + responsive CSS, table scrolls horizontally | desktop and mobile browsers |

## Files

`bt.py` (CLI) · `engine.py` (fills + metrics + prop risk) · `data.py` (CSV contract) ·
`strategies.py` (study adapters + orion_core ports) · `store.py`
(sqlite registry) · `report.py` (leaderboard + HTML/SVG) · `remote.py`
(SSH runner) · `notify.py` (completion email) · `journal.py` (time-of-day) ·
`guide.py` (interactive params) · `exporter/BacktestExporter.cpp` (chart → CSV) ·
`params.replay|orion|sweep|risk.json` · `sample_data|trend|journal.csv` · `tests/`

## Tests

```
python3 -m unittest discover -s tests -v   # from backtest/
```
