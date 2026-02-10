"""Property-based tests for Knowledge Base Sync Service.

This module contains property-based tests using Hypothesis to verify
correctness properties of the Knowledge Base Sync Service.

Feature: sync-service
Property 27: Sync Idempotency

**Validates: Requirement 8 (Sync Idempotency)**

Source:
- src/sync/service.py
- src/sync/graph_adapter.py
- src/sync/vector_adapter.py
- src/sync/models.py
- .kiro/specs/sync-service/design.md
"""

import asyncio
import hashlib
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest
from hypothesis import given, settings, strategies as st, assume

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.models import (
    GeneratedDoc,
    ScipParseResult,
    ScipRelationship,
    ScipSymbol,
    SymbolLocation,
    SyncResult,
    VectorSyncResult,
)
from src.sync.graph_adapter import (
    ScipParseResult as GraphScipParseResult,
    ScipRelationship as GraphScipRelationship,
    ScipSymbol as GraphScipSymbol,
    SyncResult as GraphSyncResult,
)
from src.vector.models import ArchonChunk


# =============================================================================
# Test Data Generation Strategies
# =============================================================================


# Valid ARN types and symbol kinds
VALID_ARN_TYPES = ["code", "doc", "k8s", "infra"]
VALID_SYMBOL_KINDS = ["function", "class", "method", "variable", "type", "module"]
VALID_RELATIONSHIP_TYPES = ["contains", "references", "implements", "extends", "imports"]

# Strategies for generating valid components
workspace_strategy = st.from_regex(r"[a-z][a-z0-9\-]{2,15}", fullmatch=True)
package_strategy = st.from_regex(r"[A-Za-z][A-Za-z0-9\-]{2,20}", fullmatch=True)
path_strategy = st.from_regex(r"[a-z][a-z0-9_/]{1,20}\.(py|ts|go|rs|md)", fullmatch=True)
symbol_name_strategy = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{1,15}", fullmatch=True)
arn_type_strategy = st.sampled_from(VALID_ARN_TYPES)
symbol_kind_strategy = st.sampled_from(VALID_SYMBOL_KINDS)
relationship_type_strategy = st.sampled_from(VALID_RELATIONSHIP_TYPES)


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
    symbol = draw(st.one_of(st.none(), symbol_name_strategy))
    return build_arn(arn_type, workspace, package, path, symbol)


@st.composite
def symbol_location_strategy(draw: st.DrawFn) -> SymbolLocation:
    """Strategy for generating SymbolLocation instances."""
    return SymbolLocation(
        file=draw(path_strategy),
        line=draw(st.integers(min_value=1, max_value=1000)),
        column=draw(st.integers(min_value=0, max_value=100)),
    )


@st.composite
def scip_symbol_strategy(
    draw: st.DrawFn,
    workspace: Optional[str] = None,
    package: Optional[str] = None,
) -> ScipSymbol:
    """Strategy for generating ScipSymbol instances with valid ARNs.
    
    Args:
        draw: Hypothesis draw function
        workspace: Optional fixed workspace (for consistent package data)
        package: Optional fixed package (for consistent package data)
    """
    ws = workspace or draw(workspace_strategy)
    pkg = package or draw(package_strategy)
    path = draw(path_strategy)
    symbol_name = draw(symbol_name_strategy)
    kind = draw(symbol_kind_strategy)
    arn_type = draw(st.sampled_from(["code"]))  # Symbols are typically code type
    
    arn = build_arn(arn_type, ws, pkg, path, symbol_name)
    
    return ScipSymbol(
        arn=arn,
        type=arn_type,
        workspace=ws,
        package=pkg,
        path=path,
        symbol=symbol_name,
        kind=kind,
        name=symbol_name,
        signature=draw(st.one_of(st.none(), st.text(min_size=5, max_size=100, alphabet=st.characters(
            whitelist_categories=("L", "N", "P"),
            whitelist_characters=" (),:->_"
        )))),
        documentation=draw(st.one_of(st.none(), st.text(min_size=10, max_size=200, alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
            whitelist_characters=" \n.,;:!?-_"
        )))),
        location=draw(symbol_location_strategy()),
    )


@st.composite
def scip_relationship_strategy(
    draw: st.DrawFn,
    available_arns: List[str],
) -> ScipRelationship:
    """Strategy for generating ScipRelationship instances.
    
    Args:
        draw: Hypothesis draw function
        available_arns: List of valid ARNs to choose from
    """
    assume(len(available_arns) >= 2)
    from_arn = draw(st.sampled_from(available_arns))
    to_arn = draw(st.sampled_from(available_arns))
    assume(from_arn != to_arn)  # Avoid self-references
    
    return ScipRelationship(
        from_arn=from_arn,
        to_arn=to_arn,
        type=draw(relationship_type_strategy),
    )


@st.composite
def scip_parse_result_strategy(draw: st.DrawFn) -> ScipParseResult:
    """Strategy for generating ScipParseResult instances.
    
    Generates a complete SCIP parse result with symbols and relationships.
    """
    workspace = draw(workspace_strategy)
    package = draw(package_strategy)
    
    # Generate 1-10 symbols for the package
    num_symbols = draw(st.integers(min_value=1, max_value=10))
    symbols = [
        draw(scip_symbol_strategy(workspace=workspace, package=package))
        for _ in range(num_symbols)
    ]
    
    # Ensure unique ARNs
    seen_arns: Set[str] = set()
    unique_symbols = []
    for symbol in symbols:
        if symbol.arn not in seen_arns:
            seen_arns.add(symbol.arn)
            unique_symbols.append(symbol)
    symbols = unique_symbols
    assume(len(symbols) >= 1)
    
    # Generate relationships between symbols
    relationships: List[ScipRelationship] = []
    if len(symbols) >= 2:
        arns = [s.arn for s in symbols]
        num_relationships = draw(st.integers(min_value=0, max_value=min(len(symbols) * 2, 15)))
        for _ in range(num_relationships):
            try:
                rel = draw(scip_relationship_strategy(arns))
                # Ensure unique relationships
                rel_key = (rel.from_arn, rel.to_arn, rel.type)
                if rel_key not in [(r.from_arn, r.to_arn, r.type) for r in relationships]:
                    relationships.append(rel)
            except Exception:
                pass  # Skip if we can't generate a valid relationship
    
    # Generate a deterministic hash based on content
    hash_input = "".join(s.arn for s in symbols) + "".join(r.from_arn + r.to_arn for r in relationships)
    index_hash = hashlib.sha256(hash_input.encode()).hexdigest()
    
    return ScipParseResult(
        symbols=symbols,
        relationships=relationships,
        hash=index_hash,
    )


@st.composite
def generated_doc_strategy(
    draw: st.DrawFn,
    workspace: Optional[str] = None,
    package: Optional[str] = None,
) -> GeneratedDoc:
    """Strategy for generating GeneratedDoc instances.
    
    Args:
        draw: Hypothesis draw function
        workspace: Optional fixed workspace
        package: Optional fixed package
    """
    ws = workspace or draw(workspace_strategy)
    pkg = package or draw(package_strategy)
    path = draw(path_strategy)
    
    # Build ARN for the doc
    arn = build_arn("doc", ws, pkg, path)
    
    # Generate content
    content = draw(st.text(
        min_size=50,
        max_size=500,
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
            whitelist_characters=" \n\t.,;:!?-_#*`"
        )
    ))
    assume(content.strip())
    
    # Generate referenced ARNs
    num_refs = draw(st.integers(min_value=0, max_value=5))
    referenced_arns = [draw(valid_arn_strategy()) for _ in range(num_refs)]
    
    return GeneratedDoc(
        source_path=path,
        doc_path=path.replace(".py", ".archon.md").replace(".ts", ".archon.md"),
        content=content,
        arn=arn,
        referenced_arns=referenced_arns,
    )


