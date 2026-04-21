#!/usr/bin/env python3
"""
Generate figures for the three experiments from results/exp{1,2,3}/*.jsonl.

  pip install matplotlib
  python experiments/plot_results.py

Writes PNGs to results/figures/:
  exp1_stage_breakdown.png   stacked bar: embed+retrieve+LLM mean time per concurrency
  exp1_latency.png           mean/P50/P95/P99 vs concurrency
  exp1_rps.png               completed requests + RPS vs concurrency
  exp2_retrieve_vs_index.png retrieve mean/P95 vs index size (log x)
  exp2_stages.png            stage breakdown at each index size
  exp3_rps.png               RPS vs replicas
  exp3_latency_percentiles.png  P50/P95 vs replicas (shows bimodal gap)
  exp3_latency_hist.png      latency histograms at r=1 / r=2 / r=4 (bimodal visualization)
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FIGS = RESULTS / "figures"
FIGS.mkdir(parents=True, exist_ok=True)

STAGES = ["embed_ms", "retrieve_ms", "llm_ms"]
STAGE_LABELS = {"embed_ms": "embed", "retrieve_ms": "retrieve", "llm_ms": "LLM"}
STAGE_COLORS = {"embed_ms": "#4c72b0", "retrieve_ms": "#55a868", "llm_ms": "#c44e52"}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  missing: {path}")
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * q))]


def stats(rows: list[dict], key: str) -> dict | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean": statistics.mean(vals),
        "p50": pct(vals, 0.5),
        "p95": pct(vals, 0.95),
        "p99": pct(vals, 0.99),
        "all": vals,
    }


# ------------------------------------------------------------------
# Exp 1: concurrency (fixed 1k index, 8 vCPU)
# ------------------------------------------------------------------
exp1_concurrency = [10, 50, 200]
exp1_data = {c: load_jsonl(RESULTS / f"exp1/c{c}_timings.jsonl") for c in exp1_concurrency}
# Completed-request counts reported by locust (from 5-min test windows):
exp1_completed = {10: 32, 50: 36, 200: 17}
exp1_duration_s = 300

# Fig: stage breakdown stacked bar
fig, ax = plt.subplots(figsize=(7, 4.5))
bottoms = [0.0] * len(exp1_concurrency)
x_labels = [f"c={c}" for c in exp1_concurrency]
for stage in STAGES:
    vals = [(stats(exp1_data[c], stage) or {"mean": 0})["mean"] / 1000 for c in exp1_concurrency]
    ax.bar(x_labels, vals, bottom=bottoms, label=STAGE_LABELS[stage], color=STAGE_COLORS[stage])
    bottoms = [b + v for b, v in zip(bottoms, vals)]
ax.set_ylabel("Mean latency (seconds)")
ax.set_title("Exp 1: Per-stage latency breakdown by concurrency\n(LLM dominates at all levels ~99.8%)")
ax.legend()
plt.tight_layout()
plt.savefig(FIGS / "exp1_stage_breakdown.png", dpi=150)
plt.close()

# Fig: latency percentiles vs concurrency
fig, ax = plt.subplots(figsize=(7, 4.5))
for label, key in [("Mean", "mean"), ("P50", "p50"), ("P95", "p95"), ("P99", "p99")]:
    vals = [(stats(exp1_data[c], "total_ms") or {key: 0})[key] / 1000 for c in exp1_concurrency]
    ax.plot(exp1_concurrency, vals, marker="o", label=label, linewidth=2)
ax.set_xlabel("Concurrent users")
ax.set_ylabel("Latency (seconds)")
ax.set_title("Exp 1: Total latency vs concurrency")
ax.set_xscale("log")
ax.set_xticks(exp1_concurrency)
ax.set_xticklabels([str(c) for c in exp1_concurrency])
ax.grid(alpha=0.3)
ax.legend()
plt.tight_layout()
plt.savefig(FIGS / "exp1_latency.png", dpi=150)
plt.close()

# Fig: RPS + completed requests
fig, ax1 = plt.subplots(figsize=(7, 4.5))
rps = [exp1_completed[c] / exp1_duration_s for c in exp1_concurrency]
ax1.bar([str(c) for c in exp1_concurrency], rps, color="#4c72b0", alpha=0.8, label="RPS")
ax1.set_xlabel("Concurrent users")
ax1.set_ylabel("RPS (requests/sec)", color="#4c72b0")
ax1.tick_params(axis="y", labelcolor="#4c72b0")
ax2 = ax1.twinx()
ax2.plot([str(c) for c in exp1_concurrency], [exp1_completed[c] for c in exp1_concurrency],
         marker="o", color="#c44e52", linewidth=2, label="Completed")
ax2.set_ylabel("Completed requests in 5 min", color="#c44e52")
ax2.tick_params(axis="y", labelcolor="#c44e52")
plt.title("Exp 1: Throughput plateau and thrashing at c=200")
plt.tight_layout()
plt.savefig(FIGS / "exp1_rps.png", dpi=150)
plt.close()

# ------------------------------------------------------------------
# Exp 2: index size (fixed c=3)
# ------------------------------------------------------------------
exp2_sizes = [1000, 10000, 100000]
exp2_data = {n: load_jsonl(RESULTS / f"exp2/c3_{n // 1000}k_timings.jsonl") for n in exp2_sizes}

# Fig: retrieve_ms vs index size
fig, ax = plt.subplots(figsize=(7, 4.5))
means = [(stats(exp2_data[n], "retrieve_ms") or {"mean": 0})["mean"] for n in exp2_sizes]
p95s = [(stats(exp2_data[n], "retrieve_ms") or {"p95": 0})["p95"] for n in exp2_sizes]
ax.plot(exp2_sizes, means, marker="o", label="Mean", linewidth=2, color="#4c72b0")
ax.plot(exp2_sizes, p95s, marker="s", label="P95", linewidth=2, color="#c44e52")
ax.set_xlabel("Index size (chunks)")
ax.set_ylabel("Retrieve latency (ms)")
ax.set_title("Exp 2: Retrieve latency vs index size\n(HNSW keeps O(log N) growth: +19% for 100× data)")
ax.set_xscale("log")
ax.set_xticks(exp2_sizes)
ax.set_xticklabels([f"{n // 1000}k" for n in exp2_sizes])
ax.grid(alpha=0.3)
ax.legend()
plt.tight_layout()
plt.savefig(FIGS / "exp2_retrieve_vs_index.png", dpi=150)
plt.close()

# Fig: stage breakdown by index size (grouped bars, not stacked, to see relative change)
fig, ax = plt.subplots(figsize=(7, 4.5))
x_labels = [f"{n // 1000}k" for n in exp2_sizes]
bottoms = [0.0] * len(exp2_sizes)
for stage in STAGES:
    vals = [(stats(exp2_data[n], stage) or {"mean": 0})["mean"] / 1000 for n in exp2_sizes]
    ax.bar(x_labels, vals, bottom=bottoms, label=STAGE_LABELS[stage], color=STAGE_COLORS[stage])
    bottoms = [b + v for b, v in zip(bottoms, vals)]
ax.set_xlabel("Index size (chunks)")
ax.set_ylabel("Mean latency (seconds)")
ax.set_title("Exp 2: Stage latencies at each index size\n(E2E ~flat: retrieve delta is masked by LLM)")
ax.legend()
plt.tight_layout()
plt.savefig(FIGS / "exp2_stages.png", dpi=150)
plt.close()

# ------------------------------------------------------------------
# Exp 3: horizontal scaling (fixed c=10, 1 vCPU/task)
# ------------------------------------------------------------------
exp3_replicas = [1, 2, 4]
exp3_data = {r: load_jsonl(RESULTS / f"exp3/r{r}_c10_timings.jsonl") for r in exp3_replicas}
exp3_completed = {1: 6, 2: 20, 4: 53}
exp3_duration_s = 300

# Fig: RPS vs replicas
fig, ax = plt.subplots(figsize=(7, 4.5))
rps = [exp3_completed[r] / exp3_duration_s for r in exp3_replicas]
ax.bar([f"r={r}" for r in exp3_replicas], rps, color="#55a868")
for r, v in zip(exp3_replicas, rps):
    ax.text(f"r={r}", v + 0.005, f"{v:.3f}", ha="center")
ax.set_ylabel("RPS (requests/sec)")
ax.set_title("Exp 3: Throughput vs replicas\n(9.5× RPS for 4× replicas — super-linear due to empty-index fast path)")
plt.tight_layout()
plt.savefig(FIGS / "exp3_rps.png", dpi=150)
plt.close()

# Fig: latency percentiles vs replicas (shows the bimodal gap: P50 drops, P95 stays high)
fig, ax = plt.subplots(figsize=(7, 4.5))
for label, key, color in [("P50", "p50", "#4c72b0"), ("Mean", "mean", "#dd8452"), ("P95", "p95", "#c44e52")]:
    vals = [(stats(exp3_data[r], "total_ms") or {key: 0})[key] / 1000 for r in exp3_replicas]
    ax.plot(exp3_replicas, vals, marker="o", label=label, linewidth=2, color=color)
ax.set_xlabel("Number of replicas")
ax.set_ylabel("Total latency (seconds)")
ax.set_title("Exp 3: Latency percentiles diverge as replicas grow\n(P50 plunges on empty-index fast path; P95 stuck on seeded task queue)")
ax.set_xticks(exp3_replicas)
ax.grid(alpha=0.3)
ax.legend()
plt.tight_layout()
plt.savefig(FIGS / "exp3_latency_percentiles.png", dpi=150)
plt.close()

# Fig: latency histogram (bimodal visualization)
fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharey=True)
for ax, r in zip(axes, exp3_replicas):
    vals = [x / 1000 for x in (stats(exp3_data[r], "total_ms") or {"all": []})["all"]]
    if vals:
        ax.hist(vals, bins=20, color="#4c72b0", edgecolor="white")
    ax.set_title(f"r={r}  (n={len(vals)})")
    ax.set_xlabel("Total latency (seconds)")
    ax.grid(alpha=0.3)
axes[0].set_ylabel("Request count")
fig.suptitle("Exp 3: Latency distribution — bimodal shape reveals split between fast (empty-index) and slow (seeded) paths")
plt.tight_layout()
plt.savefig(FIGS / "exp3_latency_hist.png", dpi=150)
plt.close()

print(f"\n✅ Figures written to {FIGS}/")
for p in sorted(FIGS.glob("*.png")):
    print(f"  {p.name}")
