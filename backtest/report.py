"""Comparison reports. Stdlib only, dependency-free HTML.

`build_html` takes run dicts (as returned by store.list_runs) with an
extra "equity" key: list of (stamp, value). Output is one self-contained
.html file: sortable-by-PnL table plus overlaid equity curves as inline
SVG. Double-click to open, no server, no JS, works offline.
"""

from __future__ import annotations

import html

PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
           "#db2777", "#0891b2", "#65a30d", "#4f46e5", "#ea580c"]

COLS = [("trades", "Trades"), ("win_rate", "Win%"), ("total_pnl", "Total $"),
        ("expectancy", "Exp $"), ("profit_factor", "PF"),
        ("max_drawdown", "MaxDD $"), ("calmar", "Calmar"),
        ("sharpe_trade", "SharpeT"), ("max_consec_losses", "MaxLoss#")]


def _fmt(m: dict, key: str) -> str:
    v = m.get(key, 0)
    if key == "win_rate":
        return f"{v * 100:.0f}"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v}"
    return str(v)


def rank_key(r: dict) -> float:
    oos = r.get("oos_metrics")
    if oos:
        return oos.get("total_pnl", 0)
    return r.get("metrics", {}).get("total_pnl", 0)


def leaderboard_text(runs: list[dict]) -> str:
    head = (f"{'rank':<4} {'run':<34} {'study':<14} {'dataset':<18} "
            f"{'n':>3} {'win%':>4} {'total$':>9} {'oos$':>9} {'PF':>5} {'maxDD$':>8} {'trials':>6}")
    lines = [head]
    for i, r in enumerate(sorted(runs, key=rank_key, reverse=True), 1):
        m = r["metrics"]
        oos = (r.get("oos_metrics") or {}).get("total_pnl", "")
        ds = r["dataset"].split("/")[-1][:18]
        lines.append(
            f"{i:<4} {r['id']:<34} {r['study']:<14} {ds:<18} "
            f"{m.get('trades', 0):>3} {_fmt(m, 'win_rate'):>4} "
            f"{m.get('total_pnl', 0):>9,.0f} "
            f"{(f'{oos:,.0f}' if oos != '' else '-'):>9} "
            f"{m.get('profit_factor', 0):>5} {m.get('max_drawdown', 0):>8,.0f} "
            f"{(r.get('n_trials') if r.get('n_trials') is not None else '-'):>6}")
    return "\n".join(lines)


