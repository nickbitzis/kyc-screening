"""Application service: wires the screener, KYB analysis, scorecard, and review queue together.

The API and the CLI both call this layer, so the rules live in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evaluate import Dataset, build_dataset
from .kyb import Registry, build_registry, compute_ubos, red_flags, registry_stats
from .normalize import normalize_name
from .review import ReviewStore
from .risk import RiskScorecard, load_yaml
from .screening import Screener


@dataclass
class Settings:
    threshold: float
    target_recall: float
    candidate_floor: float

    @classmethod
    def load(cls) -> Settings:
        cfg = load_yaml("screening.yaml")
        return cls(float(cfg["threshold"]), float(cfg["target_recall"]), float(cfg["candidate_floor"]))


class KYCService:
    def __init__(self, dataset: Dataset | None = None, registry: Registry | None = None,
                 store: ReviewStore | None = None, settings: Settings | None = None,
                 scorecard: RiskScorecard | None = None) -> None:
        self.ds = dataset or build_dataset()
        self.screener = Screener(self.ds.watchlist, self.ds.freq)
        self.registry = registry or build_registry(self.ds.watchlist)
        self.stats = registry_stats(self.registry)
        self.store = store or ReviewStore()
        self.settings = settings or Settings.load()
        self.scorecard = scorecard or RiskScorecard()

    # ------------------------------------------------------------------ persons
    def screen_name(self, name: str, kind: str = "person", dob: str | None = None, nationality: str | None = None,
                    limit: int = 10) -> dict:
        cands = self.screener.screen(name, kind, dob, nationality, min_score=self.settings.candidate_floor, limit=limit)
        t = self.settings.threshold
        rows = [c.to_dict(self.screener.entries[c.entry_id]) for c in cands]
        for r in rows:
            r["alert"] = r["score"] >= t
        return {"query": name, "normalized": normalize_name(name, entity=kind == "entity"), "threshold": t,
                "candidates": rows, "hits": [r for r in rows if r["alert"]]}

    def _open_cases(self, subject_ref: str, subject_name: str, hits: list[dict]) -> list[int]:
        return [self.store.open_case(subject_ref, subject_name, h["entry_id"], h["list_type"], h["score"]) for h in hits]

    @staticmethod
    def _screening_factors(hits: list[dict]) -> list[str]:
        out = []
        if any(h["list_type"] == "sanctions" for h in hits):
            out.append("sanctions_possible_match")
        if any(h["list_type"] == "pep" for h in hits):
            out.append("pep_hit")
        return out

    def screen_person(self, name: str, dob: str | None = None, nationality: str | None = None,
                      customer_id: str | None = None, product: str | None = None) -> dict:
        res = self.screen_name(name, "person", dob, nationality)
        ref = customer_id or f"adhoc:{name}"
        res["case_ids"] = self._open_cases(ref, name, res["hits"])
        res["risk"] = self.scorecard.rate("individual", [nationality] if nationality else [], product,
                                          self._screening_factors(res["hits"]))
        self.store.audit("api", "screen_person", subject_ref=ref, hits=[h["entry_id"] for h in res["hits"]],
                         rating=res["risk"]["rating"])
        return res

    # ------------------------------------------------------------------ businesses
    def screen_business(self, registry_id: str | None = None, name: str | None = None,
                        product: str | None = "business_account", persist: bool = True) -> dict:
        """Full KYB screen. With persist=False no cases or audit rows are written (used to draw the graph)."""
        reg = self.registry
        comp = reg.companies.get(registry_id) if registry_id else reg.find_by_name(name or "")
        if comp is None:
            raise KeyError(registry_id or name)
        ubo = compute_ubos(reg, comp.id)
        flags = red_flags(reg, comp.id, ubo, self.stats)
        biz = self.screen_name(comp.name, "entity", None, comp.country, limit=5)
        people = {u["person_id"]: "ubo" for u in ubo["ubos"]}
        for d in comp.directors:
            people[d] = "ubo+director" if d in people else "director"
        person_results = []
        all_hits = list(biz["hits"])
        hit_nodes: set[str] = set()
        cases = self._open_cases(comp.id, comp.name, biz["hits"]) if persist else []
        for pid, role in people.items():
            p = reg.persons[pid]
            r = self.screen_name(p.name, "person", p.dob, p.nationality, limit=3)
            if r["hits"]:
                hit_nodes.add(pid)
                all_hits += r["hits"]
                if persist:
                    cases += self._open_cases(pid, p.name, r["hits"])
            person_results.append({"person_id": pid, "name": p.name, "role": role, "dob": p.dob,
                                   "nationality": p.nationality, "hits": r["hits"],
                                   "top_candidate": r["candidates"][0] if r["candidates"] else None})
        countries = [comp.country, *ubo["jurisdictions"], *(reg.persons[p].nationality for p in people)]
        risk = self.scorecard.rate(comp.kind if comp.kind != "nominee" else "company", countries, product,
                                   self._screening_factors(all_hits), [f["flag"] for f in flags])
        explanation = [u["explanation"] for u in ubo["ubos"]]
        explanation += [f"{f['flag'].replace('_', ' ')}: {f['detail']}" for f in flags]
        explanation += [f"{pr['name']} ({pr['role']}) matched {h['primary_name']} ({h['list_type']}, score {h['score']:.1f})"
                        for pr in person_results for h in pr["hits"]]
        explanation += [f"Business name matched {h['primary_name']} (score {h['score']:.1f})" for h in biz["hits"]]
        if persist:
            self.store.audit("api", "screen_business", subject_ref=comp.id, hits=[h["entry_id"] for h in all_hits],
                             rating=risk["rating"])
        return {"company": {"id": comp.id, "name": comp.name, "country": comp.country, "kind": comp.kind,
                            "address": comp.address, "directors": comp.directors},
                "ubos": ubo["ubos"], "owners": ubo["owners"], "cycles": ubo["cycle_names"], "opaque": ubo["opaque"],
                "max_depth": ubo["max_depth"], "jurisdictions": ubo["jurisdictions"], "flags": flags,
                "business_hits": biz["hits"], "people": person_results, "hits": all_hits, "case_ids": cases,
                "risk": risk, "explanation": explanation, "_hit_nodes": sorted(hit_nodes), "_ubo": ubo}
