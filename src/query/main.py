"""Query service FastAPI application.

This module provides the REST API for document retrieval. It exposes
endpoints for semantic search and health checks.
"""

import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from ..common.config import Settings
from aphex_clients import EmbeddingClient
from ..common.vector_store import VectorStore, VectorStoreError
from .retriever import Retriever
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


settings: Settings | None = None
retriever: Retriever | None = None
embedding_client: EmbeddingClient | None = None
vector_store: VectorStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global settings, retriever, embedding_client, vector_store
    
    logger.info("Starting Query service")
    
    settings = Settings()
    
    embedding_client = EmbeddingClient(
        base_url=settings.embedding_service_url,
        model=settings.embedding_model,
    )
    
    vector_store = VectorStore(
        url=settings.vector_db_url,
        collection=settings.collection_name,
    )
    
    retriever = Retriever(
        embedding_client=embedding_client,
        vector_store=vector_store,
        default_k=settings.retrieval_k,
    )
    
    logger.info("Query service started")
    
    yield
    
    logger.info("Shutting down Query service")
    if embedding_client:
        await embedding_client.close()


app = FastAPI(
    title="Archon Knowledge Base Query Service",
    description="Semantic search API for document retrieval",
    version="1.0.0",
    lifespan=lifespan,
)


class RetrieveRequest(BaseModel):
    """Request body for /v1/retrieve endpoint."""
    
    query: str
    k: Optional[int] = None


class ChunkResult(BaseModel):
    """A single retrieved chunk."""
    
    content: str
    source: str
    chunk_index: int
    score: float


class RetrieveResponse(BaseModel):
    """Response body for /v1/retrieve endpoint."""
    
    chunks: List[ChunkResult]
    query: str


@app.post("/v1/retrieve", response_model=RetrieveResponse)
async def retrieve(request: RetrieveRequest) -> RetrieveResponse:
    """Retrieve relevant document chunks for a query.
    
    Args:
        request: Query request with query string and optional k
        
    Returns:
        List of relevant document chunks ordered by similarity
        
    Raises:
        503: If embedding service or vector store is unavailable
    """
    if retriever is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized",
        )
    
    try:
        results = await retriever.retrieve(request.query, k=request.k)
        
        return RetrieveResponse(
            chunks=[
                ChunkResult(
                    content=r.content,
                    source=r.source,
                    chunk_index=r.chunk_index,
                    score=r.score,
                )
                for r in results
            ],
            query=request.query,
        )
        
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.error(f"Embedding service error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding service unavailable",
        )
        
    except VectorStoreError as e:
        logger.error(f"Vector store error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store unavailable",
        )


@app.get("/health")
async def health():
    """Health check endpoint.
    
    Returns 200 if the service is running.
    """
    return {"status": "healthy"}


@app.get("/ready")
async def ready():
    """Readiness check endpoint.
    
    Returns 200 if the service is ready to handle requests.
    Checks connectivity to embedding service and vector store.
    """
    if settings is None or vector_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized",
        )
    
    # Direct health check - aphex_clients health_check has base_url bug
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{settings.embedding_service_url}/health", timeout=5)
            embedding_healthy = resp.status_code == 200
        except Exception:
            embedding_healthy = False
    
    vector_healthy = await vector_store.health_check()
    
    if not embedding_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding service not ready",
        )
    
    if not vector_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store not ready",
        )
    
    return {
        "status": "ready",
        "embedding_service": "healthy",
        "vector_store": "healthy",
    }


class DocumentRequest(BaseModel):
    """Request body for /v1/document endpoint."""
    
    doc_id: str


class DocumentResponse(BaseModel):
    """Response body for /v1/document endpoint."""
    
    doc_id: str
    content: str
    chunk_count: int


@app.post("/v1/document", response_model=DocumentResponse)
async def get_document(request: DocumentRequest) -> DocumentResponse:
    """Retrieve full document by doc_id (source path).
    
    Args:
        request: Document request with doc_id (e.g., "api/v1alpha1/knowledgebase_types.archon.md")
        
    Returns:
        Full document content reconstructed from chunks
        
    Raises:
        404: If document not found
        503: If vector store is unavailable
    """
    if vector_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized",
        )
    
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        # Query all chunks for this document
        results = await vector_store.client.scroll(
            collection_name=vector_store.collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="source",
                        match=MatchValue(value=request.doc_id),
                    )
                ]
            ),
            limit=1000,  # Max chunks per document
            with_payload=True,
            with_vectors=False,
        )
        
        if not results[0]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document not found: {request.doc_id}",
            )
        
        # Sort chunks by chunk_index
        chunks = sorted(
            results[0],
            key=lambda p: p.payload.get("chunk_index", 0)
        )
        
        # Reconstruct document
        content = "\n\n".join(
            chunk.payload.get("content", "")
            for chunk in chunks
        )
        
        return DocumentResponse(
            doc_id=request.doc_id,
            content=content,
            chunk_count=len(chunks),
        )
        
    except VectorStoreError as e:
        logger.error(f"Vector store error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store unavailable",
        )


@app.get("/metrics")
async def metrics():
    """Metrics endpoint placeholder.
    
    Returns basic service metrics.
    """
    return {
        "service": "archon-knowledge-base-query",
        "version": "1.0.0",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
