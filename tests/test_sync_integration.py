"""Integration tests for Knowledge Base Sync Service end-to-end flow.

This module contains integration tests that verify the complete sync flow
using mock PostgreSQL (via mock GraphQL service) and in-memory Qdrant.

Feature: sync-service

Source:
- src/sync/service.py
- src/sync/graph_adapter.py
- src/sync/vector_adapter.py
- src/sync/change_detector.py
- .kiro/specs/sync-service/design.md

Validates:
    Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4,
    5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""

import asyncio
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.change_detector import ChangeDetector
from src.sync.models import (
    GeneratedDoc,
    ScipParseResult,
    ScipRelationship,
    ScipSymbol,
    SymbolLocation,
)
from src.sync.service import KnowledgeBaseSyncService
from src.vector.models import ArchonChunk

EMBEDDING_DIMENSION = 768
COLLECTION_NAME = "archon-docs"


# =============================================================================
# Mock Components for Integration Testing
# =============================================================================


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


@dataclass
class MockGraphNode:
    """Represents a node stored in the mock graph."""

    arn: str
    name: str
    kind: str
    workspace: str
    package: str
    path: str
    signature: Optional[str] = None
    documentation: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    index_hash: Optional[str] = None


@dataclass
class MockGraphEdge:
    """Represents an edge stored in the mock graph."""

    from_arn: str
    to_arn: str
    type: str


@dataclass
class MockSyncResult:
    """Result from mock graph sync operation."""

    nodes_created: int = 0
    nodes_updated: int = 0
    edges_created: int = 0
    edges_removed: int = 0


class MockGraphService:
    """Mock GraphQL service that stores nodes and edges in memory.

    Simulates the Code Graph PostgreSQL storage for integration testing.
    """

    def __init__(self):
        self.nodes: Dict[str, MockGraphNode] = {}
        self.edges: List[MockGraphEdge] = []
        self.execute_calls: List[Dict[str, Any]] = []

    async def execute(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a mock GraphQL mutation."""
        self.execute_calls.append({"query": query, "variables": variables})

        if "syncFromScip" in query:
            return self._handle_sync_from_scip(variables or {})
        elif "prunePackage" in query:
            return self._handle_prune_package(variables or {})

        return {"data": None, "errors": [{"message": "Unknown query"}]}

    def _handle_sync_from_scip(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Handle syncFromScip mutation."""
        symbols = variables.get("symbols", [])
        relationships = variables.get("relationships", [])
        index_hash = variables.get("indexHash", "")

        nodes_created = 0
        nodes_updated = 0

        for symbol in symbols:
            arn = symbol["arn"]
            if arn in self.nodes:
                nodes_updated += 1
            else:
                nodes_created += 1

            self.nodes[arn] = MockGraphNode(
                arn=arn,
                name=symbol.get("name", ""),
                kind=symbol.get("kind", ""),
                workspace=symbol.get("workspace", ""),
                package=symbol.get("package", ""),
                path=symbol.get("path", ""),
                signature=symbol.get("signature"),
                documentation=symbol.get("documentation"),
                file_path=symbol.get("filePath"),
                line_number=symbol.get("lineNumber"),
                index_hash=index_hash,
            )

        edges_created = 0
        for rel in relationships:
            edge = MockGraphEdge(
                from_arn=rel["fromArn"],
                to_arn=rel["toArn"],
                type=rel["type"],
            )
            existing = [
                e
                for e in self.edges
                if e.from_arn == edge.from_arn
                and e.to_arn == edge.to_arn
                and e.type == edge.type
            ]
            if not existing:
                self.edges.append(edge)
                edges_created += 1

        return {
            "data": {
                "syncFromScip": {
                    "nodesCreated": nodes_created,
                    "nodesUpdated": nodes_updated,
                    "edgesCreated": edges_created,
                    "edgesRemoved": 0,
                }
            }
        }

    def _handle_prune_package(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Handle prunePackage mutation."""
        package = variables.get("package", "")
        keep_arns = set(variables.get("keepArns", []))

        nodes_to_remove = [
            arn
            for arn, node in self.nodes.items()
            if node.package == package and arn not in keep_arns
        ]

        for arn in nodes_to_remove:
            del self.nodes[arn]
            self.edges = [
                e for e in self.edges if e.from_arn != arn and e.to_arn != arn
            ]

        return {"data": {"prunePackage": len(nodes_to_remove)}}

    def get_nodes_by_package(self, package: str) -> List[MockGraphNode]:
        """Get all nodes for a package."""
        return [n for n in self.nodes.values() if n.package == package]

    def get_edges_by_package(self, package: str) -> List[MockGraphEdge]:
        """Get all edges where from_arn belongs to package."""
        package_arns = {n.arn for n in self.get_nodes_by_package(package)}
        return [e for e in self.edges if e.from_arn in package_arns]


class MockGraphSyncAdapter:
    """Mock GraphSyncAdapter that uses MockGraphService for storage."""

    def __init__(self, graph_service: MockGraphService):
        self._service = graph_service

    async def sync(self, parse_result) -> MockSyncResult:
        """Sync SCIP parse result to the mock graph."""
        symbol_inputs = [
            {
                "arn": s.arn,
                "type": "CODE",
                "workspace": parse_result.workspace,
                "package": parse_result.package,
                "path": s.file_path,
                "symbol": s.name,
                "kind": s.kind.upper(),
                "name": s.name,
                "signature": s.signature,
                "documentation": s.documentation,
                "filePath": s.file_path,
                "lineNumber": s.line_number,
            }
            for s in parse_result.symbols
        ]

        relationship_inputs = [
            {
                "fromArn": r.from_arn,
                "toArn": r.to_arn,
                "type": r.type.upper(),
            }
            for r in parse_result.relationships
        ]

        result = await self._service.execute(
            "mutation SyncFromScip { syncFromScip { ... } }",
            {
                "packagePath": parse_result.package_path,
                "symbols": symbol_inputs,
                "relationships": relationship_inputs,
                "indexHash": parse_result.index_hash,
            },
        )

        sync_data = result["data"]["syncFromScip"]
        return MockSyncResult(
            nodes_created=sync_data["nodesCreated"],
            nodes_updated=sync_data["nodesUpdated"],
            edges_created=sync_data["edgesCreated"],
            edges_removed=sync_data["edgesRemoved"],
        )

    async def prune(self, package: str, keep_arns: List[str]) -> int:
        """Prune stale nodes from the mock graph."""
        result = await self._service.execute(
            "mutation PrunePackage { prunePackage }",
            {"package": package, "keepArns": keep_arns},
        )
        return result["data"]["prunePackage"]


class MockVectorStoreService:
    """Mock VectorStoreService using in-memory Qdrant."""

    def __init__(self, qdrant_client: QdrantClient, embedding_client: MockEmbeddingClient):
        self._client = qdrant_client
        self._embedding_client = embedding_client
        self.collection_name = COLLECTION_NAME

    @property
    def client(self) -> QdrantClient:
        """Get the Qdrant client."""
        return self._client

    @property
    def embedding_client(self) -> MockEmbeddingClient:
        """Get the embedding client."""
        return self._embedding_client

    def _generate_chunk_id(self, arn: str, chunk_index: int) -> str:
        """Generate a deterministic point ID for a chunk."""
        import hashlib

        id_input = f"{arn}:{chunk_index}"
        hash_bytes = hashlib.sha256(id_input.encode("utf-8")).digest()
        return hash_bytes[:16].hex()

    async def upsert(self, chunks: List[ArchonChunk]) -> int:
        """Upsert chunks to the vector store."""
        if not chunks:
            return 0

        texts = [chunk.content for chunk in chunks]
        embeddings = await self._embedding_client.embed(texts)

        points = [
            PointStruct(
                id=self._generate_chunk_id(chunk.arn, chunk.chunk_index),
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
            for chunk, embedding in zip(chunks, embeddings)
        ]

        self._client.upsert(collection_name=self.collection_name, points=points)
        return len(points)

    async def delete_by_package(self, package: str) -> int:
        """Delete all chunks for a package."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        package_filter = Filter(
            must=[FieldCondition(key="package", match=MatchValue(value=package))]
        )

        count_result = self._client.count(
            collection_name=self.collection_name,
            count_filter=package_filter,
            exact=True,
        )
        count_to_delete = count_result.count

        if count_to_delete > 0:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=package_filter,
                wait=True,
            )

        return count_to_delete

    def get_chunks_by_package(self, package: str) -> List[Dict[str, Any]]:
        """Get all chunks for a package."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        package_filter = Filter(
            must=[FieldCondition(key="package", match=MatchValue(value=package))]
        )

        results = self._client.scroll(
            collection_name=self.collection_name,
            scroll_filter=package_filter,
            limit=1000,
            with_payload=True,
        )

        return [point.payload for point in results[0]]

    def get_chunks_by_arn(self, arn: str) -> List[Dict[str, Any]]:
        """Get all chunks for an ARN."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        arn_filter = Filter(
            must=[FieldCondition(key="arn", match=MatchValue(value=arn))]
        )

        results = self._client.scroll(
            collection_name=self.collection_name,
            scroll_filter=arn_filter,
            limit=1000,
            with_payload=True,
        )

        return [point.payload for point in results[0]]


class MockVectorSyncAdapter:
    """Mock VectorSyncAdapter using MockVectorStoreService."""

    def __init__(self, vector_store: MockVectorStoreService):
        self._vector_store = vector_store
        self._chunk_size = 600
        self._chunk_overlap = 100

    @property
    def vector_store(self) -> MockVectorStoreService:
        """Get the vector store service."""
        return self._vector_store

    def chunk_document(self, doc: GeneratedDoc) -> List[ArchonChunk]:
        """Chunk a document into segments."""
        content = doc.content
        if not content or not content.strip():
            return []

        symbol_name = doc.arn.split("#")[-1] if "#" in doc.arn else None
        package = self._extract_package_from_arn(doc.arn)

        target_chars = self._chunk_size * 4
        if len(content) <= target_chars:
            return [
                ArchonChunk(
                    content=content,
                    source=doc.doc_path,
                    chunk_index=0,
                    arn=doc.arn,
                    related_arns=doc.referenced_arns,
                    symbol_name=symbol_name,
                    symbol_kind="function",
                    package=package,
                )
            ]

        chunks = []
        start = 0
        chunk_index = 0
        overlap_chars = self._chunk_overlap * 4

        while start < len(content):
            end = min(start + target_chars, len(content))
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
                        symbol_kind="function",
                        package=package,
                    )
                )
                chunk_index += 1

            if end >= len(content):
                break
            start = max(start + 1, end - overlap_chars)

        return chunks

    def _extract_package_from_arn(self, arn: str) -> str:
        """Extract package name from ARN."""
        import re

        match = re.match(r"arn:archon:\w+:[^/]+/([^/]+)/", arn)
        return match.group(1) if match else ""

    async def sync_docs(
        self, package: str, archon_docs: List[GeneratedDoc]
    ):
        """Sync Archon documentation to the Vector Store."""
        from src.sync.vector_adapter import VectorSyncResult

        errors = []
        chunks_upserted = 0
        chunks_pruned = 0

        if not archon_docs:
            return VectorSyncResult(chunks_upserted=0, chunks_pruned=0, errors=[])

        all_chunks = []
        current_arns = []

        for doc in archon_docs:
            doc_chunks = self.chunk_document(doc)
            for chunk in doc_chunks:
                chunk.package = package
            all_chunks.extend(doc_chunks)
            current_arns.append(doc.arn)

        if all_chunks:
            chunks_upserted = await self._vector_store.upsert(all_chunks)

        chunks_pruned = await self.prune_stale_chunks(package, current_arns)

        return VectorSyncResult(
            chunks_upserted=chunks_upserted,
            chunks_pruned=chunks_pruned,
            errors=errors,
        )

    async def prune_stale_chunks(self, package: str, current_arns: List[str]) -> int:
        """Remove chunks not in the current Archon docs."""
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        if not current_arns:
            return await self._vector_store.delete_by_package(package)

        stale_filter = Filter(
            must=[FieldCondition(key="package", match=MatchValue(value=package))],
            must_not=[FieldCondition(key="arn", match=MatchAny(any=current_arns))],
        )

        count_result = self._vector_store.client.count(
            collection_name=self._vector_store.collection_name,
            count_filter=stale_filter,
            exact=True,
        )
        count_to_prune = count_result.count

        if count_to_prune > 0:
            self._vector_store.client.delete(
                collection_name=self._vector_store.collection_name,
                points_selector=stale_filter,
                wait=True,
            )

        return count_to_prune


# =============================================================================
# Test Data Factories
# =============================================================================


def create_test_location(
    file: str = "src/main.py", line: int = 42, column: int = 0
) -> SymbolLocation:
    """Create a test SymbolLocation."""
    return SymbolLocation(file=file, line=line, column=column)


def create_test_symbol(
    arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#test_func",
    type_: str = "code",
    workspace: str = "personal-work",
    package: str = "TestPackage",
    path: str = "src/main.py",
    symbol: Optional[str] = "test_func",
    kind: str = "function",
    name: str = "test_func",
    signature: Optional[str] = "def test_func(x: int) -> str",
    documentation: Optional[str] = "Test function documentation.",
    location: Optional[SymbolLocation] = None,
) -> ScipSymbol:
    """Create a test ScipSymbol."""
    return ScipSymbol(
        arn=arn,
        type=type_,
        workspace=workspace,
        package=package,
        path=path,
        symbol=symbol,
        kind=kind,
        name=name,
        signature=signature,
        documentation=documentation,
        location=location or create_test_location(file=path),
    )


def create_test_relationship(
    from_arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#MyClass",
    to_arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#my_method",
    rel_type: str = "contains",
) -> ScipRelationship:
    """Create a test ScipRelationship."""
    return ScipRelationship(from_arn=from_arn, to_arn=to_arn, type=rel_type)


def create_test_parse_result(
    symbols: Optional[List[ScipSymbol]] = None,
    relationships: Optional[List[ScipRelationship]] = None,
    hash_: str = "abc123def456789012345678901234567890123456789012345678901234",
) -> ScipParseResult:
    """Create a test ScipParseResult."""
    return ScipParseResult(
        symbols=symbols or [],
        relationships=relationships or [],
        hash=hash_,
    )


def create_test_doc(
    source_path: str = "src/main.py",
    doc_path: str = "src/main.archon.md",
    content: str = "Test documentation content for the main module.",
    arn: str = "arn:archon:doc:personal-work/TestPackage/src/main.py",
    referenced_arns: Optional[List[str]] = None,
) -> GeneratedDoc:
    """Create a test GeneratedDoc."""
    return GeneratedDoc(
        source_path=source_path,
        doc_path=doc_path,
        content=content,
        arn=arn,
        referenced_arns=referenced_arns or [],
    )


# =============================================================================
# Integration Test Fixtures
# =============================================================================


@pytest.fixture
def qdrant_client():
    """Create an in-memory Qdrant client for testing."""
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
    )
    return client


@pytest.fixture
def embedding_client():
    """Create a mock embedding client."""
    return MockEmbeddingClient()


@pytest.fixture
def graph_service():
    """Create a mock graph service."""
    return MockGraphService()


@pytest.fixture
def vector_store(qdrant_client, embedding_client):
    """Create a mock vector store service."""
    return MockVectorStoreService(qdrant_client, embedding_client)


@pytest.fixture
def graph_adapter(graph_service):
    """Create a mock graph sync adapter."""
    return MockGraphSyncAdapter(graph_service)


@pytest.fixture
def vector_adapter(vector_store):
    """Create a mock vector sync adapter."""
    return MockVectorSyncAdapter(vector_store)


@pytest.fixture
def change_detector(tmp_path):
    """Create a change detector with temporary state file."""
    state_file = tmp_path / ".archon" / "sync-state.json"
    return ChangeDetector(state_file=str(state_file))


@pytest.fixture
def sync_service(graph_adapter, vector_adapter, change_detector):
    """Create a KnowledgeBaseSyncService with mock dependencies."""
    return KnowledgeBaseSyncService(
        graph_adapter=graph_adapter,
        vector_adapter=vector_adapter,
        change_detector=change_detector,
    )


# =============================================================================
# Integration Test: Full Sync Flow
# =============================================================================


class TestFullSyncFlow:
    """End-to-end tests for the complete sync flow.

    Tests the full sync flow with mock PostgreSQL (via MockGraphService)
    and in-memory Qdrant. Verifies nodes, edges, and chunks are created
    correctly.

    Validates:
        Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4,
        5.1, 5.2, 5.3
    """

    def test_full_sync_creates_nodes_in_graph(
        self, sync_service, graph_service
    ):
        """Test that full sync creates nodes in the Code Graph.

        Validates: Requirements 2.1, 2.2, 2.3, 2.4
        """
        symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                name="func_a",
                kind="function",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#ClassA",
                name="ClassA",
                kind="class",
            ),
        ]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            assert result.success is True
            assert result.nodes_created == 2

            nodes = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes) == 2

            arns = {n.arn for n in nodes}
            assert "arn:archon:code:personal-work/TestPackage/src/main.py#func_a" in arns
            assert "arn:archon:code:personal-work/TestPackage/src/main.py#ClassA" in arns

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_full_sync_creates_edges_in_graph(
        self, sync_service, graph_service
    ):
        """Test that full sync creates edges in the Code Graph.

        Validates: Requirements 3.1, 3.2, 3.3, 3.4
        """
        symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#MyClass",
                name="MyClass",
                kind="class",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#my_method",
                name="my_method",
                kind="method",
            ),
        ]
        relationships = [
            create_test_relationship(
                from_arn="arn:archon:code:personal-work/TestPackage/src/main.py#MyClass",
                to_arn="arn:archon:code:personal-work/TestPackage/src/main.py#my_method",
                rel_type="contains",
            )
        ]
        scip_result = create_test_parse_result(
            symbols=symbols, relationships=relationships
        )
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            assert result.success is True
            assert result.edges_created == 1

            edges = graph_service.get_edges_by_package("TestPackage")
            assert len(edges) == 1
            assert edges[0].type == "CONTAINS"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_full_sync_creates_chunks_in_vector_store(
        self, sync_service, vector_store
    ):
        """Test that full sync creates chunks in the Vector Store.

        Validates: Requirements 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [
            create_test_doc(
                content="This is documentation for the test function. " * 10,
                arn="arn:archon:doc:personal-work/TestPackage/src/main.py#test_func",
            )
        ]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            assert result.success is True
            assert result.chunks_upserted >= 1

            chunks = vector_store.get_chunks_by_package("TestPackage")
            assert len(chunks) >= 1

            chunk = chunks[0]
            assert chunk["package"] == "TestPackage"
            assert "arn" in chunk
            assert "content" in chunk

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_full_sync_preserves_arn_metadata_in_chunks(
        self, sync_service, vector_store
    ):
        """Test that chunks preserve ARN metadata.

        Validates: Requirements 4.3, 5.1
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        doc_arn = "arn:archon:doc:personal-work/TestPackage/src/main.py#test_func"
        related_arns = [
            "arn:archon:code:personal-work/TestPackage/src/utils.py#helper"
        ]
        archon_docs = [
            create_test_doc(
                content="Documentation with references to helper functions.",
                arn=doc_arn,
                referenced_arns=related_arns,
            )
        ]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            assert result.success is True

            chunks = vector_store.get_chunks_by_arn(doc_arn)
            assert len(chunks) >= 1

            chunk = chunks[0]
            assert chunk["arn"] == doc_arn
            assert chunk["related_arns"] == related_arns
            assert chunk["symbol_name"] == "test_func"

        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Integration Test: Incremental Sync
# =============================================================================


class TestIncrementalSync:
    """Tests for incremental sync behavior.

    Verifies that adding new symbols only adds new nodes without
    duplicating existing ones.

    Validates:
        Requirements 2.3, 2.4, 8.1, 8.2, 8.3, 8.4
    """

    def test_incremental_sync_adds_new_symbols(
        self, sync_service, graph_service
    ):
        """Test that incremental sync adds only new symbols.

        Validates: Requirements 2.3, 2.4
        """
        initial_symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                name="func_a",
            )
        ]
        initial_result = create_test_parse_result(
            symbols=initial_symbols, hash_="hash_v1"
        )
        archon_docs = [create_test_doc()]

        async def run_test():
            result1 = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=initial_result,
                archon_docs=archon_docs,
            )
            assert result1.nodes_created == 1

            updated_symbols = [
                create_test_symbol(
                    arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                    name="func_a",
                ),
                create_test_symbol(
                    arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_b",
                    name="func_b",
                ),
            ]
            updated_result = create_test_parse_result(
                symbols=updated_symbols, hash_="hash_v2"
            )

            result2 = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=updated_result,
                archon_docs=archon_docs,
            )

            assert result2.nodes_created == 1
            assert result2.nodes_updated == 1

            nodes = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes) == 2

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_incremental_sync_updates_existing_symbols(
        self, sync_service, graph_service
    ):
        """Test that incremental sync updates existing symbols.

        Validates: Requirements 2.3, 2.4
        """
        initial_symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                name="func_a",
                documentation="Original documentation",
            )
        ]
        initial_result = create_test_parse_result(
            symbols=initial_symbols, hash_="hash_v1"
        )
        archon_docs = [create_test_doc()]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=initial_result,
                archon_docs=archon_docs,
            )

            updated_symbols = [
                create_test_symbol(
                    arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                    name="func_a",
                    documentation="Updated documentation",
                )
            ]
            updated_result = create_test_parse_result(
                symbols=updated_symbols, hash_="hash_v2"
            )

            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=updated_result,
                archon_docs=archon_docs,
            )

            assert result.nodes_updated == 1

            nodes = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes) == 1
            assert nodes[0].documentation == "Updated documentation"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_incremental_sync_adds_new_docs(
        self, sync_service, vector_store
    ):
        """Test that incremental sync adds new documentation chunks.

        Validates: Requirements 4.1, 4.2, 5.1
        """
        symbols = [create_test_symbol()]
        scip_result_v1 = create_test_parse_result(symbols=symbols, hash_="hash_v1")
        initial_docs = [
            create_test_doc(
                arn="arn:archon:doc:personal-work/TestPackage/src/main.py#func_a",
                content="Documentation for func_a",
            )
        ]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result_v1,
                archon_docs=initial_docs,
            )

            initial_chunks = vector_store.get_chunks_by_package("TestPackage")
            initial_count = len(initial_chunks)

            scip_result_v2 = create_test_parse_result(symbols=symbols, hash_="hash_v2")
            updated_docs = [
                create_test_doc(
                    arn="arn:archon:doc:personal-work/TestPackage/src/main.py#func_a",
                    content="Documentation for func_a",
                ),
                create_test_doc(
                    arn="arn:archon:doc:personal-work/TestPackage/src/main.py#func_b",
                    content="Documentation for func_b",
                ),
            ]

            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result_v2,
                archon_docs=updated_docs,
            )

            assert result.chunks_upserted >= initial_count

            final_chunks = vector_store.get_chunks_by_package("TestPackage")
            assert len(final_chunks) >= initial_count

        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Integration Test: Stale Data Pruning
# =============================================================================


class TestStaleDataPruning:
    """Tests for stale data pruning behavior.

    Verifies that removed symbols and docs are pruned from the knowledge base.

    Validates:
        Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """

    def test_prune_removes_stale_nodes(
        self, sync_service, graph_service
    ):
        """Test that pruning removes stale nodes from the Code Graph.

        Validates: Requirements 6.1, 6.2, 6.6
        """
        initial_symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                name="func_a",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_b",
                name="func_b",
            ),
        ]
        initial_result = create_test_parse_result(
            symbols=initial_symbols, hash_="hash_v1"
        )
        archon_docs = [create_test_doc()]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=initial_result,
                archon_docs=archon_docs,
            )

            nodes_before = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes_before) == 2

            reduced_symbols = [
                create_test_symbol(
                    arn="arn:archon:code:personal-work/TestPackage/src/main.py#func_a",
                    name="func_a",
                )
            ]
            reduced_result = create_test_parse_result(
                symbols=reduced_symbols, hash_="hash_v2"
            )

            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=reduced_result,
                archon_docs=archon_docs,
            )

            assert result.nodes_pruned == 1

            nodes_after = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes_after) == 1
            assert nodes_after[0].name == "func_a"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_removes_stale_edges(
        self, sync_service, graph_service
    ):
        """Test that pruning removes edges when nodes are removed.

        Validates: Requirements 6.1, 6.6
        """
        initial_symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#ClassA",
                name="ClassA",
                kind="class",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#method_a",
                name="method_a",
                kind="method",
            ),
        ]
        initial_relationships = [
            create_test_relationship(
                from_arn="arn:archon:code:personal-work/TestPackage/src/main.py#ClassA",
                to_arn="arn:archon:code:personal-work/TestPackage/src/main.py#method_a",
                rel_type="contains",
            )
        ]
        initial_result = create_test_parse_result(
            symbols=initial_symbols,
            relationships=initial_relationships,
            hash_="hash_v1",
        )
        archon_docs = [create_test_doc()]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=initial_result,
                archon_docs=archon_docs,
            )

            edges_before = graph_service.get_edges_by_package("TestPackage")
            assert len(edges_before) == 1

            reduced_symbols = [
                create_test_symbol(
                    arn="arn:archon:code:personal-work/TestPackage/src/main.py#ClassA",
                    name="ClassA",
                    kind="class",
                )
            ]
            reduced_result = create_test_parse_result(
                symbols=reduced_symbols, hash_="hash_v2"
            )

            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=reduced_result,
                archon_docs=archon_docs,
            )

            edges_after = graph_service.get_edges_by_package("TestPackage")
            assert len(edges_after) == 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_removes_stale_chunks(
        self, sync_service, vector_store
    ):
        """Test that pruning removes stale chunks from the Vector Store.

        Validates: Requirements 6.3, 6.4, 6.5
        """
        symbols = [create_test_symbol()]
        scip_result_v1 = create_test_parse_result(symbols=symbols, hash_="hash_v1")
        initial_docs = [
            create_test_doc(
                arn="arn:archon:doc:personal-work/TestPackage/src/main.py#func_a",
                content="Documentation for func_a",
            ),
            create_test_doc(
                arn="arn:archon:doc:personal-work/TestPackage/src/main.py#func_b",
                content="Documentation for func_b",
            ),
        ]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result_v1,
                archon_docs=initial_docs,
            )

            chunks_before = vector_store.get_chunks_by_package("TestPackage")
            assert len(chunks_before) == 2

            scip_result_v2 = create_test_parse_result(symbols=symbols, hash_="hash_v2")
            reduced_docs = [
                create_test_doc(
                    arn="arn:archon:doc:personal-work/TestPackage/src/main.py#func_a",
                    content="Documentation for func_a",
                )
            ]

            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result_v2,
                archon_docs=reduced_docs,
            )

            assert result.chunks_pruned == 1

            chunks_after = vector_store.get_chunks_by_package("TestPackage")
            assert len(chunks_after) == 1

            arns = {c["arn"] for c in chunks_after}
            assert "arn:archon:doc:personal-work/TestPackage/src/main.py#func_a" in arns
            assert "arn:archon:doc:personal-work/TestPackage/src/main.py#func_b" not in arns

        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Integration Test: Hash State Persistence
# =============================================================================


class TestHashStatePersistence:
    """Tests for hash state persistence and change detection.

    Verifies that hash state is persisted correctly and sync is skipped
    when hash is unchanged.

    Validates:
        Requirements 7.1, 7.2, 7.3, 7.4, 7.5
    """

    def test_hash_state_persisted_after_sync(
        self, sync_service, change_detector
    ):
        """Test that hash state is persisted after successful sync.

        Validates: Requirement 7.2
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols, hash_="test_hash_123")
        archon_docs = [create_test_doc()]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            stored_hash = change_detector.get_stored_hash("TestPackage")
            assert stored_hash == "test_hash_123"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_skipped_when_hash_unchanged(
        self, sync_service, graph_service, change_detector
    ):
        """Test that sync is skipped when hash is unchanged.

        Validates: Requirements 7.1, 7.5
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols, hash_="same_hash")
        archon_docs = [create_test_doc()]

        async def run_test():
            result1 = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result1.skipped is False
            assert result1.nodes_created == 1

            result2 = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result2.skipped is True
            assert result2.skip_reason is not None
            assert "hash" in result2.skip_reason.lower()
            assert result2.nodes_created == 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_proceeds_when_hash_changed(
        self, sync_service, graph_service, change_detector
    ):
        """Test that sync proceeds when hash has changed.

        Validates: Requirement 7.1
        """
        symbols = [create_test_symbol()]
        scip_result_v1 = create_test_parse_result(symbols=symbols, hash_="hash_v1")
        archon_docs = [create_test_doc()]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result_v1,
                archon_docs=archon_docs,
            )

            scip_result_v2 = create_test_parse_result(symbols=symbols, hash_="hash_v2")
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result_v2,
                archon_docs=archon_docs,
            )

            assert result.skipped is False
            assert result.nodes_updated == 1

            stored_hash = change_detector.get_stored_hash("TestPackage")
            assert stored_hash == "hash_v2"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_force_bypasses_change_detection(
        self, sync_service, graph_service, change_detector
    ):
        """Test that force=True bypasses change detection.

        Validates: Requirements 7.3, 7.4
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols, hash_="same_hash")
        archon_docs = [create_test_doc()]

        async def run_test():
            await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
                force=True,
            )

            assert result.skipped is False
            assert result.nodes_updated == 1

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_hash_state_survives_reload(self, tmp_path):
        """Test that hash state survives detector reload.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"

        detector1 = ChangeDetector(state_file=str(state_file))
        detector1.update_hash("TestPackage", "persistent_hash")

        detector2 = ChangeDetector(state_file=str(state_file))
        stored_hash = detector2.get_stored_hash("TestPackage")

        assert stored_hash == "persistent_hash"


# =============================================================================
# Integration Test: Error Recovery
# =============================================================================


class TestErrorRecovery:
    """Tests for error recovery and partial failure handling.

    Verifies that the sync service handles errors gracefully and
    provides meaningful error information.

    Validates:
        Requirements 8.1, 8.2, 8.3, 8.4, 9.2
    """

    def test_validation_error_returns_failure_result(self, sync_service):
        """Test that validation errors return a failure result.

        Validates: Requirement 9.2
        """
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="",
                scip_result=create_test_parse_result(),
                archon_docs=archon_docs,
            )

            assert result.success is False
            assert len(result.errors) > 0
            assert any("package_path" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_invalid_arn_logged_and_skipped(self, sync_service, graph_service):
        """Test that invalid ARNs are logged and skipped.

        Validates: Requirement 1.4
        """
        valid_symbol = create_test_symbol(
            arn="arn:archon:code:personal-work/TestPackage/src/main.py#valid_func",
            name="valid_func",
        )
        invalid_symbol = create_test_symbol(
            arn="invalid-arn-format",
            name="invalid_func",
        )
        scip_result = create_test_parse_result(
            symbols=[valid_symbol, invalid_symbol]
        )
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            assert result.success is True
            assert result.nodes_created == 1

            nodes = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes) == 1
            assert nodes[0].name == "valid_func"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_empty_docs_handled_gracefully(self, sync_service, vector_store):
        """Test that empty docs list is handled gracefully.

        Validates: Requirement 9.2
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=[],
            )

            assert result.success is True
            assert result.chunks_upserted == 0

            chunks = vector_store.get_chunks_by_package("TestPackage")
            assert len(chunks) == 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_empty_symbols_handled_gracefully(self, sync_service, graph_service):
        """Test that empty symbols list is handled gracefully.

        Validates: Requirement 9.2
        """
        scip_result = create_test_parse_result(symbols=[])
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await sync_service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )

            assert result.success is True
            assert result.nodes_created == 0

            nodes = graph_service.get_nodes_by_package("TestPackage")
            assert len(nodes) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Integration Test: Multiple Packages
