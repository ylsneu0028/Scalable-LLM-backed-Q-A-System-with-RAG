#!/usr/bin/env python3
"""
Experiment 2 (retrieval leg): time embed(question) + vector search only (no LLM).

  python experiments/bench_retrieval.py --samples 30 --output results/preliminary/exp2_retrieval.json

Env: EMBED_URL, VECTOR_URL (same defaults as seed_index.py)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import httpx

EMBED_URL = os.environ.get("EMBED_URL", "http://127.0.0.1:8001").rstrip("/")
VECTOR_URL = os.environ.get("VECTOR_URL", "http://127.0.0.1:8002").rstrip("/")
TOP_K = int(os.environ.get("TOP_K", "5"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=30)
    p.add_argument("--question", type=str, default="What is the on-call policy for P1 incidents?")
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--label", type=str, default="", help="e.g. 1k or 10k for your table")
    args = p.parse_args()

    embed_ms: list[float] = []
    search_ms: list[float] = []
    total_ms: list[float] = []

    with httpx.Client(timeout=120.0) as client:
        for i in range(args.samples):
            q = f"{args.question} (run {i})"
            t0 = time.perf_counter()
            er = client.post(f"{EMBED_URL}/embed", json={"texts": [q]})
            er.raise_for_status()
            vec = er.json()["vectors"][0]
            t1 = time.perf_counter()
            sr = client.post(
                f"{VECTOR_URL}/points/search",
                json={"vector": vec, "limit": TOP_K},
            )
            sr.raise_for_status()
            t2 = time.perf_counter()
            embed_ms.append((t1 - t0) * 1000)
            search_ms.append((t2 - t1) * 1000)
            total_ms.append((t2 - t0) * 1000)

    def pct(xs: list[float], p: float) -> float:
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
        return xs[k]

    out = {
        "label": args.label,
        "samples": args.samples,
        "question_template": args.question,
        "embed_ms": {
            "mean": round(statistics.mean(embed_ms), 3),
            "p95": round(pct(embed_ms, 95), 3),
        },
        "search_ms": {
            "mean": round(statistics.mean(search_ms), 3),
            "p95": round(pct(search_ms, 95), 3),
        },
        "retrieve_total_ms": {
            "mean": round(statistics.mean(total_ms), 3),
            "p95": round(pct(total_ms, 95), 3),
        },
    }
    print(json.dumps(out, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
