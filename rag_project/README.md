# Scalable LLM-backed Q&A System with RAG

A Retrieval-Augmented Generation (RAG) question-answering service built as a CS6650 final project (Yalin Sun, solo team). Users upload text / markdown documents, ask natural-language questions, and get answers grounded in the retrieved passages. The system is decomposed into independently deployable services so that each tier can be scaled — or identified as a bottleneck — on its own.

## Why this project

Teams routinely store knowledge in internal docs. Keyword search breaks down when the question is phrased differently from the source, or when the answer lives across several documents. LLMs can bridge that gap, but a naive single-process "ask GPT on the whole corpus" approach doesn't scale: the corpus grows, many users ask concurrently, and LLM inference is expensive. This project builds a RAG pipeline the way a real distributed system would — with explicit service boundaries, per-stage observability, and empirical scaling experiments — and then uses load tests to show exactly **where** the system breaks and **why**.

The experiments answer three questions a production team would actually ask:
- How many concurrent users can one instance handle before tail latency explodes?
- Does retrieval latency degrade as the knowledge base grows from 1k to 100k chunks?
- Does horizontal scaling of the bottleneck tier recover throughput?

## Architecture

Four logical services behind an API gateway, plus a vector DB and an LLM runtime:

| Service | Port | Role |
|--------|------|------|
| `api` | 8000 | Upload / query orchestration; calls embed → vector → llm |
| `embed` | 8001 | `sentence-transformers/all-MiniLM-L6-v2` — 384-dim embeddings |
| `vector` | 8002 | Qdrant client wrapper: upsert + HNSW similarity search |
| `llm` | 8003 | Thin HTTP wrapper around Ollama's `/api/generate` |
| `qdrant` | 6333 | Vector database (HNSW index) |
| `ollama` | 11434 | Actual LLM inference (`llama3.2:1b`) |

Query flow: `POST /query` → embed question → vector search top-K → build prompt with retrieved context → LLM generates answer. Every response includes `timings_ms = {embed, retrieve, llm, total}` for per-stage observability.

```
              ┌────────┐
              │  api   │
              └───┬────┘
        ┌─────────┼──────────┐
        ▼         ▼          ▼
    ┌──────┐ ┌────────┐  ┌──────┐
    │embed │ │ vector │  │ llm  │
    └──────┘ └───┬────┘  └───┬──┘
                 ▼           ▼
             ┌───────┐   ┌────────┐
             │qdrant │   │ ollama │
             └───────┘   └────────┘
```

## Project journey

This project shipped in three phases over the course. Each phase added a new axis of realism.

**Phase 1 — Single-machine proof (Assignment 9, March 2026).**
Built the end-to-end pipeline on one laptop with Docker Compose. Validated `/documents` ingestion, `/query` retrieval, and LLM generation manually. Wrote a preliminary benchmark suite (`experiments/run_preliminary_suite.py`) and produced early data at 1k / 5k chunks, concurrency 3 / 8. Results under `results/preliminary/`. Key takeaway at this stage: LLM inference was already dominating total latency (~95%+) even at low concurrency, foreshadowing the final conclusion.

**Phase 2 — AWS deployment on ECS Fargate (April 2026).**
Productionized the stack onto AWS with Terraform: VPC, ALB, ECR, ECS Fargate (single task, multi-container, including Ollama). Wrote `deploy/aws/build-and-push.sh` to rebuild images and force a new ECS deployment in one step. This phase uncovered two non-trivial bugs that are instructive enough to document here:

