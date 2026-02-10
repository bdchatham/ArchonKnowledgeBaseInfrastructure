"""Vector store service for ARN-enriched semantic search.

This module provides the VectorStoreService class for storing and searching
document chunks with ARN metadata in Qdrant. The service integrates with
the embedding service for generating embeddings and supports filtering
by package and symbol kind.

The service supports:
- Upsert operations with automatic embedding generation
- Semantic search with optional package and symbol_kind filters
- Delete operations by package or ARN
- Collection initialization with proper vector configuration

Source:
- .kiro/specs/vector-store-arn/design.md
- .kiro/specs/vector-store-arn/requirements.md

Validates:
    Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from src.common.embedding import EmbeddingServiceClient, EMBEDDING_DIMENSION
from src.vector.models import ArchonChunk, SearchResult

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION_NAME = "archon-docs"


class VectorStoreError(Exception):
    """Base exception for vector store errors."""

    pass


class VectorStoreConnectionError(VectorStoreError):
    """Raised when connection to Qdrant fails."""

    pass


class VectorStoreConfigurationError(VectorStoreError):
    """Raised when there's a configuration error."""

    pass


class VectorStoreService:
    """Service for vector store operations with ARN metadata.

    Provides methods for storing and searching document chunks with ARN
    metadata in Qdrant. Integrates with the embedding service for generating
    embeddings and supports filtering by package and symbol kind.

    The service manages:
    - Qdrant client connection and collection configuration
    - Embedding generation via the embedding service
    - Chunk upsert with full ARN metadata payload
    - Semantic search with optional filters
    - Delete operations by package or ARN

    Usage:
        service = VectorStoreService(
            qdrant_url="http://localhost:6333",
            collection_name="archon-docs",
            embedding_service_url="http://embedding-svc:8000",
        )

        # Upsert chunks
        chunks = [ArchonChunk(...), ArchonChunk(...)]
        count = await service.upsert(chunks)

        # Search with filters
        results = await service.search(
            query="How to generate SCIP indexes?",
            limit=10,
            package="ArchonDocumentationMCPTools",
            symbol_kind="function",
        )

    Attributes:
        qdrant_url: URL of the Qdrant server
        collection_name: Name of the Qdrant collection
        embedding_service_url: URL of the embedding service (optional)

    Validates:
        Requirements 2.1, 2.2, 2.3, 2.4
    """

    def __init__(
        self,
        qdrant_url: str,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_service_url: Optional[str] = None,
    ) -> None:
        """Initialize the vector store service.

        Creates a Qdrant client connection and optionally configures the
        embedding service client. The collection is created if it doesn't
        exist with the proper vector configuration (768 dimensions, COSINE
        distance).

        Args:
            qdrant_url: URL of the Qdrant server (e.g., "http://localhost:6333")
            collection_name: Name of the Qdrant collection (default: "archon-docs")
            embedding_service_url: URL of the embedding service (optional).
                If not provided, embedding operations will fail.

        Raises:
            VectorStoreConnectionError: If connection to Qdrant fails

        Example:
            >>> service = VectorStoreService(
            ...     qdrant_url="http://localhost:6333",
            ...     collection_name="archon-docs",
            ...     embedding_service_url="http://embedding-svc:8000",
            ... )
        """
        self.qdrant_url = qdrant_url
        self.collection_name = collection_name
        self.embedding_service_url = embedding_service_url

        self._embedding_client: Optional[EmbeddingServiceClient] = None
        if embedding_service_url:
            self._embedding_client = EmbeddingServiceClient(embedding_service_url)

        try:
            self._client = QdrantClient(url=qdrant_url)
            logger.info(f"Connected to Qdrant at {qdrant_url}")
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant at {qdrant_url}: {e}")
            raise VectorStoreConnectionError(
                f"Failed to connect to Qdrant at {qdrant_url}: {e}"
            ) from e

    @property
    def client(self) -> QdrantClient:
        """Get the Qdrant client instance."""
        return self._client

    @property
    def embedding_client(self) -> Optional[EmbeddingServiceClient]:
        """Get the embedding service client instance."""
        return self._embedding_client

    async def ensure_collection(self) -> None:
        """Ensure the collection exists with proper configuration.

        Creates the collection if it doesn't exist, configured with:
        - Vector size: 768 (BGE-base embedding dimension)
        - Distance metric: COSINE
        - On-disk payload: True (for large collections)

        Raises:
            VectorStoreConfigurationError: If collection creation fails
        """
        try:
            collections = self._client.get_collections()
            collection_names = [c.name for c in collections.collections]

            if self.collection_name not in collection_names:
                logger.info(f"Creating collection '{self.collection_name}'")
                self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=EMBEDDING_DIMENSION,
                        distance=Distance.COSINE,
                    ),
                    on_disk_payload=True,
                )
                logger.info(f"Collection '{self.collection_name}' created successfully")
            else:
                logger.debug(f"Collection '{self.collection_name}' already exists")
        except Exception as e:
            logger.error(f"Failed to ensure collection '{self.collection_name}': {e}")
            raise VectorStoreConfigurationError(
                f"Failed to ensure collection '{self.collection_name}': {e}"
            ) from e

    def _chunk_to_payload(self, chunk: ArchonChunk) -> Dict[str, Any]:
        """Convert an ArchonChunk to a Qdrant payload dictionary.

        Args:
            chunk: The ArchonChunk to convert

        Returns:
            Dictionary containing all chunk metadata for Qdrant payload
        """
        return {
            "content": chunk.content,
            "source": chunk.source,
            "chunk_index": chunk.chunk_index,
            "arn": chunk.arn,
            "related_arns": chunk.related_arns,
            "symbol_name": chunk.symbol_name,
            "symbol_kind": chunk.symbol_kind,
            "package": chunk.package,
        }

    def _payload_to_search_result(
        self, payload: Dict[str, Any], score: float
    ) -> SearchResult:
        """Convert a Qdrant payload to a SearchResult.

        Args:
            payload: The Qdrant payload dictionary
            score: The similarity score

        Returns:
            SearchResult instance with all metadata
        """
        return SearchResult(
            content=payload.get("content", ""),
            source=payload.get("source", ""),
            chunk_index=payload.get("chunk_index", 0),
            score=score,
            arn=payload.get("arn", ""),
            related_arns=payload.get("related_arns", []),
            symbol_name=payload.get("symbol_name"),
            symbol_kind=payload.get("symbol_kind"),
            package=payload.get("package", ""),
        )

    async def close(self) -> None:
        """Close the service and release resources.

        Closes the embedding service client if it was initialized.
        The Qdrant client doesn't require explicit closing.
        """
        if self._embedding_client is not None:
            await self._embedding_client.close()
            logger.debug("Embedding service client closed")

    async def __aenter__(self) -> "VectorStoreService":
        """Enter async context manager."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context manager."""
        await self.close()

    async def upsert(self, chunks: List[ArchonChunk]) -> int:
        """Upsert chunks to the vector store.

        Generates embeddings for each chunk and stores them with
        the full ARN metadata payload. Processes chunks in batches
        of up to 100 points for optimal performance.

        Args:
            chunks: List of ArchonChunk instances to upsert

        Returns:
            Number of chunks upserted

        Raises:
            VectorStoreConfigurationError: If embedding service is not configured
            EmbeddingServiceError: If embedding generation fails

        Example:
            >>> chunks = [
            ...     ArchonChunk(
            ...         content="This function generates SCIP indexes...",
            ...         source="ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md",
            ...         chunk_index=0,
            ...         arn="arn:archon:doc:workspace/Package/src/file.ts",
            ...         related_arns=["arn:archon:code:workspace/Package/src/lib.ts#func"],
            ...         symbol_name="generateScipIndex",
            ...         symbol_kind="function",
            ...         package="ArchonDocumentationMCPTools",
            ...     )
            ... ]
            >>> count = await service.upsert(chunks)
            >>> print(f"Upserted {count} chunks")

        Validates:
            Requirements 2.1, 2.2, 2.3, 2.4
        """
        if not chunks:
            return 0

        if self._embedding_client is None:
            raise VectorStoreConfigurationError(
                "Embedding service not configured. "
                "Provide embedding_service_url when initializing VectorStoreService."
            )

        await self.ensure_collection()

        texts = [chunk.content for chunk in chunks]
        embeddings = await self._embedding_client.embed(texts)

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload=self._chunk_to_payload(chunk),
            )
            for chunk, embedding in zip(chunks, embeddings)
        ]

        upsert_batch_size = 100
        total_upserted = 0

        for i in range(0, len(points), upsert_batch_size):
            batch = points[i : i + upsert_batch_size]
            try:
                self._client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                )
                total_upserted += len(batch)
                logger.debug(
                    f"Upserted batch of {len(batch)} points "
                    f"({total_upserted}/{len(points)} total)"
                )
            except Exception as e:
                logger.error(f"Failed to upsert batch: {e}")
                raise VectorStoreError(f"Failed to upsert batch: {e}") from e

        logger.info(
            f"Successfully upserted {total_upserted} chunks to '{self.collection_name}'"
        )
        return total_upserted

    def _build_search_filter(
        self,
        package: Optional[str] = None,
        symbol_kind: Optional[str] = None,
    ) -> Optional[Filter]:
        """Build a Qdrant filter for search operations.

        Constructs a filter with AND logic combining package and symbol_kind
        conditions when provided.

        Args:
            package: Optional package name to filter by
            symbol_kind: Optional symbol kind to filter by

        Returns:
            Qdrant Filter if any conditions are specified, None otherwise
        """
        conditions: List[FieldCondition] = []

        if package is not None:
            conditions.append(
                FieldCondition(
                    key="package",
                    match=MatchValue(value=package),
                )
            )

        if symbol_kind is not None:
            conditions.append(
                FieldCondition(
                    key="symbol_kind",
                    match=MatchValue(value=symbol_kind),
                )
            )

        if not conditions:
            return None

        return Filter(must=conditions)

    async def search(
        self,
        query: str,
        limit: int = 10,
        package: Optional[str] = None,
        symbol_kind: Optional[str] = None,
    ) -> List[SearchResult]:
        """Search for chunks matching the query.

        Generates an embedding for the query text and performs a similarity
        search in Qdrant. Results can be filtered by package and/or symbol_kind.
        Results are returned ordered by similarity score (highest first).

        Args:
            query: Search query text
            limit: Maximum number of results to return (default: 10)
            package: Optional package filter - only return chunks from this package
            symbol_kind: Optional symbol kind filter - only return chunks with
                this symbol kind (function, class, method, variable, type, module)

        Returns:
            List of SearchResult instances ordered by score (highest first)

        Raises:
            VectorStoreConfigurationError: If embedding service is not configured
                or collection doesn't exist
            EmbeddingServiceError: If embedding generation fails
            EmbeddingValidationError: If query is empty

        Example:
            >>> results = await service.search(
            ...     query="How to generate SCIP indexes?",
            ...     limit=5,
            ...     package="ArchonDocumentationMCPTools",
            ...     symbol_kind="function",
            ... )
            >>> for result in results:
            ...     print(f"{result.score:.2f}: {result.symbol_name} - {result.content[:50]}")

        Validates:
            Requirements 3.1, 3.2, 3.3, 3.4, 5.1, 5.2, 5.3, 5.4, 5.5,
            6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 7.4
        """
        if self._embedding_client is None:
            raise VectorStoreConfigurationError(
                "Embedding service not configured. "
                "Provide embedding_service_url when initializing VectorStoreService."
            )

        query_embedding = await self._embedding_client.embed_single(query)

        search_filter = self._build_search_filter(package=package, symbol_kind=symbol_kind)

        try:
            search_results = self._client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                query_filter=search_filter,
                limit=limit,
                with_payload=True,
            )
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise VectorStoreError(f"Search failed: {e}") from e

        results = [
            self._payload_to_search_result(
                payload=hit.payload or {},
                score=hit.score,
            )
            for hit in search_results
        ]

        logger.debug(
            f"Search returned {len(results)} results for query: {query[:50]}..."
            + (f" (package={package})" if package else "")
            + (f" (symbol_kind={symbol_kind})" if symbol_kind else "")
        )

        return results

    async def delete_by_package(self, package: str) -> int:
        """Delete all chunks for a package.

        Deletes all chunks in the collection that match the specified package
        name. First counts the matching chunks, then performs the deletion.

        Args:
            package: Package name to delete chunks for

        Returns:
            Number of chunks deleted

        Raises:
            VectorStoreError: If the delete operation fails

        Example:
            >>> deleted_count = await service.delete_by_package("ArchonDocumentationMCPTools")
            >>> print(f"Deleted {deleted_count} chunks")

        Validates:
            Requirement 5.1
        """
        package_filter = Filter(
            must=[
                FieldCondition(
                    key="package",
                    match=MatchValue(value=package),
                )
            ]
        )

        try:
            count_result = self._client.count(
                collection_name=self.collection_name,
                count_filter=package_filter,
                exact=True,
            )
            count_to_delete = count_result.count
        except Exception as e:
            logger.error(f"Failed to count chunks for package '{package}': {e}")
            raise VectorStoreError(
                f"Failed to count chunks for package '{package}': {e}"
            ) from e

        if count_to_delete == 0:
            logger.debug(f"No chunks found for package '{package}'")
            return 0

        try:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=package_filter,
                wait=True,
            )
            logger.info(
                f"Deleted {count_to_delete} chunks for package '{package}' "
                f"from '{self.collection_name}'"
            )
            return count_to_delete
        except Exception as e:
            logger.error(f"Failed to delete chunks for package '{package}': {e}")
            raise VectorStoreError(
                f"Failed to delete chunks for package '{package}': {e}"
            ) from e

    async def delete_by_arn(self, arn: str) -> int:
        """Delete all chunks with a specific ARN.

        Deletes all chunks in the collection that match the specified ARN.
        First counts the matching chunks, then performs the deletion.

        Args:
            arn: ARN to delete chunks for

        Returns:
            Number of chunks deleted

        Raises:
            VectorStoreError: If the delete operation fails

        Example:
            >>> deleted_count = await service.delete_by_arn(
            ...     "arn:archon:doc:workspace/Package/src/file.ts"
            ... )
            >>> print(f"Deleted {deleted_count} chunks")

        Validates:
            Requirement 4.1
        """
        arn_filter = Filter(
            must=[
                FieldCondition(
                    key="arn",
                    match=MatchValue(value=arn),
                )
            ]
        )

        try:
            count_result = self._client.count(
                collection_name=self.collection_name,
                count_filter=arn_filter,
                exact=True,
            )
            count_to_delete = count_result.count
        except Exception as e:
            logger.error(f"Failed to count chunks for ARN '{arn}': {e}")
            raise VectorStoreError(
                f"Failed to count chunks for ARN '{arn}': {e}"
            ) from e

        if count_to_delete == 0:
            logger.debug(f"No chunks found for ARN '{arn}'")
            return 0

        try:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=arn_filter,
                wait=True,
            )
            logger.info(
                f"Deleted {count_to_delete} chunks for ARN '{arn}' "
                f"from '{self.collection_name}'"
            )
            return count_to_delete
        except Exception as e:
            logger.error(f"Failed to delete chunks for ARN '{arn}': {e}")
            raise VectorStoreError(
                f"Failed to delete chunks for ARN '{arn}': {e}"
            ) from e
