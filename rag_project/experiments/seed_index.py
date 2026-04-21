#!/usr/bin/env python3
"""
Bulk-insert synthetic chunks for Experiment 2 (index size).
Calls embed + vector services (same vectors as production).

Examples (stack running, ports published):
  pip install -r experiments/requirements-bench.txt
  python experiments/seed_index.py --reset --num-chunks 1000 --output results/preliminary/seed_1k.json

Env:
  EMBED_URL  default http://127.0.0.1:8001
  VECTOR_URL default http://127.0.0.1:8002
  QDRANT_URL optional http://127.0.0.1:6333 (for points_count in output)
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx

EMBED_URL = os.environ.get("EMBED_URL", "http://127.0.0.1:8001").rstrip("/")
VECTOR_URL = os.environ.get("VECTOR_URL", "http://127.0.0.1:8002").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
COLLECTION = os.environ.get("COLLECTION_NAME", "rag_chunks")

EMBED_BATCH = 32
UPSERT_BATCH = 128


def synthetic_chunk(i: int) -> str:
    # Vary text so embeddings are not identical (helps vector search behave realistically).
    return (
        f"Synthetic benchmark chunk {i}. "
        f"On-call policy section {i % 50}: acknowledge P1 within 15 minutes. "
        f"Escalation path {i % 7}. Release checklist item {i % 13}. " * 3
    )[:480]


def qdrant_points_count() -> int | None:
    try:
        r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION}", timeout=10.0)
        r.raise_for_status()
        data = r.json()
        return int(data.get("result", {}).get("points_count", 0))
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--num-chunks", type=int, required=True)
    p.add_argument("--reset", action="store_true", help="Call vector /admin/reset_collection first")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    out: dict = {"num_chunks_target": args.num_chunks, "embed_url": EMBED_URL, "vector_url": VECTOR_URL}

    with httpx.Client(timeout=600.0) as client:
        if args.reset:
            t0 = time.perf_counter()
            r = client.post(f"{VECTOR_URL}/admin/reset_collection")
            out["reset_http_status"] = r.status_code
            out["reset_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            if r.status_code != 200:
                out["reset_body"] = r.text[:500]
                print(json.dumps(out, indent=2))
                return 1

        t_embed0 = time.perf_counter()
        global_idx = 0
        for start in range(0, args.num_chunks, UPSERT_BATCH):
            batch_end = min(start + UPSERT_BATCH, args.num_chunks)
            texts = [synthetic_chunk(i) for i in range(start, batch_end)]
            points: list[dict] = []
            for i in range(0, len(texts), EMBED_BATCH):
                sub = texts[i : i + EMBED_BATCH]
                er = client.post(f"{EMBED_URL}/embed", json={"texts": sub})
                er.raise_for_status()
                vectors = er.json()["vectors"]
                for text, vec in zip(sub, vectors):
                    points.append(
                        {
                            "id": str(uuid.uuid4()),
                            "vector": vec,
                            "payload": {"text": text, "filename": "synthetic", "chunk_index": global_idx},
                        }
                    )
                    global_idx += 1
            ur = client.post(f"{VECTOR_URL}/points/upsert", json={"points": points})
            ur.raise_for_status()

        out["embed_upsert_total_ms"] = round((time.perf_counter() - t_embed0) * 1000, 2)
        out["points_upserted"] = global_idx

    out["qdrant_points_count"] = qdrant_points_count()
    print(json.dumps(out, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
