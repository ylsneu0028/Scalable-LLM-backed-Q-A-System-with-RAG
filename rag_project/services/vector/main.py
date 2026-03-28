import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vector-service")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("COLLECTION_NAME", "rag_chunks")
VECTOR_DIM = int(os.environ.get("VECTOR_DIM", "384"))

_client: QdrantClient | None = None


def get_qdrant() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL)
    return _client


def qdrant_vector_search(client: QdrantClient, query_vector: list[float], limit: int):
    if hasattr(client, "search"):
        return client.search(
            collection_name=COLLECTION,
            query_vector=query_vector,
            limit=limit,
        )
    resp = client.query_points(
        collection_name=COLLECTION,
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    return list(resp.points)


def ensure_collection() -> None:
    client = get_qdrant()
    names = [c.name for c in client.get_collections().collections]
    if COLLECTION not in names:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection %s dim=%s", COLLECTION, VECTOR_DIM)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_collection()
    yield


app = FastAPI(title="Vector search service", lifespan=lifespan)

ALLOW_BENCH_RESET = os.environ.get("ALLOW_BENCH_RESET", "0") == "1"


class PointIn(BaseModel):
    id: str
    vector: list[float]
    payload: dict = Field(default_factory=dict)


class UpsertRequest(BaseModel):
    points: list[PointIn]


class SearchRequest(BaseModel):
    vector: list[float]
    limit: int = Field(default=5, ge=1, le=100)


class SearchHit(BaseModel):
    score: float | None = None
    payload: dict


class SearchResponse(BaseModel):
    hits: list[SearchHit]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "collection": COLLECTION, "qdrant": QDRANT_URL}


@app.post("/admin/reset_collection")
def reset_collection() -> dict:
    """Drop and recreate the collection (for benchmark runs only)."""
    if not ALLOW_BENCH_RESET:
        raise HTTPException(status_code=404, detail="Not enabled")
    client = get_qdrant()
    try:
        client.delete_collection(collection_name=COLLECTION)
    except Exception as e:
        log.warning("delete_collection: %s", e)
    ensure_collection()
    return {"ok": True, "collection": COLLECTION}


@app.post("/points/upsert")
def upsert_points(req: UpsertRequest) -> dict:
    if not req.points:
        raise HTTPException(status_code=400, detail="No points")
    client = get_qdrant()
    points = [
        PointStruct(id=p.id, vector=p.vector, payload=p.payload or {}) for p in req.points
    ]
    client.upsert(collection_name=COLLECTION, points=points, wait=True)
    return {"upserted": len(points)}


@app.post("/points/search", response_model=SearchResponse)
def search_points(req: SearchRequest) -> SearchResponse:
    client = get_qdrant()
    raw = qdrant_vector_search(client, req.vector, req.limit)
    hits: list[SearchHit] = []
    for h in raw:
        score = getattr(h, "score", None)
        pl = getattr(h, "payload", None) or {}
        if not isinstance(pl, dict):
            pl = dict(pl)
        hits.append(SearchHit(score=score, payload=pl))
    return SearchResponse(hits=hits)
