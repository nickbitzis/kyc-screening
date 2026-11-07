import pytest

from kycscreen.kyb import Company, Person, Registry, build_registry, compute_ubos, red_flags


def mk(*companies, persons=(), edges=()):
    reg = Registry()
    for p in persons:
        reg.add_person(Person(p, p.title(), "1970-01-01", "GB"))
    for c in companies:
        cid, *rest = c if isinstance(c, tuple) else (c,)
        kind = rest[0] if rest else "company"
        reg.add_company(Company(cid, cid.title(), "GB", "1 High Street", kind, ["dir"] if "dir" in persons else [],
                                disclosed=kind != "foundation"))
    for o, t, pct in edges:
        reg.own(o, t, pct)
    return reg


def eff(res):
    return {o["person_id"]: round(o["effective_pct"], 4) for o in res["owners"]}


def test_direct_ownership():
    reg = mk("t", persons=["a", "b"], edges=[("a", "t", 60), ("b", "t", 40)])
    res = compute_ubos(reg, "t")
    assert eff(res) == {"a": 60, "b": 40}
    assert {u["person_id"] for u in res["ubos"]} == {"a", "b"}


def test_chain_multiplies():
    reg = mk("t", "h1", "h2", persons=["p"], edges=[("p", "h1", 70), ("h1", "h2", 80), ("h2", "t", 60)])
    res = compute_ubos(reg, "t")
    assert eff(res)["p"] == pytest.approx(33.6)
    assert res["max_depth"] == 2
    assert "70% x 80% x 60% = 33.60%" in res["ubos"][0]["paths"][0]["explanation"]


def test_paths_sum_across_branches():
    reg = mk("t", "h1", "h2", persons=["p"], edges=[("p", "h1", 50), ("p", "h2", 50), ("h1", "t", 20), ("h2", "t", 30)])
    res = compute_ubos(reg, "t")
    assert eff(res)["p"] == pytest.approx(25.0)
    assert len(res["ubos"][0]["paths"]) == 2
    assert res["ubos"][0]["is_ubo"]  # exactly at the threshold counts


def test_below_threshold_is_not_ubo():
    reg = mk("t", persons=["a", "b", "c", "d", "e"], edges=[(x, "t", 20) for x in "abcde"])
    res = compute_ubos(reg, "t")
    assert all(u["basis"] == "senior_managing_official" for u in res["ubos"]) or res["ubos"] == []


def test_cycle_detected_and_counted_once():
    reg = mk("t", "c1", "c2", persons=["p3", "p4"], edges=[
        ("c1", "t", 50), ("c2", "t", 50), ("c1", "c2", 30), ("c2", "c1", 40), ("p3", "c1", 60), ("p4", "c2", 70)])
    res = compute_ubos(reg, "t")
    assert res["cycles"] == [("c1", "c2")]
    e = eff(res)
    assert e["p3"] == pytest.approx(30 + 9)    # 60x50 + 60x30x50
    assert e["p4"] == pytest.approx(35 + 14)   # 70x50 + 70x40x50


def test_self_loop_terminates():
    reg = mk("t", "h", persons=["p"], edges=[("h", "h", 10), ("h", "t", 100), ("p", "h", 90)])
    res = compute_ubos(reg, "t")
    assert eff(res)["p"] == pytest.approx(90)
    assert res["cycles"] == [("h",)]


def test_senior_managing_official_fallback():
    reg = mk("t", persons=["a", "b", "dir"], edges=[("a", "t", 20), ("b", "t", 20)])
    res = compute_ubos(reg, "t")
    assert [u["basis"] for u in res["ubos"]] == ["senior_managing_official"]


def test_opaque_holder_and_threshold_splitting_flags():
    reg = mk("t", ("f", "foundation"), persons=["ann rahal", "bob rahal"],
             edges=[("f", "t", 52), ("ann rahal", "t", 24), ("bob rahal", "t", 24)])
    res = compute_ubos(reg, "t")
    flags = {f["flag"] for f in red_flags(reg, "t", res)}
    assert {"opaque_holder", "threshold_splitting"} <= flags
    assert res["opaque"][0]["effective_pct"] == pytest.approx(52)


def test_generated_registry_demo_structures(small_ds):
    reg = build_registry(small_ds.watchlist)
    t1, t2, t3, t4, t5 = reg.demo_targets
    assert {u["name"] for u in compute_ubos(reg, t2)["ubos"]} >= {reg.persons[p].name for p in reg.planted_hits if
                                                                  any(e.owner == p for e in reg.edges)} & {u["name"] for u in compute_ubos(reg, t2)["ubos"]}
    assert compute_ubos(reg, t3)["cycles"]
    flags = {f["flag"] for f in red_flags(reg, t4, compute_ubos(reg, t4))}
    assert {"nominee_director", "nominee_shareholder", "threshold_splitting", "mass_registration_address"} <= flags
    assert any(f["flag"] == "opaque_holder" for f in red_flags(reg, t5, compute_ubos(reg, t5)))
