"""Candidate generation (blocking).

Scoring every customer against every watchlist name is 20,000 x 6,000 = 120 million
comparisons. Blocking cuts that down by only scoring names that share at least one
"similar token" with the query. Two tokens are similar when either:

1. they have the same phonetic key (catches Mohamed / Muhammad, Hussein / Husayn), or
2. their character trigram sets have Jaccard overlap >= 0.5 (catches typos).

The relation is symmetric, which is what makes delta screening exact: when a watchlist
entry changes, the customers that could block against it are found by running the same
test from the entry side against the customer vocabulary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .similarity import ngrams, phonetic_key

MIN_JACCARD = 0.4


class TokenBlocker:
    """Index over a token vocabulary that answers `similar(token)` quickly."""

    def __init__(self, vocab: Iterable[str] = ()) -> None:
        self.by_key: dict[str, set[str]] = defaultdict(set)
        self.by_gram: dict[str, set[str]] = defaultdict(set)
        self.grams: dict[str, set[str]] = {}
        self._cache: dict[str, frozenset[str]] = {}
        for tok in vocab:
            self.add(tok)

    def add(self, tok: str) -> None:
        if len(tok) < 2 or tok in self.grams:
            return
        g = ngrams(tok)
        self.grams[tok] = g
        self.by_key[phonetic_key(tok)].add(tok)
        for x in g:
            self.by_gram[x].add(tok)
        self._cache.clear()

    def __contains__(self, tok: str) -> bool:
        return tok in self.grams

    def __len__(self) -> int:
        return len(self.grams)

    def similar(self, tok: str) -> frozenset[str]:
        hit = self._cache.get(tok)
        if hit is not None:
            return hit
        out: set[str] = set()
        if len(tok) >= 2:
            out |= self.by_key.get(phonetic_key(tok), set())
            g = ngrams(tok)
            counts: dict[str, int] = defaultdict(int)
            for x in g:
                for v in self.by_gram.get(x, ()):
                    counts[v] += 1
            for v, shared in counts.items():
                if shared / (len(g) + len(self.grams[v]) - shared) >= MIN_JACCARD:
                    out.add(v)
            if tok in self.grams:
                out.add(tok)
        res = frozenset(out)
        self._cache[tok] = res
        return res


def blocking_tokens(tokens: Iterable[str]) -> list[str]:
    """Tokens used for blocking.

    Initials carry no blocking signal and are skipped. Adjacent short tokens are also joined
    ("zhi peng" adds "zhipeng") so split and merged given names still meet in the index.
    """
    toks = list(tokens)
    out = [t for t in toks if len(t) >= 2]
    for a, b in zip(toks, toks[1:], strict=False):
        if 2 <= len(a) <= 5 and 2 <= len(b) <= 5:
            out.append(a + b)
    return out
