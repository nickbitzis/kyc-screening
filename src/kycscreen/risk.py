"""Transparent risk scorecard, configured in YAML.

Every point added is listed with the reason, so the rating can be explained to a customer,
an auditor, or an examiner line by line.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent / "config"


def load_yaml(name_or_path: str | Path) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = CONFIG_DIR / str(name_or_path)
    return yaml.safe_load(p.read_text())


class RiskScorecard:
    def __init__(self, config: dict | None = None) -> None:
        self.cfg = config or load_yaml("risk.yaml")

    def rate(self, customer_type: str, countries: list[str], product: str | None = None,
             screening: list[str] | None = None, ownership_flags: list[str] | None = None) -> dict:
        cfg = self.cfg
        reasons: list[dict] = []
        triggers: set[str] = set()

        def add(points: float, factor: str, why: str) -> None:
            if points:
                reasons.append({"factor": factor, "points": points, "reason": why})

        add(cfg["customer_type"].get(customer_type, 0), "customer_type", f"customer type {customer_type}")
        geo = cfg["geography"]
        seen = set()
        for c in countries:
            if not c or c in seen:
                continue
            seen.add(c)
            for level in ("high", "elevated"):
                if c.upper() in geo[level]["countries"]:
                    add(geo[level]["points"], "geography", f"{c} is on the {level} risk country list")
                    triggers.add(f"geography_{level}")
                    break
        if product:
            add(cfg["product"].get(product, 0), "product", f"product {product}")
        for s in sorted(set(screening or [])):
            add(cfg["screening"].get(s, 0), "screening", s.replace("_", " "))
            triggers.add(s)
        for f in sorted(set(ownership_flags or [])):
            add(cfg["ownership"].get(f, 0), "ownership", f.replace("_", " "))
        total = sum(r["points"] for r in reasons)
        bands = cfg["bands"]
        rating = "high" if total >= bands["high"] else "medium" if total >= bands["medium"] else "low"
        forced = sorted(triggers & set(cfg.get("overrides", [])))
        if forced:
            rating = "high"
        return {"rating": rating, "points": total, "reasons": reasons, "overrides": forced,
                "bands": bands}
