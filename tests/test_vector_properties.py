"""Property-based tests for Vector Store with ARN Metadata.

This module contains property-based tests using Hypothesis to verify
correctness properties of the Vector Store with ARN metadata.

Feature: vector-store-arn
Property 25: Vector Chunk ARN Metadata

**Validates: Requirements 1.1, 1.2, 4.1, 4.2**

Source:
- src/vector/store.py
- src/vector/models.py
- src/vector/arn.py
- .kiro/specs/vector-store-arn/design.md
"""

import asyncio
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from hypothesis import given, settings, strategies as st, assume

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vector.models import ArchonChunk, SearchResult, SymbolKind
from src.vector.arn import validate_arn, ARN_PATTERN


ARN_REGEX = re.compile(
    r"^arn:archon:(code|doc|k8s|infra):[^/]+/[^/]+/[^#]+(#.+)?$"
)

VALID_ARN_TYPES = ["code", "doc", "k8s", "infra"]
VALID_SYMBOL_KINDS = ["function", "class", "method", "variable", "type", "module"]


workspace_strategy = st.from_regex(r"[a-z][a-z0-9\-]{2,20}", fullmatch=True)
package_strategy = st.from_regex(r"[A-Za-z][A-Za-z0-9\-]{2,30}", fullmatch=True)
path_strategy = st.from_regex(r"[a-z][a-z0-9_/]{1,30}\.(py|ts|go|rs|md)", fullmatch=True)
symbol_strategy = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{1,20}", fullmatch=True)
arn_type_strategy = st.sampled_from(VALID_ARN_TYPES)


def build_arn(
    arn_type: str,
    workspace: str,
    package: str,
    path: str,
    symbol: Optional[str] = None,
) -> str:
    """Build an ARN from components.

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    """
    base = f"arn:archon:{arn_type}:{workspace}/{package}/{path}"
    if symbol:
        return f"{base}#{symbol}"
    return base


@st.composite
def valid_arn_strategy(draw: st.DrawFn) -> str:
    """Strategy for generating valid ARN strings."""
    arn_type = draw(arn_type_strategy)
    workspace = draw(workspace_strategy)
    package = draw(package_strategy)
    path = draw(path_strategy)
    symbol = draw(st.one_of(st.none(), symbol_strategy))
    return build_arn(arn_type, workspace, package, path, symbol)


@st.composite
def archon_chunk_strategy(draw: st.DrawFn) -> ArchonChunk:
    """Strategy for generating random ArchonChunk instances with valid ARNs."""
    content = draw(st.text(min_size=10, max_size=500, alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        whitelist_characters=" \n\t.,;:!?-_"
    )))
    assume(content.strip())
    
    source = draw(path_strategy)
    chunk_index = draw(st.integers(min_value=0, max_value=100))
    arn = draw(valid_arn_strategy())
    related_arns = draw(st.lists(valid_arn_strategy(), min_size=0, max_size=10))
    symbol_name = draw(st.one_of(st.none(), symbol_strategy))
    symbol_kind = draw(st.one_of(st.none(), st.sampled_from(VALID_SYMBOL_KINDS)))
    package = draw(package_strategy)
    
    return ArchonChunk(
        content=content,
        source=source,
        chunk_index=chunk_index,
        arn=arn,
        related_arns=related_arns,
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        package=package,
    )


@st.composite
def archon_chunk_with_empty_related_arns(draw: st.DrawFn) -> ArchonChunk:
    """Strategy for generating ArchonChunk with empty related_arns."""
    content = draw(st.text(min_size=10, max_size=200, alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        whitelist_characters=" \n\t.,;:!?-_"
    )))
    assume(content.strip())
    
    return ArchonChunk(
        content=content,
        source=draw(path_strategy),
        chunk_index=draw(st.integers(min_value=0, max_value=100)),
        arn=draw(valid_arn_strategy()),
        related_arns=[],
        symbol_name=draw(st.one_of(st.none(), symbol_strategy)),
        symbol_kind=draw(st.one_of(st.none(), st.sampled_from(VALID_SYMBOL_KINDS))),
        package=draw(package_strategy),
    )


