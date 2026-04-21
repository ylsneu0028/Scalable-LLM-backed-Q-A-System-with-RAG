import asyncio
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rag-llm")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")

app = FastAPI(title="LLM inference service")


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str | None = None


class GenerateResponse(BaseModel):
    text: str
    model: str


async def ollama_generate(prompt: str, model: str) -> str:
    """Call Ollama; retry while model is loading or server is warming (common on first ECS request)."""
    max_attempts = 8
    pause_s = 15.0
    for attempt in range(1, max_attempts + 1):
        async with httpx.AsyncClient(timeout=600.0) as client:
            try:
                r = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                r.raise_for_status()
            except httpx.RequestError as e:
                log.warning("Ollama request error (attempt %s/%s): %s", attempt, max_attempts, e)
                if attempt < max_attempts:
                    await asyncio.sleep(pause_s)
                    continue
                raise HTTPException(
                    status_code=502,
                    detail=f"Cannot reach Ollama at {OLLAMA_URL}. Error: {e}",
                ) from e
            except httpx.HTTPStatusError as e:
                body = (e.response.text or "")[:500]
                log.warning(
                    "Ollama HTTP %s (attempt %s/%s): %s",
                    e.response.status_code,
                    attempt,
                    max_attempts,
                    body,
                )
                if attempt < max_attempts and e.response.status_code in (404, 500, 502, 503, 504):
                    await asyncio.sleep(pause_s)
                    continue
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Ollama returned {e.response.status_code}. "
                        f"Model may still be downloading on first use (`ollama pull {model}`). "
                        f"OLLAMA_URL={OLLAMA_URL}. Body: {body!r}"
                    ),
                ) from e
            try:
                body = r.json()
            except ValueError:
                snippet = (r.text or "")[:400]
                log.error("Ollama returned non-JSON (first 400 chars): %r", snippet)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Ollama response was not JSON. Is Ollama running? "
                        f"OLLAMA_URL={OLLAMA_URL}. Raw snippet: {snippet!r}"
                    ),
                ) from None
            return (body.get("response") or "").strip()

    raise HTTPException(status_code=502, detail="Ollama: exhausted retries without success")


@app.get("/health")
async def health() -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
        except Exception as e:
            return {"ok": False, "ollama_url": OLLAMA_URL, "error": str(e)}
    return {"ok": True, "ollama_url": OLLAMA_URL, "default_model": DEFAULT_MODEL}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    model = (req.model or DEFAULT_MODEL).strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is empty")
    try:
        text = await ollama_generate(req.prompt, model)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Ollama request failed. Check Ollama is running and model is pulled "
                f"(`ollama pull {model}`). OLLAMA_URL={OLLAMA_URL}. Error: {e}"
            ),
        ) from e
    return GenerateResponse(text=text, model=model)
