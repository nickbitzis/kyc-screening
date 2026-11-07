"""Evaluation: recall, precision, and analyst workload across thresholds, against two baselines.

Workload is measured as alerted customers per 1,000 screened. Each alerted customer is a
case an analyst has to open, so this is the number that drives headcount.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .data import Customer, Entry, build_customers, build_watchlist
from .normalize import fold, normalize_name
from .scoring import NameFrequency
from .screening import Screener
from .similarity import levenshtein_ratio

THRESHOLDS = [round(40 + 0.5 * i, 1) for i in range(121)]  # 40.0 .. 100.0
_SPACE = re.compile(r"\s+")


@dataclass
class Dataset:
    watchlist: list[Entry]
    customers: list[Customer]
    freq: NameFrequency

    @property
    def positives(self) -> list[Customer]:
        return [c for c in self.customers if c.true_entry]


def build_dataset(n_customers: int = 20_000, scale: float = 1.0, seed: int = 7) -> Dataset:
    wl = build_watchlist(n_persons=int(2200 * scale), n_entities=int(800 * scale), seed=seed)
    cs = build_customers(wl, n=n_customers, n_true_persons=int(320 * scale), n_true_entities=int(80 * scale),
                         n_namesakes=int(160 * scale), seed=seed + 4)
    return Dataset(wl, cs, customer_frequency(cs))


def customer_frequency(customers: list[Customer]) -> NameFrequency:
    return NameFrequency(normalize_name(c.name, entity=c.kind == "entity") for c in customers)


@dataclass
class Run:
    """Scores for every (customer, entry) pair that came out of blocking with score >= floor."""
    name: str
    scores: dict[str, dict[str, float]]          # customer id -> entry id -> score
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


def run_engine(ds: Dataset, screener: Screener | None = None, floor: float = 30.0) -> Run:
    screener = screener or Screener(ds.watchlist, ds.freq)
    t0 = time.perf_counter()
    scores: dict[str, dict[str, float]] = {}
    n_cand = 0
    blocked_true = 0
    for c in ds.customers:
        toks = normalize_name(c.name, entity=c.kind == "entity")
        recs = screener.candidate_records(toks)
        n_cand += len(recs)
        if c.true_entry and any(screener.records[i].entry_id == c.true_entry for i in recs):
            blocked_true += 1
        cands = screener.screen(c.name, c.kind, c.dob, c.nationality, min_score=floor, explain=False)
        scores[c.id] = {x.entry_id: x.score for x in cands}
    secs = time.perf_counter() - t0
    n_pos = len(ds.positives)
    return Run("engine", scores, secs, {
        "names_per_second": round(len(ds.customers) / secs, 1),
        "blocking_recall": round(blocked_true / n_pos, 4) if n_pos else 1.0,
        "avg_candidates": round(n_cand / len(ds.customers), 1),
        "index_names": screener.size,
        "reduction_ratio": round(1 - n_cand / (len(ds.customers) * screener.size), 5),
    })


def _plain(s: str) -> str:
    return _SPACE.sub(" ", s.lower()).strip()


def run_exact(ds: Dataset) -> Run:
    """Baseline 1: exact match on the lowercased name, no other normalization."""
    t0 = time.perf_counter()
    index: dict[str, set[str]] = defaultdict(set)
    for e in ds.watchlist:
        for nm in e.names():
            index[_plain(nm)].add(e.id)
    scores = {c.id: {eid: 100.0 for eid in index.get(_plain(c.name), ())} for c in ds.customers}
    return Run("exact", scores, time.perf_counter() - t0)


def run_levenshtein(ds: Dataset, screener: Screener, floor: float = 50.0) -> Run:
    """Baseline 2: Levenshtein ratio on folded full names, no token logic, no secondary attributes.

    To keep it affordable in pure Python it scores the same blocked candidates as the engine,
    which can only help it.
    """
    t0 = time.perf_counter()
    scores: dict[str, dict[str, float]] = {}
    folded = {e.id: [fold(_plain(n)) for n in e.names()] for e in ds.watchlist}
    for c in ds.customers:
        q = fold(_plain(c.name))
        out: dict[str, float] = {}
        for eid in screener.candidate_entries(c.name, c.kind):
            best = 0.0
            for n in folded[eid]:
                if min(len(q), len(n)) / max(len(q), len(n)) * 100 < floor:
                    continue
                best = max(best, levenshtein_ratio(q, n, floor / 100) * 100)
            if best >= floor:
                out[eid] = round(best, 2)
        scores[c.id] = out
    return Run("levenshtein", scores, time.perf_counter() - t0)


def metrics_at(ds: Dataset, run: Run, t: float) -> dict:
    n = len(ds.customers)
    truth = {c.id: c.true_entry for c in ds.customers}
    alert_pairs = tp = 0
    alerted = 0
    alerted_neg = 0
    for cid, cands in run.scores.items():
        hits = [eid for eid, s in cands.items() if s >= t]
        if not hits:
            continue
        alerted += 1
        alert_pairs += len(hits)
        if truth[cid] in hits:
            tp += 1
        else:
            alerted_neg += 1
    n_pos = sum(1 for v in truth.values() if v)
    return {
        "threshold": t,
        "recall": tp / n_pos if n_pos else 1.0,
        "precision": tp / alert_pairs if alert_pairs else 1.0,
        "alert_pairs": alert_pairs,
        "alerted_customers": alerted,
        "alerts_per_1000": alerted / n * 1000,
        "false_alerts_per_1000": alerted_neg / n * 1000,
    }


def sweep(ds: Dataset, run: Run, thresholds: list[float] = THRESHOLDS) -> list[dict]:
    return [metrics_at(ds, run, t) for t in thresholds]


def recommend(curve: list[dict], target: float) -> dict | None:
    """Highest threshold that still meets the recall target (fewest alerts for that recall)."""
    ok = [p for p in curve if p["recall"] >= target]
    return max(ok, key=lambda p: p["threshold"]) if ok else None


def recall_by_perturbation(ds: Dataset, run: Run, t: float) -> list[dict]:
    tot: Counter[str] = Counter()
    hit: Counter[str] = Counter()
    for c in ds.positives:
        found = run.scores.get(c.id, {}).get(c.true_entry, 0.0) >= t
        for p in c.perturbations or ["none"]:
            tot[p] += 1
            hit[p] += found
    return [{"perturbation": p, "n": tot[p], "recall": hit[p] / tot[p]} for p in sorted(tot, key=lambda k: -tot[k])]


def misses(ds: Dataset, run: Run, t: float, screener: Screener) -> list[dict]:
    out = []
    for c in ds.positives:
        s = run.scores.get(c.id, {}).get(c.true_entry, 0.0)
        if s < t:
            e = screener.entries[c.true_entry]
            out.append({"customer": c.name, "entry": e.name, "aliases": e.aliases, "score": s,
                        "perturbations": c.perturbations})
    return out


def full_eval(ds: Dataset, target: float = 0.99, configured: float | None = None) -> dict:
    screener = Screener(ds.watchlist, ds.freq)
    eng = run_engine(ds, screener)
    exact = run_exact(ds)
    lev = run_levenshtein(ds, screener)
    eng_curve, lev_curve = sweep(ds, eng), sweep(ds, lev)
    rec = recommend(eng_curve, target)
    lev_rec = recommend(lev_curve, target)
    at = configured if configured is not None else (rec["threshold"] if rec else 100.0)
    lev_best = max(lev_curve, key=lambda p: p["recall"])
    return {
        "customers": len(ds.customers),
        "positives": len(ds.positives),
        "namesakes": sum(1 for c in ds.customers if c.namesake_of),
        "watchlist_entries": len(ds.watchlist),
        "target_recall": target,
        "engine": {**eng.extra, "seconds": round(eng.seconds, 2), "curve": eng_curve, "recommended": rec,
                   "configured": metrics_at(ds, eng, at)},
        "exact": {"seconds": round(exact.seconds, 3), "point": metrics_at(ds, exact, 100.0)},
        "levenshtein": {"seconds": round(lev.seconds, 2), "curve": lev_curve, "recommended": lev_rec,
                        "max_recall": lev_best},
        "by_perturbation": recall_by_perturbation(ds, eng, at),
        "misses": misses(ds, eng, at, screener)[:25],
    }
