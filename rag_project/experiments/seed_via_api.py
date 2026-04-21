#!/usr/bin/env python3
"""
Seed synthetic chunks into the RAG index via the public API (/documents).
Used on AWS deployments where embed/vector ports are not exposed — we go through the ALB.

  python experiments/seed_via_api.py --alb-url "$ALB" --target-chunks 1000 \
      --output results/exp1/seed_1k.json

Chunker (from services/api/main.py): size=500 chars, overlap=50 → stride=450.
Each /documents call adds roughly (text_len / 450) chunks.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

STRIDE_CHARS = 450


def synthetic_text(num_chunks_target: int, batch_idx: int) -> str:
    lines: list[str] = []
    total = 0
    need = num_chunks_target * STRIDE_CHARS + 1200
    i = 0
    while total < need:
        line = (
            f"Section {batch_idx}-{i}: On-call policy requires acknowledgement within 15 minutes. "
            f"Escalation path {(batch_idx + i) % 7}. Release checklist item {(batch_idx + i) % 13}. "
            f"Runbook entry {(batch_idx + i) % 50}: verify dashboard at the monitoring endpoint. "
            f"Payment gateway integration uses token bucket with rate 120/min. "
        )
        lines.append(line)
        total += len(line) + 1
        i += 1
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--alb-url", default=os.environ.get("ALB", "").rstrip("/"))
    p.add_argument("--target-chunks", type=int, required=True)
    p.add_argument("--per-upload-chunks", type=int, default=200,
                   help="Approx chunks per /documents call (keeps each upload short).")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    if not args.alb_url:
        print("ERROR: --alb-url not set (or $ALB env var)", flush=True)
        return 2
    alb = args.alb_url.rstrip("/")

    print(f"seeding {alb}/documents: target={args.target_chunks} chunks, ~{args.per_upload_chunks}/upload", flush=True)

    uploaded = 0
    batch_idx = 0
    uploads: list[dict] = []
    t_start = time.perf_counter()

    with httpx.Client(timeout=args.timeout) as client:
        while uploaded < args.target_chunks:
            remaining = args.target_chunks - uploaded
            this_batch = min(args.per_upload_chunks, remaining)
            text = synthetic_text(this_batch, batch_idx)
            files = {"file": (f"synthetic_{batch_idx}.txt", text.encode("utf-8"), "text/plain")}
            t0 = time.perf_counter()
            try:
                r = client.post(f"{alb}/documents", files=files)
                r.raise_for_status()
            except httpx.HTTPError as e:
                print(f"upload {batch_idx} failed: {e}", flush=True)
                return 1
            dt_ms = (time.perf_counter() - t0) * 1000
            data = r.json()
            added = int(data.get("chunks", 0))
            uploaded += added
            uploads.append({
                "batch": batch_idx,
                "chunks_added": added,
                "cumulative": uploaded,
                "wall_ms": round(dt_ms, 1),
                "ingest_time_ms_reported": data.get("ingest_time_ms"),
            })
            print(f"  batch {batch_idx}: +{added} chunks (cumulative {uploaded}/{args.target_chunks}) in {dt_ms:.0f} ms", flush=True)
            batch_idx += 1

    total_s = time.perf_counter() - t_start
    summary = {
        "alb_url": alb,
        "target_chunks": args.target_chunks,
        "uploaded_chunks": uploaded,
        "num_uploads": batch_idx,
        "total_wall_s": round(total_s, 2),
        "chunks_per_second": round(uploaded / total_s, 2) if total_s > 0 else None,
    }
    print(json.dumps(summary, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump({**summary, "uploads": uploads}, f, indent=2)
        print(f"wrote {args.output}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
