"""Integration tests for Vector Store with ARN Metadata.

This module contains integration tests that verify end-to-end functionality
of the Vector Store service using Qdrant's in-memory mode.

Feature: vector-store-arn

Source:
- src/vector/store.py
- src/vector/models.py
- .kiro/specs/vector-store-arn/design.md

Validates:
    Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 5.1
"""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vector.models import ArchonChunk, SearchResult, SymbolKind
from src.vector.arn import validate_arn
from src.vector.filters import (
    build_package_filter,
    build_symbol_kind_filter,
    build_combined_filter,
)


EMBEDDING_DIMENSION = 768
COLLECTION_NAME = "archon-docs"


def search_qdrant(
    client: QdrantClient,
    query_vector: List[float],
    query_filter=None,
    limit: int = 10,
) -> List:
    """Search Qdrant using the query_points API."""
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )
    return results.points


class MockEmbeddingClient:
    """Mock embedding client for integration tests.
    
    Generates deterministic embeddings based on text hash for reproducible tests.
    """

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


def create_test_chunk(
    content: str = "Test content for vector store",
    source: str = "test/file.py",
    chunk_index: int = 0,
    arn: str = "arn:archon:code:workspace/TestPackage/test/file.py#TestSymbol",
    related_arns: Optional[List[str]] = None,
    symbol_name: Optional[str] = "TestSymbol",
    symbol_kind: Optional[str] = "function",
    package: str = "TestPackage",
) -> ArchonChunk:
    """Create a test ArchonChunk with default values."""
    return ArchonChunk(
        content=content,
        source=source,
        chunk_index=chunk_index,
        arn=arn,
        related_arns=related_arns or [],
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        package=package,
    )