@st.composite
def archon_chunk_with_many_related_arns(draw: st.DrawFn) -> ArchonChunk:
    """Strategy for generating ArchonChunk with many related_arns (5-15)."""
    content = draw(st.text(min_size=10, max_size=200, alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        whitelist_characters=" \n\t.,;:!?-_"
    )))
    assume(content.strip())
    
    return ArchonChunk(
        content=content,
        source=draw(path_strategy),
        chunk_index=draw(st.integers(min_value=0, max_value=100)),
        arn=draw(valid_arn_strategy()),
        related_arns=draw(st.lists(valid_arn_strategy(), min_size=5, max_size=15)),
        symbol_name=draw(st.one_of(st.none(), symbol_strategy)),
        symbol_kind=draw(st.one_of(st.none(), st.sampled_from(VALID_SYMBOL_KINDS))),
        package=draw(package_strategy),
    )


class MockQdrantClient:
    """Mock Qdrant client for testing without a real database.
    
    Simulates Qdrant's upsert and search operations in memory,
    allowing property-based tests to verify ARN metadata handling.
    """

    def __init__(self):
        self._points: Dict[str, Dict[str, Any]] = {}
        self._collection_exists = False

    def get_collections(self):
        """Return mock collections list."""
        class MockCollections:
            def __init__(self, exists: bool):
                self.collections = [type("Collection", (), {"name": "archon-docs"})()] if exists else []
        return MockCollections(self._collection_exists)

    def create_collection(self, collection_name: str, **kwargs):
        """Create a mock collection."""
        self._collection_exists = True

    def upsert(self, collection_name: str, points: list):
        """Upsert points to the mock collection."""
        for point in points:
            self._points[point.id] = {
                "id": point.id,
                "vector": point.vector,
                "payload": point.payload,
            }

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        query_filter=None,
        limit: int = 10,
        with_payload: bool = True,
    ) -> list:
        """Search the mock collection."""
        results = []
        for point_id, point_data in list(self._points.items())[:limit]:
            class MockHit:
                def __init__(self, id: str, payload: dict, score: float):
                    self.id = id
                    self.payload = payload
                    self.score = score
            results.append(MockHit(point_id, point_data["payload"], 0.95))
        return results

    def count(self, collection_name: str, count_filter=None, exact: bool = True):
        """Count points in the mock collection."""
        class MockCount:
            def __init__(self, count: int):
                self.count = count
        
        if count_filter is None:
            return MockCount(len(self._points))
        
        count = 0
        for point_data in self._points.values():
            payload = point_data["payload"]
            matches = True
            if hasattr(count_filter, "must"):
                for condition in count_filter.must:
                    key = condition.key
                    value = condition.match.value
                    if payload.get(key) != value:
                        matches = False
                        break
            if matches:
                count += 1
        return MockCount(count)

    def delete(self, collection_name: str, points_selector, wait: bool = True):
        """Delete points from the mock collection."""
        if hasattr(points_selector, "must"):
            to_delete = []
            for point_id, point_data in self._points.items():
                payload = point_data["payload"]
                matches = True
                for condition in points_selector.must:
                    key = condition.key
                    value = condition.match.value
                    if payload.get(key) != value:
                        matches = False
                        break
                if matches:
                    to_delete.append(point_id)
            for point_id in to_delete:
                del self._points[point_id]

    def get_stored_chunks(self) -> List[Dict[str, Any]]:
        """Get all stored chunk payloads for verification."""
        return [point["payload"] for point in self._points.values()]


class MockEmbeddingClient:
    """Mock embedding client that returns deterministic embeddings."""

    def __init__(self, dimension: int = 768):
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


