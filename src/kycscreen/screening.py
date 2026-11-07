"""The screening engine: normalize, block, score, keep the best name per watchlist entry."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .blocking import TokenBlocker, blocking_tokens
from .data import Entry
from .normalize import normalize_name
from .scoring import NameFrequency, score_pair

MIN_BLOCK_WEIGHT = 0.4


@dataclass
class NameRecord:
    entry_id: str
    name: str
    tokens: list[str]
    is_alias: bool


@dataclass
class Candidate:
    entry_id: str
    score: float
    matched_name: str
    is_alias: bool
    contributions: list[dict]

    def to_dict(self, entry: Entry | None = None) -> dict:
        d = {"entry_id": self.entry_id, "score": self.score, "matched_name": self.matched_name,
             "matched_alias": self.is_alias, "contributions": self.contributions}
        if entry is not None:
            d.update(list_type=entry.list_type, kind=entry.kind, primary_name=entry.name, dob=entry.dob,
                     nationality=entry.nationality, program=entry.program)
        return d


class Screener:
    def __init__(self, watchlist: list[Entry], freq: NameFrequency | None = None) -> None:
        self.entries: dict[str, Entry] = {e.id: e for e in watchlist}
        self.freq = freq or NameFrequency()
        self.records: list[NameRecord] = []
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.blocker = TokenBlocker()
        for e in watchlist:
            for k, nm in enumerate(e.names()):
                toks = normalize_name(nm, entity=e.kind == "entity")
                idx = len(self.records)
                self.records.append(NameRecord(e.id, nm, toks, k > 0))
                for t in set(blocking_tokens(toks)):
                    self.postings[t].append(idx)
                    self.blocker.add(t)

    @property
    def size(self) -> int:
        return len(self.records)

    def candidate_records(self, tokens: list[str]) -> set[int]:
        """Records sharing a similar token with the query, weighted by how rare the shared tokens are.

        A record is kept when the query tokens that hit it carry at least MIN_BLOCK_WEIGHT of the
        query's total token weight. Sharing only "muhammad" with a three-part name is not enough;
        sharing a rare surname is.
        """
        if not tokens:
            return set()
        total = sum(self.freq.weight(t) for t in tokens)
        hits: dict[int, float] = defaultdict(float)
        for t in set(blocking_tokens(tokens)):
            recs: set[int] = set()
            for v in self.blocker.similar(t):
                recs.update(self.postings[v])
            w = self.freq.weight(t)
            for r in recs:
                hits[r] += w
        need = MIN_BLOCK_WEIGHT * total
        return {r for r, w in hits.items() if w >= need}

    def candidate_entries(self, name: str, kind: str = "person") -> set[str]:
        toks = normalize_name(name, entity=kind == "entity")
        return {self.records[i].entry_id for i in self.candidate_records(toks)}

    def screen(self, name: str, kind: str = "person", dob: str | None = None, nationality: str | None = None,
               min_score: float = 0.0, limit: int | None = None, explain: bool = True) -> list[Candidate]:
        toks = normalize_name(name, entity=kind == "entity")
        best: dict[str, Candidate] = {}
        for i in self.candidate_records(toks):
            rec = self.records[i]
            e = self.entries[rec.entry_id]
            s = score_pair(toks, kind, dob, nationality, rec.tokens, e.kind, e.dob, e.nationality, self.freq, explain)
            if s.total < min_score:
                continue
            cur = best.get(rec.entry_id)
            if cur is None or s.total > cur.score:
                best[rec.entry_id] = Candidate(rec.entry_id, s.total, rec.name, rec.is_alias, s.contributions)
        out = sorted(best.values(), key=lambda c: (-c.score, c.entry_id))
        return out[:limit] if limit else out