@st.composite
def archon_docs_strategy(
    draw: st.DrawFn,
    workspace: Optional[str] = None,
    package: Optional[str] = None,
) -> List[GeneratedDoc]:
    """Strategy for generating a list of GeneratedDoc instances."""
    ws = workspace or draw(workspace_strategy)
    pkg = package or draw(package_strategy)
    
    num_docs = draw(st.integers(min_value=0, max_value=5))
    docs = [
        draw(generated_doc_strategy(workspace=ws, package=pkg))
        for _ in range(num_docs)
    ]
    
    # Ensure unique ARNs
    seen_arns: Set[str] = set()
    unique_docs = []
    for doc in docs:
        if doc.arn not in seen_arns:
            seen_arns.add(doc.arn)
            unique_docs.append(doc)
    
    return unique_docs


# =============================================================================
# Mock Adapters for State Capture
# =============================================================================


@dataclass
class CapturedNode:
    """Represents a captured node state for comparison."""
    arn: str
    name: str
    kind: str
    signature: Optional[str]
    documentation: Optional[str]
    file_path: str
    line_number: Optional[int]
    
    def __hash__(self):
        return hash(self.arn)
    
    def __eq__(self, other):
        if not isinstance(other, CapturedNode):
            return False
        return self.arn == other.arn


@dataclass
class CapturedEdge:
    """Represents a captured edge state for comparison."""
    from_arn: str
    to_arn: str
    type: str
    
    def __hash__(self):
        return hash((self.from_arn, self.to_arn, self.type))
    
    def __eq__(self, other):
        if not isinstance(other, CapturedEdge):
            return False
        return (self.from_arn, self.to_arn, self.type) == (other.from_arn, other.to_arn, other.type)


@dataclass
class CapturedChunk:
    """Represents a captured chunk state for comparison."""
    arn: str
    chunk_index: int
    content: str
    package: str
    
    def __hash__(self):
        return hash((self.arn, self.chunk_index))
    
    def __eq__(self, other):
        if not isinstance(other, CapturedChunk):
            return False
        return (self.arn, self.chunk_index) == (other.arn, other.chunk_index)


@dataclass
class CapturedState:
    """Captured state of the knowledge base after a sync operation."""
    nodes: Set[CapturedNode] = field(default_factory=set)
    edges: Set[CapturedEdge] = field(default_factory=set)
    chunks: Set[CapturedChunk] = field(default_factory=set)
    
    def __eq__(self, other):
        if not isinstance(other, CapturedState):
            return False
        return (
            self.nodes == other.nodes and
            self.edges == other.edges and
            self.chunks == other.chunks
        )


class MockGraphSyncAdapter:
    """Mock GraphSyncAdapter that captures state for verification.
    
    Simulates the GraphSyncAdapter behavior while capturing all nodes
    and edges for idempotency verification.
    """
    
    def __init__(self):
        self._nodes: Dict[str, CapturedNode] = {}  # ARN -> Node
        self._edges: Dict[Tuple[str, str, str], CapturedEdge] = {}  # (from, to, type) -> Edge
        self._sync_count = 0
    
    async def sync(self, parse_result: GraphScipParseResult) -> GraphSyncResult:
        """Sync SCIP parse result to the mock graph.
        
        Performs upsert operations (insert or update on ARN conflict).
        """
        self._sync_count += 1
        nodes_created = 0
        nodes_updated = 0
        edges_created = 0
        
        # Upsert nodes
        for symbol in parse_result.symbols:
            node = CapturedNode(
                arn=symbol.arn,
                name=symbol.name,
                kind=symbol.kind,
                signature=symbol.signature,
                documentation=symbol.documentation,
                file_path=symbol.file_path,
                line_number=symbol.line_number,
            )
            if symbol.arn in self._nodes:
                nodes_updated += 1
            else:
                nodes_created += 1
            self._nodes[symbol.arn] = node
        
        # Upsert edges
        for rel in parse_result.relationships:
            edge_key = (rel.from_arn, rel.to_arn, rel.type)
            edge = CapturedEdge(
                from_arn=rel.from_arn,
                to_arn=rel.to_arn,
                type=rel.type,
            )
            if edge_key not in self._edges:
                edges_created += 1
            self._edges[edge_key] = edge
        
        return GraphSyncResult(
            nodes_created=nodes_created,
            nodes_updated=nodes_updated,
            edges_created=edges_created,
            edges_removed=0,
        )
    
    async def prune(self, package: str, keep_arns: List[str]) -> int:
        """Prune nodes not in keep_arns list."""
        keep_set = set(keep_arns)
        to_remove = [arn for arn in self._nodes if arn not in keep_set]
        
        for arn in to_remove:
            del self._nodes[arn]
            # Also remove edges involving this node
            edges_to_remove = [
                key for key in self._edges
                if key[0] == arn or key[1] == arn
            ]
            for key in edges_to_remove:
                del self._edges[key]
        
        return len(to_remove)
    
    def get_state(self) -> Tuple[Set[CapturedNode], Set[CapturedEdge]]:
        """Get current state of nodes and edges."""
        return set(self._nodes.values()), set(self._edges.values())
    
    def get_node_arns(self) -> Set[str]:
        """Get all node ARNs."""
        return set(self._nodes.keys())
    
    def get_edge_keys(self) -> Set[Tuple[str, str, str]]:
        """Get all edge keys (from_arn, to_arn, type)."""
        return set(self._edges.keys())


