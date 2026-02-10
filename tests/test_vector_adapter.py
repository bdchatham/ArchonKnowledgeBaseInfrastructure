"""Unit tests for VectorSyncAdapter.

This module contains unit tests for the VectorSyncAdapter class, testing
document chunking, chunk ID generation, sync_docs, and prune_stale_chunks
operations.

Feature: sync-service

Source:
- src/sync/vector_adapter.py
- .kiro/specs/sync-service/design.md

Validates:
    Requirements 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.3, 6.4, 6.5, 8.3
"""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.models import GeneratedDoc
from src.sync.vector_adapter import (
    VectorSyncAdapter,
    VectorSyncResult,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    CHARS_PER_TOKEN,
)
from src.vector.models import ArchonChunk
from src.vector.store import VectorStoreService


EMBEDDING_DIMENSION = 768


class MockEmbeddingClient:
    """Mock embedding client for unit tests."""

    def __init__(self, dimension: int = EMBEDDING_DIMENSION):
        self._dimension = dimension

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate deterministic embeddings based on text hash."""
        embeddings = []
        for text in texts:
            text_hash = hash(text)
            embedding = [(text_hash + i) % 1000 / 1000.0 for i in range(self._dimension)]
            embeddings.append(embedding)
        return embeddings

    async def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        embeddings = await self.embed([text])
        return embeddings[0]

    async def close(self):
        """Close the mock client (no-op)."""
        pass


def create_test_doc(
    source_path: str = "src/main.py",
    doc_path: str = "src/main.archon.md",
    content: str = "Test documentation content for the main module.",
    arn: str = "arn:archon:doc:workspace/TestPackage/src/main.py",
    referenced_arns: Optional[List[str]] = None,
) -> GeneratedDoc:
    """Create a test GeneratedDoc with default values."""
    return GeneratedDoc(
        source_path=source_path,
        doc_path=doc_path,
        content=content,
        arn=arn,
        referenced_arns=referenced_arns or [],
    )


class TestChunkDocument:
    """Tests for chunk_document method.
    
    Validates: Requirements 4.1, 4.3
    """

    @pytest.fixture
    def adapter(self):
        """Create a VectorSyncAdapter with mock dependencies."""
        mock_store = MagicMock(spec=VectorStoreService)
        mock_embedding = MockEmbeddingClient()
        return VectorSyncAdapter(
            vector_store=mock_store,
            embedding_client=mock_embedding,
            chunk_size=DEFAULT_CHUNK_SIZE,
            chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        )

    def test_chunk_short_document_single_chunk(self, adapter):
        """Test that short documents produce a single chunk.
        
        Validates: Requirement 4.1
        """
        doc = create_test_doc(
            content="Short content that fits in one chunk.",
            arn="arn:archon:doc:workspace/TestPackage/src/main.py#func",
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) == 1, "Short document should produce 1 chunk"
        assert chunks[0].content == doc.content
        assert chunks[0].arn == doc.arn
        assert chunks[0].chunk_index == 0

    def test_chunk_long_document_multiple_chunks(self, adapter):
        """Test that long documents produce multiple chunks.
        
        Validates: Requirement 4.1
        """
        long_content = "This is a test sentence. " * 200
        doc = create_test_doc(content=long_content)
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) > 1, "Long document should produce multiple chunks"
        
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i
            assert chunk.arn == doc.arn

    def test_chunk_preserves_arn_metadata(self, adapter):
        """Test that chunking preserves ARN metadata in each chunk.
        
        Validates: Requirement 4.3
        """
        referenced_arns = [
            "arn:archon:code:workspace/TestPackage/src/utils.py#helper",
            "arn:archon:code:workspace/TestPackage/src/lib.py#process",
        ]
        doc = create_test_doc(
            arn="arn:archon:doc:workspace/TestPackage/src/main.py#my_function",
            referenced_arns=referenced_arns,
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.arn == doc.arn
            assert chunk.related_arns == referenced_arns
            assert chunk.symbol_name == "my_function"

    def test_chunk_empty_document_returns_empty_list(self, adapter):
        """Test that empty documents return empty chunk list.
        
        Validates: Requirement 4.1
        """
        doc = create_test_doc(content="")
        
        chunks = adapter.chunk_document(doc)
        
        assert chunks == [], "Empty document should return empty list"

    def test_chunk_whitespace_only_document_returns_empty_list(self, adapter):
        """Test that whitespace-only documents return empty chunk list.
        
        Validates: Requirement 4.1
        """
        doc = create_test_doc(content="   \n\t  \n  ")
        
        chunks = adapter.chunk_document(doc)
        
        assert chunks == [], "Whitespace-only document should return empty list"

    def test_chunk_extracts_symbol_name_from_arn(self, adapter):
        """Test that symbol name is extracted from ARN.
        
        Validates: Requirement 4.3
        """
        doc = create_test_doc(
            arn="arn:archon:doc:workspace/TestPackage/src/main.py#my_function",
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        assert chunks[0].symbol_name == "my_function"

    def test_chunk_extracts_package_from_arn(self, adapter):
        """Test that package is extracted from ARN.
        
        Validates: Requirement 4.3
        """
        doc = create_test_doc(
            arn="arn:archon:doc:workspace/MyPackage/src/main.py#func",
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        assert chunks[0].package == "MyPackage"

    def test_chunk_sets_source_to_doc_path(self, adapter):
        """Test that chunk source is set to doc_path.
        
        Validates: Requirement 4.3
        """
        doc = create_test_doc(
            doc_path="src/main.archon.md",
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        assert chunks[0].source == "src/main.archon.md"


class TestGenerateChunkId:
    """Tests for generate_chunk_id method.
    
    Validates: Requirements 5.2, 8.3
    """

    @pytest.fixture
    def adapter(self):
        """Create a VectorSyncAdapter with mock dependencies."""
        mock_store = MagicMock(spec=VectorStoreService)
        return VectorSyncAdapter(vector_store=mock_store)

    def test_generate_chunk_id_deterministic(self, adapter):
        """Test that chunk ID generation is deterministic.
        
        Validates: Requirement 8.3
        """
        arn = "arn:archon:doc:workspace/TestPackage/src/main.py"
        chunk_index = 0
        
        id1 = adapter.generate_chunk_id(arn, chunk_index)
        id2 = adapter.generate_chunk_id(arn, chunk_index)
        
        assert id1 == id2, "Same inputs should produce same ID"

    def test_generate_chunk_id_different_for_different_arns(self, adapter):
        """Test that different ARNs produce different IDs.
        
        Validates: Requirement 5.2
        """
        arn1 = "arn:archon:doc:workspace/TestPackage/src/main.py"
        arn2 = "arn:archon:doc:workspace/TestPackage/src/other.py"
        
        id1 = adapter.generate_chunk_id(arn1, 0)
        id2 = adapter.generate_chunk_id(arn2, 0)
        
        assert id1 != id2, "Different ARNs should produce different IDs"

    def test_generate_chunk_id_different_for_different_indices(self, adapter):
        """Test that different chunk indices produce different IDs.
        
        Validates: Requirement 5.2
        """
        arn = "arn:archon:doc:workspace/TestPackage/src/main.py"
        
        id1 = adapter.generate_chunk_id(arn, 0)
        id2 = adapter.generate_chunk_id(arn, 1)
        
        assert id1 != id2, "Different indices should produce different IDs"

    def test_generate_chunk_id_returns_hex_string(self, adapter):
        """Test that chunk ID is a valid hex string.
        
        Validates: Requirement 5.2
        """
        arn = "arn:archon:doc:workspace/TestPackage/src/main.py"
        
        chunk_id = adapter.generate_chunk_id(arn, 0)
        
        assert isinstance(chunk_id, str)
        assert len(chunk_id) == 32, "ID should be 32 hex characters"
        int(chunk_id, 16)


class TestSyncDocs:
    """Tests for sync_docs method.
    
    Validates: Requirements 4.1, 4.2, 4.4, 5.1, 5.3
    """

    @pytest.fixture
    def qdrant_client(self):
        """Create an in-memory Qdrant client for testing."""
        client = QdrantClient(":memory:")
        client.create_collection(
            collection_name="archon-docs",
            vectors_config=VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        return client

    @pytest.fixture
    def mock_vector_store(self, qdrant_client):
        """Create a mock VectorStoreService."""
        store = MagicMock(spec=VectorStoreService)
        store.client = qdrant_client
        store.collection_name = "archon-docs"
        store.embedding_client = MockEmbeddingClient()
        
        async def mock_upsert(chunks):
            embedding_client = MockEmbeddingClient()
            for chunk in chunks:
                embedding = await embedding_client.embed_single(chunk.content)
                point = PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={
                        "content": chunk.content,
                        "source": chunk.source,
                        "chunk_index": chunk.chunk_index,
                        "arn": chunk.arn,
                        "related_arns": chunk.related_arns,
                        "symbol_name": chunk.symbol_name,
                        "symbol_kind": chunk.symbol_kind,
                        "package": chunk.package,
                    },
                )
                qdrant_client.upsert(collection_name="archon-docs", points=[point])
            return len(chunks)
        
        store.upsert = AsyncMock(side_effect=mock_upsert)
        store.delete_by_package = AsyncMock(return_value=0)
        
        return store

    @pytest.fixture
    def adapter(self, mock_vector_store):
        """Create a VectorSyncAdapter with mock dependencies."""
        return VectorSyncAdapter(
            vector_store=mock_vector_store,
            embedding_client=MockEmbeddingClient(),
        )

    def test_sync_docs_empty_list_returns_zero_counts(self, adapter):
        """Test that syncing empty docs returns zero counts.
        
        Validates: Requirement 5.1
        """
        async def run_test():
            result = await adapter.sync_docs("TestPackage", [])
            
            assert result.chunks_upserted == 0
            assert result.chunks_pruned == 0
            assert result.errors == []

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_upserts_chunks(self, adapter, mock_vector_store):
        """Test that sync_docs upserts chunks to vector store.
        
        Validates: Requirements 4.1, 5.1, 5.3
        """
        docs = [
            create_test_doc(
                content="Documentation for function A.",
                arn="arn:archon:doc:workspace/TestPackage/src/a.py#func_a",
            ),
            create_test_doc(
                content="Documentation for function B.",
                arn="arn:archon:doc:workspace/TestPackage/src/b.py#func_b",
            ),
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert result.chunks_upserted == 2
            assert mock_vector_store.upsert.called

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_sets_package_on_chunks(self, adapter, mock_vector_store):
        """Test that sync_docs sets package on all chunks.
        
        Validates: Requirement 4.3
        """
        docs = [
            create_test_doc(
                content="Documentation content.",
                arn="arn:archon:doc:workspace/OtherPackage/src/a.py#func",
            ),
        ]

        async def run_test():
            await adapter.sync_docs("TargetPackage", docs)
            
            call_args = mock_vector_store.upsert.call_args
            chunks = call_args[0][0]
            
            for chunk in chunks:
                assert chunk.package == "TargetPackage"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_returns_result_with_counts(self, adapter):
        """Test that sync_docs returns VectorSyncResult with counts.
        
        Validates: Requirement 5.3
        """
        docs = [
            create_test_doc(
                content="Short doc.",
                arn="arn:archon:doc:workspace/TestPackage/src/a.py#func",
            ),
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert isinstance(result, VectorSyncResult)
            assert result.chunks_upserted >= 0
            assert result.chunks_pruned >= 0
            assert isinstance(result.errors, list)

        asyncio.get_event_loop().run_until_complete(run_test())


class TestPruneStaleChunks:
    """Tests for prune_stale_chunks method.
    
    Validates: Requirements 6.3, 6.4, 6.5
    """

    @pytest.fixture
    def qdrant_client(self):
        """Create an in-memory Qdrant client for testing."""
        client = QdrantClient(":memory:")
        client.create_collection(
            collection_name="archon-docs",
            vectors_config=VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        return client

    @pytest.fixture
    def mock_vector_store(self, qdrant_client):
        """Create a mock VectorStoreService."""
        store = MagicMock(spec=VectorStoreService)
        store.client = qdrant_client
        store.collection_name = "archon-docs"
        store.delete_by_package = AsyncMock(return_value=0)
        return store

    @pytest.fixture
    def adapter(self, mock_vector_store):
        """Create a VectorSyncAdapter with mock dependencies."""
        return VectorSyncAdapter(vector_store=mock_vector_store)

    def test_prune_stale_chunks_empty_current_arns_deletes_all(
        self, adapter, mock_vector_store
    ):
        """Test that empty current_arns deletes all chunks for package.
        
        Validates: Requirement 6.3
        """
        mock_vector_store.delete_by_package = AsyncMock(return_value=5)

        async def run_test():
            pruned = await adapter.prune_stale_chunks("TestPackage", [])
            
            assert pruned == 5
            mock_vector_store.delete_by_package.assert_called_once_with("TestPackage")

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_stale_chunks_returns_count(self, adapter, qdrant_client):
        """Test that prune_stale_chunks returns count of pruned chunks.
        
        Validates: Requirement 6.5
        """
        embedding_client = MockEmbeddingClient()
        
        async def seed_and_test():
            embedding = await embedding_client.embed_single("test content")
            point = PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "content": "test content",
                    "source": "test.py",
                    "chunk_index": 0,
                    "arn": "arn:archon:doc:workspace/TestPackage/src/stale.py",
                    "related_arns": [],
                    "symbol_name": "stale",
                    "symbol_kind": "function",
                    "package": "TestPackage",
                },
            )
            qdrant_client.upsert(collection_name="archon-docs", points=[point])
            
            current_arns = ["arn:archon:doc:workspace/TestPackage/src/current.py"]
            pruned = await adapter.prune_stale_chunks("TestPackage", current_arns)
            
            assert isinstance(pruned, int)

        asyncio.get_event_loop().run_until_complete(seed_and_test())


class TestAdapterInitialization:
    """Tests for VectorSyncAdapter initialization.
    
    Validates: Requirements 4.1, 5.1
    """

    def test_adapter_accepts_vector_store(self):
        """Test that adapter accepts vector_store parameter."""
        mock_store = MagicMock(spec=VectorStoreService)
        
        adapter = VectorSyncAdapter(vector_store=mock_store)
        
        assert adapter.vector_store == mock_store

    def test_adapter_accepts_embedding_client(self):
        """Test that adapter accepts embedding_client parameter."""
        mock_store = MagicMock(spec=VectorStoreService)
        mock_embedding = MockEmbeddingClient()
        
        adapter = VectorSyncAdapter(
            vector_store=mock_store,
            embedding_client=mock_embedding,
        )
        
        assert adapter.embedding_client == mock_embedding

    def test_adapter_accepts_chunk_size(self):
        """Test that adapter accepts chunk_size parameter."""
        mock_store = MagicMock(spec=VectorStoreService)
        
        adapter = VectorSyncAdapter(
            vector_store=mock_store,
            chunk_size=800,
        )
        
        assert adapter.chunk_size == 800

    def test_adapter_accepts_chunk_overlap(self):
        """Test that adapter accepts chunk_overlap parameter."""
        mock_store = MagicMock(spec=VectorStoreService)
        
        adapter = VectorSyncAdapter(
            vector_store=mock_store,
            chunk_overlap=150,
        )
        
        assert adapter.chunk_overlap == 150

    def test_adapter_uses_default_chunk_size(self):
        """Test that adapter uses default chunk_size."""
        mock_store = MagicMock(spec=VectorStoreService)
        
        adapter = VectorSyncAdapter(vector_store=mock_store)
        
        assert adapter.chunk_size == DEFAULT_CHUNK_SIZE

    def test_adapter_uses_default_chunk_overlap(self):
        """Test that adapter uses default chunk_overlap."""
        mock_store = MagicMock(spec=VectorStoreService)
        
        adapter = VectorSyncAdapter(vector_store=mock_store)
        
        assert adapter.chunk_overlap == DEFAULT_CHUNK_OVERLAP


class TestChunkDocumentEdgeCases:
    """Additional tests for chunk_document edge cases.
    
    Validates: Requirements 4.1, 4.3
    """

    @pytest.fixture
    def adapter(self):
        """Create a VectorSyncAdapter with mock dependencies."""
        mock_store = MagicMock(spec=VectorStoreService)
        mock_embedding = MockEmbeddingClient()
        return VectorSyncAdapter(
            vector_store=mock_store,
            embedding_client=mock_embedding,
            chunk_size=DEFAULT_CHUNK_SIZE,
            chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        )

    def test_chunk_document_at_exact_chunk_boundary(self, adapter):
        """Test chunking when content is exactly at chunk size boundary.
        
        Validates: Requirement 4.1
        """
        target_chars = DEFAULT_CHUNK_SIZE * CHARS_PER_TOKEN
        content = "A" * target_chars
        doc = create_test_doc(content=content)
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) == 1, "Content at exact boundary should produce 1 chunk"
        assert chunks[0].chunk_index == 0

    def test_chunk_document_just_over_boundary(self, adapter):
        """Test chunking when content is just over chunk size boundary.
        
        Validates: Requirement 4.1
        """
        target_chars = DEFAULT_CHUNK_SIZE * CHARS_PER_TOKEN
        content = "A" * (target_chars + 100)
        doc = create_test_doc(content=content)
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 2, "Content over boundary should produce multiple chunks"

    def test_chunk_document_preserves_sentence_boundaries(self, adapter):
        """Test that chunking respects sentence boundaries when possible.
        
        Validates: Requirement 4.1
        """
        sentence = "This is a complete sentence. "
        content = sentence * 100
        doc = create_test_doc(content=content)
        
        chunks = adapter.chunk_document(doc)
        
        for chunk in chunks:
            if chunk.chunk_index < len(chunks) - 1:
                assert chunk.content.endswith(".") or chunk.content.endswith(". "), \
                    "Non-final chunks should end at sentence boundaries when possible"

    def test_chunk_document_with_newlines(self, adapter):
        """Test chunking content with newlines.
        
        Validates: Requirement 4.1
        """
        paragraph = "This is a paragraph.\n\n"
        content = paragraph * 100
        doc = create_test_doc(content=content)
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.content.strip(), "Chunks should have non-empty content"

    def test_chunk_document_with_code_blocks(self, adapter):
        """Test chunking content with code blocks.
        
        Validates: Requirement 4.1
        """
        content = """
