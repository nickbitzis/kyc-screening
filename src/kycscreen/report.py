"""Static HTML report with inline SVG charts. No JavaScript, no external assets."""

from __future__ import annotations

import html

W, H = 640, 340
PAD_L, PAD_R, PAD_T, PAD_B = 56, 20, 16, 44

STYLE = """
:root { --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --grid:#e4e3df; --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --grid:#33322f; --s1:#3987e5; --s2:#d95926; --s3:#199e70; } }
:root[data-theme="dark"] { --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --grid:#33322f; --s1:#3987e5; --s2:#d95926; --s3:#199e70; }
body { background:var(--surface); color:var(--ink); font:15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
  margin:0 auto; padding:24px 16px; max-width:760px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:28px 0 6px; }
p.sub { color:var(--ink2); margin:0 0 16px; }
svg { width:100%; height:auto; display:block; }
svg text { fill:var(--ink2); font-size:12px; }
.grid line { stroke:var(--grid); }
.legend { display:flex; gap:16px; flex-wrap:wrap; color:var(--ink2); font-size:13px; margin:4px 0 8px; }
.legend span::before { content:""; display:inline-block; width:12px; height:3px; margin-right:6px; vertical-align:middle;
  background:var(--c); border-radius:2px; }
table { border-collapse:collapse; width:100%; font-size:13px; margin-top:8px; }
th, td { text-align:right; padding:4px 8px; border-bottom:1px solid var(--grid); }
th:first-child, td:first-child { text-align:left; }
.wrap { overflow-x:auto; }
"""


def _sx(x: float, lo: float, hi: float) -> float:
    return PAD_L + (x - lo) / (hi - lo) * (W - PAD_L - PAD_R)


def _sy(y: float, lo: float, hi: float) -> float:
    return H - PAD_B - (y - lo) / (hi - lo) * (H - PAD_T - PAD_B)


def _axes(xlo, xhi, ylo, yhi, xticks, yticks, xlabel, ylabel, yfmt) -> list[str]:
    out = ['<g class="grid">']
    for t in yticks:
        y = _sy(t, ylo, yhi)
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
    out.append("</g>")
    for t in yticks:
        out.append(f'<text x="{PAD_L - 8}" y="{_sy(t, ylo, yhi) + 4:.1f}" text-anchor="end">{yfmt(t)}</text>')
    for t in xticks:
        out.append(f'<text x="{_sx(t, xlo, xhi):.1f}" y="{H - PAD_B + 18}" text-anchor="middle">{t:g}</text>')
    out.append(f'<text x="{(PAD_L + W - PAD_R) / 2}" y="{H - 6}" text-anchor="middle">{xlabel}</text>')
    out.append(f'<text x="14" y="{(PAD_T + H - PAD_B) / 2}" text-anchor="middle" transform="rotate(-90 14 {(PAD_T + H - PAD_B) / 2})">{ylabel}</text>')
    return out


def _line(points, color, xr, yr, label_key) -> str:
    pts = " ".join(f"{_sx(x, *xr):.1f},{_sy(y, *yr):.1f}" for x, y, _ in points)
    dots = "".join(
        f'<circle cx="{_sx(x, *xr):.1f}" cy="{_sy(y, *yr):.1f}" r="6" fill="transparent"><title>{html.escape(t)}</title></circle>'
        for x, y, t in points)
    return f'<polyline points="{pts}" fill="none" stroke="var({color})" stroke-width="2" stroke-linejoin="round"/>{dots}'


def tradeoff_svg(res: dict) -> str:
    """Recall against analyst workload (alerted customers per 1,000)."""
    xmax = 200.0
    yr = (0.0, 1.0)
    xr = (0.0, xmax)

    def pts(curve):
        return [(min(p["alerts_per_1000"], xmax), p["recall"],
                 f"threshold {p['threshold']:g}: recall {p['recall']:.1%}, {p['alerts_per_1000']:.1f} alerts per 1,000")
                for p in curve if p["alerts_per_1000"] <= xmax]

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Recall versus alerts per 1,000 customers">']
    out += _axes(*xr, *yr, [0, 25, 50, 75, 100, 125, 150, 175, 200], [0, 0.25, 0.5, 0.75, 0.9, 1.0],
                 "alerted customers per 1,000 screened", "recall", lambda t: f"{t:.0%}")
    out.append(_line(pts(res["levenshtein"]["curve"]), "--s2", xr, yr, "lev"))
    out.append(_line(pts(res["engine"]["curve"]), "--s1", xr, yr, "eng"))
    ex = res["exact"]["point"]
    out.append(f'<circle cx="{_sx(ex["alerts_per_1000"], *xr):.1f}" cy="{_sy(ex["recall"], *yr):.1f}" r="5" '
               f'fill="var(--s3)" stroke="var(--surface)" stroke-width="2"><title>exact match: recall {ex["recall"]:.1%}, '
               f'{ex["alerts_per_1000"]:.1f} alerts per 1,000</title></circle>')
    rec = res["engine"]["recommended"]
    if rec:
        cx, cy = _sx(rec["alerts_per_1000"], *xr), _sy(rec["recall"], *yr)
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/>')
        out.append(f'<text x="{cx + 8:.1f}" y="{cy + 18:.1f}" style="fill:var(--ink)">threshold {rec["threshold"]:g}: '
                   f'{rec["recall"]:.1%} recall, {rec["alerts_per_1000"]:.0f} per 1,000</text>')
    out.append("</svg>")
    return "".join(out)