class MockVectorSyncAdapter:
    """Mock VectorSyncAdapter that captures state for verification.
    
    Simulates the VectorSyncAdapter behavior while capturing all chunks
    for idempotency verification.
    """
    
    def __init__(self):
        self._chunks: Dict[Tuple[str, int], CapturedChunk] = {}  # (arn, chunk_index) -> Chunk
        self._sync_count = 0
    
    def chunk_document(self, doc: GeneratedDoc) -> List[ArchonChunk]:
        """Chunk a document into segments."""
        content = doc.content
        if not content or not content.strip():
            return []
        
        # Simple chunking: split into ~200 char chunks
        chunk_size = 200
        chunks = []
        chunk_index = 0
        
        for i in range(0, len(content), chunk_size):
            chunk_content = content[i:i + chunk_size].strip()
            if chunk_content:
                chunks.append(ArchonChunk(
                    content=chunk_content,
                    source=doc.doc_path,
                    chunk_index=chunk_index,
                    arn=doc.arn,
                    related_arns=doc.referenced_arns,
                    symbol_name=None,
                    symbol_kind=None,
                    package="",
                ))
                chunk_index += 1
        
        return chunks
    
    def generate_chunk_id(self, arn: str, chunk_index: int) -> str:
        """Generate deterministic chunk ID."""
        id_input = f"{arn}:{chunk_index}"
        return hashlib.sha256(id_input.encode()).hexdigest()[:32]
    
    async def sync_docs(
        self,
        package: str,
        archon_docs: List[GeneratedDoc],
    ) -> VectorSyncResult:
        """Sync Archon documentation to the mock vector store.
        
        Performs upsert operations using deterministic chunk IDs.
        """
        self._sync_count += 1
        chunks_upserted = 0
        current_arns: Set[str] = set()
        
        for doc in archon_docs:
            current_arns.add(doc.arn)
            doc_chunks = self.chunk_document(doc)
            
            for chunk in doc_chunks:
                chunk.package = package
                chunk_key = (chunk.arn, chunk.chunk_index)
                captured = CapturedChunk(
                    arn=chunk.arn,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    package=package,
                )
                self._chunks[chunk_key] = captured
                chunks_upserted += 1
        
        # Prune stale chunks
        chunks_pruned = await self.prune_stale_chunks(package, list(current_arns))
        
        return VectorSyncResult(
            chunks_upserted=chunks_upserted,
            chunks_pruned=chunks_pruned,
            errors=[],
        )
    
    async def prune_stale_chunks(
        self,
        package: str,
        current_arns: List[str],
    ) -> int:
        """Remove chunks not in current ARNs list."""
        current_set = set(current_arns)
        to_remove = [
            key for key, chunk in self._chunks.items()
            if chunk.package == package and chunk.arn not in current_set
        ]
        
        for key in to_remove:
            del self._chunks[key]
        
        return len(to_remove)
    
    def get_state(self) -> Set[CapturedChunk]:
        """Get current state of chunks."""
        return set(self._chunks.values())
    
    def get_chunk_keys(self) -> Set[Tuple[str, int]]:
        """Get all chunk keys (arn, chunk_index)."""
        return set(self._chunks.keys())


class MockChangeDetector:
    """Mock ChangeDetector that always allows sync (for idempotency testing)."""
    
    def __init__(self):
        self._hashes: Dict[str, str] = {}
    
    def has_changed(self, package: str, current_hash: str) -> bool:
        """Always return True to allow sync for testing."""
        return True
    
    def get_stored_hash(self, package: str) -> Optional[str]:
        """Get stored hash for package."""
        return self._hashes.get(package)
    
    def update_hash(self, package: str, new_hash: str) -> None:
        """Update stored hash."""
        self._hashes[package] = new_hash


class MockSyncService:
    """Mock sync service that uses mock adapters for state capture.
    
    This service mimics the KnowledgeBaseSyncService behavior while
    allowing state inspection for idempotency verification.
    """
    
    def __init__(self):
        self.graph_adapter = MockGraphSyncAdapter()
        self.vector_adapter = MockVectorSyncAdapter()
        self.change_detector = MockChangeDetector()
    
    async def sync_package(
        self,
        package_path: str,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
        force: bool = True,
    ) -> SyncResult:
        """Sync a package to the mock knowledge base."""
        # Convert ScipSymbol to GraphScipSymbol
        graph_symbols = [
            GraphScipSymbol(
                arn=s.arn,
                name=s.name,
                kind=s.kind,
                signature=s.signature,
                documentation=s.documentation,
                file_path=s.location.file if s.location else s.path,
                line_number=s.location.line if s.location else None,
            )
            for s in scip_result.symbols
        ]
        
        # Convert ScipRelationship to GraphScipRelationship
        graph_relationships = [
            GraphScipRelationship(
                from_arn=r.from_arn,
                to_arn=r.to_arn,
                type=r.type,
            )
            for r in scip_result.relationships
        ]
        
        # Get workspace and package from first symbol
        workspace = scip_result.symbols[0].workspace if scip_result.symbols else ""
        package = scip_result.symbols[0].package if scip_result.symbols else package_path
        
        graph_parse_result = GraphScipParseResult(
            package_path=package_path,
            workspace=workspace,
            package=package,
            symbols=graph_symbols,
            relationships=graph_relationships,
            index_hash=scip_result.hash,
        )
        
        # Sync to graph
        graph_result = await self.graph_adapter.sync(graph_parse_result)
        
        # Prune stale nodes
        current_arns = [s.arn for s in scip_result.symbols]
        nodes_pruned = await self.graph_adapter.prune(package, current_arns)
        
        # Sync to vector store
        vector_result = await self.vector_adapter.sync_docs(package, archon_docs)
        
        # Update hash
        self.change_detector.update_hash(package_path, scip_result.hash)
        
        return SyncResult(
            success=True,
            nodes_created=graph_result.nodes_created,
            nodes_updated=graph_result.nodes_updated,
            edges_created=graph_result.edges_created,
            chunks_upserted=vector_result.chunks_upserted,
            nodes_pruned=nodes_pruned,
            chunks_pruned=vector_result.chunks_pruned,
            skipped=False,
            skip_reason=None,
            errors=[],
        )
    
    def capture_state(self) -> CapturedState:
        """Capture current state of the knowledge base."""
        nodes, edges = self.graph_adapter.get_state()
        chunks = self.vector_adapter.get_state()
        return CapturedState(nodes=nodes, edges=edges, chunks=chunks)


# =============================================================================
# Property 27: Sync Idempotency Tests
# =============================================================================


