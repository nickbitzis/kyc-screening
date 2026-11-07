"""String similarity in pure Python: Jaro, Jaro-Winkler, Levenshtein, and a phonetic key.

The phonetic key is a simplified form of Double Metaphone's primary code. It is tuned for
names transliterated into Latin script, where vowels and doubled letters vary a lot
between spellings but the consonant skeleton is stable (Mohammed, Muhammad, Mohamed all
give MHMT).
"""

from __future__ import annotations

from functools import lru_cache


def jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    l1, l2 = len(s1), len(s2)
    if l1 == 0 or l2 == 0:
        return 0.0
    window = max(max(l1, l2) // 2 - 1, 0)
    m1 = [False] * l1
    m2 = [False] * l2
    matches = 0
    for i, ch in enumerate(s1):
        lo, hi = max(0, i - window), min(i + window + 1, l2)
        for j in range(lo, hi):
            if not m2[j] and s2[j] == ch:
                m1[i] = m2[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1
    t = transpositions / 2
    return (matches / l1 + matches / l2 + (matches - t) / matches) / 3


def jaro_winkler(s1: str, s2: str, p: float = 0.1, max_prefix: int = 4) -> float:
    """Jaro-Winkler similarity with the standard prefix scale 0.1 and prefix cap 4."""
    j = jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1[:max_prefix], s2[:max_prefix], strict=False):
        if a != b:
            break
        prefix += 1
    return j + prefix * p * (1 - j)


@lru_cache(maxsize=500_000)
def jw_cached(a: str, b: str) -> float:
    if a > b:
        a, b = b, a
    return jaro_winkler(a, b)


def levenshtein(a: str, b: str, max_dist: int | None = None) -> int:
    """Edit distance. With max_dist, stops early and returns max_dist + 1 once it is exceeded."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if max_dist is not None and len(a) - len(b) > max_dist:
        return max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if max_dist is not None and min(cur) > max_dist:
            return max_dist + 1
        prev = cur
    return prev[-1]


def levenshtein_ratio(a: str, b: str, floor: float = 0.0) -> float:
    """1 - distance / longer length. Returns 0.0 early when the ratio cannot reach `floor`."""
    if not a and not b:
        return 1.0
    n = max(len(a), len(b))
    max_dist = int((1 - floor) * n) if floor > 0 else None
    d = levenshtein(a, b, max_dist)
    if max_dist is not None and d > max_dist:
        return 0.0
    return 1.0 - d / n


def ngrams(token: str, n: int = 3) -> set[str]:
    padded = f"^{token}$"
    if len(padded) <= n:
        return {padded}
    return {padded[i:i + n] for i in range(len(padded) - n + 1)}


_VOWELS = set("aeiouy")


@lru_cache(maxsize=200_000)
def phonetic_key(token: str, length: int = 4) -> str:
    """Simplified Double Metaphone primary key (consonant skeleton, max 4 chars)."""
    w = "".join(ch for ch in token.lower() if ch.isalpha())
    if not w:
        return ""
    for src, dst in (("x", "ks"), ("sch", "x"), ("tch", "x"), ("ph", "f"), ("ck", "k"), ("kh", "h"), ("gh", "h"),
                     ("sh", "x"), ("ch", "x"), ("zh", "j"), ("dzh", "j"), ("dj", "j"), ("tz", "s"),
                     ("ts", "s"), ("th", "t"), ("wr", "r"), ("kn", "n"), ("ou", "u"), ("q", "k"),
                     ("w", "v"), ("z", "s"), ("dh", "d")):
        w = w.replace(src, dst)
    out = []
    for i, ch in enumerate(w):
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in _VOWELS:
            if i == 0:
                out.append("A")
            continue
        if ch == "c":
            ch = "s" if nxt in ("e", "i", "y") else "k"
        elif ch == "g" and nxt in ("e", "i", "y") and i > 0:
            ch = "j"
        elif ch == "j":
            ch = "j"
        elif ch == "d":
            ch = "t"
        elif ch == "b":
            ch = "p"
        elif ch == "v":
            ch = "f"
        elif ch == "h" and i > 0 and (w[i - 1] not in _VOWELS):
            continue
        code = ch.upper()
        if out and out[-1] == code:
            continue
        out.append(code)
    return "".join(out)[:length]
