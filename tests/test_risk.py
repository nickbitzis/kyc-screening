import copy

from kycscreen.risk import RiskScorecard, load_yaml


def test_simple_individual_is_low():
    r = RiskScorecard().rate("individual", ["GB"], "savings")
    assert r["rating"] == "low" and r["points"] == 0


def test_points_and_reasons_add_up():
    r = RiskScorecard().rate("company", ["CY"], "cross_border_wire", [], ["layered_structure"])
    assert r["points"] == sum(x["points"] for x in r["reasons"]) == 10 + 15 + 20 + 10
    assert r["rating"] == "high"
    assert RiskScorecard().rate("company", ["CY"], "savings")["rating"] == "medium"


def test_sanctions_possible_match_forces_high():
    r = RiskScorecard().rate("individual", ["GB"], "savings", ["sanctions_possible_match"])
    assert r["rating"] == "high" and "sanctions_possible_match" in r["overrides"]


def test_high_risk_geography_forces_high():
    assert RiskScorecard().rate("individual", ["KP"], "savings")["rating"] == "high"


def test_bands_are_configurable():
    cfg = copy.deepcopy(load_yaml("risk.yaml"))
    cfg["bands"] = {"medium": 5, "high": 8}
    assert RiskScorecard(cfg).rate("company", ["GB"], "savings")["rating"] == "high"
