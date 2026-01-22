"""Document ingestion for embedding generation and storage.

This module handles the ingestion workflow: chunking documents,
generating embeddings, and storing them in the vector store.
"""

import logging
from typing import List

from aphex_clients import EmbeddingClient
from ..common.vector_store import VectorStore
from .chunker import DocumentChunker

logger = logging.getLogger(__name__)


class DocumentIngester:
    """Handles embedding generation and vector store updates.
    
    Orchestrates the ingestion workflow: takes document content,
    chunks it, generates embeddings, and stores them in Qdrant.
    
    Usage:
        ingester = DocumentIngester(
            embedding_client=EmbeddingClient(...),
            vector_store=VectorStore(...),
            chunker=DocumentChunker(...),
        )
        
        # Ingest a document
        await ingester.ingest_document(
            repo_file_path="github.com/user/repo/.kiro/docs/overview.md",
            content="Document content..."
        )
        
        # Remove a document
        await ingester.remove_document(
            repo_file_path="github.com/user/repo/.kiro/docs/overview.md"
        )
    """
    
    def __init__(
        self,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        chunker: DocumentChunker,
    ):
        """Initialize the ingester.
        
        Args:
            embedding_client: Client for generating embeddings
            vector_store: Qdrant vector store wrapper
            chunker: Document chunker
        """
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        self.chunker = chunker
    
    async def ingest_document(
        self,
        repo_file_path: str,
        content: str,
    ) -> int:
        """Chunk, embed, and store a document.
        
        Args:
            repo_file_path: Unique path identifying the document
            content: Document text content
            
        Returns:
            Number of chunks stored
        """
        await self.vector_store.delete_by_source(repo_file_path)
        
        chunks = self.chunker.chunk_with_metadata(content, repo_file_path)
        
        if not chunks:
            logger.warning(f"No chunks generated for {repo_file_path}")
            return 0
        
        texts = [chunk["text"] for chunk in chunks]
        embeddings = await self.embedding_client.embed(texts)
        
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            point_id = f"{repo_file_path}:{chunk['chunk_index']}"
            points.append((
                point_id,
                embedding,
                {
                    "source": chunk["source"],
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                },
            ))
        
        await self.vector_store.upsert_batch(points)
        
        logger.info(f"Ingested {len(chunks)} chunks for {repo_file_path}")
        return len(chunks)
    
    async def remove_document(self, repo_file_path: str) -> None:
        """Remove all chunks for a document from vector store.
        
        Args:
            repo_file_path: Path of the document to remove
        """
        await self.vector_store.delete_by_source(repo_file_path)
        logger.info(f"Removed document: {repo_file_path}")
    
    async def ingest_documents(
        self,
        documents: List[tuple[str, str]],
    ) -> int:
        """Ingest multiple documents.
        
        Args:
            documents: List of (repo_file_path, content) tuples
            
        Returns:
            Total number of chunks stored
        """
        total_chunks = 0
        
        for repo_file_path, content in documents:
            try:
                chunks = await self.ingest_document(repo_file_path, content)
                total_chunks += chunks
            except Exception as e:
                logger.error(f"Failed to ingest {repo_file_path}: {e}")
                continue
        
        return total_chunks
