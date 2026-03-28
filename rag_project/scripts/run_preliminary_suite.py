#!/usr/bin/env python3
"""
Run a small preliminary matrix (shorter than final experiments) and write JSON + CSV summary.

  cd rag_project
  pip install -r scripts/requirements-bench.txt
  python scripts/run_preliminary_suite.py

Tune INDEX_SIZES or CONCURRENCY_LEVELS inside this file if runs are too slow.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
RESULTS = ROOT / "results" / "preliminary"

# Smaller than final report targets so a laptop can finish in reasonable time.
INDEX_SIZES = [1000, 5000]  # add 10000, 100000 when you have time
CONCURRENCY_LEVELS = [3, 8]  # add 10, 50, 200 for final


def run(cmd: list[str], env: dict) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def main() -> int:
    env = os.environ.copy()
    # Defaults for host-run scripts against published compose ports
    env.setdefault("EMBED_URL", "http://127.0.0.1:8001")
    env.setdefault("VECTOR_URL", "http://127.0.0.1:8002")
    env.setdefault("API_URL", "http://127.0.0.1:8000")
    env.setdefault("QDRANT_URL", "http://127.0.0.1:6333")
    env.setdefault("COLLECTION_NAME", "rag_chunks")

    RESULTS.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []

    py = sys.executable

    # --- Exp 2: index size (retrieval microbench) ---
    for n in INDEX_SIZES:
        seed_path = RESULTS / f"seed_{n}.json"
        run(
            [py, str(SCRIPTS / "seed_index.py"), "--reset", "--num-chunks", str(n), "--output", str(seed_path)],
            env,
        )
        retr_path = RESULTS / f"exp2_retrieval_{n}.json"
        run(
            [
                py,
                str(SCRIPTS / "bench_retrieval.py"),
                "--samples",
                "25",
                "--label",
                str(n),
                "--output",
                str(retr_path),
            ],
            env,
        )
        retr = json.loads(retr_path.read_text())
        summary_rows.append(
            {
                "experiment": "exp2_index_size",
                "index_chunks": n,
                "retrieve_mean_ms": retr["retrieve_total_ms"]["mean"],
                "retrieve_p95_ms": retr["retrieve_total_ms"]["p95"],
            }
        )

        # Light E2E sample (LLM-heavy; keep small)
        e2e_path = RESULTS / f"exp2_e2e_{n}.json"
        run(
            [
                py,
                str(SCRIPTS / "loadtest_query.py"),
                "--concurrency",
                "2",
                "--total",
                "6",
                "--output",
                str(e2e_path),
            ],
            env,
        )
        e2e = json.loads(e2e_path.read_text())
        summary_rows[-1]["e2e_mean_ms"] = e2e["latency_ms"]["mean"]
        summary_rows[-1]["e2e_p95_ms"] = e2e["latency_ms"]["p95"]

    # --- Exp 1: concurrency (same index as last seed state) ---
    for c in CONCURRENCY_LEVELS:
        out_path = RESULTS / f"exp1_concurrency_{c}.json"
        total = max(20, c * 4)
        run(
            [
                py,
                str(SCRIPTS / "loadtest_query.py"),
                "--concurrency",
                str(c),
                "--total",
                str(total),
                "--output",
                str(out_path),
            ],
            env,
        )
        data = json.loads(out_path.read_text())
        summary_rows.append(
            {
                "experiment": "exp1_concurrency",
                "concurrency": c,
                "total": total,
                "success": data["success"],
                "errors": data["errors"],
                "error_rate": data["error_rate"],
                "mean_ms": data["latency_ms"]["mean"],
                "p95_ms": data["latency_ms"]["p95"],
                "rps": data["throughput_rps"],
            }
        )

    # --- Exp 3 placeholder note ---
    exp3_note = {
        "experiment": "exp3_horizontal_scaling",
        "status": "not_run_in_preliminary",
        "reason": "Needs duplicate api/embed/vector services behind a load balancer (e.g. nginx) and compose scale.",
        "planned_instances": [1, 2, 4],
        "metrics": ["throughput_rps", "mean_ms", "p95_ms"],
    }
    (RESULTS / "exp3_placeholder.json").write_text(json.dumps(exp3_note, indent=2), encoding="utf-8")

    csv_path = RESULTS / "preliminary_summary.csv"
    if summary_rows:
        keys: list[str] = []
        for row in summary_rows:
            for k in row:
                if k not in keys:
                    keys.append(k)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(summary_rows)

    readme = RESULTS / "README.txt"
    readme.write_text(
        "Preliminary benchmark outputs for your report.\n"
        "- exp2_*: after seed_index, retrieval + small E2E sample\n"
        "- exp1_*: concurrency sweep on POST /query\n"
        "- exp3_placeholder.json: document what you will run for final horizontal scaling\n"
        "Paste numbers into your report; keep JSON files as appendix evidence.\n",
        encoding="utf-8",
    )

    print(f"\nWrote: {csv_path} and JSON under {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