# Function Documentation

This function does something important.

```python
def example_function():
    return "Hello, World!"
```

The function returns a greeting string.
""" * 20
        doc = create_test_doc(content=content)
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.content.strip(), "Chunks should have non-empty content"

    def test_chunk_document_with_unicode_content(self, adapter):
        """Test chunking content with unicode characters.
        
        Validates: Requirement 4.1
        """
        content = "Unicode test: 你好世界 🌍 émojis and spëcial çharacters. " * 100
        doc = create_test_doc(content=content)
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.content.strip(), "Chunks should have non-empty content"

    def test_chunk_document_extracts_symbol_kind_from_doc_arn(self, adapter):
        """Test that symbol kind is extracted correctly from doc ARN.
        
        Validates: Requirement 4.3
        """
        doc = create_test_doc(
            arn="arn:archon:doc:workspace/TestPackage/src/main.py#my_function",
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        assert chunks[0].symbol_kind == "module"

    def test_chunk_document_extracts_symbol_kind_from_code_arn(self, adapter):
        """Test that symbol kind is extracted correctly from code ARN.
        
        Validates: Requirement 4.3
        """
        doc = create_test_doc(
            arn="arn:archon:code:workspace/TestPackage/src/main.py#my_function",
        )
        
        chunks = adapter.chunk_document(doc)
        
        assert len(chunks) >= 1
        assert chunks[0].symbol_kind == "function"


class TestErrorHandling:
    """Tests for error handling in VectorSyncAdapter.
    
    Validates: Requirements 4.4, 5.3
    """

    @pytest.fixture
    def mock_vector_store(self):
        """Create a mock VectorStoreService."""
        store = MagicMock(spec=VectorStoreService)
        store.collection_name = "archon-docs"
        return store

    @pytest.fixture
    def adapter(self, mock_vector_store):
        """Create a VectorSyncAdapter with mock dependencies."""
        return VectorSyncAdapter(
            vector_store=mock_vector_store,
            embedding_client=MockEmbeddingClient(),
        )

    def test_sync_docs_handles_upsert_vector_store_error(self, adapter, mock_vector_store):
        """Test that sync_docs handles VectorStoreError during upsert.
        
        Validates: Requirement 5.3
        """
        from src.vector.store import VectorStoreError
        
        mock_vector_store.upsert = AsyncMock(
            side_effect=VectorStoreError("Connection failed")
        )
        mock_vector_store.delete_by_package = AsyncMock(return_value=0)
        
        docs = [
            create_test_doc(
                content="Test content for error handling.",
                arn="arn:archon:doc:workspace/TestPackage/src/a.py#func",
            ),
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert result.chunks_upserted == 0
            assert len(result.errors) >= 1
            assert "Failed to upsert chunks" in result.errors[0]

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_handles_unexpected_upsert_error(self, adapter, mock_vector_store):
        """Test that sync_docs handles unexpected errors during upsert.
        
        Validates: Requirement 5.3
        """
        mock_vector_store.upsert = AsyncMock(
            side_effect=RuntimeError("Unexpected error")
        )
        mock_vector_store.delete_by_package = AsyncMock(return_value=0)
        
        docs = [
            create_test_doc(
                content="Test content for error handling.",
                arn="arn:archon:doc:workspace/TestPackage/src/a.py#func",
            ),
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert result.chunks_upserted == 0
            assert len(result.errors) >= 1
            assert "Unexpected error" in result.errors[0]

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_handles_prune_error(self, adapter, mock_vector_store):
        """Test that sync_docs handles errors during pruning.
        
        Validates: Requirement 5.3
        """
        mock_vector_store.upsert = AsyncMock(return_value=1)
        mock_vector_store.delete_by_package = AsyncMock(
            side_effect=Exception("Prune failed")
        )
        
        mock_client = MagicMock()
        mock_client.count.side_effect = Exception("Count failed")
        mock_vector_store.client = mock_client
        
        docs = [
            create_test_doc(
                content="Test content for error handling.",
                arn="arn:archon:doc:workspace/TestPackage/src/a.py#func",
            ),
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert result.chunks_upserted == 1
            assert len(result.errors) >= 1
            assert "Failed to prune stale chunks" in result.errors[0]

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_handles_chunking_error(self, adapter, mock_vector_store):
        """Test that sync_docs handles errors during document chunking.
        
        When chunk_document raises an exception, sync_docs should catch it
        and add an error to the result.
        
        Validates: Requirement 4.4
        """
        mock_vector_store.upsert = AsyncMock(return_value=0)
        mock_vector_store.delete_by_package = AsyncMock(return_value=0)
        
        doc = create_test_doc(
            content="Valid content.",
            arn="arn:archon:doc:workspace/TestPackage/src/a.py#func",
        )
        
        with patch.object(adapter, 'chunk_document', side_effect=ValueError("Chunking failed")):
            async def run_test():
                result = await adapter.sync_docs("TestPackage", [doc])
                
                assert len(result.errors) >= 1
                assert "Failed to chunk document" in result.errors[0]

            asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_continues_after_single_doc_error(self, adapter, mock_vector_store):
        """Test that sync_docs continues processing after a single doc error.
        
        When one document fails to chunk, sync_docs should continue with
        the remaining documents and report the error.
        
        Validates: Requirement 4.4
        """
        mock_vector_store.upsert = AsyncMock(return_value=1)
        mock_vector_store.delete_by_package = AsyncMock(return_value=0)
        
        bad_doc = create_test_doc(
            content="Bad content.",
            arn="arn:archon:doc:workspace/TestPackage/src/bad.py#func",
        )
        
        good_doc = create_test_doc(
            content="Valid content for good doc.",
            arn="arn:archon:doc:workspace/TestPackage/src/good.py#func",
        )
        
        original_chunk_document = adapter.chunk_document
        call_count = [0]
        
        def mock_chunk_document(doc):
            call_count[0] += 1
            if doc.arn == bad_doc.arn:
                raise ValueError("Chunking failed for bad doc")
            return original_chunk_document(doc)
        
        with patch.object(adapter, 'chunk_document', side_effect=mock_chunk_document):
            async def run_test():
                result = await adapter.sync_docs("TestPackage", [bad_doc, good_doc])
                
                assert result.chunks_upserted >= 0
                assert len(result.errors) >= 1
                assert "Failed to chunk document" in result.errors[0]

            asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_handles_none_content_gracefully(self, adapter, mock_vector_store):
        """Test that sync_docs handles None content gracefully.
        
        Documents with None content should produce empty chunk lists,
        not errors.
        
        Validates: Requirement 4.1
        """
        mock_vector_store.upsert = AsyncMock(return_value=0)
        mock_vector_store.delete_by_package = AsyncMock(return_value=0)
        
        doc = create_test_doc(
            content="Valid content.",
            arn="arn:archon:doc:workspace/TestPackage/src/a.py#func",
        )
        doc.content = None

        async def run_test():
            result = await adapter.sync_docs("TestPackage", [doc])
            
            assert result.chunks_upserted == 0
            assert len(result.errors) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestBatchEmbeddingAndUpsert:
    """Tests for batch embedding and upsert operations.
    
    Validates: Requirements 4.2, 5.1, 5.3
    """

    @pytest.fixture
    def qdrant_client(self):
        """Create an in-memory Qdrant client for testing."""
        client = QdrantClient(":memory:")
        client.create_collection(
            collection_name="archon-docs",
            vectors_config=VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        return client

    @pytest.fixture
    def mock_vector_store(self, qdrant_client):
        """Create a mock VectorStoreService."""
        store = MagicMock(spec=VectorStoreService)
        store.client = qdrant_client
        store.collection_name = "archon-docs"
        store.embedding_client = MockEmbeddingClient()
        
        async def mock_upsert(chunks):
            embedding_client = MockEmbeddingClient()
            for chunk in chunks:
                embedding = await embedding_client.embed_single(chunk.content)
                point = PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={
                        "content": chunk.content,
                        "source": chunk.source,
                        "chunk_index": chunk.chunk_index,
                        "arn": chunk.arn,
                        "related_arns": chunk.related_arns,
                        "symbol_name": chunk.symbol_name,
                        "symbol_kind": chunk.symbol_kind,
                        "package": chunk.package,
                    },
                )
                qdrant_client.upsert(collection_name="archon-docs", points=[point])
            return len(chunks)
        
        store.upsert = AsyncMock(side_effect=mock_upsert)
        store.delete_by_package = AsyncMock(return_value=0)
        
        return store

    @pytest.fixture
    def adapter(self, mock_vector_store):
        """Create a VectorSyncAdapter with mock dependencies."""
        return VectorSyncAdapter(
            vector_store=mock_vector_store,
            embedding_client=MockEmbeddingClient(),
        )

    def test_sync_docs_with_multiple_documents(self, adapter, mock_vector_store):
        """Test sync_docs with multiple documents.
        
        Validates: Requirements 4.2, 5.1
        """
        docs = [
            create_test_doc(
                content=f"Documentation for function {i}.",
                arn=f"arn:archon:doc:workspace/TestPackage/src/file{i}.py#func{i}",
            )
            for i in range(5)
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert result.chunks_upserted == 5
            assert mock_vector_store.upsert.called

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_with_large_documents_creates_multiple_chunks(
        self, adapter, mock_vector_store
    ):
        """Test sync_docs with large documents that create multiple chunks.
        
        Validates: Requirements 4.1, 4.2
        """
        long_content = "This is a test sentence with some content. " * 200
        docs = [
            create_test_doc(
                content=long_content,
                arn="arn:archon:doc:workspace/TestPackage/src/large.py#func",
            ),
        ]

        async def run_test():
            result = await adapter.sync_docs("TestPackage", docs)
            
            assert result.chunks_upserted > 1
            assert mock_vector_store.upsert.called
            
            call_args = mock_vector_store.upsert.call_args
            chunks = call_args[0][0]
            assert len(chunks) > 1

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_docs_preserves_all_metadata_in_batch(
        self, adapter, mock_vector_store
    ):
        """Test that sync_docs preserves all metadata when batching.
        
        Validates: Requirements 4.3, 5.1
        """
        referenced_arns = [
            "arn:archon:code:workspace/TestPackage/src/utils.py#helper",
        ]
        docs = [
            create_test_doc(
                content="Documentation with references.",
                arn="arn:archon:doc:workspace/TestPackage/src/main.py#my_function",
                referenced_arns=referenced_arns,
            ),
        ]

        async def run_test():
            await adapter.sync_docs("TestPackage", docs)
            
            call_args = mock_vector_store.upsert.call_args
            chunks = call_args[0][0]
            
            assert len(chunks) >= 1
            chunk = chunks[0]
            assert chunk.arn == docs[0].arn
            assert chunk.related_arns == referenced_arns
            assert chunk.symbol_name == "my_function"
            assert chunk.package == "TestPackage"

        asyncio.get_event_loop().run_until_complete(run_test())
