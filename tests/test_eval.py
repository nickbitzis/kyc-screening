from kycscreen import cli
from kycscreen.evaluate import metrics_at, recommend, run_engine, run_exact, run_levenshtein, sweep
from kycscreen.service import Settings


def test_engine_beats_both_baselines(small_ds, screener):
    eng = run_engine(small_ds, screener)
    ex = run_exact(small_ds)
    lev = run_levenshtein(small_ds, screener)
    e_rec = recommend(sweep(small_ds, eng), 0.95)
    assert e_rec is not None
    assert metrics_at(small_ds, ex, 100)["recall"] < 0.5
    lev_curve = sweep(small_ds, lev)
    lev_same_load = min(lev_curve, key=lambda p: abs(p["alerts_per_1000"] - e_rec["alerts_per_1000"]))
    assert lev_same_load["recall"] < e_rec["recall"]


def test_recommend_picks_highest_threshold_meeting_target():
    curve = [{"threshold": 60, "recall": 1.0}, {"threshold": 70, "recall": 0.99}, {"threshold": 80, "recall": 0.9}]
    assert recommend(curve, 0.99)["threshold"] == 70
    assert recommend(curve, 1.01) is None


def test_recall_falls_and_workload_falls_as_threshold_rises(small_ds, screener):
    curve = sweep(small_ds, run_engine(small_ds, screener))
    for a, b in zip(curve, curve[1:], strict=False):
        assert b["recall"] <= a["recall"] and b["alerts_per_1000"] <= a["alerts_per_1000"]


def test_gate_passes_and_fails(monkeypatch, capsys):
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: cls(40.0, 0.95, 30.0)))
    assert cli.main(["eval", "--gate", "--customers", "2000"]) == 0
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: cls(99.5, 0.99, 30.0)))
    assert cli.main(["eval", "--gate", "--customers", "2000"]) == 1
    assert "GATE FAIL" in capsys.readouterr().out
