"""FastAPI service.

POST /screen/person      screen one person, open review cases for hits, return a risk rating
POST /screen/business    UBOs with paths, red flags, hits on the business, UBOs and directors
GET  /review/queue       open cases, highest score first
POST /review/{id}/disposition
GET  /audit              most recent audit rows, plus a hash chain check
GET  /healthz, /metrics  liveness and Prometheus metrics
GET  /                   web UI
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from . import __version__
from .graph import ownership_svg
from .logs import event, get_logger, now_ms
from .report import render_report
from .review import ReviewStore
from .service import KYCService

STATIC = Path(__file__).parent / "static"
DEFAULT_REPORT = Path(__file__).resolve().parents[2] / "reports" / "eval.json"


class PersonRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dob: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}-\d{2})?$")
    nationality: str | None = Field(default=None, min_length=2, max_length=2)
    customer_id: str | None = Field(default=None, max_length=64)
    product: str | None = None


class BusinessRequest(BaseModel):
    registry_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    product: str | None = "business_account"


class NameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="person", pattern="^(person|entity)$")
    dob: str | None = None
    nationality: str | None = None


class Disposition(BaseModel):
    disposition: str
    analyst: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=2000)


class Metrics:
    def __init__(self) -> None:
        r = self.registry = CollectorRegistry()
        self.requests = Counter("kyc_requests_total", "HTTP requests", ["path", "status"], registry=r)
        self.screens = Counter("kyc_screenings_total", "Screenings run", ["kind"], registry=r)
        self.alerts = Counter("kyc_alerts_total", "Alerts at or above threshold", ["list_type"], registry=r)
        self.ratings = Counter("kyc_risk_ratings_total", "Risk ratings issued", ["rating"], registry=r)
        self.dispositions = Counter("kyc_dispositions_total", "Case dispositions", ["disposition"], registry=r)
        self.open_cases = Gauge("kyc_open_cases", "Cases waiting for an analyst", registry=r)
        self.latency = Histogram("kyc_screen_seconds", "Screening latency", ["kind"], registry=r,
                                 buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1))


def _clean(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def create_app(service: KYCService | None = None, report_path: str | Path | None = None) -> FastAPI:
    if service is None:
        service = KYCService(store=ReviewStore(os.environ.get("KYC_REVIEW_DB", ":memory:")))
    report_path = Path(report_path or os.environ.get("KYC_REPORT", DEFAULT_REPORT))
    metrics = Metrics()
    log = get_logger()
    app = FastAPI(title="kycscreen", version=__version__)
    app.state.service, app.state.metrics = service, metrics
    known = {"/", "/healthz", "/metrics", "/screen/person", "/screen/business", "/screen/name", "/review/queue",
             "/audit", "/report", "/api/registry"}

    @app.middleware("http")
    async def count(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        path = request.url.path if request.url.path in known else "other"
        metrics.requests.labels(path, str(response.status_code)).inc()
        if path not in ("/healthz", "/metrics"):
            event(log, "request", path=request.url.path, method=request.method, status=response.status_code,
                  ms=now_ms(t0))
        return response

    def record(kind: str, res: dict, t0: float) -> None:
        metrics.screens.labels(kind).inc()
        metrics.latency.labels(kind).observe(time.perf_counter() - t0)
        for h in res["hits"]:
            metrics.alerts.labels(h["list_type"]).inc()
        metrics.ratings.labels(res["risk"]["rating"]).inc()
        metrics.open_cases.set(service.store.counts().get("open", 0))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": __version__, "watchlist_entries": len(service.screener.entries),
                "indexed_names": service.screener.size, "threshold": service.settings.threshold}

    @app.get("/metrics")
    def prom():
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text()

    @app.post("/screen/name")
    def screen_name(req: NameRequest):
        """Candidates with score breakdowns, without opening cases. Used by the UI."""
        return service.screen_name(req.name, req.kind, req.dob, req.nationality)

    @app.post("/screen/person")
    def screen_person(req: PersonRequest):
        t0 = time.perf_counter()
        res = service.screen_person(req.name, req.dob, req.nationality.upper() if req.nationality else None,
                                    req.customer_id, req.product)
        record("person", res, t0)
        event(log, "screen_person", hits=len(res["hits"]), rating=res["risk"]["rating"], ms=now_ms(t0))
        return res

    @app.post("/screen/business")
    def screen_business(req: BusinessRequest):
        if not req.registry_id and not req.name:
            raise HTTPException(422, "registry_id or name is required")
        t0 = time.perf_counter()
        try:
            res = service.screen_business(req.registry_id, req.name, req.product)
        except KeyError:
            raise HTTPException(404, "company not found in registry") from None
        record("business", res, t0)
        event(log, "screen_business", company=res["company"]["id"], ubos=len(res["ubos"]), hits=len(res["hits"]),
              rating=res["risk"]["rating"], ms=now_ms(t0))
        return _clean(res)

    @app.get("/api/registry")
    def registry(limit: int = 12):
        """Demo customers first (chain, cycle, nominee, opaque), then other registry companies."""
        reg = service.registry
        ids = list(reg.demo_targets) + [c for c in reg.companies if c not in reg.demo_targets
                                        and reg.companies[c].kind == "company" and reg.owners_of(c)]
        return [{"id": i, "name": reg.companies[i].name, "country": reg.companies[i].country} for i in ids[:limit]]

    @app.get("/api/business/{registry_id}/graph.svg")
    def graph(registry_id: str):
        reg = service.registry
        if registry_id not in reg.companies:
            raise HTTPException(404, "company not found in registry")
        res = service.screen_business(registry_id, persist=False)
        svg = ownership_svg(reg, registry_id, res["_ubo"], set(res["_hit_nodes"]))
        return Response(svg, media_type="image/svg+xml")

    @app.get("/review/queue")
    def queue(status: str = "open", limit: int = 100):
        return {"counts": service.store.counts(), "cases": service.store.queue(status or None, limit)}

    @app.post("/review/{case_id}/disposition")
    def dispose(case_id: int, body: Disposition):
        try:
            case = service.store.dispose(case_id, body.disposition, body.analyst, body.note)
        except KeyError:
            raise HTTPException(404, "case not found") from None
        except ValueError as e:
            raise HTTPException(409 if "closed" in str(e) else 422, str(e)) from None
        metrics.dispositions.labels(body.disposition).inc()
        metrics.open_cases.set(service.store.counts().get("open", 0))
        event(log, "disposition", case_id=case_id, disposition=body.disposition)
        return case

    @app.get("/audit")
    def audit(limit: int = 100):
        return {"chain_ok": service.store.verify_chain(), "rows": service.store.audit_log(limit)}

    @app.get("/report", response_class=HTMLResponse)
    def report():
        if not report_path.exists():
            return JSONResponse({"error": "no eval report yet; run `kycscreen eval`"}, status_code=404)
        return render_report(json.loads(report_path.read_text()))

    return app


def app_factory() -> FastAPI:
    return create_app()
