def test_perturbed_true_matches_rank_first(small_ds, screener):
    pos = small_ds.positives
    top1 = 0
    for c in pos:
        res = screener.screen(c.name, c.kind, c.dob, c.nationality, limit=1)
        top1 += bool(res) and res[0].entry_id == c.true_entry
    assert top1 / len(pos) >= 0.9


def test_one_candidate_per_entry_sorted(screener, small_ds):
    e = small_ds.watchlist[0]
    res = screener.screen(e.name, e.kind, e.dob, e.nationality)
    ids = [c.entry_id for c in res]
    assert len(ids) == len(set(ids))
    assert res[0].entry_id == e.id and res[0].score >= 95
    assert [c.score for c in res] == sorted((c.score for c in res), reverse=True)


def test_alias_match_is_reported(screener, small_ds):
    e = next(x for x in small_ds.watchlist if x.aliases and x.kind == "person")
    res = screener.screen(e.aliases[0], e.kind)
    top = next(c for c in res if c.entry_id == e.id)
    assert top.score > 70


def test_min_score_filters(screener):
    assert all(c.score >= 60 for c in screener.screen("Muhammad Haddad", min_score=60))