class TestProperty25VectorChunkARNMetadata:
    """Property 25: Vector Chunk ARN Metadata.

    For any chunk stored in the Vector Store, the chunk SHALL have a non-empty
    `arn` field conforming to the ARN format, and the `related_arns` field
    SHALL be a list (possibly empty) of valid ARN strings.

    Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
    **Validates: Requirements 1.1, 1.2, 4.1, 4.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_chunk_arn_is_non_empty_and_valid(self, chunk: ArchonChunk) -> None:
        """Test that chunk ARN is non-empty and matches ARN format.

        Property: Every ArchonChunk SHALL have a non-empty `arn` field
        that conforms to the ARN format regex.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.1, 4.1, 4.2**
        """
        assert chunk.arn, "ARN field must be non-empty"
        assert len(chunk.arn) > 0, "ARN field must have length > 0"
        
        assert ARN_REGEX.match(chunk.arn), (
            f"ARN '{chunk.arn}' does not match expected format: "
            f"arn:archon:<type>:<workspace>/<package>/<path>#<symbol>"
        )
        
        assert validate_arn(chunk.arn), (
            f"ARN '{chunk.arn}' failed validation via validate_arn()"
        )

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_related_arns_is_list(self, chunk: ArchonChunk) -> None:
        """Test that related_arns is always a list.

        Property: Every ArchonChunk SHALL have a `related_arns` field
        that is a list (possibly empty).

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.2**
        """
        assert isinstance(chunk.related_arns, list), (
            f"related_arns must be a list, got {type(chunk.related_arns)}"
        )

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_each_related_arn_is_valid(self, chunk: ArchonChunk) -> None:
        """Test that each item in related_arns matches ARN format.

        Property: Each item in the `related_arns` list SHALL be a valid
        ARN string conforming to the ARN format regex.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.2, 4.2**
        """
        for i, related_arn in enumerate(chunk.related_arns):
            assert isinstance(related_arn, str), (
                f"related_arns[{i}] must be a string, got {type(related_arn)}"
            )
            assert related_arn, (
                f"related_arns[{i}] must be non-empty"
            )
            assert ARN_REGEX.match(related_arn), (
                f"related_arns[{i}] '{related_arn}' does not match ARN format"
            )
            assert validate_arn(related_arn), (
                f"related_arns[{i}] '{related_arn}' failed validation"
            )

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_with_empty_related_arns())
    def test_empty_related_arns_is_valid(self, chunk: ArchonChunk) -> None:
        """Test that empty related_arns list is valid.

        Property: An empty `related_arns` list is a valid state for
        chunks that don't reference other ARNs.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.2**
        """
        assert chunk.related_arns == [], (
            f"Expected empty related_arns, got {chunk.related_arns}"
        )
        assert isinstance(chunk.related_arns, list), (
            "related_arns must be a list even when empty"
        )
        
        assert chunk.arn, "ARN must still be non-empty"
        assert validate_arn(chunk.arn), "ARN must still be valid"

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_with_many_related_arns())
    def test_many_related_arns_all_valid(self, chunk: ArchonChunk) -> None:
        """Test that chunks with many related_arns have all valid ARNs.

        Property: When a chunk has many related_arns (5-15), each one
        SHALL still conform to the ARN format.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.2, 4.2**
        """
        assert len(chunk.related_arns) >= 5, (
            f"Expected at least 5 related_arns, got {len(chunk.related_arns)}"
        )
        
        for i, related_arn in enumerate(chunk.related_arns):
            assert validate_arn(related_arn), (
                f"related_arns[{i}] '{related_arn}' is invalid"
            )

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_upsert_preserves_arn_metadata(self, chunk: ArchonChunk) -> None:
        """Test that upserting a chunk preserves ARN metadata.

        Property: When a chunk is upserted to the vector store, the
        stored payload SHALL preserve the `arn` and `related_arns` fields
        exactly as provided.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.1, 1.2, 4.1, 4.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            stored_chunks = mock_client.get_stored_chunks()
            assert len(stored_chunks) == 1, "Expected exactly one stored chunk"
            
            stored = stored_chunks[0]
            assert stored["arn"] == chunk.arn, (
                f"Stored ARN '{stored['arn']}' != original '{chunk.arn}'"
            )
            assert stored["related_arns"] == chunk.related_arns, (
                f"Stored related_arns {stored['related_arns']} != "
                f"original {chunk.related_arns}"
            )
            
            assert validate_arn(stored["arn"]), "Stored ARN must be valid"
            for related_arn in stored["related_arns"]:
                assert validate_arn(related_arn), (
                    f"Stored related_arn '{related_arn}' must be valid"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_retrieved_chunk_has_valid_arn_metadata(self, chunk: ArchonChunk) -> None:
        """Test that retrieved chunks have valid ARN metadata.

        Property: When a chunk is retrieved from the vector store via
        search, the result SHALL have a non-empty `arn` field conforming
        to the ARN format, and `related_arns` SHALL be a list of valid ARNs.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.1, 1.2, 4.1, 4.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert payload["arn"], "Retrieved ARN must be non-empty"
            assert ARN_REGEX.match(payload["arn"]), (
                f"Retrieved ARN '{payload['arn']}' does not match format"
            )
            assert validate_arn(payload["arn"]), (
                f"Retrieved ARN '{payload['arn']}' failed validation"
            )
            
            assert isinstance(payload["related_arns"], list), (
                "Retrieved related_arns must be a list"
            )
            for related_arn in payload["related_arns"]:
                assert validate_arn(related_arn), (
                    f"Retrieved related_arn '{related_arn}' is invalid"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(arn_type=arn_type_strategy)
    def test_all_arn_types_are_valid(self, arn_type: str) -> None:
        """Test that all ARN types (code, doc, k8s, infra) are valid.

        Property: ARNs with any of the valid types (code, doc, k8s, infra)
        SHALL pass validation.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 4.2**
        """
        arn = f"arn:archon:{arn_type}:workspace/package/path/file.py"
        assert validate_arn(arn), f"ARN with type '{arn_type}' should be valid"
        assert ARN_REGEX.match(arn), f"ARN with type '{arn_type}' should match regex"

    @settings(max_examples=100, deadline=None)
    @given(
        arn_type=arn_type_strategy,
        workspace=workspace_strategy,
        package=package_strategy,
        path=path_strategy,
        symbol=st.one_of(st.none(), symbol_strategy),
    )
    def test_arn_components_produce_valid_arn(
        self,
        arn_type: str,
        workspace: str,
        package: str,
        path: str,
        symbol: Optional[str],
    ) -> None:
        """Test that valid ARN components produce a valid ARN.

        Property: Building an ARN from valid components SHALL always
        produce a valid ARN string.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 4.2**
        """
        arn = build_arn(arn_type, workspace, package, path, symbol)
        
        assert arn, "Built ARN must be non-empty"
        assert validate_arn(arn), f"Built ARN '{arn}' must be valid"
        assert ARN_REGEX.match(arn), f"Built ARN '{arn}' must match regex"
        
        assert arn.startswith("arn:archon:"), "ARN must start with 'arn:archon:'"
        assert f":{arn_type}:" in arn, f"ARN must contain type '{arn_type}'"
        assert f"/{package}/" in arn, f"ARN must contain package '{package}'"


class TestARNEdgeCases:
    """Edge case tests for ARN validation.

    Tests special characters, boundary conditions, and edge cases
    in ARN format validation.

    Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
    **Validates: Requirements 4.1, 4.2**
    """

    def test_empty_arn_is_invalid(self) -> None:
        """Test that empty ARN string is invalid."""
        assert not validate_arn(""), "Empty ARN should be invalid"
        assert not ARN_REGEX.match(""), "Empty ARN should not match regex"

    def test_arn_without_prefix_is_invalid(self) -> None:
        """Test that ARN without 'arn:archon:' prefix is invalid."""
        invalid_arns = [
            "archon:code:workspace/package/path",
            "arn:code:workspace/package/path",
            "code:workspace/package/path",
            "workspace/package/path",
        ]
        for arn in invalid_arns:
            assert not validate_arn(arn), f"ARN '{arn}' should be invalid"

    def test_arn_with_invalid_type_is_invalid(self) -> None:
        """Test that ARN with invalid type is invalid."""
        invalid_arns = [
            "arn:archon:invalid:workspace/package/path",
            "arn:archon:CODE:workspace/package/path",
            "arn:archon:Doc:workspace/package/path",
            "arn:archon::workspace/package/path",
        ]
        for arn in invalid_arns:
            assert not validate_arn(arn), f"ARN '{arn}' should be invalid"

    def test_arn_with_missing_components_is_invalid(self) -> None:
        """Test that ARN with missing components is invalid."""
        invalid_arns = [
            "arn:archon:code:",
            "arn:archon:code:workspace",
            "arn:archon:code:workspace/",
            "arn:archon:code:workspace/package",
            "arn:archon:code:/package/path",
            "arn:archon:code:workspace//path",
        ]
        for arn in invalid_arns:
            assert not validate_arn(arn), f"ARN '{arn}' should be invalid"

    def test_arn_with_symbol_is_valid(self) -> None:
        """Test that ARN with symbol fragment is valid."""
        arn = "arn:archon:code:workspace/package/path/file.py#MyClass"
        assert validate_arn(arn), f"ARN with symbol '{arn}' should be valid"

    def test_arn_without_symbol_is_valid(self) -> None:
        """Test that ARN without symbol fragment is valid."""
        arn = "arn:archon:doc:workspace/package/path/file.md"
        assert validate_arn(arn), f"ARN without symbol '{arn}' should be valid"

    def test_arn_with_nested_path_is_valid(self) -> None:
        """Test that ARN with deeply nested path is valid."""
        arn = "arn:archon:code:workspace/package/src/deep/nested/path/file.py#Symbol"
        assert validate_arn(arn), f"ARN with nested path '{arn}' should be valid"

    def test_arn_with_underscores_in_symbol_is_valid(self) -> None:
        """Test that ARN with underscores in symbol is valid."""
        arn = "arn:archon:code:workspace/package/path/file.py#my_function_name"
        assert validate_arn(arn), f"ARN with underscores '{arn}' should be valid"

    def test_arn_with_numbers_in_components_is_valid(self) -> None:
        """Test that ARN with numbers in components is valid."""
        arn = "arn:archon:code:workspace123/package456/path/file2.py#Symbol3"
        assert validate_arn(arn), f"ARN with numbers '{arn}' should be valid"

    @settings(max_examples=100, deadline=None)
    @given(chunks=st.lists(archon_chunk_strategy(), min_size=1, max_size=5))
    def test_batch_chunks_all_have_valid_arns(self, chunks: List[ArchonChunk]) -> None:
        """Test that a batch of chunks all have valid ARN metadata.

        Property: When multiple chunks are generated, each one SHALL
        have valid ARN metadata.

        Feature: vector-store-arn, Property 25: Vector Chunk ARN Metadata
        **Validates: Requirements 1.1, 1.2, 4.1, 4.2**
        """
        for i, chunk in enumerate(chunks):
            assert chunk.arn, f"Chunk {i} ARN must be non-empty"
            assert validate_arn(chunk.arn), f"Chunk {i} ARN '{chunk.arn}' must be valid"
            
            assert isinstance(chunk.related_arns, list), (
                f"Chunk {i} related_arns must be a list"
            )
            for j, related_arn in enumerate(chunk.related_arns):
                assert validate_arn(related_arn), (
                    f"Chunk {i} related_arns[{j}] '{related_arn}' must be valid"
                )


class TestProperty26ARNEnrichedSearchResults:
    """Property 26: ARN-Enriched Search Results.

    For any search query returning results, each result SHALL include the
    `arn`, `related_arns`, `symbol_name`, `symbol_kind`, and `package`
    metadata fields alongside `content`, `source`, and `score`.

    Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
    **Validates: Requirements 3.1, 3.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_contains_content(self, chunk: ArchonChunk) -> None:
        """Test that search results contain non-empty content.

        Property: Every SearchResult SHALL have a non-empty `content` field.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert payload.get("content"), "content must be non-empty"
            assert isinstance(payload["content"], str), "content must be a string"
            assert len(payload["content"]) > 0, "content must have length > 0"

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_contains_source(self, chunk: ArchonChunk) -> None:
        """Test that search results contain non-empty source.

        Property: Every SearchResult SHALL have a non-empty `source` field.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert payload.get("source"), "source must be non-empty"
            assert isinstance(payload["source"], str), "source must be a string"

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_score_in_valid_range(self, chunk: ArchonChunk) -> None:
        """Test that search result score is between 0.0 and 1.0.

        Property: Every SearchResult SHALL have a `score` field that is
        a float between 0.0 and 1.0.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            score = result.score
            
            assert isinstance(score, (int, float)), "score must be numeric"
            assert 0.0 <= score <= 1.0, f"score {score} must be between 0.0 and 1.0"

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_contains_arn_metadata(self, chunk: ArchonChunk) -> None:
        """Test that search results contain valid ARN metadata.

        Property: Every SearchResult SHALL have a non-empty `arn` field
        conforming to the ARN format.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert payload.get("arn"), "arn must be non-empty"
            assert isinstance(payload["arn"], str), "arn must be a string"
            assert ARN_REGEX.match(payload["arn"]), (
                f"arn '{payload['arn']}' must match ARN format"
            )
            assert validate_arn(payload["arn"]), (
                f"arn '{payload['arn']}' must pass validation"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_contains_related_arns(self, chunk: ArchonChunk) -> None:
        """Test that search results contain related_arns as a list.

        Property: Every SearchResult SHALL have a `related_arns` field
        that is a list of strings (may be empty).

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert "related_arns" in payload, "related_arns must be present"
            assert isinstance(payload["related_arns"], list), (
                "related_arns must be a list"
            )
            
            for i, related_arn in enumerate(payload["related_arns"]):
                assert isinstance(related_arn, str), (
                    f"related_arns[{i}] must be a string"
                )
                assert validate_arn(related_arn), (
                    f"related_arns[{i}] '{related_arn}' must be valid"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_contains_symbol_metadata(self, chunk: ArchonChunk) -> None:
        """Test that search results contain symbol_name and symbol_kind.

        Property: Every SearchResult SHALL have `symbol_name` and `symbol_kind`
        fields (may be None). If `symbol_kind` is present, it must be a valid kind.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert "symbol_name" in payload, "symbol_name must be present"
            if payload["symbol_name"] is not None:
                assert isinstance(payload["symbol_name"], str), (
                    "symbol_name must be a string or None"
                )
            
            assert "symbol_kind" in payload, "symbol_kind must be present"
            if payload["symbol_kind"] is not None:
                assert isinstance(payload["symbol_kind"], str), (
                    "symbol_kind must be a string or None"
                )
                assert payload["symbol_kind"] in VALID_SYMBOL_KINDS, (
                    f"symbol_kind '{payload['symbol_kind']}' must be valid"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_contains_package(self, chunk: ArchonChunk) -> None:
        """Test that search results contain non-empty package.

        Property: Every SearchResult SHALL have a non-empty `package` field.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            assert payload.get("package"), "package must be non-empty"
            assert isinstance(payload["package"], str), "package must be a string"

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunks=st.lists(archon_chunk_strategy(), min_size=2, max_size=5))
    def test_search_with_multiple_results_all_have_metadata(
        self, chunks: List[ArchonChunk]
    ) -> None:
        """Test that all search results have complete metadata.

        Property: When multiple results are returned, each one SHALL
        have all required metadata fields.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            from qdrant_client.models import PointStruct
            
            mock_client._collection_exists = True
            
            for chunk in chunks:
                embedding = await mock_embedding.embed_single(chunk.content)
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
                mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=len(chunks),
                with_payload=True,
            )
            
            assert len(results) == len(chunks), (
                f"Expected {len(chunks)} results, got {len(results)}"
            )
            
            for i, result in enumerate(results):
                payload = result.payload
                
                assert payload.get("content"), f"Result {i}: content must be non-empty"
                assert payload.get("source"), f"Result {i}: source must be non-empty"
                assert payload.get("arn"), f"Result {i}: arn must be non-empty"
                assert validate_arn(payload["arn"]), (
                    f"Result {i}: arn must be valid"
                )
                assert isinstance(payload.get("related_arns"), list), (
                    f"Result {i}: related_arns must be a list"
                )
                assert payload.get("package"), f"Result {i}: package must be non-empty"
                
                assert 0.0 <= result.score <= 1.0, (
                    f"Result {i}: score must be between 0.0 and 1.0"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(chunk=archon_chunk_strategy())
    def test_search_result_to_search_result_dataclass(
        self, chunk: ArchonChunk
    ) -> None:
        """Test that search results can be converted to SearchResult dataclass.

        Property: Search result payloads SHALL be convertible to the
        SearchResult dataclass with all fields populated correctly.

        Feature: vector-store-arn, Property 26: ARN-Enriched Search Results
        **Validates: Requirements 3.1, 3.2**
        """
        mock_client = MockQdrantClient()
        mock_embedding = MockEmbeddingClient()

        async def run_test():
            embedding = await mock_embedding.embed_single(chunk.content)
            
            from qdrant_client.models import PointStruct
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
            
            mock_client._collection_exists = True
            mock_client.upsert(collection_name="archon-docs", points=[point])
            
            query_embedding = await mock_embedding.embed_single("test query")
            results = mock_client.search(
                collection_name="archon-docs",
                query_vector=query_embedding,
                limit=10,
                with_payload=True,
            )
            
            assert len(results) == 1, "Expected one search result"
            
            result = results[0]
            payload = result.payload
            
            search_result = SearchResult(
                content=payload.get("content", ""),
                source=payload.get("source", ""),
                chunk_index=payload.get("chunk_index", 0),
                score=result.score,
                arn=payload.get("arn", ""),
                related_arns=payload.get("related_arns", []),
                symbol_name=payload.get("symbol_name"),
                symbol_kind=payload.get("symbol_kind"),
                package=payload.get("package", ""),
            )
            
            assert search_result.content == chunk.content
            assert search_result.source == chunk.source
            assert search_result.chunk_index == chunk.chunk_index
            assert search_result.arn == chunk.arn
            assert search_result.related_arns == chunk.related_arns
            assert search_result.symbol_name == chunk.symbol_name
            assert search_result.symbol_kind == chunk.symbol_kind
            assert search_result.package == chunk.package
            assert 0.0 <= search_result.score <= 1.0

        asyncio.get_event_loop().run_until_complete(run_test())
