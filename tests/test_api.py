import pytest
from fastapi.testclient import TestClient

from kycscreen.api import create_app
from kycscreen.service import KYCService


@pytest.fixture(scope="module")
def client(small_ds, tmp_path_factory):
    svc = KYCService(dataset=small_ds)
    report = tmp_path_factory.mktemp("r") / "missing.json"
    return TestClient(create_app(svc, report_path=report))


def test_health_and_metrics(client):
    h = client.get("/healthz").json()
    assert h["status"] == "ok" and h["watchlist_entries"] == 300
    client.post("/screen/person", json={"name": "Nobody Particular"})
    m = client.get("/metrics").text
    assert "kyc_screenings_total" in m and "kyc_requests_total" in m


def test_ui_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "KYC screening" in r.text


def test_screen_person_hit_opens_case_and_rates_high(client, small_ds):
    e = next(x for x in small_ds.watchlist if x.kind == "person" and x.list_type == "sanctions" and x.dob and len(x.dob) == 10)
    r = client.post("/screen/person", json={"name": f"Dr. {e.name}", "dob": e.dob, "nationality": e.nationality,
                                            "customer_id": "C-T1"}).json()
    assert r["hits"][0]["entry_id"] == e.id
    assert r["case_ids"] and r["risk"]["rating"] == "high"
    feats = {c["feature"] for c in r["hits"][0]["contributions"]}
    assert {"name", "dob", "nationality"} <= feats


def test_screen_person_validation(client):
    assert client.post("/screen/person", json={"name": ""}).status_code == 422
    assert client.post("/screen/person", json={"name": "x", "dob": "May 4"}).status_code == 422


def test_screen_business_returns_ubos_paths_and_risk(client):
    reg = client.app.state.service.registry
    t2 = reg.demo_targets[1]
    r = client.post("/screen/business", json={"registry_id": t2}).json()
    assert r["company"]["id"] == t2
    indirect = [u for u in r["ubos"] if u["paths"] and len(u["paths"][0]["nodes"]) > 2]
    assert indirect and "x" in indirect[0]["paths"][0]["explanation"]
    assert r["risk"]["rating"] in {"medium", "high"} and r["explanation"]
    assert "_ubo" not in r


def test_screen_business_by_name_and_404(client):
    reg = client.app.state.service.registry
    name = reg.companies[reg.demo_targets[0]].name
    assert client.post("/screen/business", json={"name": name}).status_code == 200
    assert client.post("/screen/business", json={"registry_id": "B-9999"}).status_code == 404
    assert client.post("/screen/business", json={}).status_code == 422


def test_graph_svg_and_registry_list(client):
    reg = client.app.state.service.registry
    lst = client.get("/api/registry").json()
    assert lst[0]["id"] == reg.demo_targets[0]
    r = client.get(f"/api/business/{reg.demo_targets[2]}/graph.svg")
    assert r.status_code == 200 and r.text.startswith("<svg") and "cyc" in r.text


def test_review_queue_disposition_and_audit(client, small_ds):
    e = next(x for x in small_ds.watchlist if x.kind == "person")
    client.post("/screen/person", json={"name": e.name, "customer_id": "C-T2"})
    q = client.get("/review/queue").json()
    case = next(c for c in q["cases"] if c["subject_ref"] == "C-T2")
    r = client.post(f"/review/{case['id']}/disposition", json={"disposition": "false_positive", "analyst": "ana", "note": "DOB differs"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert client.post(f"/review/{case['id']}/disposition", json={"disposition": "true_match", "analyst": "ana"}).status_code == 409
    assert client.post("/review/999999/disposition", json={"disposition": "true_match", "analyst": "ana"}).status_code == 404
    a = client.get("/audit").json()
    assert a["chain_ok"] and any(row["action"] == "case_disposition" for row in a["rows"])


def test_report_missing_returns_404(client):
    assert client.get("/report").status_code == 404
