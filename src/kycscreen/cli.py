"""Command line: eval, gate, bench, screen, business, rescreen, gen, serve."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .data import evolve_watchlist
from .delta import CustomerIndex, delta_rescreen, full_rescreen
from .evaluate import build_dataset, full_eval, metrics_at, run_engine
from .report import render_report, summary_rows
from .service import KYCService, Settings


def _pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def cmd_eval(args) -> int:
    settings = Settings.load()
    ds = build_dataset(args.customers, scale=args.customers / 20_000)
    if args.gate:
        run = run_engine(ds)
        m = metrics_at(ds, run, settings.threshold)
        ok = m["recall"] >= settings.target_recall
        print(f"recall at threshold {settings.threshold:g}: {m['recall']:.4f} (target {settings.target_recall}), "
              f"{m['alerts_per_1000']:.1f} alerts per 1,000, blocking recall {run.extra['blocking_recall']:.4f}")
        print("GATE PASS" if ok else "GATE FAIL: recall below target")
        return 0 if ok else 1
    res = full_eval(ds, settings.target_recall, settings.threshold)
    print(f"{res['customers']:,} customers, {res['positives']} true matches, {res['namesakes']} namesakes, "
          f"{res['watchlist_entries']:,} watchlist entries\n")
    print(f"{'method':48} {'thresh':>6} {'recall':>7} {'prec':>7} {'alerts/1k':>9}")
    for name, p in summary_rows(res):
        print(f"{name:48} {p['threshold']:6g} {_pct(p['recall']):>7} {_pct(p['precision']):>7} {p['alerts_per_1000']:9.1f}")
    e = res["engine"]
    print(f"\nengine: {e['names_per_second']:,.0f} names/s, blocking recall {e['blocking_recall']:.2%}, "
          f"{e['avg_candidates']} candidates per customer of {e['index_names']:,} names "
          f"(reduction {e['reduction_ratio']:.2%})")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval.json").write_text(json.dumps(res, indent=1))
    (out / "tradeoff.html").write_text(render_report(res))
    print(f"wrote {out / 'eval.json'} and {out / 'tradeoff.html'}")
    return 0


def cmd_bench(args) -> int:
    ds = build_dataset(args.customers, scale=args.customers / 20_000)
    run = run_engine(ds)
    print(json.dumps({"customers": len(ds.customers), "seconds": round(run.seconds, 2), **run.extra}, indent=1))
    return 0


def cmd_screen(args) -> int:
    svc = KYCService()
    res = svc.screen_name(args.name, args.kind, args.dob, args.nationality)
    print(f"normalized: {' '.join(res['normalized'])}   threshold {res['threshold']:g}")
    for c in res["candidates"][: args.top]:
        flag = "ALERT" if c["alert"] else "     "
        parts = ", ".join(f"{x['feature']} {x['points']:+.1f}" for x in c["contributions"])
        print(f"{flag} {c['score']:6.2f}  {c['entry_id']}  {c['primary_name']:<36} ({c['list_type']})  {parts}")
    return 0


def cmd_business(args) -> int:
    svc = KYCService()
    res = svc.screen_business(args.registry_id, persist=False)
    print(f"{res['company']['name']} ({res['company']['id']}, {res['company']['country']})  risk: {res['risk']['rating'].upper()} "
          f"({res['risk']['points']} points)")
    for line in res["explanation"]:
        print(f"  - {line}")
    for u in res["ubos"]:
        for p in u["paths"]:
            print(f"    path: {p['explanation']}")
    return 0


def cmd_rescreen(args) -> int:
    settings = Settings.load()
    ds = build_dataset(args.customers, scale=args.customers / 20_000)
    t = settings.threshold
    print(f"baseline screen of {len(ds.customers):,} customers against {len(ds.watchlist):,} entries...")
    prev, t_prev = full_rescreen(ds.customers, ds.watchlist, ds.freq, t)
    new_wl, change = evolve_watchlist(ds.watchlist, args.add, args.modify, args.remove)
    print(f"list update: {len(change['added'])} added, {len(change['modified'])} amended, {len(change['removed'])} removed")
    full, t_full = full_rescreen(ds.customers, new_wl, ds.freq, t)
    t_i = time.perf_counter()
    cindex = CustomerIndex(ds.customers, ds.freq)
    t_index = time.perf_counter() - t_i
    delta, stats = delta_rescreen(prev, ds.customers, new_wl, change, ds.freq, t, cindex)
    same = delta == full
    print(f"full rescreen:  {t_full:6.2f}s, {len(ds.customers):,} customers screened")
    print(f"delta rescreen: {stats['seconds']:6.2f}s, {stats['customers_rechecked']:,} customers rechecked "
          f"(customer index built once in {t_index:.2f}s)")
    print(f"speedup {t_full / max(stats['seconds'], 1e-6):.0f}x; new alerts {len(stats['new_alerts'])}, "
          f"cleared {len(stats['cleared_alerts'])}; identical to full rescreen: {same}")
    return 0 if same else 1


def cmd_gen(args) -> int:
    from .kyb import build_registry, registry_to_dict
    ds = build_dataset(args.customers, scale=args.customers / 20_000)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "watchlist.jsonl").open("w") as f:
        for e in ds.watchlist:
            f.write(json.dumps(e.to_dict()) + "\n")
    with (out / "customers.jsonl").open("w") as f:
        for c in ds.customers:
            f.write(json.dumps(c.to_dict()) + "\n")
    (out / "registry.json").write_text(json.dumps(registry_to_dict(build_registry(ds.watchlist)), indent=1))
    print(f"wrote {len(ds.watchlist):,} entries, {len(ds.customers):,} customers, and the registry to {out}/")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("kycscreen.api:app_factory", factory=True, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kycscreen", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="threshold sweep with baselines, writes reports/")
    e.add_argument("--gate", action="store_true", help="engine only; exit 1 if recall at the configured threshold is below target")
    e.add_argument("--customers", type=int, default=20_000)
    e.add_argument("--out", default="reports")
    e.set_defaults(fn=cmd_eval)

    b = sub.add_parser("bench", help="throughput and blocking recall")
    b.add_argument("--customers", type=int, default=20_000)
    b.set_defaults(fn=cmd_bench)

    s = sub.add_parser("screen", help="screen one name and show score breakdowns")
    s.add_argument("name")
    s.add_argument("--kind", default="person", choices=["person", "entity"])
    s.add_argument("--dob")
    s.add_argument("--nationality")
    s.add_argument("--top", type=int, default=5)
    s.set_defaults(fn=cmd_screen)

    k = sub.add_parser("business", help="KYB analysis for a registry company")
    k.add_argument("registry_id")
    k.set_defaults(fn=cmd_business)

    r = sub.add_parser("rescreen", help="watchlist update: delta versus full rescreen")
    r.add_argument("--customers", type=int, default=20_000)
    r.add_argument("--add", type=int, default=40)
    r.add_argument("--modify", type=int, default=25)
    r.add_argument("--remove", type=int, default=15)
    r.set_defaults(fn=cmd_rescreen)

    g = sub.add_parser("gen", help="write the synthetic data to disk as JSON")
    g.add_argument("--customers", type=int, default=20_000)
    g.add_argument("--out", default="data")
    g.set_defaults(fn=cmd_gen)

    v = sub.add_parser("serve", help="run the API and web UI")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    t0 = time.perf_counter()
    rc = args.fn(args)
    if args.cmd not in ("serve", "screen", "business"):
        print(f"done in {time.perf_counter() - t0:.1f}s", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
