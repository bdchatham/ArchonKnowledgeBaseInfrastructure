"""Vector sync adapter for syncing Archon docs to the vector store.

This module provides an adapter that bridges Archon documentation with the
Vector Store. It transforms GeneratedDoc instances into ArchonChunk instances,
generates embeddings, and calls the VectorStoreService to upsert embeddings
and delete stale chunks.

The adapter:
- Chunks GeneratedDoc instances into ArchonChunk segments (400-800 tokens)
- Generates deterministic chunk IDs for idempotent upserts
- Delegates upsert operations to VectorStoreService
- Delegates delete operations to VectorStoreService
- Prunes stale chunks not in current ARN list
- Returns VectorSyncResult with operation counts and errors

Source:
- .kiro/specs/sync-service/design.md (Vector Sync Adapter section)
- .kiro/specs/sync-service/requirements.md (Requirements 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.3, 6.4, 6.5)
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from src.vector.models import ArchonChunk
from src.vector.store import VectorStoreService, VectorStoreError

if TYPE_CHECKING:
    from src.common.embedding import EmbeddingServiceClient
    from src.sync.models import GeneratedDoc

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 32
DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 100
CHARS_PER_TOKEN = 4


@dataclass
class VectorSyncResult:
    """Result of a vector sync operation.

    This dataclass captures the outcome of syncing Archon documentation
    to the vector store, including counts of upserted and pruned chunks
    and any errors encountered during the operation.

    Attributes:
        chunks_upserted: Count of chunks successfully upserted to the vector store
        chunks_pruned: Count of stale chunks pruned from the vector store
        errors: List of error messages encountered during the sync operation

    Example:
        result = VectorSyncResult(
            chunks_upserted=10,
            chunks_pruned=2,
            errors=[],
        )

    Validates: Requirements 4.1, 5.1, 6.3
    """

    chunks_upserted: int
    chunks_pruned: int
    errors: list[str] = field(default_factory=list)


class VectorSyncAdapterError(Exception):
    """Raised when vector sync adapter operations fail."""

    pass


class VectorSyncAdapter:
    """Adapter for syncing Archon docs to the vector store.

    The VectorSyncAdapter bridges Archon documentation (GeneratedDoc instances)
    with the Vector Store. It chunks documents, generates embeddings, and
    delegates upsert and delete operations to the VectorStoreService.

    The adapter performs these main operations:
    1. chunk_document(): Splits GeneratedDoc into ArchonChunk segments
    2. generate_chunk_id(): Creates deterministic IDs for idempotent upserts
    3. sync_docs(): Chunks, embeds, and upserts documentation for a package
    4. prune_stale_chunks(): Removes chunks not in current ARN list
    5. sync_package(): Legacy method for upserting pre-chunked ArchonChunks
    6. delete_package(): Deletes all chunks for a package

    Usage:
        vector_store = VectorStoreService(
            qdrant_url="http://localhost:6333",
            embedding_service_url="http://embedding-svc:8000",
        )
        embedding_client = EmbeddingServiceClient("http://embedding-svc:8000")
        adapter = VectorSyncAdapter(vector_store, embedding_client)

        # Sync Archon documentation
        docs = [GeneratedDoc(...), GeneratedDoc(...)]
        result = await adapter.sync_docs("MyPackage", docs)
        print(f"Upserted {result.chunks_upserted} chunks")

    Validates: Requirements 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.3, 6.4, 6.5

    Source:
    - .kiro/specs/sync-service/design.md (Vector Sync Adapter section)
    - .kiro/specs/sync-service/requirements.md
    """

    def __init__(
        self,
        vector_store: VectorStoreService,
        embedding_client: Optional["EmbeddingServiceClient"] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        """Initialize the sync adapter.

        Args:
            vector_store: Vector store service instance for upsert and delete
                operations. The service must be configured with an embedding
                service URL for upsert operations to work.
            embedding_client: Embedding service client for generating embeddings.
                If not provided, uses the vector_store's embedding client.
            chunk_size: Target size for chunks in tokens (default: 600).
                Chunks will be in the 400-800 token range.
            chunk_overlap: Overlap between consecutive chunks in tokens
                (default: 100). Preserves context across chunk boundaries.

        Example:
            >>> vector_store = VectorStoreService(
            ...     qdrant_url="http://localhost:6333",
            ...     embedding_service_url="http://embedding-svc:8000",
            ... )
            >>> embedding_client = EmbeddingServiceClient("http://embedding-svc:8000")
            >>> adapter = VectorSyncAdapter(
            ...     vector_store,
            ...     embedding_client,
            ...     chunk_size=600,
            ...     chunk_overlap=100,
            ... )

        Validates: Requirements 4.1, 5.1
        """
        self._vector_store = vector_store
        self._embedding_client = embedding_client
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    @property
    def vector_store(self) -> VectorStoreService:
        """Get the vector store service instance."""
        return self._vector_store

    @property
    def embedding_client(self) -> Optional["EmbeddingServiceClient"]:
        """Get the embedding service client instance."""
        return self._embedding_client or self._vector_store.embedding_client

    @property
    def chunk_size(self) -> int:
        """Get the target chunk size in tokens."""
        return self._chunk_size

    @property
    def chunk_overlap(self) -> int:
        """Get the chunk overlap in tokens."""
        return self._chunk_overlap

    def _estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in a text string.

        Uses a simple character-based estimation (4 chars per token).
        This is a rough approximation suitable for chunking purposes.

        Args:
            text: Text string to estimate tokens for

        Returns:
            Estimated number of tokens
        """
        return len(text) // CHARS_PER_TOKEN

    def _extract_symbol_info(self, arn: str) -> tuple[Optional[str], Optional[str]]:
        """Extract symbol name and kind from an ARN.

        Parses the ARN to extract the symbol name (after #) and infers
        the symbol kind from the ARN type component.

        ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>

        Args:
            arn: Archon Resource Name to parse

        Returns:
            Tuple of (symbol_name, symbol_kind) or (None, None) if not found
        """
        symbol_name = None
        symbol_kind = None

        if "#" in arn:
            symbol_name = arn.split("#")[-1]

        arn_match = re.match(r"arn:archon:(\w+):", arn)
        if arn_match:
            arn_type = arn_match.group(1)
            if arn_type == "doc":
                symbol_kind = "module"
            elif arn_type == "code":
                symbol_kind = "function"

        return symbol_name, symbol_kind

    def _extract_package_from_arn(self, arn: str) -> str:
        """Extract package name from an ARN.

        Parses the ARN to extract the package component.

        ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>

        Args:
            arn: Archon Resource Name to parse

        Returns:
            Package name or empty string if not found
        """
        arn_match = re.match(r"arn:archon:\w+:[^/]+/([^/]+)/", arn)
        if arn_match:
            return arn_match.group(1)
        return ""

    def chunk_document(self, doc: "GeneratedDoc") -> list[ArchonChunk]:
        """Chunk a document into segments suitable for embedding.

        Splits document content into chunks of 400-800 tokens with overlap
        for context preservation. Each chunk preserves ARN metadata from
        the source document.

        The chunking algorithm:
        1. Estimates target chunk size in characters (chunk_size * 4)
        2. Splits content at sentence boundaries when possible
        3. Applies overlap between consecutive chunks
        4. Preserves all ARN metadata in each chunk

        Args:
            doc: Generated Archon documentation to chunk

        Returns:
            List of ArchonChunk instances with preserved metadata

        Example:
            >>> doc = GeneratedDoc(
            ...     source_path="src/main.py",
            ...     doc_path="src/main.archon.md",
            ...     content="Long documentation content...",
            ...     arn="arn:archon:doc:workspace/Package/src/main.py",
            ...     referenced_arns=["arn:archon:code:workspace/Package/src/utils.py#helper"],
            ... )
            >>> chunks = adapter.chunk_document(doc)
            >>> print(f"Created {len(chunks)} chunks")

        Validates: Requirements 4.1, 4.3
        """
        content = doc.content
        if not content or not content.strip():
            return []

        symbol_name, symbol_kind = self._extract_symbol_info(doc.arn)
        package = self._extract_package_from_arn(doc.arn) or ""

        target_chars = self._chunk_size * CHARS_PER_TOKEN
        overlap_chars = self._chunk_overlap * CHARS_PER_TOKEN

        if len(content) <= target_chars:
            return [
                ArchonChunk(
                    content=content,
                    source=doc.doc_path,
                    chunk_index=0,
                    arn=doc.arn,
                    related_arns=doc.referenced_arns,
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                    package=package,
                )
            ]

        chunks: list[ArchonChunk] = []
        start = 0
        chunk_index = 0

        while start < len(content):
            end = min(start + target_chars, len(content))

            if end < len(content):
                sentence_end = content.rfind(". ", start, end)
                if sentence_end > start + (target_chars // 2):
                    end = sentence_end + 1
                else:
                    newline_end = content.rfind("\n", start, end)
                    if newline_end > start + (target_chars // 2):
                        end = newline_end + 1
                    else:
                        space_end = content.rfind(" ", start, end)
                        if space_end > start + (target_chars // 2):
                            end = space_end + 1

            chunk_content = content[start:end].strip()

            if chunk_content:
                chunks.append(
                    ArchonChunk(
                        content=chunk_content,
                        source=doc.doc_path,
                        chunk_index=chunk_index,
                        arn=doc.arn,
                        related_arns=doc.referenced_arns,
                        symbol_name=symbol_name,
                        symbol_kind=symbol_kind,
                        package=package,
                    )
                )
                chunk_index += 1

            if end >= len(content):
                break

            start = max(start + 1, end - overlap_chars)

        logger.debug(
            f"Chunked document '{doc.doc_path}' into {len(chunks)} chunks "
            f"(content length: {len(content)} chars)"
        )

        return chunks

    def generate_chunk_id(self, arn: str, chunk_index: int) -> str:
        """Generate a deterministic point ID for a chunk.

        Creates a unique, deterministic ID by hashing the ARN and chunk index.
        This ensures idempotent upserts - the same ARN and chunk index will
        always produce the same ID.

        The ID is a 32-character hexadecimal string derived from SHA-256.

        Args:
            arn: ARN of the documented symbol/file
            chunk_index: Index of the chunk within the document

        Returns:
            Deterministic 32-character hexadecimal point ID

        Example:
            >>> chunk_id = adapter.generate_chunk_id(
            ...     "arn:archon:doc:workspace/Package/src/main.py",
            ...     0,
            ... )
            >>> print(chunk_id)  # e.g., "a1b2c3d4e5f6..."

        Validates: Requirements 5.2, 8.3
        """
        id_input = f"{arn}:{chunk_index}"
        hash_bytes = hashlib.sha256(id_input.encode("utf-8")).digest()
        return hash_bytes[:16].hex()

    async def sync_docs(
        self,
        package: str,
        archon_docs: list["GeneratedDoc"],
    ) -> VectorSyncResult:
        """Sync Archon documentation to the Vector Store.

        Performs the complete sync workflow:
        1. Chunk each document into segments
        2. Generate embeddings for chunks (batch of 32)
        3. Upsert chunks to Vector Store with full metadata payload
        4. Prune stale chunks not in current ARN list

        Args:
            package: Package name for the documentation being synced
            archon_docs: List of GeneratedDoc instances to sync

        Returns:
            VectorSyncResult with counts of upserted/pruned chunks and errors

        Example:
            >>> docs = [
            ...     GeneratedDoc(
            ...         source_path="src/main.py",
            ...         doc_path="src/main.archon.md",
            ...         content="Documentation content...",
            ...         arn="arn:archon:doc:workspace/Package/src/main.py",
            ...         referenced_arns=[],
            ...     )
            ... ]
            >>> result = await adapter.sync_docs("Package", docs)
            >>> print(f"Upserted {result.chunks_upserted}, pruned {result.chunks_pruned}")

        Validates: Requirements 4.1, 4.2, 4.4, 5.1, 5.3
        """
        errors: list[str] = []
        chunks_upserted = 0
        chunks_pruned = 0

        if not archon_docs:
            logger.debug(f"No documents to sync for package '{package}'")
            return VectorSyncResult(
                chunks_upserted=0,
                chunks_pruned=0,
                errors=[],
            )

        all_chunks: list[ArchonChunk] = []
        current_arns: list[str] = []

        for doc in archon_docs:
            try:
                doc_chunks = self.chunk_document(doc)
                for chunk in doc_chunks:
                    chunk.package = package
                all_chunks.extend(doc_chunks)
                current_arns.append(doc.arn)
            except Exception as e:
                error_msg = f"Failed to chunk document '{doc.doc_path}': {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        if all_chunks:
            try:
                chunks_upserted = await self._vector_store.upsert(all_chunks)
                logger.info(
                    f"Successfully upserted {chunks_upserted} chunks for package '{package}'"
                )
            except VectorStoreError as e:
                error_msg = f"Failed to upsert chunks for package '{package}': {e}"
                logger.error(error_msg)
                errors.append(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error upserting chunks for package '{package}': {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        try:
            chunks_pruned = await self.prune_stale_chunks(package, current_arns)
        except Exception as e:
            error_msg = f"Failed to prune stale chunks for package '{package}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)

        return VectorSyncResult(
            chunks_upserted=chunks_upserted,
            chunks_pruned=chunks_pruned,
            errors=errors,
        )

    async def prune_stale_chunks(
        self,
        package: str,
        current_arns: list[str],
    ) -> int:
        """Remove chunks not in the current Archon docs.

        Queries for chunks in the package that have ARNs not in the
        current_arns list and deletes them. This ensures the Vector Store
        stays consistent with the current documentation state.

        Args:
            package: Package name to prune chunks for
            current_arns: List of ARNs from the current Archon docs

        Returns:
            Number of chunks pruned

        Raises:
            VectorStoreError: If the prune operation fails

        Example:
            >>> current_arns = [
            ...     "arn:archon:doc:workspace/Package/src/main.py",
            ...     "arn:archon:doc:workspace/Package/src/utils.py",
            ... ]
            >>> pruned = await adapter.prune_stale_chunks("Package", current_arns)
            >>> print(f"Pruned {pruned} stale chunks")

        Validates: Requirements 6.3, 6.4, 6.5
        """
        if not current_arns:
            deleted = await self._vector_store.delete_by_package(package)
            logger.info(f"Pruned all {deleted} chunks for package '{package}' (no current ARNs)")
            return deleted

        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchValue,
            MatchAny,
        )

        try:
            stale_filter = Filter(
                must=[
                    FieldCondition(
                        key="package",
                        match=MatchValue(value=package),
                    ),
                ],
                must_not=[
                    FieldCondition(
                        key="arn",
                        match=MatchAny(any=current_arns),
                    ),
                ],
            )

            count_result = self._vector_store.client.count(
                collection_name=self._vector_store.collection_name,
                count_filter=stale_filter,
                exact=True,
            )
            count_to_prune = count_result.count

            if count_to_prune == 0:
                logger.debug(f"No stale chunks to prune for package '{package}'")
                return 0

            self._vector_store.client.delete(
                collection_name=self._vector_store.collection_name,
                points_selector=stale_filter,
                wait=True,
            )

            logger.info(f"Pruned {count_to_prune} stale chunks for package '{package}'")
            return count_to_prune

        except Exception as e:
            logger.error(f"Failed to prune stale chunks for package '{package}': {e}")
            raise VectorStoreError(
                f"Failed to prune stale chunks for package '{package}': {e}"
            ) from e

    async def sync_package(
        self,
        package: str,
        archon_docs: list[ArchonChunk],
    ) -> VectorSyncResult:
        """Sync pre-chunked Archon documentation for a package.

        Legacy method that upserts pre-chunked ArchonChunk instances to the
        vector store. For new code, prefer sync_docs() which handles chunking.

        The sync operation:
        1. Validates the package name matches all chunks
        2. Upserts all chunks to the vector store
        3. Tracks and reports any errors encountered

        Args:
            package: Package name for the documentation being synced
            archon_docs: List of ArchonChunk instances for the package

        Returns:
            VectorSyncResult with counts of upserted chunks and any errors

        Example:
            >>> chunks = [
            ...     ArchonChunk(
            ...         content="This function generates SCIP indexes...",
            ...         source="Package/src/tools/scip_indexing.archon.md",
            ...         chunk_index=0,
            ...         arn="arn:archon:doc:workspace/Package/src/tools/scip_indexing.ts",
            ...         related_arns=[],
            ...         symbol_name="generateScipIndex",
            ...         symbol_kind="function",
            ...         package="Package",
            ...     )
            ... ]
            >>> result = await adapter.sync_package("Package", chunks)
            >>> print(f"Upserted {result.chunks_upserted} chunks")

        Validates:
            Requirements 2.1, 2.2, 2.3, 2.4
        """
        errors: list[str] = []
        chunks_upserted = 0

        if not archon_docs:
            logger.debug(f"No chunks to sync for package '{package}'")
            return VectorSyncResult(
                chunks_upserted=0,
                chunks_pruned=0,
                errors=[],
            )

        mismatched_packages = [
            chunk for chunk in archon_docs if chunk.package != package
        ]
        if mismatched_packages:
            error_msg = (
                f"Package mismatch: {len(mismatched_packages)} chunks have "
                f"package != '{package}'"
            )
            logger.warning(error_msg)
            errors.append(error_msg)

        try:
            chunks_upserted = await self._vector_store.upsert(archon_docs)
            logger.info(
                f"Successfully synced {chunks_upserted} chunks for package '{package}'"
            )
        except VectorStoreError as e:
            error_msg = f"Failed to upsert chunks for package '{package}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error syncing package '{package}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)

        return VectorSyncResult(
            chunks_upserted=chunks_upserted,
            chunks_pruned=0,
            errors=errors,
        )

    async def delete_package(self, package: str) -> int:
        """Delete all chunks for a package.

        Delegates to VectorStoreService.delete_by_package to remove all
        chunks matching the specified package name from the vector store.

        Args:
            package: Package name to delete chunks for

        Returns:
            Number of chunks deleted

        Raises:
            VectorSyncAdapterError: If the delete operation fails

        Example:
            >>> deleted = await adapter.delete_package("MyPackage")
            >>> print(f"Deleted {deleted} chunks")

        Validates:
            Requirement 5.1
        """
        try:
            deleted_count = await self._vector_store.delete_by_package(package)
            logger.info(f"Deleted {deleted_count} chunks for package '{package}'")
            return deleted_count
        except VectorStoreError as e:
            error_msg = f"Failed to delete chunks for package '{package}': {e}"
            logger.error(error_msg)
            raise VectorSyncAdapterError(error_msg) from e