# =============================================================================


class TestMultiplePackages:
    """Tests for syncing multiple packages.

    Verifies that syncing multiple packages keeps data isolated.

    Validates:
        Requirements 9.3, 9.4
    """

    def test_packages_isolated_in_graph(
        self, sync_service, graph_service
    ):
        """Test that packages are isolated in the Code Graph.

        Validates: Requirements 2.1, 6.2
        """
        symbols_a = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/PackageA/src/a.py#func_a",
                name="func_a",
                package="PackageA",
            )
        ]
        symbols_b = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/PackageB/src/b.py#func_b",
                name="func_b",
                package="PackageB",
            )
        ]

        async def run_test():
            await sync_service.sync_package(
                package_path="PackageA",
                scip_result=create_test_parse_result(symbols=symbols_a, hash_="hash_a"),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageA/src/a.py"
                    )
                ],
            )

            await sync_service.sync_package(
                package_path="PackageB",
                scip_result=create_test_parse_result(symbols=symbols_b, hash_="hash_b"),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageB/src/b.py"
                    )
                ],
            )

            nodes_a = graph_service.get_nodes_by_package("PackageA")
            nodes_b = graph_service.get_nodes_by_package("PackageB")

            assert len(nodes_a) == 1
            assert len(nodes_b) == 1
            assert nodes_a[0].name == "func_a"
            assert nodes_b[0].name == "func_b"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_packages_isolated_in_vector_store(
        self, sync_service, vector_store
    ):
        """Test that packages are isolated in the Vector Store.

        Validates: Requirements 5.1, 6.4
        """
        symbols = [create_test_symbol()]

        async def run_test():
            await sync_service.sync_package(
                package_path="PackageA",
                scip_result=create_test_parse_result(symbols=symbols, hash_="hash_a"),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageA/src/a.py",
                        content="Documentation for PackageA",
                    )
                ],
            )

            await sync_service.sync_package(
                package_path="PackageB",
                scip_result=create_test_parse_result(symbols=symbols, hash_="hash_b"),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageB/src/b.py",
                        content="Documentation for PackageB",
                    )
                ],
            )

            chunks_a = vector_store.get_chunks_by_package("PackageA")
            chunks_b = vector_store.get_chunks_by_package("PackageB")

            assert len(chunks_a) == 1
            assert len(chunks_b) == 1
            assert "PackageA" in chunks_a[0]["content"]
            assert "PackageB" in chunks_b[0]["content"]

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_only_affects_target_package(
        self, sync_service, graph_service, vector_store
    ):
        """Test that pruning only affects the target package.

        Validates: Requirements 6.2, 6.4
        """
        symbols_a = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/PackageA/src/a.py#func_a",
                name="func_a",
                package="PackageA",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/PackageA/src/a.py#func_a2",
                name="func_a2",
                package="PackageA",
            ),
        ]
        symbols_b = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/PackageB/src/b.py#func_b",
                name="func_b",
                package="PackageB",
            )
        ]

        async def run_test():
            await sync_service.sync_package(
                package_path="PackageA",
                scip_result=create_test_parse_result(symbols=symbols_a, hash_="hash_a1"),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageA/src/a.py"
                    )
                ],
            )

            await sync_service.sync_package(
                package_path="PackageB",
                scip_result=create_test_parse_result(symbols=symbols_b, hash_="hash_b"),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageB/src/b.py"
                    )
                ],
            )

            reduced_symbols_a = [symbols_a[0]]
            await sync_service.sync_package(
                package_path="PackageA",
                scip_result=create_test_parse_result(
                    symbols=reduced_symbols_a, hash_="hash_a2"
                ),
                archon_docs=[
                    create_test_doc(
                        arn="arn:archon:doc:personal-work/PackageA/src/a.py"
                    )
                ],
            )

            nodes_a = graph_service.get_nodes_by_package("PackageA")
            nodes_b = graph_service.get_nodes_by_package("PackageB")

            assert len(nodes_a) == 1
            assert len(nodes_b) == 1

        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Integration Test: Concurrent Sync