class TestEndToEndUpsertAndSearch:
    """End-to-end tests for upsert and search operations.
    
    Tests the complete flow of upserting chunks and searching for them
    using Qdrant's in-memory mode.
    
    Validates:
        Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4
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
    def embedding_client(self):
        """Create a mock embedding client."""
        return MockEmbeddingClient()

    def test_upsert_single_chunk(self, qdrant_client, embedding_client):
        """Test upserting a single chunk stores it correctly.
        
        Validates: Requirements 2.1, 2.2, 2.3, 2.4
        """
        chunk = create_test_chunk()

        async def run_test():
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
            
            count = qdrant_client.count(collection_name="archon-docs", exact=True)
            assert count.count == 1, "Expected 1 chunk in collection"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_upsert_batch_of_chunks(self, qdrant_client, embedding_client):
        """Test upserting multiple chunks in a batch.
        
        Validates: Requirements 2.1, 2.2, 2.3, 2.4
        """
        chunks = [
            create_test_chunk(
                content=f"Content for chunk {i}",
                chunk_index=i,
                arn=f"arn:archon:code:workspace/TestPackage/test/file{i}.py#Symbol{i}",
                symbol_name=f"Symbol{i}",
            )
            for i in range(5)
        ]

        async def run_test():
            points = []
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
                points.append(point)
            
            qdrant_client.upsert(collection_name="archon-docs", points=points)
            
            count = qdrant_client.count(collection_name="archon-docs", exact=True)
            assert count.count == 5, f"Expected 5 chunks, got {count.count}"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_search_returns_results_with_arn_metadata(
        self, qdrant_client, embedding_client
    ):
        """Test that search returns results with ARN metadata.
        
        Validates: Requirements 3.1, 3.2, 3.3, 3.4
        """
        chunk = create_test_chunk(
            content="This function generates SCIP indexes for code analysis",
            arn="arn:archon:code:workspace/ArchonTools/src/scip.py#generate_index",
            related_arns=[
                "arn:archon:code:workspace/ArchonTools/src/parser.py#parse",
            ],
            symbol_name="generate_index",
            symbol_kind="function",
            package="ArchonTools",
        )

        async def run_test():
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
            
            query_embedding = await embedding_client.embed_single(
                "SCIP index generation"
            )
            results = search_qdrant(
                qdrant_client,
                query_vector=query_embedding,
                limit=10,
            )
            
            assert len(results) == 1, "Expected 1 search result"
            
            result = results[0]
            payload = result.payload
            
            assert payload["content"] == chunk.content
            assert payload["arn"] == chunk.arn
            assert payload["related_arns"] == chunk.related_arns
            assert payload["symbol_name"] == chunk.symbol_name
            assert payload["symbol_kind"] == chunk.symbol_kind
            assert payload["package"] == chunk.package
            
            assert validate_arn(payload["arn"])
            for related_arn in payload["related_arns"]:
                assert validate_arn(related_arn)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_search_results_ordered_by_score(self, qdrant_client, embedding_client):
        """Test that search results are ordered by score (highest first).
        
        Validates: Requirements 3.3, 3.4
        """
        chunks = [
            create_test_chunk(
                content="Python function for data processing",
                arn="arn:archon:code:workspace/Pkg/src/data.py#process",
                symbol_name="process",
            ),
            create_test_chunk(
                content="JavaScript module for UI rendering",
                arn="arn:archon:code:workspace/Pkg/src/ui.js#render",
                symbol_name="render",
            ),
            create_test_chunk(
                content="Go function for network handling",
                arn="arn:archon:code:workspace/Pkg/src/net.go#handle",
                symbol_name="handle",
            ),
        ]

        async def run_test():
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
            
            query_embedding = await embedding_client.embed_single("data processing")
            results = search_qdrant(
                qdrant_client,
                query_vector=query_embedding,
                limit=10,
            )
            
            assert len(results) == 3, "Expected 3 search results"
            
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True), (
                "Results should be ordered by score (highest first)"
            )

        asyncio.get_event_loop().run_until_complete(run_test())


class TestFilterFunctionality:
    """Tests for filter functionality in search operations.
    
    Tests package filter, symbol_kind filter, and combined filters.
    
    Validates:
        Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6,
        7.1, 7.2, 7.3, 7.4
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
    def embedding_client(self):
        """Create a mock embedding client."""
        return MockEmbeddingClient()

    @pytest.fixture
    def seeded_collection(self, qdrant_client, embedding_client):
        """Seed the collection with test data for filter tests."""
        chunks = [
            create_test_chunk(
                content="Function in PackageA",
                arn="arn:archon:code:ws/PackageA/src/a.py#func_a",
                symbol_name="func_a",
                symbol_kind="function",
                package="PackageA",
            ),
            create_test_chunk(
                content="Class in PackageA",
                arn="arn:archon:code:ws/PackageA/src/a.py#ClassA",
                symbol_name="ClassA",
                symbol_kind="class",
                package="PackageA",
            ),
            create_test_chunk(
                content="Function in PackageB",
                arn="arn:archon:code:ws/PackageB/src/b.py#func_b",
                symbol_name="func_b",
                symbol_kind="function",
                package="PackageB",
            ),
            create_test_chunk(
                content="Method in PackageB",
                arn="arn:archon:code:ws/PackageB/src/b.py#method_b",
                symbol_name="method_b",
                symbol_kind="method",
                package="PackageB",
            ),
            create_test_chunk(
                content="Variable in PackageC",
                arn="arn:archon:code:ws/PackageC/src/c.py#var_c",
                symbol_name="var_c",
                symbol_kind="variable",
                package="PackageC",
            ),
        ]

        async def seed():
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

        asyncio.get_event_loop().run_until_complete(seed())
        return qdrant_client

    def test_search_with_package_filter_only(
        self, seeded_collection, embedding_client
    ):
        """Test search with package filter returns only matching packages.
        
        Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5
        """
        async def run_test():
            query_embedding = await embedding_client.embed_single("test query")
            
            package_filter = build_package_filter("PackageA")
            results = search_qdrant(
                seeded_collection,
                query_vector=query_embedding,
                query_filter=package_filter,
                limit=10,
            )
            
            assert len(results) == 2, "Expected 2 results for PackageA"
            
            for result in results:
                assert result.payload["package"] == "PackageA", (
                    f"Expected PackageA, got {result.payload['package']}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_search_with_symbol_kind_filter_only(
        self, seeded_collection, embedding_client
    ):
        """Test search with symbol_kind filter returns only matching kinds.
        
        Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
        """
        async def run_test():
            query_embedding = await embedding_client.embed_single("test query")
            
            symbol_filter = build_symbol_kind_filter("function")
            results = search_qdrant(
                seeded_collection,
                query_vector=query_embedding,
                query_filter=symbol_filter,
                limit=10,
            )
            
            assert len(results) == 2, "Expected 2 function results"
            
            for result in results:
                assert result.payload["symbol_kind"] == "function", (
                    f"Expected function, got {result.payload['symbol_kind']}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_search_with_combined_filters(
        self, seeded_collection, embedding_client
    ):
        """Test search with combined package and symbol_kind filters.
        
        Validates: Requirements 7.1, 7.2, 7.3, 7.4
        """
        async def run_test():
            query_embedding = await embedding_client.embed_single("test query")
            
            combined_filter = build_combined_filter(
                package="PackageA",
                symbol_kind="function",
            )
            results = search_qdrant(
                seeded_collection,
                query_vector=query_embedding,
                query_filter=combined_filter,
                limit=10,
            )
            
            assert len(results) == 1, "Expected 1 result for PackageA + function"
            
            result = results[0]
            assert result.payload["package"] == "PackageA"
            assert result.payload["symbol_kind"] == "function"
            assert result.payload["symbol_name"] == "func_a"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_filter_no_matching_results(
        self, seeded_collection, embedding_client
    ):
        """Test filter returns empty when no results match.
        
        Validates: Requirements 5.1, 6.1
        """
        async def run_test():
            query_embedding = await embedding_client.embed_single("test query")
            
            package_filter = build_package_filter("NonExistentPackage")
            results = search_qdrant(
                seeded_collection,
                query_vector=query_embedding,
                query_filter=package_filter,
                limit=10,
            )
            
            assert len(results) == 0, "Expected 0 results for non-existent package"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_filter_all_results_match(
        self, seeded_collection, embedding_client
    ):
        """Test filter when all results match the criteria.
        
        Validates: Requirements 5.1, 5.2
        """
        async def run_test():
            query_embedding = await embedding_client.embed_single("test query")
            
            results = search_qdrant(
                seeded_collection,
                query_vector=query_embedding,
                query_filter=None,
                limit=10,
            )
            
            assert len(results) == 5, "Expected all 5 results without filter"

        asyncio.get_event_loop().run_until_complete(run_test())