def _svg(equities: list[tuple[str, list[float]]], w: int = 920, h: int = 300) -> str:
    if not equities:
        return "<p>No equity curves.</p>"
    allv = [v for _, pts in equities for v in pts] or [0.0]
    lo, hi = min(allv + [0.0]), max(allv + [0.0])
    span = (hi - lo) or 1.0
    nmax = max(len(pts) for _, pts in equities)

    def xy(i: int, n: int, v: float) -> tuple[float, float]:
        x = 46 + (w - 66) * (i / max(n - 1, 1)) if n > 1 else 46
        y = 10 + (h - 40) * (1 - (v - lo) / span)
        return x, y

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for f in (0.0, 0.5, 1.0):
        _, y = xy(0, 2, lo + span * f)
        val = lo + span * f
        parts.append(
            f'<line x1="46" y1="{y:.1f}" x2="{w - 20}" y2="{y:.1f}" '
            f'stroke="#e5e7eb"/><text x="2" y="{y + 4:.1f}" font-size="11" '
            f'fill="#6b7280">${val:,.0f}</text>')
    _, zy = xy(0, 2, 0.0)
    if lo < 0 < hi:
        parts.append(f'<line x1="46" y1="{zy:.1f}" x2="{w - 20}" y2="{zy:.1f}" '
                     f'stroke="#9ca3af" stroke-dasharray="4 3"/>')
    for k, (label, pts) in enumerate(equities):
        c = PALETTE[k % len(PALETTE)]
        path = " ".join(f"{'M' if i == 0 else 'L'}{xy(i, len(pts), v)[0]:.1f},"
                        f"{xy(i, len(pts), v)[1]:.1f}" for i, v in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{c}" stroke-width="2"/>')
    parts.append("</svg>")
    legend = "".join(
        f'<span style="color:{PALETTE[k % len(PALETTE)]}">\u25a0</span> '
        f'{html.escape(label)} &nbsp; ' for k, (label, _) in enumerate(equities))
    return "\n".join(parts) + f"<p>{legend}</p>"


def build_html(runs: list[dict], title: str = "Study comparison") -> str:
    ordered = sorted(runs, key=rank_key, reverse=True)
    has_oos = any(r.get("oos_metrics") for r in runs)
    th = "".join(f"<th>{label}</th>" for _, label in COLS)
    th += "<th>Trials</th>"
    if has_oos:
        th += "<th>IS $</th><th>OOS $</th>"
    rows = []
    for i, r in enumerate(ordered, 1):
        m = r["metrics"]
        tds = "".join(f"<td>{_fmt(m, k)}</td>" for k, _ in COLS)
        if has_oos:
            isv = (r.get("is_metrics") or {}).get("total_pnl", "-")
            oosv = (r.get("oos_metrics") or {}).get("total_pnl", "-")
            tds += f"<td>{isv}</td><td>{oosv}</td>"
        flag = ""
        if r.get("is_metrics") and r.get("oos_metrics"):
            if r["is_metrics"].get("total_pnl", 0) > 0 >= r["oos_metrics"].get("total_pnl", 0):
                flag = ' <b style="color:#b45309" title="profitable in-sample, flat/losing out-of-sample">&#9888;</b>'
        trials = r.get("n_trials")
        rows.append(
            f"<tr><td>{i}</td><td><code>{html.escape(r['id'])}</code></td>"
            f"<td>{html.escape(r['study'])}</td>"
            f"<td>{html.escape(r['dataset'].split('/')[-1])}</td>"
            f"<td>{html.escape(r.get('tag', ''))}</td>{tds}"
            f"<td>{trials if trials is not None else '-'}</td>{flag}</tr>")
    curves = [(f"{r['study']} {r.get('tag', '')} ({r['id'][-6:]})".strip(),
               [v for _, v in r.get("equity", [])]) for r in ordered]
    cards = []
    for i, r in enumerate(ordered[:5], 1):
        m = r["metrics"]
        oos = r.get("oos_metrics")
        headline = (f"OOS ${oos.get('total_pnl', 0):,.0f}" if oos
                    else f"Total ${m.get('total_pnl', 0):,.0f}")
        cards.append(
            f"<div class='card'><b>#{i} {html.escape(r['study'])}</b>"
            f"<span class='big'>{headline}</span>"
            f"<span>{html.escape(r.get('tag', ''))}</span>"
            f"<span>Win {_fmt(m, 'win_rate')}% · PF {m.get('profit_factor', 0)} · "
            f"MaxDD ${m.get('max_drawdown', 0):,.0f} · "
            f"{m.get('trades', 0)} trades</span></div>")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:1em auto;max-width:1200px;
padding:0 12px;line-height:1.45}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}}
.card{{border:1px solid #d1d5db;border-radius:10px;padding:12px;display:flex;
flex-direction:column;gap:4px;background:#f9fafb}}
.card .big{{font-size:1.4em;font-weight:700}}
.tablewrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
table{{border-collapse:collapse;font-size:13px;min-width:760px}}th,td{{border:1px solid #d1d5db;
padding:4px 8px;text-align:right;white-space:nowrap}}th{{background:#f3f4f6}}td:nth-child(-n+5){{text-align:left}}
code{{font-size:12px}}
@media (max-width:600px){{body{{font-size:15px}}h1{{font-size:1.3em}}h2{{font-size:1.1em}}}}</style></head><body>
<h1>{html.escape(title)}</h1>
<h2>Top runs</h2>
<div class="cards">{"".join(cards)}</div>
<h2>Equity (overlaid, shared scale)</h2>
{_svg(curves)}
<h2>Leaderboard (ranked by OOS total when present, else total)</h2>
<div class="tablewrap"><table><tr><th>#</th><th>run</th><th>study</th><th>dataset</th><th>tag</th>{th}</tr>
{"".join(rows)}</table></div>
<p>&#9888; = profitable in-sample but flat/losing out-of-sample (overfit smell).</p>
</body></html>
"""
