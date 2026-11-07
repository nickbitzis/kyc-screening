"""Ownership graph as SVG: the customer at the bottom, owners stacked above by layer.

Edges on a UBO path are drawn in the accent color. Owners with a screening hit get a red
outline and a "hit" tag, so the picture never relies on color alone.
"""

from __future__ import annotations

import html
from collections import defaultdict, deque

from .kyb import Registry

BOX_W, BOX_H, GAP_X, GAP_Y = 168, 44, 64, 74


def _layers(reg: Registry, target: str) -> dict[str, int]:
    depth = {target: 0}
    q = deque([target])
    while q:
        n = q.popleft()
        if n in reg.persons:
            continue
        for e in reg.owners_of(n):
            if e.owner not in depth:
                depth[e.owner] = depth[n] + 1
                q.append(e.owner)
    return depth


def _short(s: str, n: int = 24) -> str:
    return s if len(s) <= n else s[: n - 1] + "."


def ownership_svg(reg: Registry, target: str, ubo: dict, hits: set[str] | None = None) -> str:
    hits = hits or set()
    depth = _layers(reg, target)
    by_layer: dict[int, list[str]] = defaultdict(list)
    for n, d in sorted(depth.items(), key=lambda kv: (kv[1], kv[0])):
        by_layer[d].append(n)
    max_layer = max(by_layer)
    widest = max(len(v) for v in by_layer.values())
    width = max(widest * (BOX_W + GAP_X) + GAP_X, 360)
    height = (max_layer + 1) * (BOX_H + GAP_Y) + 20
    pos = {}
    for d, nodes in by_layer.items():
        row_w = len(nodes) * (BOX_W + GAP_X) - GAP_X
        x0 = (width - row_w) / 2
        y = height - 20 - BOX_H - d * (BOX_H + GAP_Y)
        for i, n in enumerate(nodes):
            pos[n] = (x0 + i * (BOX_W + GAP_X), y)

    on_path: set[tuple[str, str]] = set()
    for u in ubo["ubos"]:
        for p in u.get("paths", []):
            for a, b in zip(p["nodes"], p["nodes"][1:], strict=False):
                on_path.add((a, b))
    cyc_edges = {(c[i], c[(i + 1) % len(c)]) for c in ubo["cycles"] for i in range(len(c))}

    out = [f'<svg viewBox="0 0 {width:.0f} {height:.0f}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="Ownership graph for {html.escape(reg.name_of(target))}" class="og">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
           '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>']
    for e in reg.edges:
        if e.owner not in pos or e.owned not in pos:
            continue
        x1, y1 = pos[e.owner]
        x2, y2 = pos[e.owned]
        sx, sy = x1 + BOX_W / 2, y1 + BOX_H
        tx, ty = x2 + BOX_W / 2, y2
        hot = (e.owner, e.owned) in on_path
        cyc = (e.owner, e.owned) in cyc_edges
        cls = "edge" + (" hot" if hot else "") + (" cyc" if cyc else "")
        label_dy = 0.0
        if depth[e.owner] == depth[e.owned]:
            # same-layer edge (cross holding): side to side, bowing up when going right, down when going left
            right = x1 < x2
            sx, sy = (x1 + BOX_W, y1 + BOX_H / 2 - 8) if right else (x1, y1 + BOX_H / 2 + 8)
            tx, ty = (x2, y2 + BOX_H / 2 - 8) if right else (x2 + BOX_W, y2 + BOX_H / 2 + 8)
            bow = -26 if right else 26
            label_dy = -22 if right else 26
            path = f"M{sx:.0f},{sy:.0f} C{sx:.0f},{sy + bow:.0f} {tx:.0f},{ty + bow:.0f} {tx:.0f},{ty:.0f}"
        elif depth[e.owner] < depth[e.owned]:
            # backward edge in a longer cycle: route around the right side
            sx, sy, tx, ty = x1 + BOX_W, y1 + BOX_H / 2, x2 + BOX_W, y2 + BOX_H / 2
            path = f"M{sx:.0f},{sy:.0f} C{sx + 60:.0f},{sy:.0f} {tx + 60:.0f},{ty:.0f} {tx:.0f},{ty:.0f}"
        else:
            path = f"M{sx:.0f},{sy:.0f} C{sx:.0f},{(sy + ty) / 2:.0f} {tx:.0f},{(sy + ty) / 2:.0f} {tx:.0f},{ty:.0f}"
        out.append(f'<path d="{path}" class="{cls}" marker-end="url(#arr)"/>')
        mx, my = (sx + tx) / 2, (sy + ty) / 2 + label_dy
        out.append(f'<text x="{mx + 4:.0f}" y="{my:.0f}" class="pct">{e.pct:g}%</text>')
    ubo_ids = {u["person_id"] for u in ubo["ubos"]}
    eff = {o["person_id"]: o["effective_pct"] for o in ubo["owners"]}
    for n, (x, y) in pos.items():
        is_p = n in reg.persons
        kind = "person" if is_p else reg.companies[n].kind
        cls = ["node", "person" if is_p else "company"]
        if n == target:
            cls.append("target")
        if n in ubo_ids:
            cls.append("ubo")
        if n in hits:
            cls.append("hit")
        sub = (f"effective {eff.get(n, 0):.1f}%" if is_p else f"{kind}, {reg.companies[n].country}")
        if n in ubo_ids:
            sub += ", UBO"
        if n in hits:
            sub += ", hit"
        out.append(f'<g class="{" ".join(cls)}"><title>{html.escape(reg.name_of(n))} ({n})</title>'
                   f'<rect x="{x:.0f}" y="{y:.0f}" width="{BOX_W}" height="{BOX_H}" rx="{20 if is_p else 4}"/>'
                   f'<text x="{x + BOX_W / 2:.0f}" y="{y + 18:.0f}" text-anchor="middle" class="nm">{html.escape(_short(reg.name_of(n)))}</text>'
                   f'<text x="{x + BOX_W / 2:.0f}" y="{y + 34:.0f}" text-anchor="middle" class="sub">{html.escape(sub)}</text></g>')
    out.append("</svg>")
    return "".join(out)
