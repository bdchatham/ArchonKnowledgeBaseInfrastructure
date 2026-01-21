"""Embedding service using sentence-transformers.

Provides an OpenAI-compatible /v1/embeddings endpoint for generating
text embeddings using the BAAI/bge-base-en-v1.5 model.
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")

model = None


def load_model():
    """Load the sentence-transformers model."""
    global model
    if model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {MODEL_NAME}")
        model = SentenceTransformer(MODEL_NAME)
        logger.info("Model loaded successfully")
    return model


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model on startup."""
    load_model()
    yield


app = FastAPI(
    title="Archon Embedding Service",
    description="OpenAI-compatible embedding service using sentence-transformers",
    version="1.0.0",
    lifespan=lifespan,
)


class EmbeddingRequest(BaseModel):
    """OpenAI-compatible embedding request."""
    input: str | list[str]
    model: str = MODEL_NAME


class EmbeddingData(BaseModel):
    """Single embedding result."""
    object: str = "embedding"
    embedding: list[float]
    index: int


class EmbeddingUsage(BaseModel):
    """Token usage information."""
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    """OpenAI-compatible embedding response."""
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest):
    """Generate embeddings for input text(s).
    
    Accepts single string or list of strings, returns embeddings
    in OpenAI-compatible format.
    """
    try:
        m = load_model()
        
        texts = [request.input] if isinstance(request.input, str) else request.input
        
        embeddings = m.encode(texts, normalize_embeddings=True)
        
        data = [
            EmbeddingData(embedding=emb.tolist(), index=i)
            for i, emb in enumerate(embeddings)
        ]
        
        total_chars = sum(len(t) for t in texts)
        approx_tokens = total_chars // 4
        
        return EmbeddingResponse(
            data=data,
            model=MODEL_NAME,
            usage=EmbeddingUsage(
                prompt_tokens=approx_tokens,
                total_tokens=approx_tokens,
            ),
        )
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """Health check endpoint for Kubernetes probes."""
    return {"status": "healthy"}


@app.get("/ready")
async def ready():
    """Readiness check - verifies model is loaded."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ready", "model": MODEL_NAME}
