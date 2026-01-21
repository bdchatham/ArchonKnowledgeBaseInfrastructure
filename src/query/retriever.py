"""Retrieval logic for semantic search.

This module provides the core retrieval functionality: generating
query embeddings and searching the vector store for similar documents.
"""

import logging
from dataclasses import dataclass
from typing import List

from ..common.embedding_client import EmbeddingClient
from ..common.vector_store import VectorStore, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Result from a retrieval query."""
    
    content: str
    source: str
    chunk_index: int
    score: float


class Retriever:
    """Retrieves relevant document chunks for queries.
    
    Handles the retrieval workflow: generates an embedding for the
    query, searches the vector store, and returns relevant chunks.
    
    Usage:
        retriever = Retriever(
            embedding_client=EmbeddingClient(...),
            vector_store=VectorStore(...),
            default_k=5,
        )
        
        results = await retriever.retrieve("How does the system work?")
        for result in results:
            print(f"Score: {result.score}, Source: {result.source}")
            print(result.content)
    """
    
    def __init__(
        self,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        default_k: int = 5,
    ):
        """Initialize the retriever.
        
        Args:
            embedding_client: Client for generating embeddings
            vector_store: Qdrant vector store wrapper
            default_k: Default number of results to return
        """
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        self.default_k = default_k
    
    async def retrieve(
        self,
        query: str,
        k: int | None = None,
    ) -> List[RetrievalResult]:
        """Retrieve relevant document chunks for a query.
        
        Args:
            query: Natural language query string
            k: Number of results to return (uses default_k if not specified)
            
        Returns:
            List of RetrievalResult objects ordered by relevance
        """
        if k is None:
            k = self.default_k
        
        query_embedding = await self.embedding_client.embed_single(query)
        
        search_results = await self.vector_store.search(query_embedding, k=k)
        
        results = [
            RetrievalResult(
                content=result.content,
                source=result.source,
                chunk_index=result.chunk_index,
                score=result.score,
            )
            for result in search_results
        ]
        
        logger.debug(f"Retrieved {len(results)} results for query: {query[:50]}...")
        return results
    
    async def retrieve_with_context(
        self,
        query: str,
        k: int | None = None,
    ) -> str:
        """Retrieve and format context for LLM augmentation.
        
        Args:
            query: Natural language query string
            k: Number of results to return
            
        Returns:
            Formatted context string for LLM prompt augmentation
        """
        results = await self.retrieve(query, k)
        
        if not results:
            return ""
        
        context_parts = []
        for i, result in enumerate(results, 1):
            context_parts.append(
                f"[Source {i}: {result.source}]\n{result.content}"
            )
        
        return "\n\n---\n\n".join(context_parts)