def threshold_svg(res: dict) -> str:
    """Engine recall and precision across thresholds."""
    curve = res["engine"]["curve"]
    xr, yr = (40.0, 100.0), (0.0, 1.0)
    rec = [(p["threshold"], p["recall"], f"threshold {p['threshold']:g}: recall {p['recall']:.1%}") for p in curve]
    prec = [(p["threshold"], p["precision"], f"threshold {p['threshold']:g}: precision {p['precision']:.1%}") for p in curve]
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Recall and precision by threshold">']
    out += _axes(*xr, *yr, [40, 50, 60, 70, 80, 90, 100], [0, 0.25, 0.5, 0.75, 1.0], "alert threshold (score)",
                 "rate", lambda t: f"{t:.0%}")
    out.append(_line(rec, "--s1", xr, yr, "r"))
    out.append(_line(prec, "--s2", xr, yr, "p"))
    r = res["engine"]["recommended"]
    if r:
        x = _sx(r["threshold"], *xr)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T}" y2="{H - PAD_B}" stroke="var(--ink2)" stroke-dasharray="4 4"/>')
    out.append("</svg>")
    return "".join(out)


def _row(name, p) -> str:
    return (f"<tr><td>{name}</td><td>{p['threshold']:g}</td><td>{p['recall']:.1%}</td><td>{p['precision']:.1%}</td>"
            f"<td>{p['alerts_per_1000']:.1f}</td><td>{p['alerted_customers']:,}</td></tr>")


def summary_rows(res: dict) -> list[tuple[str, dict]]:
    rows = [("Exact match (lowercased)", res["exact"]["point"])]
    lev = res["levenshtein"]
    if lev.get("recommended"):
        rows.append(("Levenshtein at 99% recall", lev["recommended"]))
    rows.append((f"Levenshtein, best recall (threshold {lev['max_recall']['threshold']:g})", lev["max_recall"]))
    lev_match = min(lev["curve"], key=lambda p: abs(p["alerts_per_1000"] - res["engine"]["recommended"]["alerts_per_1000"]))
    rows.append(("Levenshtein at the same workload", lev_match))
    rows.append(("This engine, recommended", res["engine"]["recommended"]))
    return rows


def render_report(res: dict) -> str:
    rec = res["engine"]["recommended"]
    rows = "".join(_row(n, p) for n, p in summary_rows(res))
    pert = "".join(f"<tr><td>{p['perturbation']}</td><td>{p['n']}</td><td>{p['recall']:.1%}</td></tr>"
                   for p in res["by_perturbation"])
    eng = res["engine"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Screening Tradeoff Report</title>
<style>{STYLE}</style></head><body>
<h1>Screening tradeoff: missed hits versus analyst workload</h1>
<p class="sub">{res['customers']:,} synthetic customers, {res['positives']} true matches, {res['namesakes']} namesakes
(same name, different person), {res['watchlist_entries']:,} fictional watchlist entries. Target recall {res['target_recall']:.0%}.</p>
<h2>Recall against workload</h2>
<div class="legend"><span style="--c:var(--s1)">this engine</span><span style="--c:var(--s2)">Levenshtein baseline</span>
<span style="--c:var(--s3)">exact match</span></div>
{tradeoff_svg(res)}
<h2>Recall and precision by threshold</h2>
<div class="legend"><span style="--c:var(--s1)">recall</span><span style="--c:var(--s2)">precision</span></div>
{threshold_svg(res)}
<h2>Summary</h2>
<div class="wrap"><table><tr><th>Method</th><th>Threshold</th><th>Recall</th><th>Precision</th><th>Alerts per 1,000</th><th>Alerted customers</th></tr>
{rows}</table></div>
<p>Recommended threshold <b>{rec['threshold']:g}</b>: {rec['recall']:.1%} recall, {rec['alerts_per_1000']:.1f} alerted customers per 1,000,
which is {rec['alerted_customers']:,} cases for this population. Blocking recall {eng['blocking_recall']:.2%},
{eng['avg_candidates']} candidates per customer out of {eng['index_names']:,} indexed names,
{eng['names_per_second']:,.0f} names per second in pure Python.</p>
<h2>Recall by perturbation at the recommended threshold</h2>
<div class="wrap"><table><tr><th>Perturbation</th><th>Customers</th><th>Recall</th></tr>{pert}</table></div>
</body></html>"""
