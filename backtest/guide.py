"""Guided params builder. Stdlib only.

`run_guide()` asks a few questions (every one has a default — hammer
Enter) and writes a params file, then prints the exact `bt.py run`
command. `build_guide_params(answers)` is the pure, tested core.
"""

from __future__ import annotations

import json
import os

STRATEGY_CHOICES = {"1": "signal_replay", "2": "orion_bar"}


def build_guide_params(a: dict) -> dict:
    strategy = STRATEGY_CHOICES.get(str(a.get("strategy", "1")), "signal_replay")
    size_mode = str(a.get("size_mode", "fixed"))
    engine = {
        "qty": int(a.get("qty", 1)),
        "stop_ticks": int(a.get("stop_ticks", 12)),
        "target_ticks": int(a.get("target_ticks", 24)),
        "fee_per_side": float(a.get("fee_per_side", 2.10)),
        "slippage_ticks": float(a.get("slippage_ticks", 1.0)),
        "exec_mode": "next_open",
        "allow_long": True,
        "allow_short": True,
        "exit_on_opposite": True,
        "reverse_on_opposite": False,
        "max_hold_bars": 0,
        "account_size": 0.0,
        "risk_pct": 0.0,
        "max_qty": 10,
        "daily_loss_limit": 0.0,
        "max_drawdown_limit": 0.0,
        "regime": a.get("regime", "mean-reversion"),
    }
    if size_mode == "risk":
        engine.update({
            "qty": 1,
            "account_size": float(a.get("account_size", 50000)),
            "risk_pct": float(a.get("risk_pct", 1.0)),
            "max_qty": int(a.get("max_qty", 10)),
            "daily_loss_limit": float(a.get("daily_loss_limit", 1000)),
            "max_drawdown_limit": float(a.get("max_drawdown_limit", 2000)),
        })
    if size_mode == "atr":
        engine.update({
            "qty": 1,
            "stop_ticks": 0,  # required: one sizing rule per run
            "size_by_atr": True,
            "atr_risk_mult": float(a.get("atr_risk_mult", 1.0)),
            "account_size": float(a.get("account_size", 50000)),
            "risk_pct": float(a.get("risk_pct", 1.0)),
            "max_qty": int(a.get("max_qty", 10)),
            "daily_loss_limit": float(a.get("daily_loss_limit", 1000)),
            "max_drawdown_limit": float(a.get("max_drawdown_limit", 2000)),
        })
    if a.get("session_start") and a.get("session_end"):
        engine["session_start"] = a["session_start"]
        engine["session_end"] = a["session_end"]
    params = {"strategy": strategy,
              "strategy_params": a.get("strategy_params", {}),
              "tick_size": float(a.get("tick_size", 0.25)),
              "tick_value": float(a.get("tick_value", 12.50)),
              "engine": engine}
    if strategy == "orion_bar" and not params["strategy_params"]:
        params["strategy_params"] = {"setup_min_delta": 200, "rebound_pct": 50,
                                     "lifetime_bars": 3}
    return params


def _ask(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


def run_guide(out_path: str = "params.guide.json") -> dict:
    print("bt guide -- answer, or Enter for the [default].")
    a = {
        "strategy": _ask("strategy: 1=chart-signal replay, 2=orion sweep", "1"),
        "tick_size": _ask("tick size", "0.25"),
        "tick_value": _ask("tick value $/tick/contract (ES 12.50, NQ 5.00)", "12.50"),
        "stop_ticks": _ask("stop ticks (0=off)", "12"),
        "target_ticks": _ask("target ticks (0=off)", "24"),
        "size_mode": _ask("sizing: fixed, risk, or atr (ATR % sizing, no stop)", "fixed"),
    }
    a["regime"] = _ask("regime: mean-reversion, trend, breakout", "mean-reversion")
    while a["regime"] not in ("mean-reversion", "trend", "breakout"):
        print("pick exactly one: mean-reversion, trend, breakout (no mixed runs)")
        a["regime"] = _ask("regime", "mean-reversion")
    if a["size_mode"] == "risk":
        a.update({
            "account_size": _ask("account size $", "50000"),
            "risk_pct": _ask("risk per trade %", "1.0"),
            "daily_loss_limit": _ask("daily loss halt $", "1000"),
            "max_drawdown_limit": _ask("max drawdown halt $", "2000"),
        })
    if a["size_mode"] == "atr":
        a.update({
            "account_size": _ask("account size $", "50000"),
            "risk_pct": _ask("risk per trade %", "1.0"),
            "atr_risk_mult": _ask("ATR multiple (size so mult x ATR = risk $)", "1.0"),
            "daily_loss_limit": _ask("daily loss halt $", "1000"),
            "max_drawdown_limit": _ask("max drawdown halt $", "2000"),
        })
    if _ask("restrict to a time session? yes/no", "no").startswith("y"):
        a["session_start"] = _ask("session start HH:MM", "08:30")
        a["session_end"] = _ask("session end HH:MM", "11:00")
    data = _ask("data CSV path", "sample_data.csv")
    params = build_guide_params(a)
    with open(out_path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"\nwrote {out_path}\nrun it:\n"
          f"  python3 bt.py run --data {data} --params {out_path} --out out/guide")
    return params


if __name__ == "__main__":
    run_guide(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "params.guide.json"))
