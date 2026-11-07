"""Know Your Business: a synthetic company registry and ultimate beneficial owner (UBO) analysis.

Effective ownership is computed over every simple path from a person up to the target:
multiply the percentages along a path, then sum across paths. A person at or above the
threshold (25% by default) is a UBO. Every path is returned so an analyst can check the
arithmetic. Circular holdings are detected and reported; a path stops when it would revisit
a company, so each cycle is counted once rather than as an infinite series. If nobody
crosses the threshold, the directors are reported as senior managing officials, which is
the usual fallback in beneficial ownership rules.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .data import CULTURES, NATIONALITIES, Entry, _dob, _person_name, perturb_person


@dataclass
class Person:
    id: str
    name: str
    dob: str | None = None
    nationality: str | None = None


@dataclass
class Company:
    id: str
    name: str
    country: str
    address: str
    kind: str = "company"       # company | trust | foundation | nominee
    directors: list[str] = field(default_factory=list)
    disclosed: bool = True      # False: shareholders not disclosed (opaque vehicle)


@dataclass
class Edge:
    owner: str
    owned: str
    pct: float


@dataclass
class Registry:
    persons: dict[str, Person] = field(default_factory=dict)
    companies: dict[str, Company] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # labels for tests and the demo: which registry persons are really watchlist entries
    planted_hits: dict[str, str] = field(default_factory=dict)
    demo_targets: list[str] = field(default_factory=list)

    def owners_of(self, cid: str) -> list[Edge]:
        idx = getattr(self, "_owners_idx", None)
        if idx is None or getattr(self, "_owners_n", -1) != len(self.edges):
            idx = defaultdict(list)
            for e in self.edges:
                idx[e.owned].append(e)
            self._owners_idx, self._owners_n = idx, len(self.edges)
        return idx.get(cid, [])

    def name_of(self, nid: str) -> str:
        if nid in self.persons:
            return self.persons[nid].name
        return self.companies[nid].name

    def add_person(self, p: Person) -> str:
        self.persons[p.id] = p
        return p.id

    def add_company(self, c: Company) -> str:
        self.companies[c.id] = c
        return c.id

    def own(self, owner: str, owned: str, pct: float) -> None:
        self.edges.append(Edge(owner, owned, pct))

    def find_by_name(self, name: str) -> Company | None:
        key = name.strip().lower()
        return next((c for c in self.companies.values() if c.name.lower() == key), None)


# ----------------------------------------------------------------------------- UBO math

@dataclass
class OwnershipPath:
    nodes: list[str]            # person first, target last
    pcts: list[float]           # pct for each hop, same order
    effective: float

    def explain(self, reg: Registry) -> str:
        hops = []
        for i, pct in enumerate(self.pcts):
            hops.append(f"{reg.name_of(self.nodes[i])} owns {pct:g}% of {reg.name_of(self.nodes[i + 1])}")
        calc = " x ".join(f"{p:g}%" for p in self.pcts)
        return "; ".join(hops) + f". {calc} = {self.effective:.2f}%"


def trace_ownership(reg: Registry, target: str, max_depth: int = 12) -> dict:
    """Walk ownership upward from `target`. Returns person paths, cycles, and opaque holdings."""
    person_paths: dict[str, list[OwnershipPath]] = defaultdict(list)
    cycles: set[tuple[str, ...]] = set()
    opaque: dict[str, float] = defaultdict(float)
    depth_seen = 0
    jurisdictions = {reg.companies[target].country}

    def walk(node: str, chain: list[str], pcts: list[float], factor: float) -> None:
        nonlocal depth_seen
        depth_seen = max(depth_seen, len(chain) - 1)
        comp = reg.companies[node]
        jurisdictions.add(comp.country)
        owners = reg.owners_of(node)
        if not comp.disclosed or (not owners and comp.kind in ("trust", "foundation", "nominee")):
            if node != target:
                opaque[node] += factor * 100
            return
        for e in owners:
            f = factor * e.pct / 100
            if e.owner in chain:
                i = chain.index(e.owner)
                cyc = chain[i:]
                k = cyc.index(min(cyc))
                cycles.add(tuple(cyc[k:] + cyc[:k]))
                continue
            if e.owner in reg.persons:
                nodes = [e.owner, *reversed(chain)]
                person_paths[e.owner].append(OwnershipPath(nodes, [e.pct, *reversed(pcts)], round(f * 100, 4)))
            elif len(chain) < max_depth:
                walk(e.owner, [*chain, e.owner], [*pcts, e.pct], f)

    walk(target, [target], [], 1.0)
    return {"paths": person_paths, "cycles": sorted(cycles), "opaque": dict(opaque), "max_depth": depth_seen,
            "jurisdictions": sorted(jurisdictions)}


def compute_ubos(reg: Registry, target: str, threshold: float = 25.0) -> dict:
    tr = trace_ownership(reg, target)
    people = []
    for pid, paths in tr["paths"].items():
        eff = round(sum(p.effective for p in paths), 4)
        people.append({
            "person_id": pid, "name": reg.persons[pid].name, "effective_pct": eff,
            "is_ubo": eff >= threshold - 1e-9, "basis": "ownership",
            "paths": [{"nodes": p.nodes, "names": [reg.name_of(n) for n in p.nodes], "pcts": p.pcts,
                       "effective_pct": p.effective, "explanation": p.explain(reg)} for p in paths],
        })
    people.sort(key=lambda x: -x["effective_pct"])
    ubos = [p for p in people if p["is_ubo"]]
    if not ubos:
        for d in reg.companies[target].directors:
            ubos.append({"person_id": d, "name": reg.persons[d].name, "effective_pct": 0.0, "is_ubo": True,
                         "basis": "senior_managing_official", "paths": [],
                         "explanation": f"{reg.persons[d].name}: no natural person reaches the ownership threshold, "
                                        "so the director is recorded as senior managing official."})
    for u in ubos:
        if u["basis"] == "ownership":
            parts = " + ".join(f"{p['effective_pct']:.2f}%" for p in u["paths"])
            u["explanation"] = (f"{u['name']}: {len(u['paths'])} path(s): {parts} = {u['effective_pct']:.2f}%, "
                                f"at or above {threshold:g}%.")
    return {"target": target, "threshold": threshold, "ubos": ubos, "owners": people, "cycles": tr["cycles"],
            "cycle_names": [[reg.name_of(n) for n in c] for c in tr["cycles"]],
            "opaque": [{"company_id": k, "name": reg.name_of(k), "effective_pct": round(v, 2)} for k, v in tr["opaque"].items()],
            "max_depth": tr["max_depth"], "jurisdictions": tr["jurisdictions"]}


# ----------------------------------------------------------------------------- red flags

NOMINEE_DIRECTOR_MIN = 5
MASS_ADDRESS_MIN = 8


def registry_stats(reg: Registry) -> dict:
    directorships = Counter(d for c in reg.companies.values() for d in c.directors)
    addresses = Counter(c.address for c in reg.companies.values())
    return {"directorships": directorships, "addresses": addresses}


def red_flags(reg: Registry, target: str, ubo: dict, stats: dict | None = None) -> list[dict]:
    stats = stats or registry_stats(reg)
    comp = reg.companies[target]
    flags = []
    for d in comp.directors:
        n = stats["directorships"][d]
        if n >= NOMINEE_DIRECTOR_MIN:
            flags.append({"flag": "nominee_director", "detail": f"{reg.persons[d].name} is a director of {n} companies"})
    n_addr = stats["addresses"][comp.address]
    if n_addr >= MASS_ADDRESS_MIN:
        flags.append({"flag": "mass_registration_address", "detail": f"{n_addr} companies registered at {comp.address}"})
    if ubo["cycles"]:
        flags.append({"flag": "circular_ownership", "detail": "; ".join(" -> ".join(c) for c in ubo["cycle_names"])})
    for o in ubo["opaque"]:
        if o["effective_pct"] >= 10:
            flags.append({"flag": "opaque_holder", "detail": f"{o['effective_pct']:.1f}% traces to {o['name']}, which discloses no owners"})
    seen_nominee = set()
    for e in reg.owners_of(target):
        stack = [e.owner]
        while stack:
            n = stack.pop()
            if n in seen_nominee or n in reg.persons:
                continue
            seen_nominee.add(n)
            c = reg.companies[n]
            if c.kind == "nominee" or "nominee" in c.name.lower():
                flags.append({"flag": "nominee_shareholder", "detail": f"{c.name} holds shares as nominee"})
            stack.extend(x.owner for x in reg.owners_of(n))
    # Threshold avoidance: relatives each just under the threshold, together above it.
    near = [o for o in ubo["owners"] if 15 <= o["effective_pct"] < ubo["threshold"]]
    by_family = defaultdict(list)
    for o in near:
        by_family[o["name"].split()[-1].lower()].append(o)
    for fam, group in by_family.items():
        total = sum(o["effective_pct"] for o in group)
        if len(group) >= 2 and total >= ubo["threshold"]:
            flags.append({"flag": "threshold_splitting",
                          "detail": f"{len(group)} holders named {fam.title()} each below {ubo['threshold']:g}%, "
                                    f"{total:.1f}% together"})
    if ubo["max_depth"] >= 3:
        flags.append({"flag": "layered_structure", "detail": f"{ubo['max_depth']} layers of companies above the customer"})
    if len(ubo["jurisdictions"]) >= 3:
        flags.append({"flag": "multi_jurisdiction", "detail": ", ".join(ubo["jurisdictions"])})
    return flags


# ----------------------------------------------------------------------------- synthetic registry

ADDRESSES = ["Limassol", "Dublin", "Dubai", "Zug", "Singapore", "Panama City", "Road Town", "Amsterdam",
             "Manchester", "Wilmington"]
STREETS = ["Harbour Row", "Quay Street", "Market Lane", "Canal Street", "Linden Plein", "Old Mill Yard",
           "Riverside Avenue", "Station Road", "Tower Street", "Mill Lane", "High Street", "Park Avenue"]
MASS_ADDRESS = "Office 1, 77 Registered Agents House, Road Town"
_CO_A = ["Maris", "Tellar", "Orrin", "Vesta", "Quillon", "Harrowgate", "Kestrel Bay", "Brightwater", "Calder",
         "Dunmore", "Elmstead", "Fenwick", "Glenrock", "Hollis", "Ivory Gate", "Juno", "Kingsmere", "Larchmont",
         "Monarch Vale", "Netherby", "Oakhurst", "Pellham", "Quarry Hill", "Rosmar", "Saltash", "Thistledown"]
_CO_B = ["Holdings", "Investments", "Capital", "Partners", "Trading", "Ventures", "Group", "Logistics", "Equity"]
_CO_SUFFIX = {"GB": "Ltd", "CY": "Ltd", "NL": "B.V.", "CH": "AG", "AE": "FZE", "SG": "Pte Ltd", "PA": "S.A.",
              "VG": "Ltd", "IE": "Limited", "US": "LLC", "DE": "GmbH"}


def _company_name(rng: random.Random, country: str) -> str:
    return f"{rng.choice(_CO_A)} {rng.choice(_CO_B)} {_CO_SUFFIX[country]}"


def build_registry(watchlist: list[Entry], n_random: int = 160, seed: int = 21) -> Registry:
    rng = random.Random(seed)
    reg = Registry()
    persons_wl = [e for e in watchlist if e.kind == "person" and e.dob and len(e.dob) == 10]
    counter = {"P": 0, "B": 0}

    def person(name=None, culture=None, dob=None, nat=None) -> str:
        counter["P"] += 1
        culture = culture or rng.choice(CULTURES)
        if name is None:
            name, _ = _person_name(rng, culture)
        return reg.add_person(Person(f"P-{counter['P']:04d}", name, dob or _dob(rng, 1950, 1995),
                                     nat or rng.choice(NATIONALITIES[culture])))

    def watch_person(e: Entry) -> str:
        nm, _ = perturb_person(rng, e.name, e.culture, 1, honorifics=False)
        pid = person(nm, e.culture, e.dob, e.nationality)
        reg.planted_hits[pid] = e.id
        return pid

    def company(name, country, kind="company", directors=(), address=None, disclosed=True) -> str:
        counter["B"] += 1
        addr = address or f"{rng.randint(1, 240)} {rng.choice(STREETS)}, {rng.choice(ADDRESSES)}"
        return reg.add_company(Company(f"B-{counter['B']:04d}", name, country, addr, kind, list(directors), disclosed))

    nominee_dir = person("Graham Pellew Ashdown", "latin", "1961-02-11", "GB")

    # B-0001: simple, two founders.
    a, b = person("Laura Whitcombe", "latin"), person("Daniel Okoro", "latin")
    t1 = company("Harrowgate Freight Ltd", "GB", directors=[a])
    reg.own(a, t1, 60)
    reg.own(b, t1, 40)

    # B-0002: three-layer chain; the indirect owner is a (perturbed) watchlist person.
    wl_person = watch_person(persons_wl[3])
    direct = person("Sophie Lindqvist", "latin")
    t2 = company("Kestrel Bay Commodities FZE", "AE", directors=[direct])
    h2 = company("Tellar Investments B.V.", "NL", directors=[nominee_dir])
    h1 = company("Maris Holdings Ltd", "CY", directors=[nominee_dir])
    reg.own(wl_person, h1, 70)
    reg.own(h1, h2, 80)
    reg.own(h2, t2, 60)
    reg.own(direct, t2, 40)
    reg.own(person(None, "latin"), h1, 30)
    reg.own(person(None, "latin"), h2, 20)

    # B-0003: circular holding between two parents.
    p3, p4 = person("Marco Castellano", "latin"), person("Irina Volkova", "cyrillic")
    t3 = company("Quillon Trading S.A.", "PA", directors=[p3])
    c1 = company("Orrin Holdings Ltd", "CY")
    c2 = company("Vesta Partners AG", "CH")
    reg.own(c1, t3, 50)
    reg.own(c2, t3, 50)
    reg.own(c1, c2, 30)
    reg.own(c2, c1, 40)
    reg.own(p3, c1, 60)
    reg.own(p4, c2, 70)

    # B-0004: family split just under 25%, plus a nominee holder, at a mass registration address.
    fam = [person(f"{g} Rahal", "arabic") for g in ("Karim", "Nabil", "Salim", "Rami")]
    nom = company("Calder Nominees Ltd", "VG", kind="nominee", address=MASS_ADDRESS)
    t4 = company("Brightwater Ventures Ltd", "VG", directors=[nominee_dir], address=MASS_ADDRESS)
    for pid in fam:
        reg.own(pid, t4, 24)
    reg.own(nom, t4, 4)

    # B-0005: majority held by a foundation that discloses no owners.
    p5 = person("Andrés Quintero Salazar", "hispanic")
    found = company("Sable Crest Foundation", "PA", kind="foundation", disclosed=False)
    t5 = company("Dunmore Logistics LLC", "US", directors=[p5])
    reg.own(found, t5, 60)
    reg.own(p5, t5, 15)
    reg.own(person(None, "hispanic"), t5, 25)

    reg.demo_targets = [t1, t2, t3, t4, t5]

    # Random population of structures for volume and for registry-wide patterns.
    for _ in range(n_random):
        country = rng.choice(list(_CO_SUFFIX))
        addr = MASS_ADDRESS if rng.random() < 0.06 else None
        dirs = [nominee_dir] if rng.random() < 0.04 else [person()]
        target = company(_company_name(rng, country), country, directors=dirs, address=addr)
        layers = rng.choices([0, 1, 2, 3], weights=[45, 30, 17, 8])[0]
        frontier = [(target, 100.0)]
        for _layer in range(layers):
            nxt = []
            for node, _w in frontier:
                if rng.random() < 0.6:
                    hc = rng.choice(list(_CO_SUFFIX))
                    h = company(_company_name(rng, hc), hc, directors=[person()])
                    pct = rng.choice([51, 60, 75, 100, 35])
                    reg.own(h, node, pct)
                    nxt.append((h, pct))
            frontier = nxt or frontier
        for node in {n for n, _ in frontier} | {target}:
            already = sum(e.pct for e in reg.owners_of(node))
            left = 100 - already
            while left > 0.5:
                pct = min(left, rng.choice([10, 20, 25, 30, 40, 50]))
                pid = watch_person(rng.choice(persons_wl)) if rng.random() < 0.03 else person()
                reg.own(pid, node, pct)
                left -= pct
    return reg


def registry_to_dict(reg: Registry) -> dict:
    return {"persons": [asdict(p) for p in reg.persons.values()],
            "companies": [asdict(c) for c in reg.companies.values()],
            "edges": [asdict(e) for e in reg.edges]}