# =============================================================================


class TestConcurrentSync:
    """Tests for concurrent workspace sync behavior.

    Verifies that workspace sync runs packages in parallel with proper
    concurrency control, result aggregation, and error isolation.

    Validates:
        Requirements 9.3, 9.4

    Source:
    - src/sync/service.py (sync_workspace method)
    - .kiro/specs/sync-service/design.md (Concurrent sync section)
    """

    def test_workspace_sync_runs_packages_in_parallel(
        self, graph_adapter, vector_adapter, change_detector, graph_service, tmp_path
    ):
        """Test that workspace sync runs multiple packages in parallel.

        Creates a workspace with multiple packages and verifies they are
        synced concurrently using timing analysis.

        Validates: Requirements 9.3, 9.4
        """
        import time

        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        package_names = ["PackageA", "PackageB", "PackageC"]
        for pkg_name in package_names:
            pkg_dir = workspace_path / pkg_name
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        sync_times: dict[str, float] = {}
        original_sync_single = None

        class TimingGraphAdapter:
            """Graph adapter that tracks sync timing."""

            def __init__(self, base_adapter):
                self._base = base_adapter
                self._service = base_adapter._service

            async def sync(self, parse_result):
                pkg = parse_result.package
                sync_times[pkg] = time.time()
                await asyncio.sleep(0.1)
                return await self._base.sync(parse_result)

            async def prune(self, package: str, keep_arns: list[str]) -> int:
                return await self._base.prune(package, keep_arns)

        timing_adapter = TimingGraphAdapter(graph_adapter)

        service = KnowledgeBaseSyncService(
            graph_adapter=timing_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func",
                    name="func",
                    package=package,
                )
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/main.py",
                    content=f"Documentation for {package}",
                )
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return package_names

        service._discover_packages = mock_discover_packages

        async def run_test():
            start_time = time.time()
            result = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )
            total_time = time.time() - start_time

            assert result.success is True
            assert len(result.packages_synced) == 3
            assert len(result.packages_skipped) == 0

            if len(sync_times) >= 2:
                times = list(sync_times.values())
                max_gap = max(times) - min(times)
                assert max_gap < 0.15, f"Packages should start nearly simultaneously, gap was {max_gap}s"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_workspace_sync_aggregates_results_correctly(
        self, graph_adapter, vector_adapter, change_detector, graph_service, tmp_path
    ):
        """Test that workspace sync correctly aggregates results from all packages.

        Verifies that the WorkspaceSyncResult contains accurate counts
        from all synced packages.

        Validates: Requirements 9.3, 9.4
        """
        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        package_configs = [
            {"name": "PackageA", "symbols": 2, "docs": 1},
            {"name": "PackageB", "symbols": 3, "docs": 2},
            {"name": "PackageC", "symbols": 1, "docs": 1},
        ]

        for config in package_configs:
            pkg_dir = workspace_path / config["name"]
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            config = next(c for c in package_configs if c["name"] == package)
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func_{i}",
                    name=f"func_{i}",
                    package=package,
                )
                for i in range(config["symbols"])
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/doc_{i}.py",
                    content=f"Documentation {i} for {package}",
                )
                for i in range(config["docs"])
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return [c["name"] for c in package_configs]

        service._discover_packages = mock_discover_packages

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            assert result.success is True
            assert len(result.packages_synced) == 3

            total_nodes = sum(p.nodes_created for p in result.packages_synced)
            total_chunks = sum(p.chunks_upserted for p in result.packages_synced)

            assert total_nodes == 6
            assert total_chunks == 4

            package_names = {p.package for p in result.packages_synced}
            assert package_names == {"PackageA", "PackageB", "PackageC"}

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_workspace_sync_respects_semaphore_limit(
        self, graph_adapter, vector_adapter, change_detector, tmp_path
    ):
        """Test that workspace sync respects MAX_CONCURRENT_SYNCS semaphore limit.

        Creates more packages than the concurrency limit and verifies
        that no more than MAX_CONCURRENT_SYNCS (4) run simultaneously.

        Validates: Requirements 9.3, 9.4
        """
        from src.sync.service import MAX_CONCURRENT_SYNCS

        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        num_packages = 8
        package_names = [f"Package{i}" for i in range(num_packages)]

        for pkg_name in package_names:
            pkg_dir = workspace_path / pkg_name
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        class ConcurrencyTrackingAdapter:
            """Graph adapter that tracks concurrent executions."""

            def __init__(self, base_adapter):
                self._base = base_adapter
                self._service = base_adapter._service

            async def sync(self, parse_result):
                nonlocal concurrent_count, max_concurrent
                async with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)

                await asyncio.sleep(0.05)

                async with lock:
                    concurrent_count -= 1

                return await self._base.sync(parse_result)

            async def prune(self, package: str, keep_arns: list[str]) -> int:
                return await self._base.prune(package, keep_arns)

        tracking_adapter = ConcurrencyTrackingAdapter(graph_adapter)

        service = KnowledgeBaseSyncService(
            graph_adapter=tracking_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func",
                    name="func",
                    package=package,
                )
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/main.py",
                    content=f"Documentation for {package}",
                )
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return package_names

        service._discover_packages = mock_discover_packages

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            assert result.success is True
            assert len(result.packages_synced) == num_packages

            assert max_concurrent <= MAX_CONCURRENT_SYNCS, (
                f"Max concurrent syncs ({max_concurrent}) exceeded limit ({MAX_CONCURRENT_SYNCS})"
            )

            assert max_concurrent >= 2, (
                f"Expected at least 2 concurrent syncs, got {max_concurrent}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_workspace_sync_no_race_conditions(
        self, graph_adapter, vector_adapter, change_detector, graph_service, vector_store, tmp_path
    ):
        """Test that concurrent sync has no race conditions.

        Runs multiple packages concurrently and verifies that each package's
        data is correctly isolated and no data corruption occurs.

        Validates: Requirements 9.3, 9.4
        """
        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        num_packages = 6
        package_names = [f"Package{i}" for i in range(num_packages)]

        for pkg_name in package_names:
            pkg_dir = workspace_path / pkg_name
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            pkg_index = int(package.replace("Package", ""))
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func_{j}",
                    name=f"func_{j}",
                    package=package,
                )
                for j in range(pkg_index + 1)
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/main.py",
                    content=f"Documentation for {package} with unique content {pkg_index}",
                )
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return package_names

        service._discover_packages = mock_discover_packages

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            assert result.success is True
            assert len(result.packages_synced) == num_packages

            for i, pkg_name in enumerate(package_names):
                nodes = graph_service.get_nodes_by_package(pkg_name)
                expected_nodes = i + 1
                assert len(nodes) == expected_nodes, (
                    f"Package {pkg_name} should have {expected_nodes} nodes, got {len(nodes)}"
                )

                node_names = {n.name for n in nodes}
                expected_names = {f"func_{j}" for j in range(expected_nodes)}
                assert node_names == expected_names, (
                    f"Package {pkg_name} has wrong node names: {node_names}"
                )

            for pkg_name in package_names:
                chunks = vector_store.get_chunks_by_package(pkg_name)
                assert len(chunks) >= 1, f"Package {pkg_name} should have chunks"

                for chunk in chunks:
                    assert chunk["package"] == pkg_name, (
                        f"Chunk package mismatch: expected {pkg_name}, got {chunk['package']}"
                    )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_workspace_sync_partial_failures_dont_block_others(
        self, graph_adapter, vector_adapter, change_detector, graph_service, tmp_path
    ):
        """Test that partial failures don't block other packages from syncing.

        Simulates a failure in one package and verifies that other packages
        still sync successfully.

        Validates: Requirements 9.3, 9.4
        """
        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        package_names = ["PackageA", "PackageB_FAIL", "PackageC", "PackageD"]

        for pkg_name in package_names:
            pkg_dir = workspace_path / pkg_name
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        class FailingGraphAdapter:
            """Graph adapter that fails for specific packages."""

            def __init__(self, base_adapter):
                self._base = base_adapter
                self._service = base_adapter._service

            async def sync(self, parse_result):
                if "FAIL" in parse_result.package:
                    raise RuntimeError(f"Simulated failure for {parse_result.package}")
                return await self._base.sync(parse_result)

            async def prune(self, package: str, keep_arns: list[str]) -> int:
                return await self._base.prune(package, keep_arns)

        failing_adapter = FailingGraphAdapter(graph_adapter)

        service = KnowledgeBaseSyncService(
            graph_adapter=failing_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func",
                    name="func",
                    package=package,
                )
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/main.py",
                    content=f"Documentation for {package}",
                )
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return package_names

        service._discover_packages = mock_discover_packages

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            assert result.success is False

            successful_packages = {p.package for p in result.packages_synced if p.success}
            assert "PackageA" in successful_packages
            assert "PackageC" in successful_packages
            assert "PackageD" in successful_packages

            assert "PackageB_FAIL" in result.errors
            assert len(result.errors["PackageB_FAIL"]) > 0

            for pkg in ["PackageA", "PackageC", "PackageD"]:
                nodes = graph_service.get_nodes_by_package(pkg)
                assert len(nodes) == 1, f"Package {pkg} should have 1 node"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_workspace_sync_with_skipped_packages(
        self, graph_adapter, vector_adapter, change_detector, tmp_path
    ):
        """Test that workspace sync correctly handles skipped packages.

        Pre-syncs some packages and verifies they are skipped on subsequent
        workspace sync while new packages are synced.

        Validates: Requirements 9.3, 9.4, 7.1
        """
        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        package_names = ["PackageA", "PackageB", "PackageC"]

        for pkg_name in package_names:
            pkg_dir = workspace_path / pkg_name
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func",
                    name="func",
                    package=package,
                )
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/main.py",
                    content=f"Documentation for {package}",
                )
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return package_names

        service._discover_packages = mock_discover_packages

        async def run_test():
            result1 = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            assert result1.success is True
            assert len(result1.packages_synced) == 3
            assert len(result1.packages_skipped) == 0

            result2 = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            assert result2.success is True
            assert len(result2.packages_synced) == 0
            assert len(result2.packages_skipped) == 3

            skipped_set = set(result2.packages_skipped)
            assert skipped_set == {"PackageA", "PackageB", "PackageC"}

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_workspace_sync_force_overrides_skip(
        self, graph_adapter, vector_adapter, change_detector, tmp_path
    ):
        """Test that force=True overrides skip behavior for all packages.

        Pre-syncs packages and verifies that force=True causes all packages
        to be re-synced.

        Validates: Requirements 9.3, 9.4, 7.3, 7.4
        """
        workspace_path = tmp_path / "test_workspace"
        workspace_path.mkdir()

        package_names = ["PackageA", "PackageB"]

        for pkg_name in package_names:
            pkg_dir = workspace_path / pkg_name
            pkg_dir.mkdir()
            scip_dir = pkg_dir / ".scip"
            scip_dir.mkdir()
            (scip_dir / "index.scip").write_text("mock scip index")

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def mock_load_package_data(package: str, workspace_path: str):
            symbols = [
                create_test_symbol(
                    arn=f"arn:archon:code:personal-work/{package}/src/main.py#func",
                    name="func",
                    package=package,
                )
            ]
            scip_result = create_test_parse_result(
                symbols=symbols, hash_=f"hash_{package}"
            )
            archon_docs = [
                create_test_doc(
                    arn=f"arn:archon:doc:personal-work/{package}/src/main.py",
                    content=f"Documentation for {package}",
                )
            ]
            return scip_result, archon_docs

        service._load_package_data = mock_load_package_data

        async def mock_discover_packages(workspace_path: str):
            return package_names

        service._discover_packages = mock_discover_packages

        async def run_test():
            await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=False,
            )

            result = await service.sync_workspace(
                workspace_path=str(workspace_path),
                force=True,
            )

            assert result.success is True
            assert len(result.packages_synced) == 2
            assert len(result.packages_skipped) == 0

            for pkg_result in result.packages_synced:
                assert pkg_result.skipped is False
                assert pkg_result.nodes_updated >= 0

        asyncio.get_event_loop().run_until_complete(run_test())