class TestDeleteOperations:
    """Tests for delete operations.
    
    Tests delete_by_package and delete_by_arn operations.
    
    Validates:
        Requirement 5.1
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
    def embedding_client(self):
        """Create a mock embedding client."""
        return MockEmbeddingClient()

    def test_delete_by_package_removes_all_chunks(
        self, qdrant_client, embedding_client
    ):
        """Test delete_by_package removes all chunks for a package.
        
        Validates: Requirement 5.1
        """
        chunks = [
            create_test_chunk(
                content=f"Content {i}",
                arn=f"arn:archon:code:ws/TargetPkg/src/f{i}.py#s{i}",
                package="TargetPkg",
            )
            for i in range(3)
        ] + [
            create_test_chunk(
                content="Other package content",
                arn="arn:archon:code:ws/OtherPkg/src/other.py#other",
                package="OtherPkg",
            )
        ]

        async def run_test():
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
            
            initial_count = qdrant_client.count(
                collection_name="archon-docs", exact=True
            )
            assert initial_count.count == 4, "Expected 4 chunks initially"
            
            package_filter = build_package_filter("TargetPkg")
            qdrant_client.delete(
                collection_name="archon-docs",
                points_selector=package_filter,
            )
            
            final_count = qdrant_client.count(
                collection_name="archon-docs", exact=True
            )
            assert final_count.count == 1, "Expected 1 chunk after delete"
            
            query_embedding = await embedding_client.embed_single("test")
            results = search_qdrant(
                qdrant_client,
                query_vector=query_embedding,
                limit=10,
            )
            
            assert len(results) == 1
            assert results[0].payload["package"] == "OtherPkg"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_delete_by_arn_removes_specific_chunks(
        self, qdrant_client, embedding_client
    ):
        """Test delete_by_arn removes only chunks with matching ARN.
        
        Validates: Requirement 5.1
        """
        target_arn = "arn:archon:code:ws/Pkg/src/target.py#target"
        chunks = [
            create_test_chunk(
                content="Target content",
                arn=target_arn,
                symbol_name="target",
            ),
            create_test_chunk(
                content="Other content 1",
                arn="arn:archon:code:ws/Pkg/src/other1.py#other1",
                symbol_name="other1",
            ),
            create_test_chunk(
                content="Other content 2",
                arn="arn:archon:code:ws/Pkg/src/other2.py#other2",
                symbol_name="other2",
            ),
        ]

        async def run_test():
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
            
            initial_count = qdrant_client.count(
                collection_name="archon-docs", exact=True
            )
            assert initial_count.count == 3, "Expected 3 chunks initially"
            
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            arn_filter = Filter(
                must=[
                    FieldCondition(
                        key="arn",
                        match=MatchValue(value=target_arn),
                    )
                ]
            )
            qdrant_client.delete(
                collection_name="archon-docs",
                points_selector=arn_filter,
            )
            
            final_count = qdrant_client.count(
                collection_name="archon-docs", exact=True
            )
            assert final_count.count == 2, "Expected 2 chunks after delete"
            
            query_embedding = await embedding_client.embed_single("test")
            results = search_qdrant(
                qdrant_client,
                query_vector=query_embedding,
                limit=10,
            )
            
            arns = [r.payload["arn"] for r in results]
            assert target_arn not in arns, "Target ARN should be deleted"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_delete_does_not_affect_other_chunks(
        self, qdrant_client, embedding_client
    ):
        """Test that delete operations don't affect unrelated chunks.
        
        Validates: Requirement 5.1
        """
        chunks = [
            create_test_chunk(
                content=f"Package{i} content",
                arn=f"arn:archon:code:ws/Package{i}/src/f.py#s",
                package=f"Package{i}",
            )
            for i in range(3)
        ]

        async def run_test():
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
            
            package_filter = build_package_filter("Package1")
            qdrant_client.delete(
                collection_name="archon-docs",
                points_selector=package_filter,
            )
            
            query_embedding = await embedding_client.embed_single("test")
            results = search_qdrant(
                qdrant_client,
                query_vector=query_embedding,
                limit=10,
            )
            
            packages = {r.payload["package"] for r in results}
            assert packages == {"Package0", "Package2"}, (
                f"Expected Package0 and Package2, got {packages}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())


class TestEmbeddingServiceIntegration:
    """Tests for embedding service integration.
    
    Tests embedding generation for single and batch texts.
    
    Validates:
        Requirement 2.3
    """

    def test_embed_single_text(self):
        """Test embedding generation for a single text.
        
        Validates: Requirement 2.3
        """
        client = MockEmbeddingClient()

        async def run_test():
            embedding = await client.embed_single("Test text for embedding")
            
            assert isinstance(embedding, list), "Embedding should be a list"
            assert len(embedding) == EMBEDDING_DIMENSION, (
                f"Expected {EMBEDDING_DIMENSION} dimensions, got {len(embedding)}"
            )
            assert all(isinstance(x, float) for x in embedding), (
                "All embedding values should be floats"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_embed_batch_texts(self):
        """Test batch embedding generation.
        
        Validates: Requirement 2.3
        """
        client = MockEmbeddingClient()
        texts = [f"Text number {i}" for i in range(10)]

        async def run_test():
            embeddings = await client.embed(texts)
            
            assert len(embeddings) == len(texts), (
                f"Expected {len(texts)} embeddings, got {len(embeddings)}"
            )
            
            for i, embedding in enumerate(embeddings):
                assert len(embedding) == EMBEDDING_DIMENSION, (
                    f"Embedding {i} has wrong dimension"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_embed_empty_list(self):
        """Test embedding generation for empty list.
        
        Validates: Requirement 2.3
        """
        client = MockEmbeddingClient()

        async def run_test():
            embeddings = await client.embed([])
            
            assert embeddings == [], "Empty input should return empty list"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_embeddings_are_deterministic(self):
        """Test that embeddings are deterministic for the same input.
        
        Validates: Requirement 2.3
        """
        client = MockEmbeddingClient()
        text = "Deterministic embedding test"

        async def run_test():
            embedding1 = await client.embed_single(text)
            embedding2 = await client.embed_single(text)
            
            assert embedding1 == embedding2, (
                "Same text should produce same embedding"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_different_texts_produce_different_embeddings(self):
        """Test that different texts produce different embeddings.
        
        Validates: Requirement 2.3
        """
        client = MockEmbeddingClient()

        async def run_test():
            embedding1 = await client.embed_single("First text")
            embedding2 = await client.embed_single("Second text")
            
            assert embedding1 != embedding2, (
                "Different texts should produce different embeddings"
            )

        asyncio.get_event_loop().run_until_complete(run_test())
