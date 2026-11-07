"""Delta rescreening for watchlist updates.

When a list publisher adds, amends, or removes entries, rescreening the whole customer base
is the safe but slow answer. Delta screening only re-checks customers whose blocking tokens
are similar to a token of a changed entry. Because the blocking similarity test is
symmetric, that set contains every customer the full rescreen could alert on for those
entries, so the result is identical to a full rescreen. Alerts on unchanged entries carry
over untouched (the scorer's name frequencies come from the customer base, not the list,
so a list update does not move existing scores).
"""

from __future__ import annotations

import time
from collections import defaultdict

from .blocking import TokenBlocker, blocking_tokens
from .data import Customer, Entry
from .normalize import normalize_name
from .scoring import NameFrequency
from .screening import MIN_BLOCK_WEIGHT, Screener

Alerts = dict[tuple[str, str], float]   # (customer id, entry id) -> score


class CustomerIndex:
    """Inverted index from customer blocking tokens to customer ids.

    `affected_by` runs the screener's blocking rule in reverse: for each changed watchlist
    name it finds customers whose similar tokens carry at least MIN_BLOCK_WEIGHT of the
    customer's token weight. That is exactly the set of customers for which the forward
    screener would generate that name as a candidate.
    """

    def __init__(self, customers: list[Customer], freq: NameFrequency) -> None:
        self.customers = {c.id: c for c in customers}
        self.freq = freq
        self.postings: dict[str, set[str]] = defaultdict(set)
        self.need: dict[str, float] = {}
        self.blocker = TokenBlocker()
        for c in customers:
            toks = normalize_name(c.name, entity=c.kind == "entity")
            self.need[c.id] = MIN_BLOCK_WEIGHT * sum(freq.weight(t) for t in toks)
            for t in set(blocking_tokens(toks)):
                self.postings[t].add(c.id)
                self.blocker.add(t)

    def affected_by(self, entries: list[Entry]) -> set[str]:
        out: set[str] = set()
        for e in entries:
            for nm in e.names():
                rec_tokens = blocking_tokens(normalize_name(nm, entity=e.kind == "entity"))
                cust_tokens: set[str] = set()
                for v in rec_tokens:
                    cust_tokens |= self.blocker.similar(v)
                hits: dict[str, float] = defaultdict(float)
                for t in cust_tokens:
                    w = self.freq.weight(t)
                    for cid in self.postings[t]:
                        hits[cid] += w
                out.update(cid for cid, w in hits.items() if w >= self.need[cid] and self.need[cid] > 0)
        return out


def screen_all(customers: list[Customer], screener: Screener, threshold: float,
               only: set[str] | None = None) -> Alerts:
    alerts: Alerts = {}
    for c in customers:
        if only is not None and c.id not in only:
            continue
        for cand in screener.screen(c.name, c.kind, c.dob, c.nationality, min_score=threshold, explain=False):
            alerts[(c.id, cand.entry_id)] = cand.score
    return alerts


def full_rescreen(customers: list[Customer], watchlist: list[Entry], freq: NameFrequency, threshold: float) -> tuple[Alerts, float]:
    t0 = time.perf_counter()
    alerts = screen_all(customers, Screener(watchlist, freq), threshold)
    return alerts, time.perf_counter() - t0


def delta_rescreen(prev: Alerts, customers: list[Customer], new_watchlist: list[Entry], change: dict,
                   freq: NameFrequency, threshold: float, cindex: CustomerIndex | None = None) -> tuple[Alerts, dict]:
    t0 = time.perf_counter()
    cindex = cindex or CustomerIndex(customers, freq)
    t_index = time.perf_counter() - t0
    t1 = time.perf_counter()
    by_id = {e.id: e for e in new_watchlist}
    gone = set(change["removed"]) | set(change["modified"])
    changed = [by_id[i] for i in [*change["added"], *change["modified"]] if i in by_id]
    alerts = {k: v for k, v in prev.items() if k[1] not in gone}
    affected = cindex.affected_by(changed)
    mini = Screener(changed, freq)
    targets = [cindex.customers[cid] for cid in sorted(affected)]
    new = screen_all(targets, mini, threshold)
    alerts.update(new)
    secs = time.perf_counter() - t1
    dropped = sorted({k for k in prev if k[1] in gone} - set(new))
    return alerts, {
        "changed_entries": len(changed), "removed_entries": len(change["removed"]),
        "customers_rechecked": len(affected), "customers_total": len(customers),
        "new_alerts": sorted(set(new) - set(prev)), "cleared_alerts": dropped,
        "seconds": round(secs, 3), "index_seconds": round(t_index, 3),
    }
