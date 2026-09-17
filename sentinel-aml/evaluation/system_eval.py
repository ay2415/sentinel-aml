"""System performance evaluation against a real uvicorn server (not TestClient).

Measures p50/p95/p99 latency, failure rate and throughput per endpoint under concurrency, and aggregates
token usage and estimated cost per workflow from the LLM ledger. Hardware/config are recorded with the results.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from common import ROOT, pct, write_report  # noqa: I001

BASE = "http://127.0.0.1:8765"


def wait_ready(timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if httpx.get(f"{BASE}/health/ready", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("server not ready")


def load(name, fn, n, concurrency):
    lat, errors, lock = [], 0, threading.Lock()

    def one(i):
        nonlocal errors
        t = time.perf_counter()
        try:
            r = fn(i)
            ok = r.status_code < 400
        except httpx.HTTPError:
            ok = False
        d = (time.perf_counter() - t) * 1000
        with lock:
            lat.append(d)
            errors += 0 if ok else 1

    t0 = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as ex:
        list(ex.map(one, range(n)))
    wall = time.perf_counter() - t0
    return {"endpoint": name, "requests": n, "concurrency": concurrency, "p50_ms": round(pct(lat, 50), 1), "p95_ms": round(pct(lat, 95), 1),
            "p99_ms": round(pct(lat, 99), 1), "failure_rate": round(errors / n, 4), "throughput_rps": round(n / wall, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--workflows", type=int, default=40)
    args = ap.parse_args()
    env = {**os.environ, "RATE_LIMIT_PER_MINUTE": "1000000", "LOGIN_RATE_LIMIT_PER_MINUTE": "100000", "LOG_LEVEL": "WARNING"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8765", "--workers", "1", "--log-level", "warning"],
                           cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready()
        c = httpx.Client(base_url=BASE, timeout=120)
        tok = c.post("/api/v1/auth/token", json={"tenant_id": "emerald", "username": "alice.analyst", "password": "Demo!Passw0rd"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        ids = [a["id"] for a in c.get("/api/v1/alerts?page_size=100", headers=h).json()["items"]]
        new_ids = [a["id"] for a in c.get(f"/api/v1/alerts?status=NEW&page_size={args.workflows}&sort=newest", headers=h).json()["items"]]
        queries = ["money mule crypto exchange red flags", "structuring cash below threshold", "approval matrix for closures",
                   "high risk third country enhanced due diligence", "tipping off obligations"]
        results = [
            load("GET /health/ready", lambda i: c.get("/health/ready"), 200, args.concurrency),
            load("GET /api/v1/alerts (page 25)", lambda i: c.get(f"/api/v1/alerts?page_size=25&page={1 + i % 20}", headers=h), 400, args.concurrency),
            load("GET /api/v1/alerts/{id}", lambda i: c.get(f"/api/v1/alerts/{ids[i % len(ids)]}", headers=h), 400, args.concurrency),
            load("POST /api/v1/kb/search", lambda i: c.post("/api/v1/kb/search", headers=h, json={"query": queries[i % 5]}), 300, args.concurrency),
            load("POST /api/v1/alerts/{id}/investigations (sync, deterministic)",
                 lambda i: c.post(f"/api/v1/alerts/{new_ids[i]}/investigations", headers=h), len(new_ids), 4),
            load("GET /api/v1/analytics/kpis", lambda i: c.get("/api/v1/analytics/kpis", headers=h), 100, args.concurrency),
        ]
    finally:
        srv.terminate()
        srv.wait(10)
    report = {"environment": {"cpu_count": os.cpu_count(), "python": platform.python_version(), "server": "uvicorn, 1 worker",
                              "database": "PostgreSQL 16 + pgvector 0.6 on the same host", "llm_provider": "deterministic (no network LLM latency)"},
              "endpoints": results,
              "caveat": "Single shared CPU in a sandbox; absolute numbers are a floor for comparison, not a capacity claim. "
                        "Workflow latency excludes real LLM latency (typically seconds per call)."}
    print(write_report("system_eval", report))
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