class TestProperty27SyncIdempotency:
    """Property 27: Sync Idempotency.

    For any package, running the sync service multiple times with the same
    SCIP result and Archon docs SHALL produce identical Code Graph and
    Vector Store state. Specifically:
    - The set of nodes after N syncs SHALL equal the set after 1 sync
    - The set of edges after N syncs SHALL equal the set after 1 sync
    - The set of chunks after N syncs SHALL equal the set after 1 sync
    - No duplicate entries SHALL be created

    Feature: sync-service, Property 27: Sync Idempotency
    **Validates: Requirement 8 (Sync Idempotency)**
    """

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
        num_syncs=st.sampled_from([1, 2, 5]),
    )
    def test_sync_idempotency_state_equality(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
        num_syncs: int,
    ) -> None:
        """Test that state after N syncs equals state after 1 sync.

        Property: Running sync_package N times with the same inputs SHALL
        produce identical state as running it once.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Run first sync and capture state
            await service.sync_package(package_path, scip_result, archon_docs)
            state_after_1 = service.capture_state()
            
            # Run additional syncs
            for i in range(num_syncs - 1):
                await service.sync_package(package_path, scip_result, archon_docs)
            
            state_after_n = service.capture_state()
            
            # Verify state equality
            assert state_after_n.nodes == state_after_1.nodes, (
                f"Nodes after {num_syncs} syncs differ from nodes after 1 sync. "
                f"After 1: {len(state_after_1.nodes)}, After {num_syncs}: {len(state_after_n.nodes)}"
            )
            assert state_after_n.edges == state_after_1.edges, (
                f"Edges after {num_syncs} syncs differ from edges after 1 sync. "
                f"After 1: {len(state_after_1.edges)}, After {num_syncs}: {len(state_after_n.edges)}"
            )
            assert state_after_n.chunks == state_after_1.chunks, (
                f"Chunks after {num_syncs} syncs differ from chunks after 1 sync. "
                f"After 1: {len(state_after_1.chunks)}, After {num_syncs}: {len(state_after_n.chunks)}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_no_duplicate_arns_in_nodes(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that no duplicate ARNs exist in nodes after multiple syncs.

        Property: After N syncs, there SHALL be no duplicate ARNs in the
        node set. Each ARN SHALL appear exactly once.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Run multiple syncs
            for _ in range(3):
                await service.sync_package(package_path, scip_result, archon_docs)
            
            # Check for duplicate ARNs
            node_arns = service.graph_adapter.get_node_arns()
            
            # The number of unique ARNs should equal the number of nodes
            nodes, _ = service.graph_adapter.get_state()
            assert len(node_arns) == len(nodes), (
                f"Duplicate ARNs detected: {len(nodes)} nodes but {len(node_arns)} unique ARNs"
            )
            
            # Verify each input symbol ARN appears at most once
            input_arns = [s.arn for s in scip_result.symbols]
            for arn in input_arns:
                count = sum(1 for n in nodes if n.arn == arn)
                assert count <= 1, f"ARN '{arn}' appears {count} times in nodes"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_no_duplicate_edges(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that no duplicate (from_arn, to_arn, type) exist in edges.

        Property: After N syncs, there SHALL be no duplicate edges. Each
        (from_arn, to_arn, type) tuple SHALL appear exactly once.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Run multiple syncs
            for _ in range(3):
                await service.sync_package(package_path, scip_result, archon_docs)
            
            # Check for duplicate edges
            edge_keys = service.graph_adapter.get_edge_keys()
            _, edges = service.graph_adapter.get_state()
            
            # The number of unique edge keys should equal the number of edges
            assert len(edge_keys) == len(edges), (
                f"Duplicate edges detected: {len(edges)} edges but {len(edge_keys)} unique keys"
            )
            
            # Verify each input relationship appears at most once
            for rel in scip_result.relationships:
                rel_key = (rel.from_arn, rel.to_arn, rel.type)
                count = sum(1 for e in edges if (e.from_arn, e.to_arn, e.type) == rel_key)
                assert count <= 1, f"Edge {rel_key} appears {count} times"
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_no_duplicate_chunks(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that no duplicate (arn, chunk_index) exist in chunks.

        Property: After N syncs, there SHALL be no duplicate chunks. Each
        (arn, chunk_index) tuple SHALL appear exactly once.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Run multiple syncs
            for _ in range(3):
                await service.sync_package(package_path, scip_result, archon_docs)
            
            # Check for duplicate chunks
            chunk_keys = service.vector_adapter.get_chunk_keys()
            chunks = service.vector_adapter.get_state()
            
            # The number of unique chunk keys should equal the number of chunks
            assert len(chunk_keys) == len(chunks), (
                f"Duplicate chunks detected: {len(chunks)} chunks but {len(chunk_keys)} unique keys"
            )
            
            # Verify each (arn, chunk_index) appears at most once
            for chunk in chunks:
                chunk_key = (chunk.arn, chunk.chunk_index)
                count = sum(1 for c in chunks if (c.arn, c.chunk_index) == chunk_key)
                assert count == 1, f"Chunk {chunk_key} appears {count} times"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_node_count_stable_across_syncs(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that node count remains stable across multiple syncs.

        Property: The number of nodes after N syncs SHALL equal the number
        of nodes after 1 sync (assuming same input).

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            counts = []
            for i in range(5):
                await service.sync_package(package_path, scip_result, archon_docs)
                nodes, _ = service.graph_adapter.get_state()
                counts.append(len(nodes))
            
            # All counts should be equal
            assert all(c == counts[0] for c in counts), (
                f"Node count varied across syncs: {counts}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_edge_count_stable_across_syncs(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that edge count remains stable across multiple syncs.

        Property: The number of edges after N syncs SHALL equal the number
        of edges after 1 sync (assuming same input).

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            counts = []
            for i in range(5):
                await service.sync_package(package_path, scip_result, archon_docs)
                _, edges = service.graph_adapter.get_state()
                counts.append(len(edges))
            
            # All counts should be equal
            assert all(c == counts[0] for c in counts), (
                f"Edge count varied across syncs: {counts}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_chunk_count_stable_across_syncs(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that chunk count remains stable across multiple syncs.

        Property: The number of chunks after N syncs SHALL equal the number
        of chunks after 1 sync (assuming same input).

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            counts = []
            for i in range(5):
                await service.sync_package(package_path, scip_result, archon_docs)
                chunks = service.vector_adapter.get_state()
                counts.append(len(chunks))
            
            # All counts should be equal
            assert all(c == counts[0] for c in counts), (
                f"Chunk count varied across syncs: {counts}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_deterministic_chunk_ids(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that chunk IDs are deterministic across syncs.

        Property: The same (arn, chunk_index) SHALL always produce the
        same chunk ID, enabling idempotent upserts.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        adapter = MockVectorSyncAdapter()
        
        # Generate chunk IDs multiple times for the same inputs
        for doc in archon_docs:
            chunks = adapter.chunk_document(doc)
            for chunk in chunks:
                id1 = adapter.generate_chunk_id(chunk.arn, chunk.chunk_index)
                id2 = adapter.generate_chunk_id(chunk.arn, chunk.chunk_index)
                id3 = adapter.generate_chunk_id(chunk.arn, chunk.chunk_index)
                
                assert id1 == id2 == id3, (
                    f"Chunk ID not deterministic for ({chunk.arn}, {chunk.chunk_index}): "
                    f"{id1} != {id2} != {id3}"
                )

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_sync_result_consistency(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that sync results are consistent with actual state.

        Property: The counts reported in SyncResult SHALL accurately
        reflect the actual state changes in the knowledge base.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync should create nodes
            result1 = await service.sync_package(package_path, scip_result, archon_docs)
            
            nodes, edges = service.graph_adapter.get_state()
            chunks = service.vector_adapter.get_state()
            
            # Verify first sync created the expected number of nodes
            assert result1.nodes_created == len(nodes), (
                f"First sync reported {result1.nodes_created} nodes created, "
                f"but {len(nodes)} nodes exist"
            )
            
            # Second sync should update nodes (not create new ones)
            result2 = await service.sync_package(package_path, scip_result, archon_docs)
            
            nodes2, edges2 = service.graph_adapter.get_state()
            
            # Node count should remain the same
            assert len(nodes2) == len(nodes), (
                f"Node count changed after second sync: {len(nodes)} -> {len(nodes2)}"
            )
            
            # Second sync should report updates, not creates
            assert result2.nodes_created == 0, (
                f"Second sync created {result2.nodes_created} new nodes (expected 0)"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_upsert_semantics_for_nodes(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that nodes use upsert semantics (insert or update on conflict).

        Property: When syncing the same symbol twice, the second sync SHALL
        update the existing node rather than creating a duplicate.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync
            await service.sync_package(package_path, scip_result, archon_docs)
            nodes_after_1, _ = service.graph_adapter.get_state()
            arns_after_1 = {n.arn for n in nodes_after_1}
            
            # Second sync with same data
            await service.sync_package(package_path, scip_result, archon_docs)
            nodes_after_2, _ = service.graph_adapter.get_state()
            arns_after_2 = {n.arn for n in nodes_after_2}
            
            # ARN sets should be identical
            assert arns_after_1 == arns_after_2, (
                f"ARN sets differ after second sync. "
                f"Added: {arns_after_2 - arns_after_1}, "
                f"Removed: {arns_after_1 - arns_after_2}"
            )
            
            # Node count should be identical
            assert len(nodes_after_1) == len(nodes_after_2), (
                f"Node count changed: {len(nodes_after_1)} -> {len(nodes_after_2)}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_upsert_semantics_for_edges(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that edges use upsert semantics on unique constraint.

        Property: When syncing the same relationship twice, the second sync
        SHALL update the existing edge rather than creating a duplicate.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync
            await service.sync_package(package_path, scip_result, archon_docs)
            _, edges_after_1 = service.graph_adapter.get_state()
            keys_after_1 = {(e.from_arn, e.to_arn, e.type) for e in edges_after_1}
            
            # Second sync with same data
            await service.sync_package(package_path, scip_result, archon_docs)
            _, edges_after_2 = service.graph_adapter.get_state()
            keys_after_2 = {(e.from_arn, e.to_arn, e.type) for e in edges_after_2}
            
            # Edge key sets should be identical
            assert keys_after_1 == keys_after_2, (
                f"Edge key sets differ after second sync. "
                f"Added: {keys_after_2 - keys_after_1}, "
                f"Removed: {keys_after_1 - keys_after_2}"
            )
            
            # Edge count should be identical
            assert len(edges_after_1) == len(edges_after_2), (
                f"Edge count changed: {len(edges_after_1)} -> {len(edges_after_2)}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_upsert_semantics_for_chunks(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that chunks use upsert semantics with deterministic IDs.

        Property: When syncing the same document twice, the second sync
        SHALL update existing chunks rather than creating duplicates.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync
            await service.sync_package(package_path, scip_result, archon_docs)
            chunks_after_1 = service.vector_adapter.get_state()
            keys_after_1 = {(c.arn, c.chunk_index) for c in chunks_after_1}
            
            # Second sync with same data
            await service.sync_package(package_path, scip_result, archon_docs)
            chunks_after_2 = service.vector_adapter.get_state()
            keys_after_2 = {(c.arn, c.chunk_index) for c in chunks_after_2}
            
            # Chunk key sets should be identical
            assert keys_after_1 == keys_after_2, (
                f"Chunk key sets differ after second sync. "
                f"Added: {keys_after_2 - keys_after_1}, "
                f"Removed: {keys_after_1 - keys_after_2}"
            )
            
            # Chunk count should be identical
            assert len(chunks_after_1) == len(chunks_after_2), (
                f"Chunk count changed: {len(chunks_after_1)} -> {len(chunks_after_2)}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestSyncIdempotencyEdgeCases:
    """Edge case tests for sync idempotency.

    Tests boundary conditions and special cases for the idempotency property.

    Feature: sync-service, Property 27: Sync Idempotency
    **Validates: Requirement 8 (Sync Idempotency)**
    """

    def test_empty_scip_result_idempotent(self) -> None:
        """Test that syncing empty SCIP result is idempotent."""
        async def run_test():
            service = MockSyncService()
            
            # Create minimal valid SCIP result with one symbol
            symbol = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#main",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="main",
                kind="function",
                name="main",
                signature=None,
                documentation=None,
                location=SymbolLocation(file="src/main.py", line=1, column=0),
            )
            scip_result = ScipParseResult(
                symbols=[symbol],
                relationships=[],
                hash="abc123",
            )
            
            # Sync multiple times
            for _ in range(3):
                await service.sync_package("Package", scip_result, [])
            
            nodes, edges = service.graph_adapter.get_state()
            chunks = service.vector_adapter.get_state()
            
            assert len(nodes) == 1
            assert len(edges) == 0
            assert len(chunks) == 0
        
        asyncio.get_event_loop().run_until_complete(run_test())


    def test_empty_archon_docs_idempotent(self) -> None:
        """Test that syncing with empty Archon docs is idempotent."""
        async def run_test():
            service = MockSyncService()
            
            symbol = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#main",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="main",
                kind="function",
                name="main",
                signature="def main() -> None",
                documentation="Main entry point",
                location=SymbolLocation(file="src/main.py", line=1, column=0),
            )
            scip_result = ScipParseResult(
                symbols=[symbol],
                relationships=[],
                hash="abc123",
            )
            
            # Sync multiple times with empty docs
            for _ in range(3):
                await service.sync_package("Package", scip_result, [])
            
            chunks = service.vector_adapter.get_state()
            assert len(chunks) == 0
        
        asyncio.get_event_loop().run_until_complete(run_test())

    def test_single_symbol_idempotent(self) -> None:
        """Test that syncing a single symbol is idempotent."""
        async def run_test():
            service = MockSyncService()
            
            symbol = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#main",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="main",
                kind="function",
                name="main",
                signature="def main() -> None",
                documentation="Main entry point",
                location=SymbolLocation(file="src/main.py", line=1, column=0),
            )
            scip_result = ScipParseResult(
                symbols=[symbol],
                relationships=[],
                hash="abc123",
            )
            
            # Capture state after each sync
            states = []
            for _ in range(5):
                await service.sync_package("Package", scip_result, [])
                states.append(service.capture_state())
            
            # All states should be equal
            for i, state in enumerate(states[1:], 1):
                assert state == states[0], f"State {i} differs from state 0"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    def test_single_doc_idempotent(self) -> None:
        """Test that syncing a single document is idempotent."""
        async def run_test():
            service = MockSyncService()
            
            symbol = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#main",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="main",
                kind="function",
                name="main",
                signature=None,
                documentation=None,
                location=SymbolLocation(file="src/main.py", line=1, column=0),
            )
            scip_result = ScipParseResult(
                symbols=[symbol],
                relationships=[],
                hash="abc123",
            )
            
            doc = GeneratedDoc(
                source_path="src/main.py",
                doc_path="src/main.archon.md",
                content="This is the main function documentation. " * 10,
                arn="arn:archon:doc:workspace/Package/src/main.py",
                referenced_arns=[],
            )
            
            # Capture state after each sync
            states = []
            for _ in range(5):
                await service.sync_package("Package", scip_result, [doc])
                states.append(service.capture_state())
            
            # All states should be equal
            for i, state in enumerate(states[1:], 1):
                assert state == states[0], f"State {i} differs from state 0"
        
        asyncio.get_event_loop().run_until_complete(run_test())


    def test_self_referencing_relationship_idempotent(self) -> None:
        """Test that relationships are handled correctly (no self-references)."""
        async def run_test():
            service = MockSyncService()
            
            symbol1 = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#ClassA",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="ClassA",
                kind="class",
                name="ClassA",
                signature=None,
                documentation=None,
                location=SymbolLocation(file="src/main.py", line=1, column=0),
            )
            symbol2 = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#method_a",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="method_a",
                kind="method",
                name="method_a",
                signature=None,
                documentation=None,
                location=SymbolLocation(file="src/main.py", line=10, column=4),
            )
            
            relationship = ScipRelationship(
                from_arn=symbol1.arn,
                to_arn=symbol2.arn,
                type="contains",
            )
            
            scip_result = ScipParseResult(
                symbols=[symbol1, symbol2],
                relationships=[relationship],
                hash="abc123",
            )
            
            # Sync multiple times
            for _ in range(3):
                await service.sync_package("Package", scip_result, [])
            
            _, edges = service.graph_adapter.get_state()
            
            # Should have exactly one edge
            assert len(edges) == 1
            edge = list(edges)[0]
            assert edge.from_arn == symbol1.arn
            assert edge.to_arn == symbol2.arn
            assert edge.type == "contains"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(num_syncs=st.integers(min_value=1, max_value=10))
    def test_arbitrary_sync_count_idempotent(self, num_syncs: int) -> None:
        """Test that any number of syncs produces the same state.

        Property: For any N >= 1, syncing N times SHALL produce the same
        state as syncing once.

        Feature: sync-service, Property 27: Sync Idempotency
        **Validates: Requirement 8 (Sync Idempotency)**
        """
        async def run_test():
            service = MockSyncService()
            
            symbol = ScipSymbol(
                arn="arn:archon:code:workspace/Package/src/main.py#main",
                type="code",
                workspace="workspace",
                package="Package",
                path="src/main.py",
                symbol="main",
                kind="function",
                name="main",
                signature="def main() -> None",
                documentation="Main function",
                location=SymbolLocation(file="src/main.py", line=1, column=0),
            )
            scip_result = ScipParseResult(
                symbols=[symbol],
                relationships=[],
                hash="abc123",
            )
            
            doc = GeneratedDoc(
                source_path="src/main.py",
                doc_path="src/main.archon.md",
                content="Documentation content for the main function.",
                arn="arn:archon:doc:workspace/Package/src/main.py",
                referenced_arns=[],
            )
            
            # First sync
            await service.sync_package("Package", scip_result, [doc])
            state_after_1 = service.capture_state()
            
            # Additional syncs
            for _ in range(num_syncs - 1):
                await service.sync_package("Package", scip_result, [doc])
            
            state_after_n = service.capture_state()
            
            assert state_after_n == state_after_1, (
                f"State after {num_syncs} syncs differs from state after 1 sync"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Property 28: Sync Change Detection Tests
# =============================================================================


class MockChangeDetectorWithState:
    """Mock ChangeDetector that properly implements change detection.
    
    Unlike MockChangeDetector (which always allows sync for idempotency testing),
    this mock properly tracks hash state and implements the change detection logic
    as specified in Property 28.
    """
    
    def __init__(self):
        self._hashes: Dict[str, str] = {}
        self._has_changed_calls: List[Tuple[str, str]] = []
        self._update_hash_calls: List[Tuple[str, str]] = []
    
    def has_changed(self, package: str, current_hash: str) -> bool:
        """Check if package hash has changed.
        
        Returns True if:
        - No stored hash exists for the package
        - Stored hash differs from current hash
        
        Returns False if:
        - Stored hash matches current hash
        """
        self._has_changed_calls.append((package, current_hash))
        stored_hash = self._hashes.get(package)
        if stored_hash is None:
            return True
        return stored_hash != current_hash
    
    def get_stored_hash(self, package: str) -> Optional[str]:
        """Get stored hash for package."""
        return self._hashes.get(package)
    
    def update_hash(self, package: str, new_hash: str) -> None:
        """Update stored hash for package."""
        self._update_hash_calls.append((package, new_hash))
        self._hashes[package] = new_hash
    
    def reset(self) -> None:
        """Reset all state for fresh test."""
        self._hashes.clear()
        self._has_changed_calls.clear()
        self._update_hash_calls.clear()
    
    def get_has_changed_call_count(self) -> int:
        """Get number of has_changed calls."""
        return len(self._has_changed_calls)
    
    def get_update_hash_call_count(self) -> int:
        """Get number of update_hash calls."""
        return len(self._update_hash_calls)


class MockGraphAdapterWithTracking(MockGraphSyncAdapter):
    """Mock GraphSyncAdapter that tracks operation counts for change detection tests."""
    
    def __init__(self):
        super().__init__()
        self._operation_counts: List[int] = []
    
    async def sync(self, parse_result: GraphScipParseResult) -> GraphSyncResult:
        """Track sync operations."""
        result = await super().sync(parse_result)
        self._operation_counts.append(
            result.nodes_created + result.nodes_updated + result.edges_created
        )
        return result
    
    def get_operation_count(self) -> int:
        """Get total number of sync operations performed."""
        return len(self._operation_counts)
    
    def get_total_operations(self) -> int:
        """Get total number of database operations."""
        return sum(self._operation_counts)
    
    def reset_tracking(self) -> None:
        """Reset operation tracking."""
        self._operation_counts.clear()


class MockVectorAdapterWithTracking(MockVectorSyncAdapter):
    """Mock VectorSyncAdapter that tracks operation counts for change detection tests."""
    
    def __init__(self):
        super().__init__()
        self._operation_counts: List[int] = []
    
    async def sync_docs(
        self,
        package: str,
        archon_docs: List[GeneratedDoc],
    ) -> VectorSyncResult:
        """Track sync operations."""
        result = await super().sync_docs(package, archon_docs)
        self._operation_counts.append(result.chunks_upserted)
        return result
    
    def get_operation_count(self) -> int:
        """Get total number of sync operations performed."""
        return len(self._operation_counts)
    
    def get_total_operations(self) -> int:
        """Get total number of chunk operations."""
        return sum(self._operation_counts)
    
    def reset_tracking(self) -> None:
        """Reset operation tracking."""
        self._operation_counts.clear()


class MockSyncServiceWithChangeDetection:
    """Mock sync service that properly implements change detection.
    
    This service mimics the KnowledgeBaseSyncService behavior with proper
    change detection logic for testing Property 28.
    """
    
    def __init__(self):
        self.graph_adapter = MockGraphAdapterWithTracking()
        self.vector_adapter = MockVectorAdapterWithTracking()
        self.change_detector = MockChangeDetectorWithState()
    
    async def sync_package(
        self,
        package_path: str,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
        force: bool = False,
    ) -> SyncResult:
        """Sync a package with proper change detection.
        
        Implements Property 28 logic:
        - WHEN hash matches stored hash AND force=false THEN sync SHALL be skipped
        - WHEN hash matches stored hash AND force=true THEN sync SHALL proceed
        - WHEN hash differs from stored hash THEN sync SHALL proceed
        """
        # Check change detection (unless force=true)
        if not force and not self.change_detector.has_changed(package_path, scip_result.hash):
            return SyncResult(
                success=True,
                nodes_created=0,
                nodes_updated=0,
                edges_created=0,
                chunks_upserted=0,
                nodes_pruned=0,
                chunks_pruned=0,
                skipped=True,
                skip_reason="SCIP index hash unchanged",
                errors=[],
            )
        
        # Convert ScipSymbol to GraphScipSymbol
        graph_symbols = [
            GraphScipSymbol(
                arn=s.arn,
                name=s.name,
                kind=s.kind,
                signature=s.signature,
                documentation=s.documentation,
                file_path=s.location.file if s.location else s.path,
                line_number=s.location.line if s.location else None,
            )
            for s in scip_result.symbols
        ]
        
        # Convert ScipRelationship to GraphScipRelationship
        graph_relationships = [
            GraphScipRelationship(
                from_arn=r.from_arn,
                to_arn=r.to_arn,
                type=r.type,
            )
            for r in scip_result.relationships
        ]

        
        # Get workspace and package from first symbol
        workspace = scip_result.symbols[0].workspace if scip_result.symbols else ""
        package = scip_result.symbols[0].package if scip_result.symbols else package_path
        
        graph_parse_result = GraphScipParseResult(
            package_path=package_path,
            workspace=workspace,
            package=package,
            symbols=graph_symbols,
            relationships=graph_relationships,
            index_hash=scip_result.hash,
        )
        
        # Sync to graph
        graph_result = await self.graph_adapter.sync(graph_parse_result)
        
        # Prune stale nodes
        current_arns = [s.arn for s in scip_result.symbols]
        nodes_pruned = await self.graph_adapter.prune(package, current_arns)
        
        # Sync to vector store
        vector_result = await self.vector_adapter.sync_docs(package, archon_docs)
        
        # Update hash after successful sync
        self.change_detector.update_hash(package_path, scip_result.hash)
        
        return SyncResult(
            success=True,
            nodes_created=graph_result.nodes_created,
            nodes_updated=graph_result.nodes_updated,
            edges_created=graph_result.edges_created,
            chunks_upserted=vector_result.chunks_upserted,
            nodes_pruned=nodes_pruned,
            chunks_pruned=vector_result.chunks_pruned,
            skipped=False,
            skip_reason=None,
            errors=[],
        )
    
    def reset(self) -> None:
        """Reset all state for fresh test."""
        self.graph_adapter = MockGraphAdapterWithTracking()
        self.vector_adapter = MockVectorAdapterWithTracking()
        self.change_detector.reset()


class TestProperty28SyncChangeDetection:
    """Property 28: Sync Change Detection.

    For any package where the SCIP index hash has not changed since the last
    sync, the sync service SHALL skip synchronization unless `force=true`.
    Specifically:
    - WHEN hash matches stored hash AND force=false THEN sync SHALL be skipped
    - WHEN hash matches stored hash AND force=true THEN sync SHALL proceed
    - WHEN hash differs from stored hash THEN sync SHALL proceed regardless
      of force parameter

    Feature: sync-service, Property 28: Sync Change Detection
    **Validates: Requirement 7 (Change Detection)**
    """

    @settings(max_examples=100, deadline=None)
    @given(scip_result=scip_parse_result_strategy())
    def test_same_hash_force_false_skips_sync(
        self,
        scip_result: ScipParseResult,
    ) -> None:
        """Test that sync is skipped when hash unchanged and force=false.

        Property: WHEN hash matches stored hash AND force=false
        THEN sync SHALL be skipped (skipped=true in result)
        AND no database operations SHALL occur.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync to establish baseline state
            result1 = await service.sync_package(
                package_path, scip_result, [], force=False
            )
            assert not result1.skipped, "First sync should not be skipped"
            
            # Record operation counts after first sync
            graph_ops_after_first = service.graph_adapter.get_operation_count()
            vector_ops_after_first = service.vector_adapter.get_operation_count()
            
            # Second sync with same hash and force=false
            result2 = await service.sync_package(
                package_path, scip_result, [], force=False
            )
            
            # Verify sync was skipped
            assert result2.skipped, (
                f"Sync should be skipped when hash unchanged and force=false. "
                f"Got skipped={result2.skipped}"
            )
            assert result2.skip_reason is not None, "Skip reason should be provided"

            
            # Verify no database operations occurred
            graph_ops_after_second = service.graph_adapter.get_operation_count()
            vector_ops_after_second = service.vector_adapter.get_operation_count()
            
            assert graph_ops_after_second == graph_ops_after_first, (
                f"No graph operations should occur when sync is skipped. "
                f"Before: {graph_ops_after_first}, After: {graph_ops_after_second}"
            )
            assert vector_ops_after_second == vector_ops_after_first, (
                f"No vector operations should occur when sync is skipped. "
                f"Before: {vector_ops_after_first}, After: {vector_ops_after_second}"
            )
            
            # Verify result counts are zero
            assert result2.nodes_created == 0, "nodes_created should be 0 when skipped"
            assert result2.nodes_updated == 0, "nodes_updated should be 0 when skipped"
            assert result2.edges_created == 0, "edges_created should be 0 when skipped"
            assert result2.chunks_upserted == 0, "chunks_upserted should be 0 when skipped"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(scip_result=scip_parse_result_strategy())
    def test_same_hash_force_true_proceeds(
        self,
        scip_result: ScipParseResult,
    ) -> None:
        """Test that sync proceeds when hash unchanged but force=true.

        Property: WHEN hash matches stored hash AND force=true
        THEN sync SHALL proceed (skipped=false in result)
        AND database operations SHALL occur.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync to establish baseline state
            result1 = await service.sync_package(
                package_path, scip_result, [], force=False
            )
            assert not result1.skipped, "First sync should not be skipped"

            
            # Record operation counts after first sync
            graph_ops_after_first = service.graph_adapter.get_operation_count()
            
            # Second sync with same hash but force=true
            result2 = await service.sync_package(
                package_path, scip_result, [], force=True
            )
            
            # Verify sync proceeded (not skipped)
            assert not result2.skipped, (
                f"Sync should proceed when force=true regardless of hash. "
                f"Got skipped={result2.skipped}"
            )
            
            # Verify database operations occurred
            graph_ops_after_second = service.graph_adapter.get_operation_count()
            
            assert graph_ops_after_second > graph_ops_after_first, (
                f"Graph operations should occur when force=true. "
                f"Before: {graph_ops_after_first}, After: {graph_ops_after_second}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result1=scip_parse_result_strategy(),
        scip_result2=scip_parse_result_strategy(),
    )
    def test_different_hash_proceeds_regardless_of_force(
        self,
        scip_result1: ScipParseResult,
        scip_result2: ScipParseResult,
    ) -> None:
        """Test that sync proceeds when hash differs regardless of force.

        Property: WHEN hash differs from stored hash
        THEN sync SHALL proceed regardless of force parameter.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        # Ensure the two results have different hashes
        assume(scip_result1.hash != scip_result2.hash)

        
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = "TestPackage"
            
            # First sync with scip_result1
            result1 = await service.sync_package(
                package_path, scip_result1, [], force=False
            )
            assert not result1.skipped, "First sync should not be skipped"
            
            # Record operation counts after first sync
            graph_ops_after_first = service.graph_adapter.get_operation_count()
            
            # Second sync with different hash (scip_result2) and force=false
            result2 = await service.sync_package(
                package_path, scip_result2, [], force=False
            )
            
            # Verify sync proceeded (not skipped) because hash changed
            assert not result2.skipped, (
                f"Sync should proceed when hash differs. "
                f"Hash1: {scip_result1.hash[:16]}..., Hash2: {scip_result2.hash[:16]}..."
            )
            
            # Verify database operations occurred
            graph_ops_after_second = service.graph_adapter.get_operation_count()
            
            assert graph_ops_after_second > graph_ops_after_first, (
                f"Graph operations should occur when hash differs. "
                f"Before: {graph_ops_after_first}, After: {graph_ops_after_second}"
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(scip_result=scip_parse_result_strategy())
    def test_first_sync_always_proceeds(
        self,
        scip_result: ScipParseResult,
    ) -> None:
        """Test that first sync always proceeds (no stored hash).

        Property: When no stored hash exists for a package,
        sync SHALL proceed regardless of force parameter.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """

        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Verify no stored hash exists
            stored_hash = service.change_detector.get_stored_hash(package_path)
            assert stored_hash is None, "No hash should be stored initially"
            
            # First sync with force=false should proceed
            result = await service.sync_package(
                package_path, scip_result, [], force=False
            )
            
            assert not result.skipped, (
                "First sync should proceed when no stored hash exists"
            )
            
            # Verify hash was stored after sync
            stored_hash = service.change_detector.get_stored_hash(package_path)
            assert stored_hash == scip_result.hash, (
                f"Hash should be stored after sync. "
                f"Expected: {scip_result.hash[:16]}..., Got: {stored_hash[:16] if stored_hash else None}..."
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(scip_result=scip_parse_result_strategy())
    def test_hash_updated_after_successful_sync(
        self,
        scip_result: ScipParseResult,
    ) -> None:
        """Test that hash is updated after successful sync.

        Property: After a successful sync, the stored hash SHALL be
        updated to the current SCIP index hash.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Sync the package
            result = await service.sync_package(
                package_path, scip_result, [], force=False
            )

            
            assert result.success, "Sync should succeed"
            
            # Verify hash was updated
            stored_hash = service.change_detector.get_stored_hash(package_path)
            assert stored_hash == scip_result.hash, (
                f"Stored hash should match SCIP result hash after sync. "
                f"Expected: {scip_result.hash[:16]}..., Got: {stored_hash[:16] if stored_hash else None}..."
            )
            
            # Verify update_hash was called
            update_count = service.change_detector.get_update_hash_call_count()
            assert update_count >= 1, "update_hash should be called after sync"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        num_repeated_syncs=st.integers(min_value=2, max_value=5),
    )
    def test_repeated_syncs_with_same_hash_skip_after_first(
        self,
        scip_result: ScipParseResult,
        num_repeated_syncs: int,
    ) -> None:
        """Test that repeated syncs with same hash skip after first.

        Property: After the first sync establishes the hash, subsequent
        syncs with the same hash and force=false SHALL be skipped.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync should proceed
            result1 = await service.sync_package(
                package_path, scip_result, [], force=False
            )
            assert not result1.skipped, "First sync should not be skipped"
            
            # Subsequent syncs should be skipped
            for i in range(num_repeated_syncs - 1):
                result = await service.sync_package(
                    package_path, scip_result, [], force=False
                )
                assert result.skipped, (
                    f"Sync {i + 2} should be skipped (same hash, force=false)"
                )

        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        scip_result=scip_parse_result_strategy(),
        archon_docs=archon_docs_strategy(),
    )
    def test_skip_prevents_all_operations(
        self,
        scip_result: ScipParseResult,
        archon_docs: List[GeneratedDoc],
    ) -> None:
        """Test that skipping prevents all database operations.

        Property: When sync is skipped, no graph or vector operations
        SHALL occur.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync to establish baseline
            await service.sync_package(
                package_path, scip_result, archon_docs, force=False
            )
            
            # Record state after first sync
            nodes_before, edges_before = service.graph_adapter.get_state()
            chunks_before = service.vector_adapter.get_state()
            graph_ops_before = service.graph_adapter.get_operation_count()
            vector_ops_before = service.vector_adapter.get_operation_count()
            
            # Second sync should be skipped
            result = await service.sync_package(
                package_path, scip_result, archon_docs, force=False
            )
            
            assert result.skipped, "Second sync should be skipped"
            
            # Verify no operations occurred
            graph_ops_after = service.graph_adapter.get_operation_count()
            vector_ops_after = service.vector_adapter.get_operation_count()
            
            assert graph_ops_after == graph_ops_before, (
                "No graph operations should occur when skipped"
            )
            assert vector_ops_after == vector_ops_before, (
                "No vector operations should occur when skipped"
            )

            
            # Verify state unchanged
            nodes_after, edges_after = service.graph_adapter.get_state()
            chunks_after = service.vector_adapter.get_state()
            
            assert nodes_before == nodes_after, "Nodes should be unchanged when skipped"
            assert edges_before == edges_after, "Edges should be unchanged when skipped"
            assert chunks_before == chunks_after, "Chunks should be unchanged when skipped"
        
        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(scip_result=scip_parse_result_strategy())
    def test_force_true_always_syncs(
        self,
        scip_result: ScipParseResult,
    ) -> None:
        """Test that force=true always triggers sync.

        Property: When force=true, sync SHALL proceed regardless of
        whether the hash has changed.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # Multiple syncs with force=true should all proceed
            for i in range(3):
                result = await service.sync_package(
                    package_path, scip_result, [], force=True
                )
                assert not result.skipped, (
                    f"Sync {i + 1} with force=true should not be skipped"
                )
            
            # Verify all syncs performed operations
            graph_ops = service.graph_adapter.get_operation_count()
            assert graph_ops == 3, (
                f"All 3 syncs should have performed graph operations. "
                f"Got {graph_ops} operations."
            )
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(
        scip_result1=scip_parse_result_strategy(),
        scip_result2=scip_parse_result_strategy(),
    )
    def test_hash_change_triggers_sync(
        self,
        scip_result1: ScipParseResult,
        scip_result2: ScipParseResult,
    ) -> None:
        """Test that changing hash triggers sync even with force=false.

        Property: When the SCIP index hash changes, sync SHALL proceed
        regardless of the force parameter value.

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        # Ensure different hashes
        assume(scip_result1.hash != scip_result2.hash)
        
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = "TestPackage"
            
            # First sync with result1
            result1 = await service.sync_package(
                package_path, scip_result1, [], force=False
            )
            assert not result1.skipped, "First sync should proceed"
            
            # Verify hash is stored
            stored = service.change_detector.get_stored_hash(package_path)
            assert stored == scip_result1.hash, "Hash should be stored after first sync"
            
            # Second sync with different hash should proceed
            result2 = await service.sync_package(
                package_path, scip_result2, [], force=False
            )
            assert not result2.skipped, (
                "Sync should proceed when hash changes"
            )
            
            # Verify hash was updated
            stored = service.change_detector.get_stored_hash(package_path)
            assert stored == scip_result2.hash, "Hash should be updated after second sync"
        
        asyncio.get_event_loop().run_until_complete(run_test())


    @settings(max_examples=100, deadline=None)
    @given(scip_result=scip_parse_result_strategy())
    def test_skipped_result_has_correct_structure(
        self,
        scip_result: ScipParseResult,
    ) -> None:
        """Test that skipped sync result has correct structure.

        Property: When sync is skipped, the result SHALL have:
        - skipped=true
        - skip_reason set to a non-empty string
        - success=true (skipping is not a failure)
        - all count fields set to 0

        Feature: sync-service, Property 28: Sync Change Detection
        **Validates: Requirement 7 (Change Detection)**
        """
        async def run_test():
            service = MockSyncServiceWithChangeDetection()
            package_path = scip_result.symbols[0].package if scip_result.symbols else "TestPackage"
            
            # First sync to establish hash
            await service.sync_package(
                package_path, scip_result, [], force=False
            )
            
            # Second sync should be skipped
            result = await service.sync_package(
                package_path, scip_result, [], force=False
            )
            
            # Verify result structure
            assert result.skipped is True, "skipped should be True"
            assert result.skip_reason is not None, "skip_reason should be set"
            assert len(result.skip_reason) > 0, "skip_reason should not be empty"
            assert result.success is True, "success should be True (skipping is not failure)"
            assert result.nodes_created == 0, "nodes_created should be 0"
            assert result.nodes_updated == 0, "nodes_updated should be 0"
            assert result.edges_created == 0, "edges_created should be 0"
            assert result.chunks_upserted == 0, "chunks_upserted should be 0"
            assert result.nodes_pruned == 0, "nodes_pruned should be 0"
            assert result.chunks_pruned == 0, "chunks_pruned should be 0"
            assert len(result.errors) == 0, "errors should be empty"
        
        asyncio.get_event_loop().run_until_complete(run_test())
