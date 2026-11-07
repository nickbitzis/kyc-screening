"""Explainable match scoring.

A score from 0 to 100 is the sum of per-feature points, and every point is reported back:

    name similarity   up to 85   token-set Jaro-Winkler with phonetic and initial handling,
                                 weighted so common tokens (Muhammad, Li) count for less
    date of birth     +10 exact, +4 same year, 0 near miss or unknown, -25 clear mismatch
    nationality       +5 match, 0 unknown, -8 mismatch
    record type       -40 when a person is compared with a company or the reverse

The analyst reading an alert sees exactly which tokens aligned and how each feature moved
the score. That is a regulatory expectation, not a nicety: you must be able to say why a
customer was or was not flagged.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

from .similarity import jw_cached, phonetic_key

NAME_POINTS = 85.0
DOB_POINTS = {"exact": 10.0, "same_year": 4.0, "near": 0.0, "unknown": 0.0, "mismatch": -25.0}
NAT_POINTS = {"match": 5.0, "unknown": 0.0, "mismatch": -8.0}
TYPE_MISMATCH = -40.0


class NameFrequency:
    """Token document frequency across the customer base, used to down-weight common names."""

    def __init__(self, token_lists: Iterable[Iterable[str]] = ()) -> None:
        self.df: Counter[str] = Counter()
        self.n = 0
        for toks in token_lists:
            self.n += 1
            self.df.update(set(toks))
        self._lognorm = math.log(max(self.n, 100) / 50)
        self._wcache: dict[str, float] = {}

    def weight(self, tok: str) -> float:
        w = self._wcache.get(tok)
        if w is None:
            w = self._wcache[tok] = self._weight(tok)
        return w

    def _weight(self, tok: str) -> float:
        if len(tok) == 1:
            return 0.35
        df = self.df.get(tok, 0)
        if self.n == 0 or df <= 50:
            return 1.0
        return max(0.3, min(1.0, math.log(self.n / (df + 1)) / self._lognorm))


@lru_cache(maxsize=500_000)
def token_similarity(a: str, b: str) -> tuple[float, str]:
    """Similarity of two canonical tokens in [0, 1] and the rule that produced it."""
    if a == b:
        return 1.0, "exact"
    if len(a) == 1 or len(b) == 1:
        return (0.85, "initial") if a[0] == b[0] else (0.0, "none")
    jw = jw_cached(a, b)
    sim = max(0.0, (jw - 0.7) / 0.3)
    how = "jaro_winkler"
    if len(a) >= 3 and len(b) >= 3 and phonetic_key(a) == phonetic_key(b) and sim < 0.85:
        sim, how = 0.85, "phonetic"
    return sim, how


def _directional(src: list[str], dst: list[str], freq: NameFrequency, explain: bool) -> tuple[float, list[tuple]]:
    total = got = 0.0
    pairs = []
    for t in src:
        best, best_u, best_how = 0.0, None, "none"
        for u in dst:
            s, how = token_similarity(t, u)
            if s > best:
                best, best_u, best_how = s, u, how
        w = freq.weight(t)
        total += w
        got += w * best
        if explain:
            pairs.append((t, best_u, round(best, 3), best_how, round(w, 2)))
    return (got / total if total else 0.0), pairs


def name_similarity(q: list[str], r: list[str], freq: NameFrequency, explain: bool = True) -> tuple[float, dict]:
    """Token-set similarity, with a whole-string fallback for split or merged name parts."""
    if not q or not r:
        return 0.0, {}
    d1, pairs = _directional(q, r, freq, explain)
    d2, _ = _directional(r, q, freq, False)
    token_set = 0.6 * max(d1, d2) + 0.4 * min(d1, d2)
    joined_sim = 0.0
    jq, jr = "".join(q), "".join(r)
    # "Xiao Ming" vs "Xiaoming", "Al Hassan" vs "Alhassan": same letters, different token boundaries.
    if token_set < 0.9 and len(q) != len(r) and abs(len(jq) - len(jr)) <= 2:
        joined = max(jw_cached(jq, jr), jw_cached("".join(sorted(q)), "".join(sorted(r))))
        joined_sim = max(0.0, (joined - 0.7) / 0.3) * 0.95
    sim = max(token_set, joined_sim)
    if not explain:
        return sim, {}
    return sim, {
        "token_set": round(token_set, 3),
        "query_coverage": round(d1, 3),
        "record_coverage": round(d2, 3),
        "joined": round(joined_sim, 3),
        "alignment": [{"query": p[0], "record": p[1], "sim": p[2], "rule": p[3], "weight": p[4]} for p in pairs],
    }


def dob_feature(q: str | None, r: str | None) -> str:
    if not q or not r:
        return "unknown"
    if q == r:
        return "exact"
    qy, ry = q[:4], r[:4]
    if len(r) == 4 or len(q) == 4:
        return "same_year" if qy == ry else ("near" if abs(int(qy) - int(ry)) <= 1 else "mismatch")
    if qy == ry:
        qm, qd = q[5:7], q[8:10]
        rm, rd = r[5:7], r[8:10]
        return "near" if (qm == rd and qd == rm) or qm == rm or qd == rd else "same_year"
    if abs(int(qy) - int(ry)) <= 1 and q[5:] == r[5:]:
        return "near"
    return "mismatch"


def nat_feature(q: str | None, r: str | None) -> str:
    if not q or not r:
        return "unknown"
    return "match" if q.upper() == r.upper() else "mismatch"


@dataclass
class Score:
    total: float
    name_sim: float
    contributions: list[dict] = field(default_factory=list)


def score_pair(q_tokens: list[str], q_kind: str, q_dob: str | None, q_nat: str | None,
               r_tokens: list[str], r_kind: str, r_dob: str | None, r_nat: str | None,
               freq: NameFrequency, explain: bool = True) -> Score:
    sim, detail = name_similarity(q_tokens, r_tokens, freq, explain)
    name_pts = NAME_POINTS * sim
    dob = dob_feature(q_dob, r_dob)
    nat = nat_feature(q_nat, r_nat)
    contribs = [{"feature": "name", "value": round(sim, 3), "points": round(name_pts, 1), "detail": detail if explain else {}},
                {"feature": "dob", "value": dob, "points": DOB_POINTS[dob]},
                {"feature": "nationality", "value": nat, "points": NAT_POINTS[nat]}]
    total = name_pts + DOB_POINTS[dob] + NAT_POINTS[nat]
    if q_kind != r_kind:
        contribs.append({"feature": "record_type", "value": f"{q_kind} vs {r_kind}", "points": TYPE_MISMATCH})
        total += TYPE_MISMATCH
    return Score(round(max(0.0, min(100.0, total)), 2), sim, contribs)
