# RAG stack (FastAPI + embed + vector + Qdrant + Ollama)

Minimal end-to-end pipeline: **API** orchestrates **embedding** + **vector search** (HTTP) → **Qdrant** stores vectors → **Ollama** on the host answers.

| Service | Port | Role |
|--------|------|------|
| `api` | 8000 | Upload / query, calls embed + vector + Ollama |
| `embed` | 8001 | sentence-transformers |
| `vector` | 8002 | Qdrant client: upsert + similarity search |
| `qdrant` | 6333 | Vector database |
| Ollama | host 11434 | LLM |

## Prerequisite: Ollama on your Mac (host)

Ollama runs **on the host**, not inside Compose (simpler on macOS).

1. Install [Ollama](https://ollama.com) and pull a small model:

```bash
ollama pull llama3.2:1b
```

2. Keep Ollama running (menu bar app). Default URL: `http://127.0.0.1:11434`.

The API container talks to it via `host.docker.internal:11434`.

## Run with Docker Compose

From this folder:

```bash
docker compose up --build
```

First start: the **embed** image downloads PyTorch + `sentence-transformers` and then the model — it can take several minutes.

## Try it

Upload a `.txt` / `.md` file:

```bash
curl -F "file=@sample.md" http://localhost:8000/documents
```

Ask a question:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What is this document about?"}' | python3 -m json.tool
```

If `json.tool` prints `Expecting value: line 1 column 1`, **`curl` printed nothing to stdout** (empty body). Debug **without** the pipe first:

```bash
curl -i -X POST http://localhost:8000/query -H "Content-Type: application/json" -d '{"question":"test"}'
```

Common causes:

1. **Ollama not running on the Mac** or wrong model — start the Ollama app and run `ollama pull llama3.2:1b` (or whatever you set in `OLLAMA_MODEL`).
2. **API container cannot reach the host** — from another terminal:

   `docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:200])"`

   If that fails, fix Docker Desktop “host gateway” / try `OLLAMA_URL=http://host.docker.internal:11434` (already the default in `docker-compose.yml`).
3. **Request still running** — first LLM call can be slow; don’t cancel `curl` early, or increase patience (timeout is large server-side).

Single-line `curl` (avoids shell line-continuation issues):

```bash
curl -s -X POST http://localhost:8000/query -H "Content-Type: application/json" -d '{"question":"What is this document about?"}'
```

`timings_ms` in the response is your **per-stage observability** (embed / retrieve / LLM / total).

## If `/query` returns 500: `QdrantClient has no attribute search`

Rebuild the **vector** image (Qdrant client lives there now): `services/vector/requirements.txt` pins **qdrant-client 1.12.x** to match the Qdrant server image.

```bash
docker compose build --no-cache vector
docker compose up
```

## Env vars (API)

| Variable | Default | Notes |
|----------|---------|--------|
| `VECTOR_URL` | `http://vector:8002` | Vector search microservice |
| `EMBED_URL` | `http://embed:8001` | Embedding service |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Host Ollama |
| `OLLAMA_MODEL` | `llama3.2:1b` | Must be pulled locally |
| `TOP_K` | `5` | Retrieved chunks |

**Vector service** uses `QDRANT_URL`, `COLLECTION_NAME`, `VECTOR_DIM` (see `docker-compose.yml`).

## Preliminary results (for Assignment / report)

With Compose **up** and ports **8000/8001/8002/6333** on localhost, Ollama running on the host:

```bash
pip install -r scripts/requirements-bench.txt
python scripts/run_preliminary_suite.py
```

This writes JSON + `results/preliminary/preliminary_summary.csv`. It runs a **smaller** matrix than your final experiments (default index sizes **1000** and **5000**; concurrency **3** and **8**) so a laptop can finish. For the final paper, increase sizes in `scripts/run_preliminary_suite.py` (e.g. **10000 / 100000** chunks and **10 / 50 / 200** concurrent clients) and rerun.

**Single scripts**

| Script | Experiment | Notes |
|--------|------------|--------|
| `scripts/seed_index.py --reset --num-chunks N` | Exp 2 (build index) | Needs `ALLOW_BENCH_RESET=1` on vector (default in `docker-compose.yml`) |
| `scripts/bench_retrieval.py` | Exp 2 (no LLM) | Mean/P95 for embed + `/points/search` |
| `scripts/loadtest_query.py` | Exp 1 (+ partial E2E) | Mean/P95 for full `POST /query` |
| `results/preliminary/exp3_placeholder.json` | Exp 3 | Explains horizontal scaling is still to run behind nginx/scale |

**What to write in “Preliminary Results”**

- Paste tables from `preliminary_summary.csv` and 1–2 example JSON files.  
- State **what is left**: full **10→50→200** concurrency sweep, **100k** index point (if not run), **1→2→4** replicas with a load balancer.  
- **Worst case**: many concurrent `/query` calls while the LLM is single-threaded → queueing and high P95/timeouts.  
- **Base case**: low concurrency, small index, short answers → near “one embed + one search + one generation”.

## Next steps for your project

- Split **LLM** behind its own tiny HTTP service if you want four separate **application** containers for the write-up (Qdrant stays infra).
- Add **k6/Locust** against `POST /query` for Experiment 1.
- Add a **seed script** that bulk-inserts 1k/10k/100k chunks for Experiment 2.
- Put **nginx** in front of duplicated `api` or `embed` services for Experiment 3.

## Optional: run API without Docker (dev)

Start **Qdrant**, **embed** (`:8001`), and **vector** (`:8002`) locally or via Compose, then set `VECTOR_URL`, `EMBED_URL`, `OLLAMA_URL` to `http://127.0.0.1:...` and run `uvicorn main:app --reload` from `services/api`.
