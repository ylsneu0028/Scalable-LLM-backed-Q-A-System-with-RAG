#!/usr/bin/env python3
"""
Experiment 1: concurrent POST /query on the API (full RAG: embed + vector + LLM).

  python experiments/loadtest_query.py --concurrency 5 --total 30 --output results/preliminary/exp1_c5.json

Requires full stack (Compose): API calls the LLM service, which uses Ollama on the host.
Uses varied questions to reduce accidental caching.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000").rstrip("/")


def questions(n: int) -> list[str]:
    base = [
        "What is the on-call policy for P1 incidents?",
        "How long do we have to acknowledge a P1?",
        "When does the on-call rotation reset?",
        "Who do we escalate database issues to?",
        "What is the release process for production?",
        "Where is rollback documented?",
    ]
    return [base[i % len(base)] + f" (qidx={i})" for i in range(n)]


async def run_load(
    concurrency: int,
    total: int,
    timeout: float,
) -> dict:
    qs = questions(max(total, concurrency * 2))
    lat_ok_ms: list[float] = []
    errors = 0

    sem = asyncio.Semaphore(concurrency)

    async def one(client: httpx.AsyncClient, idx: int) -> None:
        nonlocal errors
        q = qs[idx % len(qs)]
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.post(f"{API_URL}/query", json={"question": q}, timeout=timeout)
                dt = (time.perf_counter() - t0) * 1000
                if r.status_code == 200:
                    lat_ok_ms.append(dt)
                else:
                    errors += 1
            except Exception:
                errors += 1

    async with httpx.AsyncClient() as client:
        tasks = [one(client, i) for i in range(total)]
        wall0 = time.perf_counter()
        await asyncio.gather(*tasks)
        wall_s = time.perf_counter() - wall0

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
        return xs[k]

    ok = len(lat_ok_ms)
    return {
        "api_url": API_URL,
        "concurrency": concurrency,
        "total_requests": total,
        "success": ok,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "duration_wall_s": round(wall_s, 3),
        "throughput_rps": round(ok / wall_s, 3) if wall_s > 0 else 0.0,
        "latency_ms": {
            "mean": round(statistics.mean(lat_ok_ms), 3) if lat_ok_ms else None,
            "p95": round(pct(lat_ok_ms, 95), 3) if lat_ok_ms else None,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--total", type=int, default=30, help="Total /query calls")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    out = asyncio.run(run_load(args.concurrency, args.total, args.timeout))
    print(json.dumps(out, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