- **Ollama entrypoint override.** The official `ollama/ollama` image has `ENTRYPOINT ["ollama"]`. Setting only ECS `command = ["/bin/sh", "-c", <script>]` produces `ollama /bin/sh -c "..."`, and the CLI rejects `/bin/sh` as an unknown subcommand. Fix: override `entryPoint = ["/bin/sh", "-c"]` at the container level (see `terraform/ecs.tf`).
- **Terraform heredoc `$` escaping.** In Terraform templates, `$${` escapes to `${`, but a bare `$$` is **not** converted to a literal `$` — it passes through unchanged. A shell script written with `PID=$$!` in a heredoc therefore sends `$$!` (shell's PID followed by `!`) to the container, producing cryptic "Syntax error: `(` unexpected" on lines using `$$((i+1))`. Fix: use single `$` throughout, since Terraform only rewrites `${…}` patterns (see `terraform/locals.tf`).

**Phase 3 — Three scalability experiments (April 2026).**
With the AWS deployment healthy, ran the three experiments from the proposal end-to-end on Fargate. To run these against the public ALB (which only exposes the `api` port), added `experiments/seed_via_api.py` so the corpus is built through the `/documents` endpoint instead of the internal embed/vector ports. Enhanced `experiments/locustfile.py` to dump per-request `timings_ms` to a JSONL file so we can analyze stage-level latency after each run. Produced the plots in `results/figures/` via `experiments/plot_results.py`.

## Run locally (Docker Compose)

Prereq: install [Ollama](https://ollama.com) and pull a small model — Ollama runs on the Mac host, not inside Compose:

```bash
ollama pull llama3.2:1b
```

Then from `rag_project/`:

```bash
docker compose up --build
```

First build downloads PyTorch + `sentence-transformers` and the model — several minutes. Try it:

```bash
curl -F "file=@sample.md" http://localhost:8000/documents
curl -s -X POST http://localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question":"What is this document about?"}' | python3 -m json.tool
```

`timings_ms` in the response is the per-stage observability.

### Troubleshooting

- **Empty body on `/query`** — Ollama probably isn't running, or the `llm` container can't reach it. Check `curl -s http://localhost:8003/health` and `docker compose exec llm python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:200])"`.
- **`QdrantClient has no attribute search`** — rebuild the vector image: `docker compose build --no-cache vector`. `services/vector/requirements.txt` pins `qdrant-client` 1.12.x to match the server image.

## Deploy on AWS (Terraform)

One ECS Fargate task running all six containers (co-located on the same task network so everything speaks `127.0.0.1`). Infrastructure-as-code under `terraform/`.

```bash
cd terraform && terraform init && terraform apply
cd .. && ./deploy/aws/build-and-push.sh
export ALB=$(terraform -chdir=terraform output -raw alb_url)
curl -sf $ALB/health
```

Sizing is controlled via `terraform.tfvars`. Defaults are 8 vCPU / 60 GiB per task. On AWS Academy / Learner Lab sandbox accounts (default Fargate vCPU quota = 6, cannot be raised), Exp 3 requires downsizing:

```hcl
# terraform.tfvars (Exp 3 config)
task_cpu      = 1024     # 1 vCPU
task_memory   = 4096     # 4 GiB
desired_count = 1        # ramp to 2 / 4 during the experiment
```

Scale during the experiment: `terraform apply -var="desired_count=2"` and wait with `aws ecs wait services-stable`.

Full list of variables: `terraform/variables.tf`. Ongoing cost is dominated by the Fargate task (~$0.40–$0.80/hr depending on size). Remember to `terraform destroy` between sessions.

## Experiments

All three experiments ran on ECS Fargate with the stack described above. Every experiment captures per-request stage timings (`embed_ms`, `retrieve_ms`, `llm_ms`, `total_ms`) through the enhanced locustfile, so the stage breakdown is authoritative. Aggregated stats come from Locust's HTML report.

### Experiment 1 — Concurrency (fixed 1k index, 8 vCPU task)

Vary concurrent users at {10, 50, 200}. Five-minute steady window each. Index seeded to 1k chunks once.

| Metric | c=10 | c=50 | c=200 |
|---|---|---|---|
| Completed requests (5 min) | 32 | 36 | 17 |
| Mean total latency (s) | 77 | 148 | 227 |
| P95 total latency (s) | 106 | 277 | 296 |
| RPS | 0.11 | 0.12 | **0.06** |
| Failures | 0 | 0 | 0 |

Stage breakdown (mean ms):

| Stage | c=10 | c=50 | c=200 |
|---|---|---|---|
| embed | 120 | 169 | 359 |
| retrieve | 29 | 82 | 294 |
| **llm** | **76,850** | **147,171** | **226,091** |
| total | 76,999 | 147,422 | 226,744 |

Throughput plateaus at ~0.12 RPS around c=50 and **decreases** at c=200 (thrashing). LLM time accounts for 99.8 % of the total at every concurrency level. Zero timeouts is a bit misleading: at c=200 roughly 180 requests were still in-flight when the 5-min window ended and are not counted in Locust's stats — only the 17 that made it to completion are.

Figures: `results/figures/exp1_stage_breakdown.png`, `exp1_latency.png`, `exp1_rps.png`.

### Experiment 2 — Index size (fixed c=3, 8 vCPU task)

Cumulative seeding to 1k → 10k → 100k chunks (the vector service's `/admin/reset_collection` is disabled behind the ALB, so each size is reached by adding to the previous). Three-concurrent-user bench for 2 minutes per size — low concurrency keeps queueing effects out of the measurement so the remaining variation reflects index-size only.

| Metric | 1k | 10k | 100k |
|---|---|---|---|
| retrieve mean (ms) | 26 | 26 | 31 |
| retrieve P95 (ms) | 31 | 31 | 42 |
| total mean (s) | 22.5 | 26.7 | 24.4 |

Retrieve latency grows only 19 % (mean) / 35 % (P95) for a 100× increase in data size — consistent with HNSW's O(log N) search complexity. End-to-end latency is dominated by LLM generation, which varies ±18 % independently of index size, so total time stays roughly flat.

Memory footprint is computed theoretically: `N chunks × 384 dims × 4 bytes`. At 100k that's ~150 MB of vector data, plus HNSW graph overhead ~300 MB total — negligible compared to Fargate's 60 GiB.

Figures: `results/figures/exp2_retrieve_vs_index.png`, `exp2_stages.png`.

### Experiment 3 — Horizontal scaling (fixed c=10, 1 vCPU tasks)

To fit 4 replicas under the sandbox 6-vCPU Fargate quota, each task was downsized to 1 vCPU / 4 GiB. Index seeded to 1k chunks before any scaling, so only the initial task has populated qdrant storage; new tasks spun up by scaling have empty qdrant (per-task ephemeral storage).

| Metric | r=1 | r=2 | r=4 |
|---|---|---|---|
| Completed (5 min) | 6 | 20 | 53 |
| Mean total (s) | 155 | 56 | 21 |
| P50 total (s) | 170 | 9.5 | 4.0 |
| P95 total (s) | 260 | 291 | 172 |
| RPS | 0.02 | 0.07 | **0.19** |

RPS scales **9.5×** for **4×** replicas — super-linear, but partly an artifact of data asymmetry. The latency distribution at r=2 and r=4 is visibly **bimodal**: requests routed by the ALB to the original task (with populated qdrant) follow the real RAG path and take tens of seconds in a queue; requests routed to the new, empty-qdrant replicas retrieve nothing, and the LLM quickly returns "I do not know" in ~4 s. The drop in P50 (170 → 4 s) is mostly this fast path; P95 (172 s at r=4) still reflects the slow path on the seeded replica.

This asymmetry is the most interesting architectural finding: **stateless-compute horizontal scaling alone is not enough** when the service carries local state. True even distribution requires decoupling the vector index — a shared managed Qdrant, an EFS-mounted storage volume, or a transactional vector DB.

Figures: `results/figures/exp3_rps.png`, `exp3_latency_percentiles.png`, `exp3_latency_hist.png` (the histogram makes the bimodal shape unmistakable).

### Cross-experiment conclusions

1. **LLM inference is the bottleneck** (Exp 1, 99.8 % of total latency at every concurrency level).
2. **Retrieval is not a bottleneck** (Exp 2, +19 % retrieve for 100× data). Embedding-model inference under load is a *secondary* issue (Exp 1 c=200 showed embed 3× slower, retrieve 10× slower vs c=10).
3. **Horizontal scaling works for throughput but exposes state coupling** (Exp 3). Scaling LLM compute is the right target, but the vector DB has to move out of per-task ephemeral storage first.

A production version would deploy (a) external managed Qdrant (or equivalent) shared across replicas and (b) multiple replicas of the API/llm/embed tier behind the same ALB — the present Terraform already sets up the ALB target group; only the storage decoupling is missing.

## Running the experiments yourself

```bash
pip install -r experiments/requirements-bench.txt
export ALB=$(terraform -chdir=terraform output -raw alb_url)

# Experiment 1: seed once, then 3 concurrency levels
python3 experiments/seed_via_api.py --alb-url $ALB --target-chunks 1000 --output results/exp1/seed_1k.json
for c in 10 50 200; do
  LOCUST_TIMINGS_FILE=results/exp1/c${c}_timings.jsonl \
  locust -f experiments/locustfile.py --host $ALB --headless \
    -u $c -r $((c/5)) -t 5m \
    --html results/exp1/c${c}_report.html --csv results/exp1/c${c}
done

# Experiment 2: cumulative seed + 3 bench runs
for delta in 1000 9000 90000; do
  python3 experiments/seed_via_api.py --alb-url $ALB --target-chunks $delta \
    --output results/exp2/seed_${delta}.json
  LOCUST_TIMINGS_FILE=results/exp2/bench.jsonl \
  locust -f experiments/locustfile.py --host $ALB --headless \
    -u 3 -r 1 -t 2m --html results/exp2/report.html
done

# Experiment 3: scale through 1/2/4 and bench each
for r in 1 2 4; do
  terraform -chdir=terraform apply -auto-approve -var="desired_count=$r"
  aws ecs wait services-stable --cluster rag-qa-cluster --services rag-qa-svc --region us-west-2
  LOCUST_TIMINGS_FILE=results/exp3/r${r}_c10_timings.jsonl \
  locust -f experiments/locustfile.py --host $ALB --headless \
    -u 10 -r 2 -t 5m --html results/exp3/r${r}_c10_report.html --csv results/exp3/r${r}_c10
done

# Plots
python3 experiments/plot_results.py
```

## Project structure

```
rag_project/
├── services/                  # FastAPI microservices
│   ├── api/                   # /documents, /query, orchestration
│   ├── embed/                 # sentence-transformers
│   ├── vector/                # qdrant-client wrapper
│   └── llm/                   # Ollama HTTP wrapper
├── experiments/               # Load tests and analysis
│   ├── seed_via_api.py        # Bulk-insert chunks through the public ALB
│   ├── locustfile.py          # /query load test + per-request timings JSONL
│   ├── plot_results.py        # Generates results/figures/*.png
│   ├── run_preliminary_suite.py   # Laptop-scale early benchmarks
│   ├── loadtest_query.py      # Sequential /query latency bench
│   ├── bench_retrieval.py     # Embed + vector only (local only; bypasses LLM)
│   ├── seed_index.py          # Direct embed+vector upsert (local only)
│   └── requirements-bench.txt
├── terraform/                 # AWS infra (VPC + ALB + ECR + ECS Fargate)
├── deploy/aws/
│   └── build-and-push.sh      # Build images → ECR → force-new-deployment
├── docker-compose.yml         # Local single-host stack
├── results/
│   ├── preliminary/           # Phase 1 laptop benchmarks
│   ├── exp1/ exp2/ exp3/      # Phase 3 full experiments
│   └── figures/               # Plots used in the report
└── sample.md
```

## Limitations and future work

- **Vector storage is per-task.** Exp 3's bimodal distribution is the most direct symptom of this. Moving Qdrant to an EFS volume shared across tasks, or to managed Qdrant Cloud, would let the LLM tier scale linearly without the current fast-path artifact.
- **Fargate has no GPU.** Observed LLM latency (~8 s / query on 8 vCPU; ~25 s / query on 1 vCPU) is entirely CPU-bound inference. The same code with GPU-backed inference (EC2 `g5`, or an external inference endpoint) would shift the bottleneck elsewhere and change the shape of every graph. Good future experiment.
- **Memory measurement is theoretical.** Container Insights wasn't enabled on this cluster, so the Exp 2 memory figure is computed from first principles rather than measured. Enabling Container Insights (one line in `terraform/ecs.tf`) would let a repeat of Exp 2 report actual `MemoryUtilized`.
- **Cloud LLM option untested.** The LLM service is a thin HTTP wrapper; swapping Ollama for Anthropic, Bedrock, etc., is a config change, but the latency and cost curves would be very different from local inference.

## References

- Lewis et al., *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*, 2020.
- Parnas, *On the criteria to be used in decomposing systems into modules*, 1972 — which is what this project's service decomposition is a concrete modern example of.
- [Qdrant documentation](https://qdrant.tech/documentation/) — HNSW indexing and search parameters.
- [Ollama](https://ollama.com) — local LLM runtime used in both Compose and the Fargate task.
