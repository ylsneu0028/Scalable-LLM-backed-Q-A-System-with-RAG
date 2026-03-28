import os

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")

app = FastAPI(title="Embedding service")
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    dim: int


@app.get("/health")
def health() -> dict:
    m = get_model()
    v = m.encode(["ping"], show_progress_bar=False)
    return {"ok": True, "model": MODEL_NAME, "dim": int(len(v[0]))}


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if not req.texts:
        return EmbedResponse(vectors=[], model=MODEL_NAME, dim=0)
    model = get_model()
    vecs = model.encode(req.texts, show_progress_bar=False)
    rows = [v.tolist() for v in vecs]
    return EmbedResponse(vectors=rows, model=MODEL_NAME, dim=len(rows[0]))
