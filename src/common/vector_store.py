"""Qdrant vector store wrapper for document embeddings.

This module provides a high-level interface to Qdrant for storing and
searching document embeddings. Each document chunk is stored with its
embedding vector and metadata (source file, chunk index, content).
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Result from a vector similarity search."""
    
    content: str
    source: str
    chunk_index: int
    score: float


class VectorStoreError(Exception):
    """Raised when vector store operations fail."""
    pass


class VectorStore:
    """Qdrant vector store wrapper for document embeddings.
    
    Provides methods for searching, upserting, and deleting document
    embeddings in Qdrant. Each vector is stored with metadata including
    the source file path, chunk index, and content text.
    
    Usage:
        store = VectorStore(url="http://qdrant:6333", collection="archon-docs")
        
        # Search for similar documents
        results = await store.search(query_embedding, k=5)
        
        # Store a document chunk
        await store.upsert(
            id="repo/file.md:0",
            embedding=[0.1, 0.2, ...],
            metadata={"source": "repo/file.md", "chunk_index": 0, "content": "..."}
        )
    """
    
    def __init__(self, url: str, collection: str):
        """Initialize the vector store client.
        
        Args:
            url: URL of the Qdrant server
            collection: Name of the collection to use
        """
        self.url = url
        self.collection = collection
        self._client: QdrantClient | None = None
    
    def _get_client(self) -> QdrantClient:
        """Get or create the Qdrant client."""
        if self._client is None:
            self._client = QdrantClient(url=self.url)
        return self._client
    
    async def search(
        self,
        embedding: List[float],
        k: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[SearchResult]:
        """Search for similar documents by embedding.
        
        Args:
            embedding: Query embedding vector
            k: Number of results to return
            score_threshold: Minimum similarity score (optional)
            
        Returns:
            List of SearchResult objects ordered by similarity
        """
        client = self._get_client()
        
        try:
            results = client.search(
                collection_name=self.collection,
                query_vector=embedding,
                limit=k,
                score_threshold=score_threshold,
            )
            
            return [
                SearchResult(
                    content=hit.payload.get("content", ""),
                    source=hit.payload.get("source", ""),
                    chunk_index=hit.payload.get("chunk_index", 0),
                    score=hit.score,
                )
                for hit in results
            ]
            
        except UnexpectedResponse as e:
            logger.error(f"Qdrant search failed: {e}")
            raise VectorStoreError(f"Vector search failed: {e}") from e
    
    async def upsert(
        self,
        id: str,
        embedding: List[float],
        metadata: dict,
    ) -> None:
        """Insert or update a vector with metadata.
        
        Args:
            id: Unique identifier for the vector (e.g., "repo/file.md:0")
            embedding: Embedding vector
            metadata: Payload containing source, chunk_index, content
        """
        client = self._get_client()
        
        try:
            client.upsert(
                collection_name=self.collection,
                points=[
                    qdrant_models.PointStruct(
                        id=self._hash_id(id),
                        vector=embedding,
                        payload=metadata,
                    )
                ],
            )
            logger.debug(f"Upserted vector: {id}")
            
        except UnexpectedResponse as e:
            logger.error(f"Qdrant upsert failed for {id}: {e}")
            raise VectorStoreError(f"Vector upsert failed: {e}") from e
    
    async def upsert_batch(
        self,
        points: List[tuple[str, List[float], dict]],
    ) -> None:
        """Insert or update multiple vectors in a batch.
        
        Args:
            points: List of (id, embedding, metadata) tuples
        """
        if not points:
            return
            
        client = self._get_client()
        
        try:
            client.upsert(
                collection_name=self.collection,
                points=[
                    qdrant_models.PointStruct(
                        id=self._hash_id(id),
                        vector=embedding,
                        payload=metadata,
                    )
                    for id, embedding, metadata in points
                ],
            )
            logger.debug(f"Upserted {len(points)} vectors")
            
        except UnexpectedResponse as e:
            logger.error(f"Qdrant batch upsert failed: {e}")
            raise VectorStoreError(f"Batch upsert failed: {e}") from e
    
    async def delete(self, ids: List[str]) -> None:
        """Delete vectors by ID.
        
        Args:
            ids: List of vector IDs to delete
        """
        if not ids:
            return
            
        client = self._get_client()
        
        try:
            client.delete(
                collection_name=self.collection,
                points_selector=qdrant_models.PointIdsList(
                    points=[self._hash_id(id) for id in ids],
                ),
            )
            logger.debug(f"Deleted {len(ids)} vectors")
            
        except UnexpectedResponse as e:
            logger.error(f"Qdrant delete failed: {e}")
            raise VectorStoreError(f"Vector delete failed: {e}") from e
    
    async def delete_by_source(self, source: str) -> None:
        """Delete all vectors for a given source file.
        
        Args:
            source: Source file path to delete vectors for
        """
        client = self._get_client()
        
        try:
            client.delete(
                collection_name=self.collection,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="source",
                                match=qdrant_models.MatchValue(value=source),
                            )
                        ]
                    )
                ),
            )
            logger.debug(f"Deleted vectors for source: {source}")
            
        except UnexpectedResponse as e:
            logger.error(f"Qdrant delete by source failed: {e}")
            raise VectorStoreError(f"Delete by source failed: {e}") from e
    
    async def health_check(self) -> bool:
        """Check if Qdrant is healthy and the collection exists.
        
        Returns:
            True if Qdrant is reachable and collection exists
        """
        try:
            client = self._get_client()
            client.get_collection(self.collection)
            return True
        except Exception:
            return False
    
    @staticmethod
    def _hash_id(id: str) -> int:
        """Convert string ID to integer hash for Qdrant.
        
        Qdrant requires integer or UUID point IDs. We use a hash
        of the string ID to generate a consistent integer ID.
        """
        return hash(id) & 0x7FFFFFFFFFFFFFFF
