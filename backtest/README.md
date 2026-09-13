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

**1a. No chart needed (offline strategies).** Convert Sierra tick files
straight to bars — no exporter, no Recalculate, no copy-paste:
`python3 bt.py scid --scid ~/.wine/drive_c/SierraChart/Data/ESU6.CME.scid --out ESU6-5m.csv`
(16.6M ticks → 20k bars in ~12s; `--check-dly 2026/09/10` verifies price
scale against the `.dly` row). Automatable per contract roll or on a
cron/scp pull from the Sierra box.
**1b. Get data out of the chart (exact chart signals only).** Copy `exporter/BacktestExporter.cpp`

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

| Rule | Key | Behavior |
|---|---|---|
| Position sizing | `account_size` + `risk_pct` (`max_qty` cap) | contracts = floor(risk $ / stop $); per-trade `qty` in `trades.csv`. Exactly one rule per run: `size_by_atr: true` sizes from the fill bar's `ATR14` instead (contracts = floor(risk $ / (`atr_risk_mult` × ATR $ + fees)), unknown ATR → qty 1 with a warning) and requires `stop_ticks: 0`, otherwise the run is rejected |
| Intraday drawdown | `daily_loss_limit` | flattens, blocks the day, resumes next day |
| Trailing drawdown | `max_drawdown_limit` | vs peak marked equity; flattens and stops the run |
| Profit target | `profit_target` | reports first-hit stamp (`target_hit`) |
| Consistency | `consistency_max_pct` | best-day share of profit-day total; `consistency_ok` PASS/FAIL |
| Trading hours | `trade_windows` (`session_start/end` folds in) | entries only inside windows |
| Instruments | `symbols` | allowlist vs `Symbol` column (symbol-less bars pass; add the column to enforce) |
| Pace | `max_trades_per_day` | entries-per-day cap |

Reports show the sizing rule, `risk halts`, target and consistency lines.
For live manual trading, the **Prop Risk Overlay** study
(`PropRiskOverlay.cpp`: Remote Build, add to chart) draws the same
guardrails from the broker account: day P/L vs limit, trailing room,
target %, size calculator, session TRADE/STANDBY, red HALT lines.

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
| `sweep` | Which params are worth a closer look? (confirm winners with `run --split`; max 200 combos per file — fail fast above; each combo is logged with its grid size as `n_trials`) |
| `walkforward --train N --test M [--embargo-days D]` | Does it survive rolling regimes, not just one lucky split? (drops the first D days of each test window, default 1; `windows.csv` records dropped bars) |
| `compare` | Which study wins, side by side, with equity curves? |
| `promote --run <id>` | May a new signal ship? (OOS expectancy>0, ≥30 OOS trades, costs on, beats buy-and-hold; exit 1 otherwise) |
| `verify --run <id>` | Does this logged run reproduce? (dataset hash + engine re-execution; exit 1 on mismatch) |
| `matrix --data D --params P.json [--params Q.json] [--session NAME=HH:MM-HH:MM] [--out m.csv]` | Which strategy works when? (entries-only slices per strategy × session; <20-trade cells unranked; nothing logged) |
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
- Live twin: `OrionExecutor.cpp` (repo root, in the bundle) turns the same trigger subgraphs into
  real `sc.BuyEntry`/`sc.SellEntry` orders (closed-bar, sim default, no reversal — new entries
  are refused while a position exists).
  To certify exactly what it will trade, wire the trigger subgraphs into the exporter's
  Signal inputs and `run` the export with `signal_replay`.

## Regimes and the signal budget

Every run declares exactly one `engine.regime`: `mean-reversion`, `trend`,
or `breakout` — there is no `mixed`. Trades inherit the run's regime and
`summary.json` carries a `by_regime` block, so strategies are compared
across runs, never blended into one equity curve. A new signal, strategy
function, or exporter signal ships only after `promote --run <id>` passes
on a walk-forward or `--split` run; tag the shipped params
`promoted:<run-id>`.


## Output schemas (stable)

Every `run` writes the same four files, same columns/keys, so anything
can parse them (`pandas.read_csv`, `jq`, a phone browser).
`walkforward` adds a fifth (`windows.csv`: per-window start/end/trades/pnl).

| File | Shape | Human surface |
|---|---|---|
| `trades.csv` | `dir,entry_idx,entry_time,entry,exit_idx,exit_time,exit,reason,pnl,bars_held,qty,regime` | spreadsheet (desktop) |
| `equity.csv` | `stamp,equity` | chart it anywhere |
| `summary.json` | `{params, metrics, is_metrics?, oos_metrics?}`; `run`/`split` metrics keys: `trades,wins,losses,win_rate,total_pnl,avg_win,avg_loss,profit_factor,expectancy,max_drawdown,calmar,sharpe_trade,max_consec_losses,avg_bars_held,risk_halts,target_hit,target_hit_stamp,best_day,best_day_pnl,best_day_pct,consistency_ok,regime,by_regime` (`profit_factor`/`calmar` are null when undefined: no losing trades / no drawdown). `walkforward` metrics carry the same keys minus the prop extras (`target_hit*`, `best_day*`, `consistency_ok`) — window detail lives in `windows.csv` | `jq .metrics` |
| `report.txt` | same numbers, aligned text | terminal, email body |
| `report.html` (`compare`) | top-5 cards + SVG equity + full leaderboard; viewport + responsive CSS, table scrolls horizontally | desktop and mobile browsers |

Input CSVs accept an optional `Symbol` column (drives the `symbols` rule).

The store (`results.db`) is append-only: each run logs code git sha
(+`-dirty`), dataset sha256/row count/stamp range, and the trial count
that produced its params (`n_trials`; sweeps record their grid size), and
the artifact dir keeps its own `params.json` copy. Old rows without
hashes still verify — the hash check is skipped with a note, and newer
metric keys are reported as "added since" rather than mismatches.

## Files

`bt.py` (CLI) · `scid.py` (.scid tick → bar CSV) · `engine.py` (fills + metrics + prop risk) · `data.py` (CSV contract) ·
`strategies.py` (study adapters + orion_core ports) · `store.py`
(sqlite registry) · `report.py` (leaderboard + HTML/SVG) · `remote.py`
(SSH runner) · `notify.py` (completion email) · `journal.py` (time-of-day) ·
`guide.py` (interactive params) · `exporter/BacktestExporter.cpp` (chart → CSV) ·
`params.replay|orion|sweep|risk.json` · `sample_data|trend|journal.csv` · `tests/`

## Tests

```
python3 -m unittest discover -s tests -v   # from backtest/
```
