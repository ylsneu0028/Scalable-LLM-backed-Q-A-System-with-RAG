import logging
import os
import time
import uuid

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rag-api")

VECTOR_URL = os.environ.get("VECTOR_URL", "http://localhost:8002")
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8001")
LLM_URL = os.environ.get("LLM_URL", "http://localhost:8003")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
COLLECTION = os.environ.get("COLLECTION_NAME", "rag_chunks")
TOP_K = int(os.environ.get("TOP_K", "5"))

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBED_BATCH = 32

app = FastAPI(title="RAG API")


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        piece = text[i : i + size].strip()
        if piece:
            chunks.append(piece)
        i += max(1, size - overlap)
    return chunks


async def call_embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(f"{EMBED_URL.rstrip('/')}/embed", json={"texts": texts})
        r.raise_for_status()
        data = r.json()
        return data["vectors"]


async def call_vector_upsert(points: list[dict]) -> None:
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{VECTOR_URL.rstrip('/')}/points/upsert",
            json={"points": points},
        )
        r.raise_for_status()


async def call_vector_search(vector: list[float], limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{VECTOR_URL.rstrip('/')}/points/search",
            json={"vector": vector, "limit": limit},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("hits", [])


async def call_llm(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{LLM_URL.rstrip('/')}/generate",
            json={"prompt": prompt, "model": OLLAMA_MODEL},
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:2000]
            log.error("llm /generate HTTP %s: %s", e.response.status_code, body[:500])
            raise HTTPException(
                status_code=502,
                detail=(
                    f"LLM service returned {e.response.status_code}. "
                    f"On ECS, Ollama runs in the same task (not your laptop). "
                    f"Check CloudWatch logs for `llm` and `ollama`. Response: {body}"
                ),
            ) from e
        data = r.json()
        return (data.get("text") or "").strip()


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    timings_ms: dict[str, float]


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "collection": COLLECTION,
        "vector_service": VECTOR_URL,
        "llm_service": LLM_URL,
    }


@app.post("/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="Empty document after chunking")

    t0 = time.perf_counter()
    point_dicts: list[dict] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        t_e0 = time.perf_counter()
        vectors = await call_embed(batch)
        log.info("embed batch %s chunks in %.0fms", len(batch), (time.perf_counter() - t_e0) * 1000)
        for ch, vec in zip(batch, vectors):
            point_dicts.append(
                {
                    "id": str(uuid.uuid4()),
                    "vector": vec,
                    "payload": {"text": ch, "filename": file.filename or "upload"},
                }
            )

    try:
        await call_vector_upsert(point_dicts)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Vector service upsert failed (is vector up?): {e}",
        ) from e

    total_ms = (time.perf_counter() - t0) * 1000
    return {
        "filename": file.filename,
        "chunks": len(chunks),
        "ingest_time_ms": round(total_ms, 2),
    }


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    t_total0 = time.perf_counter()

    t0 = time.perf_counter()
    qvec = (await call_embed([req.question]))[0]
    t_embed = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    try:
        hits = await call_vector_search(qvec, TOP_K)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Vector service search failed: {e}",
        ) from e
    t_search = (time.perf_counter() - t0) * 1000

    sources: list[str] = []
    for h in hits:
        pl = h.get("payload") or {}
        txt = pl.get("text")
        if txt:
            sources.append(txt)

    context = "\n---\n".join(sources) if sources else "(no context retrieved)"
    prompt = (
        "You are a helpful assistant. Answer using ONLY the context below. "
        "If the answer is not in the context, say you do not know.\n\n"
        f"Context:\n{context}\n\nQuestion: {req.question}\n\nAnswer:"
    )

    t0 = time.perf_counter()
    try:
        answer = await call_llm(prompt)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not reach LLM service at "
                + LLM_URL
                + ". (Docker Compose: ensure `llm` is up; AWS: same ECS task.) "
                f"Original error: {e}"
            ),
        ) from e
    t_llm = (time.perf_counter() - t0) * 1000

    t_total = (time.perf_counter() - t_total0) * 1000
    timings = {
        "embed_ms": round(t_embed, 2),
        "retrieve_ms": round(t_search, 2),
        "llm_ms": round(t_llm, 2),
        "total_ms": round(t_total, 2),
    }
    log.info("query timings %s", timings)

    return QueryResponse(answer=answer, sources=sources, timings_ms=timings)
