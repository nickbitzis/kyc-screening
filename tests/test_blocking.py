import random

from kycscreen.blocking import TokenBlocker, blocking_tokens
from kycscreen.normalize import normalize_name


def test_similar_catches_typos_and_phonetic_variants():
    b = TokenBlocker(["sabbagh", "haddad", "muhammad", "volkov"])
    assert "sabbagh" in b.similar("sabbahg")
    assert "muhammad" in b.similar("mouhamed")
    assert "volkov" not in b.similar("haddad")


def test_similarity_relation_is_symmetric(small_ds):
    vocab = sorted({t for c in small_ds.customers[:400] for t in normalize_name(c.name) if len(t) >= 2})
    b = TokenBlocker(vocab)
    rng = random.Random(3)
    for a in rng.sample(vocab, 60):
        for s in b.similar(a):
            assert a in b.similar(s)


def test_blocking_tokens_skip_initials_and_join_short_parts():
    assert blocking_tokens(["j", "garcia"]) == ["garcia"]
    assert "zhipeng" in blocking_tokens(["luo", "zhi", "peng"])


def test_blocking_recall_and_reduction(small_ds, screener):
    pos = small_ds.positives
    found = sum(c.true_entry in screener.candidate_entries(c.name, c.kind) for c in pos)
    assert found / len(pos) >= 0.97
    cands = [len(screener.candidate_records(normalize_name(c.name))) for c in small_ds.customers[:300]]
    assert sum(cands) / len(cands) < 0.1 * screener.size
