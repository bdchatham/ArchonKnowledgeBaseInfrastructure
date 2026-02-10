"""Property-based tests for Code Graph storage.

This module contains property-based tests using Hypothesis to verify
correctness properties of the Code Graph storage layer.

Feature: code-graph-storage
Property 21: Graph Node ARN Uniqueness

**Validates: Requirements 1.2**

Source:
- src/graph/repository.py
- src/graph/models.py
- .kiro/specs/code-graph-storage/design.md
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

import pytest
from hypothesis import given, settings, strategies as st

# Add the parent directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import directly from specific modules to avoid schema.py import issues
# (schema.py has a bug with @strawberry.enum on non-Enum classes)
import importlib.util

# Load models.py directly without going through __init__.py
_models_spec = importlib.util.spec_from_file_location(
    "graph_models",
    Path(__file__).parent.parent / "src" / "graph" / "models.py"
)
_models_module = importlib.util.module_from_spec(_models_spec)
_models_spec.loader.exec_module(_models_module)

EdgeType = _models_module.EdgeType
NodeType = _models_module.NodeType
SymbolKind = _models_module.SymbolKind

# Load repository.py directly
_repo_spec = importlib.util.spec_from_file_location(
    "graph_repository",
    Path(__file__).parent.parent / "src" / "graph" / "repository.py"
)
_repo_module = importlib.util.module_from_spec(_repo_spec)
# Inject the models into the repository module's namespace
sys.modules["src.graph.models"] = _models_module
_repo_spec.loader.exec_module(_repo_module)

GraphEdge = _repo_module.GraphEdge
GraphNode = _repo_module.GraphNode
GraphRepository = _repo_module.GraphRepository
GraphRepositoryError = _repo_module.GraphRepositoryError


# Strategies for generating valid ARN components
valid_node_types = st.sampled_from(list(NodeType))
valid_symbol_kinds = st.sampled_from(list(SymbolKind))
valid_edge_types = st.sampled_from(list(EdgeType))

# Strategy for generating valid workspace names (alphanumeric with hyphens)
workspace_strategy = st.from_regex(r"[a-z][a-z0-9\-]{2,20}", fullmatch=True)

# Strategy for generating valid package names
package_strategy = st.from_regex(r"[A-Za-z][A-Za-z0-9\-]{2,30}", fullmatch=True)

# Strategy for generating valid file paths
path_strategy = st.from_regex(r"[a-z][a-z0-9_/]{1,30}\.(py|ts|go|rs)", fullmatch=True)

# Strategy for generating valid symbol names
symbol_strategy = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{1,20}", fullmatch=True)

# Strategy for generating valid names
name_strategy = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{1,30}", fullmatch=True)


def build_arn(
    node_type: NodeType,
    workspace: str,
    package: str,
    path: str,
    symbol: Optional[str] = None,
) -> str:
    """Build an ARN from components.

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    """
    base = f"arn:archon:{node_type.value}:{workspace}/{package}/{path}"
    if symbol:
        return f"{base}#{symbol}"
    return base


@st.composite
def graph_node_strategy(draw: st.DrawFn) -> GraphNode:
    """Strategy for generating random GraphNode instances."""
    node_type = draw(valid_node_types)
    workspace = draw(workspace_strategy)
    package = draw(package_strategy)
    path = draw(path_strategy)
    symbol = draw(st.one_of(st.none(), symbol_strategy))
    kind = draw(st.one_of(st.none(), valid_symbol_kinds))
    name = draw(name_strategy)

    arn = build_arn(node_type, workspace, package, path, symbol)

    return GraphNode(
        arn=arn,
        type=node_type,
        workspace=workspace,
        package=package,
        path=path,
        name=name,
        symbol=symbol,
        kind=kind,
        signature=draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
        documentation=draw(st.one_of(st.none(), st.text(min_size=1, max_size=200))),
        file_path=draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
        line_number=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=10000))),
        index_hash=draw(st.one_of(st.none(), st.text(min_size=32, max_size=64))),
    )


@st.composite
def node_set_with_potential_duplicates(
    draw: st.DrawFn,
    min_size: int = 1,
    max_size: int = 10,
) -> list[GraphNode]:
    """Strategy for generating sets of nodes with potentially duplicate ARNs.

    This strategy generates a list of nodes where some may share the same ARN
    but have different property values. This is used to test the uniqueness
    property - that duplicate ARNs result in updates rather than duplicates.
    """
    # Generate base nodes
    base_nodes = draw(st.lists(graph_node_strategy(), min_size=min_size, max_size=max_size))

    if not base_nodes:
        return base_nodes

    # Potentially add duplicates with different properties
    result = list(base_nodes)
    for node in base_nodes:
        if draw(st.booleans()):
            # Create a duplicate with the same ARN but different properties
            duplicate = GraphNode(
                arn=node.arn,  # Same ARN
                type=node.type,
                workspace=node.workspace,
                package=node.package,
                path=node.path,
                name=draw(name_strategy),  # Different name
                symbol=node.symbol,
                kind=draw(st.one_of(st.none(), valid_symbol_kinds)),  # Potentially different kind
                signature=draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
                documentation=draw(st.one_of(st.none(), st.text(min_size=1, max_size=200))),
                file_path=draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
                line_number=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=10000))),
                index_hash=draw(st.one_of(st.none(), st.text(min_size=32, max_size=64))),
            )
            result.append(duplicate)

    return result


class MockAsyncpgPool:
    """Mock asyncpg pool for testing without a real database."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow - returns a single row or None."""
        if "SELECT" in query and "FROM code_graph_nodes" in query and "WHERE arn = $1" in query:
            arn = args[0]
            return self._nodes.get(arn)
        return None

    async def fetch(self, query: str, *args) -> list[dict]:
        """Mock fetch - returns multiple rows."""
        return []

    async def fetchval(self, query: str, *args):
        """Mock fetchval - returns a single value."""
        return 1

    async def execute(self, query: str, *args) -> str:
        """Mock execute - returns command tag."""
        return "DELETE 0"

    def acquire(self):
        """Return a context manager for acquiring a connection."""
        return MockConnectionContext(self)


class MockConnectionContext:
    """Mock connection context manager."""

    def __init__(self, pool: MockAsyncpgPool):
        self._pool = pool
        self._conn = MockConnection(pool)

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnection:
    """Mock database connection."""

    def __init__(self, pool: MockAsyncpgPool):
        self._pool = pool

    def transaction(self):
        """Return a transaction context manager."""
        return MockTransactionContext()

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow for upsert operations."""
        if "INSERT INTO code_graph_nodes" in query and "ON CONFLICT" in query:
            arn = args[0]
            node_type = args[1]
            workspace = args[2]
            package = args[3]
            path = args[4]
            symbol = args[5]
            kind = args[6]
            name = args[7]
            signature = args[8]
            documentation = args[9]
            file_path = args[10]
            line_number = args[11]
            index_hash = args[12]

            was_inserted = arn not in self._pool._nodes

            self._pool._nodes[arn] = {
                "arn": arn,
                "type": node_type,
                "workspace": workspace,
                "package": package,
                "path": path,
                "symbol": symbol,
                "kind": kind,
                "name": name,
                "signature": signature,
                "documentation": documentation,
                "file_path": file_path,
                "line_number": line_number,
                "index_hash": index_hash,
            }

            return {"inserted": was_inserted}
        return None

    async def execute(self, query: str, *args) -> str:
        """Mock execute for edge operations."""
        return "INSERT 0 1"


class MockTransactionContext:
    """Mock transaction context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class TestProperty21GraphNodeARNUniqueness:
    """Property 21: Graph Node ARN Uniqueness.

    For any Code Graph, each node SHALL have a unique ARN as its primary key,
    and no two nodes SHALL share the same ARN.

    Feature: code-graph-storage, Property 21: Graph Node ARN Uniqueness
    **Validates: Requirements 1.2**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPool:
        """Create a mock asyncpg pool for testing."""
        return MockAsyncpgPool()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPool) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    @settings(max_examples=100, deadline=None)
    @given(nodes=node_set_with_potential_duplicates(min_size=1, max_size=10))
    def test_upsert_duplicate_arns_updates_existing_node(
        self,
        nodes: list[GraphNode],
    ) -> None:
        """Test that inserting duplicate ARNs updates existing nodes.

        Property: When upserting nodes with duplicate ARNs, the operation
        should either fail or update the existing node. No two nodes should
        share the same ARN after the operation.

        Feature: code-graph-storage, Property 21: Graph Node ARN Uniqueness
        **Validates: Requirements 1.2**
        """
        mock_pool = MockAsyncpgPool()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            # Upsert all nodes (including potential duplicates)
            created, updated = await repo.upsert_nodes(nodes)

            # Count unique ARNs in input
            unique_arns = set(node.arn for node in nodes)

            # Verify: total created + updated should equal number of nodes processed
            assert created + updated == len(nodes), (
                f"Expected {len(nodes)} operations, got {created} created + {updated} updated"
            )

            # Verify: number of nodes in storage equals number of unique ARNs
            assert len(mock_pool._nodes) == len(unique_arns), (
                f"Expected {len(unique_arns)} unique nodes in storage, "
                f"got {len(mock_pool._nodes)}"
            )

            # Verify: each unique ARN appears exactly once in storage
            for arn in unique_arns:
                assert arn in mock_pool._nodes, f"ARN {arn} not found in storage"

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(node=graph_node_strategy())
    def test_query_by_arn_returns_exactly_one_node(
        self,
        node: GraphNode,
    ) -> None:
        """Test that querying by ARN returns exactly one node.

        Property: For any valid ARN in the Code Graph, querying by ARN
        should return exactly one node (or None if not found).

        Feature: code-graph-storage, Property 21: Graph Node ARN Uniqueness
        **Validates: Requirements 1.2**
        """
        mock_pool = MockAsyncpgPool()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            # First, insert the node
            await repo.upsert_nodes([node])

            # Query by ARN
            result = await repo.get_node(node.arn)

            # Verify: result is not None (node was found)
            assert result is not None, f"Node with ARN {node.arn} not found after insert"

            # Verify: result ARN matches query ARN
            assert result.arn == node.arn, (
                f"Expected ARN {node.arn}, got {result.arn}"
            )

            # Verify: only one node exists with this ARN
            assert len([arn for arn in mock_pool._nodes if arn == node.arn]) == 1, (
                f"Expected exactly one node with ARN {node.arn}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        base_node=graph_node_strategy(),
        updated_name=name_strategy,
        updated_kind=st.one_of(st.none(), valid_symbol_kinds),
    )
    def test_upsert_same_arn_preserves_uniqueness(
        self,
        base_node: GraphNode,
        updated_name: str,
        updated_kind: Optional[SymbolKind],
    ) -> None:
        """Test that upserting the same ARN multiple times preserves uniqueness.

        Property: When the same ARN is upserted multiple times with different
        property values, only one node should exist with that ARN, and it
        should have the most recent property values.

        Feature: code-graph-storage, Property 21: Graph Node ARN Uniqueness
        **Validates: Requirements 1.2**
        """
        mock_pool = MockAsyncpgPool()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            # Insert original node
            created1, updated1 = await repo.upsert_nodes([base_node])
            assert created1 == 1, "First insert should create a node"
            assert updated1 == 0, "First insert should not update"

            # Create updated version with same ARN
            updated_node = GraphNode(
                arn=base_node.arn,  # Same ARN
                type=base_node.type,
                workspace=base_node.workspace,
                package=base_node.package,
                path=base_node.path,
                name=updated_name,  # Different name
                symbol=base_node.symbol,
                kind=updated_kind,  # Potentially different kind
                signature=base_node.signature,
                documentation=base_node.documentation,
                file_path=base_node.file_path,
                line_number=base_node.line_number,
                index_hash=base_node.index_hash,
            )

            # Upsert updated node
            created2, updated2 = await repo.upsert_nodes([updated_node])
            assert created2 == 0, "Second insert should not create a new node"
            assert updated2 == 1, "Second insert should update existing node"

            # Verify: still only one node with this ARN
            assert len(mock_pool._nodes) == 1, (
                f"Expected 1 node, got {len(mock_pool._nodes)}"
            )

            # Verify: the node has the updated values
            stored_node = mock_pool._nodes[base_node.arn]
            assert stored_node["name"] == updated_name, (
                f"Expected name '{updated_name}', got '{stored_node['name']}'"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(nodes=st.lists(graph_node_strategy(), min_size=2, max_size=5))
    def test_multiple_unique_arns_all_stored(
        self,
        nodes: list[GraphNode],
    ) -> None:
        """Test that multiple nodes with unique ARNs are all stored.

        Property: When upserting multiple nodes with unique ARNs, all nodes
        should be stored and queryable by their respective ARNs.

        Feature: code-graph-storage, Property 21: Graph Node ARN Uniqueness
        **Validates: Requirements 1.2**
        """
        mock_pool = MockAsyncpgPool()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            # Upsert all nodes
            created, updated = await repo.upsert_nodes(nodes)

            # Get unique ARNs
            unique_arns = set(node.arn for node in nodes)

            # Verify: number of stored nodes equals unique ARNs
            assert len(mock_pool._nodes) == len(unique_arns), (
                f"Expected {len(unique_arns)} nodes, got {len(mock_pool._nodes)}"
            )

            # Verify: each unique ARN is stored exactly once
            for arn in unique_arns:
                assert arn in mock_pool._nodes, f"ARN {arn} not found in storage"
                count = sum(1 for stored_arn in mock_pool._nodes if stored_arn == arn)
                assert count == 1, f"ARN {arn} appears {count} times, expected 1"

        asyncio.get_event_loop().run_until_complete(run_test())


@st.composite
def graph_edge_strategy(
    draw: st.DrawFn,
    from_arn: Optional[str] = None,
    to_arn: Optional[str] = None,
) -> GraphEdge:
    """Strategy for generating random GraphEdge instances.

    Args:
        from_arn: Optional fixed source ARN (generates random if None)
        to_arn: Optional fixed target ARN (generates random if None)
    """
    edge_type = draw(valid_edge_types)

    if from_arn is None:
        node_type = draw(valid_node_types)
        workspace = draw(workspace_strategy)
        package = draw(package_strategy)
        path = draw(path_strategy)
        symbol = draw(st.one_of(st.none(), symbol_strategy))
        from_arn = build_arn(node_type, workspace, package, path, symbol)

    if to_arn is None:
        node_type = draw(valid_node_types)
        workspace = draw(workspace_strategy)
        package = draw(package_strategy)
        path = draw(path_strategy)
        symbol = draw(st.one_of(st.none(), symbol_strategy))
        to_arn = build_arn(node_type, workspace, package, path, symbol)

    return GraphEdge(
        from_arn=from_arn,
        to_arn=to_arn,
        type=edge_type,
    )


@st.composite
def nodes_with_edges_strategy(
    draw: st.DrawFn,
    min_nodes: int = 2,
    max_nodes: int = 5,
    min_edges: int = 1,
    max_edges: int = 5,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Strategy for generating a set of nodes with valid edges between them.

    Generates nodes first, then creates edges that reference only existing nodes.
    This ensures all edges have valid from_arn and to_arn references.
    """
    nodes = draw(st.lists(graph_node_strategy(), min_size=min_nodes, max_size=max_nodes))

    if len(nodes) < 2:
        return (nodes, [])

    arns = [node.arn for node in nodes]
    unique_arns = list(set(arns))

    if len(unique_arns) < 2:
        return (nodes, [])

    edges: list[GraphEdge] = []
    num_edges = draw(st.integers(min_value=min_edges, max_value=min(max_edges, len(unique_arns) * 2)))

    for _ in range(num_edges):
        from_arn = draw(st.sampled_from(unique_arns))
        to_arn = draw(st.sampled_from(unique_arns))
        edge_type = draw(valid_edge_types)

        edge = GraphEdge(from_arn=from_arn, to_arn=to_arn, type=edge_type)
        edges.append(edge)

    return (nodes, edges)


class MockAsyncpgPoolWithEdges:
    """Enhanced mock asyncpg pool that tracks edges and enforces referential integrity.

    This mock simulates PostgreSQL's foreign key constraints and cascade delete
    behavior for testing Property 22: Graph Edge Referential Integrity.
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow - returns a single row or None."""
        if "SELECT" in query and "FROM code_graph_nodes" in query and "WHERE arn = $1" in query:
            arn = args[0]
            return self._nodes.get(arn)
        return None

    async def fetch(self, query: str, *args) -> list[dict]:
        """Mock fetch - returns multiple rows."""
        if "SELECT" in query and "FROM code_graph_edges" in query:
            if "WHERE from_arn = $1" in query:
                from_arn = args[0]
                if "AND type = $2" in query:
                    edge_type = args[1]
                    return [e for e in self._edges if e["from_arn"] == from_arn and e["type"] == edge_type]
                return [e for e in self._edges if e["from_arn"] == from_arn]
            elif "WHERE to_arn = $1" in query:
                to_arn = args[0]
                if "AND type = $2" in query:
                    edge_type = args[1]
                    return [e for e in self._edges if e["to_arn"] == to_arn and e["type"] == edge_type]
                return [e for e in self._edges if e["to_arn"] == to_arn]
        return []

    async def fetchval(self, query: str, *args):
        """Mock fetchval - returns a single value."""
        return 1

    async def execute(self, query: str, *args) -> str:
        """Mock execute - returns command tag."""
        if "DELETE FROM code_graph_nodes" in query:
            package = args[0]
            if "AND arn != ALL($2)" in query:
                keep_arns = args[1]
                nodes_to_delete = [
                    arn for arn, node in self._nodes.items()
                    if node["package"] == package and arn not in keep_arns
                ]
            else:
                nodes_to_delete = [
                    arn for arn, node in self._nodes.items()
                    if node["package"] == package
                ]

            for arn in nodes_to_delete:
                del self._nodes[arn]
                self._edges = [
                    e for e in self._edges
                    if e["from_arn"] != arn and e["to_arn"] != arn
                ]

            return f"DELETE {len(nodes_to_delete)}"
        return "DELETE 0"

    def acquire(self):
        """Return a context manager for acquiring a connection."""
        return MockConnectionContextWithEdges(self)


class MockConnectionContextWithEdges:
    """Mock connection context manager with edge support."""

    def __init__(self, pool: MockAsyncpgPoolWithEdges):
        self._pool = pool
        self._conn = MockConnectionWithEdges(pool)

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnectionWithEdges:
    """Mock database connection with edge support and referential integrity."""

    def __init__(self, pool: MockAsyncpgPoolWithEdges):
        self._pool = pool

    def transaction(self):
        """Return a transaction context manager."""
        return MockTransactionContext()

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow for upsert operations."""
        if "INSERT INTO code_graph_nodes" in query and "ON CONFLICT" in query:
            arn = args[0]
            node_type = args[1]
            workspace = args[2]
            package = args[3]
            path = args[4]
            symbol = args[5]
            kind = args[6]
            name = args[7]
            signature = args[8]
            documentation = args[9]
            file_path = args[10]
            line_number = args[11]
            index_hash = args[12]

            was_inserted = arn not in self._pool._nodes

            self._pool._nodes[arn] = {
                "arn": arn,
                "type": node_type,
                "workspace": workspace,
                "package": package,
                "path": path,
                "symbol": symbol,
                "kind": kind,
                "name": name,
                "signature": signature,
                "documentation": documentation,
                "file_path": file_path,
                "line_number": line_number,
                "index_hash": index_hash,
            }

            return {"inserted": was_inserted}
        return None

    async def execute(self, query: str, *args) -> str:
        """Mock execute for edge operations with referential integrity."""
        if "INSERT INTO code_graph_edges" in query:
            from_arn = args[0]
            to_arn = args[1]
            edge_type = args[2]

            if from_arn not in self._pool._nodes:
                import asyncpg
                raise asyncpg.ForeignKeyViolationError(
                    f"insert or update on table \"code_graph_edges\" violates foreign key constraint: "
                    f"Key (from_arn)=({from_arn}) is not present in table \"code_graph_nodes\"."
                )

            if to_arn not in self._pool._nodes:
                import asyncpg
                raise asyncpg.ForeignKeyViolationError(
                    f"insert or update on table \"code_graph_edges\" violates foreign key constraint: "
                    f"Key (to_arn)=({to_arn}) is not present in table \"code_graph_nodes\"."
                )

            existing_edge = next(
                (e for e in self._pool._edges
                 if e["from_arn"] == from_arn and e["to_arn"] == to_arn and e["type"] == edge_type),
                None
            )

            if existing_edge is None:
                self._pool._edges.append({
                    "from_arn": from_arn,
                    "to_arn": to_arn,
                    "type": edge_type,
                })
                return "INSERT 0 1"
            return "INSERT 0 0"

        return "INSERT 0 0"


class TestProperty22GraphEdgeReferentialIntegrity:
    """Property 22: Graph Edge Referential Integrity.

    For any edge in the Code Graph, both the `from_arn` and `to_arn` SHALL
    reference existing nodes, and deleting a node SHALL cascade delete its edges.

    Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
    **Validates: Requirements 2.3, 2.5**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolWithEdges:
        """Create a mock asyncpg pool with edge support for testing."""
        return MockAsyncpgPoolWithEdges()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolWithEdges) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    @settings(max_examples=100, deadline=None)
    @given(edge=graph_edge_strategy())
    def test_edge_with_nonexistent_from_arn_fails(
        self,
        edge: GraphEdge,
    ) -> None:
        """Test that creating an edge with non-existent from_arn fails.

        Property: Attempting to create an edge where from_arn does not
        reference an existing node should raise a referential integrity error.

        Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
        **Validates: Requirements 2.3, 2.5**
        """
        mock_pool = MockAsyncpgPoolWithEdges()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            with pytest.raises(GraphRepositoryError) as exc_info:
                await repo.upsert_edges([edge])

            assert "foreign key" in str(exc_info.value).lower() or "does not exist" in str(exc_info.value).lower(), (
                f"Expected foreign key violation error, got: {exc_info.value}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        node=graph_node_strategy(),
        edge_type=valid_edge_types,
    )
    def test_edge_with_nonexistent_to_arn_fails(
        self,
        node: GraphNode,
        edge_type: EdgeType,
    ) -> None:
        """Test that creating an edge with non-existent to_arn fails.

        Property: Attempting to create an edge where to_arn does not
        reference an existing node should raise a referential integrity error,
        even if from_arn exists.

        Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
        **Validates: Requirements 2.3, 2.5**
        """
        mock_pool = MockAsyncpgPoolWithEdges()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes([node])

            nonexistent_arn = f"{node.arn}#nonexistent_symbol_xyz"
            edge = GraphEdge(
                from_arn=node.arn,
                to_arn=nonexistent_arn,
                type=edge_type,
            )

            with pytest.raises(GraphRepositoryError) as exc_info:
                await repo.upsert_edges([edge])

            assert "foreign key" in str(exc_info.value).lower() or "does not exist" in str(exc_info.value).lower(), (
                f"Expected foreign key violation error, got: {exc_info.value}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_edges_strategy(min_nodes=2, max_nodes=5, min_edges=1, max_edges=5))
    def test_edges_with_existing_nodes_succeed(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that creating edges with existing nodes succeeds.

        Property: When both from_arn and to_arn reference existing nodes,
        edge creation should succeed.

        Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
        **Validates: Requirements 2.3, 2.5**
        """
        nodes, edges = data
        if not edges:
            return

        mock_pool = MockAsyncpgPoolWithEdges()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)

            created = await repo.upsert_edges(edges)

            unique_edges = set()
            for edge in edges:
                unique_edges.add((edge.from_arn, edge.to_arn, edge.type.value))

            assert len(mock_pool._edges) == len(unique_edges), (
                f"Expected {len(unique_edges)} unique edges in storage, "
                f"got {len(mock_pool._edges)}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_edges_strategy(min_nodes=3, max_nodes=6, min_edges=2, max_edges=8))
    def test_deleting_node_cascades_to_edges(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that deleting a node cascades to delete its edges.

        Property: When a node is deleted (via prune_package), all edges
        where the node is either from_arn or to_arn should be automatically
        deleted (cascade delete).

        Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
        **Validates: Requirements 2.3, 2.5**
        """
        nodes, edges = data
        if len(nodes) < 2 or not edges:
            return

        mock_pool = MockAsyncpgPoolWithEdges()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            initial_edge_count = len(mock_pool._edges)

            unique_arns = list(set(node.arn for node in nodes))
            if len(unique_arns) < 2:
                return

            node_to_delete_arn = unique_arns[0]
            node_to_delete = mock_pool._nodes.get(node_to_delete_arn)
            if node_to_delete is None:
                return

            package = node_to_delete["package"]
            keep_arns = [arn for arn in unique_arns if arn != node_to_delete_arn]

            await repo.prune_package(package, keep_arns)

            assert node_to_delete_arn not in mock_pool._nodes, (
                f"Node {node_to_delete_arn} should have been deleted"
            )

            for edge in mock_pool._edges:
                assert edge["from_arn"] != node_to_delete_arn, (
                    f"Edge with from_arn={node_to_delete_arn} should have been cascade deleted"
                )
                assert edge["to_arn"] != node_to_delete_arn, (
                    f"Edge with to_arn={node_to_delete_arn} should have been cascade deleted"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_edges_strategy(min_nodes=3, max_nodes=5, min_edges=3, max_edges=6))
    def test_cascade_delete_preserves_unrelated_edges(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that cascade delete only removes edges related to deleted node.

        Property: When a node is deleted, only edges where the deleted node
        is from_arn or to_arn should be removed. Edges between other nodes
        should be preserved.

        Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
        **Validates: Requirements 2.3, 2.5**
        """
        nodes, edges = data
        if len(nodes) < 3 or len(edges) < 2:
            return

        mock_pool = MockAsyncpgPoolWithEdges()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            unique_arns = list(set(node.arn for node in nodes))
            if len(unique_arns) < 3:
                return

            node_to_delete_arn = unique_arns[0]
            node_to_delete = mock_pool._nodes.get(node_to_delete_arn)
            if node_to_delete is None:
                return

            edges_to_preserve = [
                (e["from_arn"], e["to_arn"], e["type"])
                for e in mock_pool._edges
                if e["from_arn"] != node_to_delete_arn and e["to_arn"] != node_to_delete_arn
            ]

            package = node_to_delete["package"]
            keep_arns = [arn for arn in unique_arns if arn != node_to_delete_arn]

            await repo.prune_package(package, keep_arns)

            remaining_edges = [
                (e["from_arn"], e["to_arn"], e["type"])
                for e in mock_pool._edges
            ]

            for edge_tuple in edges_to_preserve:
                from_arn, to_arn, edge_type = edge_tuple
                if from_arn in mock_pool._nodes and to_arn in mock_pool._nodes:
                    assert edge_tuple in remaining_edges, (
                        f"Edge {edge_tuple} should have been preserved but was deleted"
                    )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(
        node1=graph_node_strategy(),
        node2=graph_node_strategy(),
        edge_type=valid_edge_types,
    )
    def test_edge_uniqueness_constraint(
        self,
        node1: GraphNode,
        node2: GraphNode,
        edge_type: EdgeType,
    ) -> None:
        """Test that duplicate edges are handled correctly.

        Property: The combination of (from_arn, to_arn, type) should be unique.
        Attempting to insert a duplicate edge should not create a new edge.

        Feature: code-graph-storage, Property 22: Graph Edge Referential Integrity
        **Validates: Requirements 2.3, 2.5**
        """
        mock_pool = MockAsyncpgPoolWithEdges()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes([node1, node2])

            unique_arns = set([node1.arn, node2.arn])
            if len(unique_arns) < 2:
                return

            edge = GraphEdge(
                from_arn=node1.arn,
                to_arn=node2.arn,
                type=edge_type,
            )

            created1 = await repo.upsert_edges([edge])
            assert created1 == 1, "First edge insert should create 1 edge"
            assert len(mock_pool._edges) == 1, "Should have exactly 1 edge"

            created2 = await repo.upsert_edges([edge])
            assert created2 == 0, "Duplicate edge insert should create 0 edges"
            assert len(mock_pool._edges) == 1, "Should still have exactly 1 edge"

        asyncio.get_event_loop().run_until_complete(run_test())


class MockAsyncpgPoolForGraphQL:
    """Mock asyncpg pool for testing GraphQL query completeness.

    This mock simulates the database layer for GraphQL queries, storing
    nodes and edges in memory and supporting the queries needed by the
    GraphQL resolvers.

    Feature: code-graph-storage, Property 23: GraphQL Query Completeness
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow - returns a single row or None."""
        if "SELECT" in query and "FROM code_graph_nodes" in query and "WHERE arn = $1" in query:
            arn = args[0]
            return self._nodes.get(arn)
        return None

    async def fetch(self, query: str, *args) -> list[dict]:
        """Mock fetch - returns multiple rows."""
        if "SELECT" in query and "FROM code_graph_edges" in query:
            if "WHERE from_arn = $1" in query:
                from_arn = args[0]
                if "AND type = $2" in query:
                    edge_type = args[1]
                    return [e for e in self._edges if e["from_arn"] == from_arn and e["type"] == edge_type]
                return [e for e in self._edges if e["from_arn"] == from_arn]
            elif "WHERE to_arn = $1" in query:
                to_arn = args[0]
                if "AND type = $2" in query:
                    edge_type = args[1]
                    return [e for e in self._edges if e["to_arn"] == to_arn and e["type"] == edge_type]
                return [e for e in self._edges if e["to_arn"] == to_arn]
        return []

    async def fetchval(self, query: str, *args):
        """Mock fetchval - returns a single value."""
        return 1

    async def execute(self, query: str, *args) -> str:
        """Mock execute - returns command tag."""
        return "DELETE 0"

    def acquire(self):
        """Return a context manager for acquiring a connection."""
        return MockConnectionContextForGraphQL(self)


class MockConnectionContextForGraphQL:
    """Mock connection context manager for GraphQL tests."""

    def __init__(self, pool: MockAsyncpgPoolForGraphQL):
        self._pool = pool
        self._conn = MockConnectionForGraphQL(pool)

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnectionForGraphQL:
    """Mock database connection for GraphQL tests."""

    def __init__(self, pool: MockAsyncpgPoolForGraphQL):
        self._pool = pool

    def transaction(self):
        """Return a transaction context manager."""
        return MockTransactionContext()

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow for upsert operations."""
        if "INSERT INTO code_graph_nodes" in query and "ON CONFLICT" in query:
            arn = args[0]
            node_type = args[1]
            workspace = args[2]
            package = args[3]
            path = args[4]
            symbol = args[5]
            kind = args[6]
            name = args[7]
            signature = args[8]
            documentation = args[9]
            file_path = args[10]
            line_number = args[11]
            index_hash = args[12]

            was_inserted = arn not in self._pool._nodes

            self._pool._nodes[arn] = {
                "arn": arn,
                "type": node_type,
                "workspace": workspace,
                "package": package,
                "path": path,
                "symbol": symbol,
                "kind": kind,
                "name": name,
                "signature": signature,
                "documentation": documentation,
                "file_path": file_path,
                "line_number": line_number,
                "index_hash": index_hash,
            }

            return {"inserted": was_inserted}
        return None

    async def execute(self, query: str, *args) -> str:
        """Mock execute for edge operations."""
        if "INSERT INTO code_graph_edges" in query:
            from_arn = args[0]
            to_arn = args[1]
            edge_type = args[2]

            existing_edge = next(
                (e for e in self._pool._edges
                 if e["from_arn"] == from_arn and e["to_arn"] == to_arn and e["type"] == edge_type),
                None
            )

            if existing_edge is None:
                self._pool._edges.append({
                    "from_arn": from_arn,
                    "to_arn": to_arn,
                    "type": edge_type,
                })
                return "INSERT 0 1"
            return "INSERT 0 0"

        return "INSERT 0 0"


@st.composite
def graph_node_with_all_properties_strategy(draw: st.DrawFn) -> GraphNode:
    """Strategy for generating GraphNode instances with ALL properties populated.

    This strategy ensures all optional properties are populated to test
    that GraphQL queries return complete node data.

    Feature: code-graph-storage, Property 23: GraphQL Query Completeness
    """
    node_type = draw(valid_node_types)
    workspace = draw(workspace_strategy)
    package = draw(package_strategy)
    path = draw(path_strategy)
    symbol = draw(symbol_strategy)
    kind = draw(valid_symbol_kinds)
    name = draw(name_strategy)

    arn = build_arn(node_type, workspace, package, path, symbol)

    return GraphNode(
        arn=arn,
        type=node_type,
        workspace=workspace,
        package=package,
        path=path,
        name=name,
        symbol=symbol,
        kind=kind,
        signature=draw(st.text(min_size=5, max_size=100, alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'), whitelist_characters=' (),:->[]'))),
        documentation=draw(st.text(min_size=10, max_size=200, alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'), whitelist_characters=' .,\n'))),
        file_path=draw(st.text(min_size=5, max_size=100, alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='/_.-'))),
        line_number=draw(st.integers(min_value=1, max_value=10000)),
        index_hash=draw(st.text(min_size=32, max_size=64, alphabet='0123456789abcdef')),
    )


@st.composite
def nodes_with_relationships_strategy(
    draw: st.DrawFn,
    min_nodes: int = 2,
    max_nodes: int = 4,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Strategy for generating nodes with relationships for GraphQL testing.

    Generates a set of nodes with all properties populated and creates
    edges between them to test relationship field resolution.

    Feature: code-graph-storage, Property 23: GraphQL Query Completeness
    """
    nodes = draw(st.lists(
        graph_node_with_all_properties_strategy(),
        min_size=min_nodes,
        max_size=max_nodes,
    ))

    if len(nodes) < 2:
        return (nodes, [])

    unique_arns = list(set(node.arn for node in nodes))

    if len(unique_arns) < 2:
        return (nodes, [])

    edges: list[GraphEdge] = []

    if len(unique_arns) >= 2:
        container_arn = unique_arns[0]
        contained_arn = unique_arns[1]
        edges.append(GraphEdge(
            from_arn=container_arn,
            to_arn=contained_arn,
            type=EdgeType.CONTAINS,
        ))

    if len(unique_arns) >= 3:
        referencer_arn = unique_arns[1]
        referenced_arn = unique_arns[2]
        edges.append(GraphEdge(
            from_arn=referencer_arn,
            to_arn=referenced_arn,
            type=EdgeType.REFERENCES,
        ))

    return (nodes, edges)


class TestProperty23GraphQLQueryCompleteness:
    """Property 23: GraphQL Query Completeness.

    For any valid ARN in the Code Graph, querying by ARN SHALL return
    the node with all its properties and relationships.

    Feature: code-graph-storage, Property 23: GraphQL Query Completeness
    **Validates: Requirements 4.1, 4.2, 5.1**
    """

    @settings(max_examples=100, deadline=None)
    @given(node=graph_node_with_all_properties_strategy())
    def test_graphql_query_returns_all_scalar_properties(
        self,
        node: GraphNode,
    ) -> None:
        """Test that GraphQL node query returns all scalar properties.

        Property: When querying a node by ARN via GraphQL, all scalar
        properties (arn, type, workspace, package, path, symbol, kind,
        name, signature, documentation, filePath, lineNumber) should
        be returned correctly.

        Feature: code-graph-storage, Property 23: GraphQL Query Completeness
        **Validates: Requirements 4.1, 4.2, 5.1**
        """
        mock_pool = MockAsyncpgPoolForGraphQL()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes([node])

            from src.graph.service import GraphQLService, GraphQLServiceConfig

            config = GraphQLServiceConfig(
                database_url="postgresql://test:test@localhost/test",
            )
            service = GraphQLService(config)
            service.repository = repo

            query = """
                query GetNode($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        type
                        workspace
                        package
                        path
                        symbol
                        kind
                        name
                        signature
                        documentation
                        filePath
                        lineNumber
                    }
                }
            """

            result = await service.execute(query, variables={"arn": node.arn})

            assert "errors" not in result or result["errors"] is None, (
                f"GraphQL query returned errors: {result.get('errors')}"
            )

            assert result["data"] is not None, "GraphQL query returned no data"
            assert result["data"]["node"] is not None, (
                f"Node with ARN {node.arn} not found in GraphQL response"
            )

            returned_node = result["data"]["node"]

            assert returned_node["arn"] == node.arn, (
                f"Expected ARN {node.arn}, got {returned_node['arn']}"
            )
            assert returned_node["type"] == node.type.value.upper(), (
                f"Expected type {node.type.value.upper()}, got {returned_node['type']}"
            )
            assert returned_node["workspace"] == node.workspace, (
                f"Expected workspace {node.workspace}, got {returned_node['workspace']}"
            )
            assert returned_node["package"] == node.package, (
                f"Expected package {node.package}, got {returned_node['package']}"
            )
            assert returned_node["path"] == node.path, (
                f"Expected path {node.path}, got {returned_node['path']}"
            )
            assert returned_node["name"] == node.name, (
                f"Expected name {node.name}, got {returned_node['name']}"
            )

            if node.symbol is not None:
                assert returned_node["symbol"] == node.symbol, (
                    f"Expected symbol {node.symbol}, got {returned_node['symbol']}"
                )

            if node.kind is not None:
                assert returned_node["kind"] == node.kind.value.upper(), (
                    f"Expected kind {node.kind.value.upper()}, got {returned_node['kind']}"
                )

            if node.signature is not None:
                assert returned_node["signature"] == node.signature, (
                    f"Expected signature {node.signature}, got {returned_node['signature']}"
                )

            if node.documentation is not None:
                assert returned_node["documentation"] == node.documentation, (
                    f"Expected documentation {node.documentation}, got {returned_node['documentation']}"
                )

            if node.file_path is not None:
                assert returned_node["filePath"] == node.file_path, (
                    f"Expected filePath {node.file_path}, got {returned_node['filePath']}"
                )

            if node.line_number is not None:
                assert returned_node["lineNumber"] == node.line_number, (
                    f"Expected lineNumber {node.line_number}, got {returned_node['lineNumber']}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_relationships_strategy(min_nodes=2, max_nodes=4))
    def test_graphql_query_returns_contains_relationship(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that GraphQL node query returns contains relationship.

        Property: When querying a node that has CONTAINS edges, the
        `contains` field should return the connected nodes.

        Feature: code-graph-storage, Property 23: GraphQL Query Completeness
        **Validates: Requirements 4.1, 4.2, 5.1**
        """
        nodes, edges = data
        if not edges:
            return

        contains_edges = [e for e in edges if e.type == EdgeType.CONTAINS]
        if not contains_edges:
            return

        mock_pool = MockAsyncpgPoolForGraphQL()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            from src.graph.service import GraphQLService, GraphQLServiceConfig

            config = GraphQLServiceConfig(
                database_url="postgresql://test:test@localhost/test",
            )
            service = GraphQLService(config)
            service.repository = repo

            container_edge = contains_edges[0]
            container_arn = container_edge.from_arn

            query = """
                query GetNodeWithContains($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        contains {
                            arn
                            name
                        }
                    }
                }
            """

            result = await service.execute(query, variables={"arn": container_arn})

            assert "errors" not in result or result["errors"] is None, (
                f"GraphQL query returned errors: {result.get('errors')}"
            )

            assert result["data"] is not None, "GraphQL query returned no data"
            assert result["data"]["node"] is not None, (
                f"Node with ARN {container_arn} not found in GraphQL response"
            )

            returned_node = result["data"]["node"]
            assert "contains" in returned_node, "contains field not in response"

            contained_arns = [n["arn"] for n in returned_node["contains"]]
            expected_contained_arns = [
                e.to_arn for e in contains_edges if e.from_arn == container_arn
            ]

            for expected_arn in expected_contained_arns:
                assert expected_arn in contained_arns, (
                    f"Expected contained node {expected_arn} not found in contains field"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_relationships_strategy(min_nodes=2, max_nodes=4))
    def test_graphql_query_returns_contained_by_relationship(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that GraphQL node query returns containedBy relationship.

        Property: When querying a node that is contained by another node,
        the `containedBy` field should return the containing node.

        Feature: code-graph-storage, Property 23: GraphQL Query Completeness
        **Validates: Requirements 4.1, 4.2, 5.1**
        """
        nodes, edges = data
        if not edges:
            return

        contains_edges = [e for e in edges if e.type == EdgeType.CONTAINS]
        if not contains_edges:
            return

        mock_pool = MockAsyncpgPoolForGraphQL()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            from src.graph.service import GraphQLService, GraphQLServiceConfig

            config = GraphQLServiceConfig(
                database_url="postgresql://test:test@localhost/test",
            )
            service = GraphQLService(config)
            service.repository = repo

            container_edge = contains_edges[0]
            contained_arn = container_edge.to_arn
            container_arn = container_edge.from_arn

            query = """
                query GetNodeWithContainedBy($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        containedBy {
                            arn
                            name
                        }
                    }
                }
            """

            result = await service.execute(query, variables={"arn": contained_arn})

            assert "errors" not in result or result["errors"] is None, (
                f"GraphQL query returned errors: {result.get('errors')}"
            )

            assert result["data"] is not None, "GraphQL query returned no data"
            assert result["data"]["node"] is not None, (
                f"Node with ARN {contained_arn} not found in GraphQL response"
            )

            returned_node = result["data"]["node"]
            assert "containedBy" in returned_node, "containedBy field not in response"

            if returned_node["containedBy"] is not None:
                assert returned_node["containedBy"]["arn"] == container_arn, (
                    f"Expected containedBy ARN {container_arn}, "
                    f"got {returned_node['containedBy']['arn']}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_relationships_strategy(min_nodes=3, max_nodes=4))
    def test_graphql_query_returns_references_relationship(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that GraphQL node query returns references relationship.

        Property: When querying a node that has REFERENCES edges, the
        `references` field should return the referenced nodes.

        Feature: code-graph-storage, Property 23: GraphQL Query Completeness
        **Validates: Requirements 4.1, 4.2, 5.1**
        """
        nodes, edges = data
        if not edges:
            return

        references_edges = [e for e in edges if e.type == EdgeType.REFERENCES]
        if not references_edges:
            return

        mock_pool = MockAsyncpgPoolForGraphQL()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            from src.graph.service import GraphQLService, GraphQLServiceConfig

            config = GraphQLServiceConfig(
                database_url="postgresql://test:test@localhost/test",
            )
            service = GraphQLService(config)
            service.repository = repo

            referencer_edge = references_edges[0]
            referencer_arn = referencer_edge.from_arn

            query = """
                query GetNodeWithReferences($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        references {
                            arn
                            name
                        }
                    }
                }
            """

            result = await service.execute(query, variables={"arn": referencer_arn})

            assert "errors" not in result or result["errors"] is None, (
                f"GraphQL query returned errors: {result.get('errors')}"
            )

            assert result["data"] is not None, "GraphQL query returned no data"
            assert result["data"]["node"] is not None, (
                f"Node with ARN {referencer_arn} not found in GraphQL response"
            )

            returned_node = result["data"]["node"]
            assert "references" in returned_node, "references field not in response"

            referenced_arns = [n["arn"] for n in returned_node["references"]]
            expected_referenced_arns = [
                e.to_arn for e in references_edges if e.from_arn == referencer_arn
            ]

            for expected_arn in expected_referenced_arns:
                assert expected_arn in referenced_arns, (
                    f"Expected referenced node {expected_arn} not found in references field"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=nodes_with_relationships_strategy(min_nodes=3, max_nodes=4))
    def test_graphql_query_returns_referenced_by_relationship(
        self,
        data: tuple[list[GraphNode], list[GraphEdge]],
    ) -> None:
        """Test that GraphQL node query returns referencedBy relationship.

        Property: When querying a node that is referenced by other nodes,
        the `referencedBy` field should return the referencing nodes.

        Feature: code-graph-storage, Property 23: GraphQL Query Completeness
        **Validates: Requirements 4.1, 4.2, 5.1**
        """
        nodes, edges = data
        if not edges:
            return

        references_edges = [e for e in edges if e.type == EdgeType.REFERENCES]
        if not references_edges:
            return

        mock_pool = MockAsyncpgPoolForGraphQL()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            from src.graph.service import GraphQLService, GraphQLServiceConfig

            config = GraphQLServiceConfig(
                database_url="postgresql://test:test@localhost/test",
            )
            service = GraphQLService(config)
            service.repository = repo

            referencer_edge = references_edges[0]
            referenced_arn = referencer_edge.to_arn
            referencer_arn = referencer_edge.from_arn

            query = """
                query GetNodeWithReferencedBy($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        referencedBy {
                            arn
                            name
                        }
                    }
                }
            """

            result = await service.execute(query, variables={"arn": referenced_arn})

            assert "errors" not in result or result["errors"] is None, (
                f"GraphQL query returned errors: {result.get('errors')}"
            )

            assert result["data"] is not None, "GraphQL query returned no data"
            assert result["data"]["node"] is not None, (
                f"Node with ARN {referenced_arn} not found in GraphQL response"
            )

            returned_node = result["data"]["node"]
            assert "referencedBy" in returned_node, "referencedBy field not in response"

            referencing_arns = [n["arn"] for n in returned_node["referencedBy"]]

            assert referencer_arn in referencing_arns, (
                f"Expected referencing node {referencer_arn} not found in referencedBy field"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(arn=st.text(min_size=10, max_size=50, alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters=':/-#_')))
    def test_graphql_query_nonexistent_arn_returns_null(
        self,
        arn: str,
    ) -> None:
        """Test that GraphQL node query returns null for non-existent ARN.

        Property: When querying a node by an ARN that doesn't exist in
        the graph, the query should return null without errors.

        Feature: code-graph-storage, Property 23: GraphQL Query Completeness
        **Validates: Requirements 4.1, 4.2, 5.1**
        """
        mock_pool = MockAsyncpgPoolForGraphQL()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig

            config = GraphQLServiceConfig(
                database_url="postgresql://test:test@localhost/test",
            )
            service = GraphQLService(config)
            service.repository = repo

            query = """
                query GetNode($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                    }
                }
            """

            result = await service.execute(query, variables={"arn": arn})

            assert "errors" not in result or result["errors"] is None, (
                f"GraphQL query returned errors for non-existent ARN: {result.get('errors')}"
            )

            assert result["data"] is not None, "GraphQL query returned no data"
            assert result["data"]["node"] is None, (
                f"Expected null for non-existent ARN {arn}, got {result['data']['node']}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())


# ============================================================================
# Property 24: Graph Traversal Correctness
# ============================================================================


class MockAsyncpgPoolForTraversal:
    """Mock asyncpg pool for testing graph traversal correctness.

    This mock simulates the database layer for traversal queries, storing
    nodes and edges in memory and supporting BFS traversal with cycle detection.

    Feature: code-graph-storage, Property 24: Graph Traversal Correctness
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow - returns a single row or None."""
        if "SELECT" in query and "FROM code_graph_nodes" in query and "WHERE arn = $1" in query:
            arn = args[0]
            return self._nodes.get(arn)
        return None

    async def fetch(self, query: str, *args) -> list[dict]:
        """Mock fetch - returns multiple rows."""
        if "SELECT DISTINCT e.to_arn" in query and "FROM code_graph_edges" in query:
            frontier_arns = args[0]
            edge_types = args[1]
            visited_arns = args[2]

            results = []
            for edge in self._edges:
                if (edge["from_arn"] in frontier_arns and
                    edge["type"] in edge_types and
                    edge["to_arn"] not in visited_arns):
                    results.append({"to_arn": edge["to_arn"]})

            seen = set()
            unique_results = []
            for r in results:
                if r["to_arn"] not in seen:
                    seen.add(r["to_arn"])
                    unique_results.append(r)
            return unique_results

        if "SELECT" in query and "FROM code_graph_nodes" in query and "WHERE arn = ANY($1)" in query:
            arns = args[0]
            return [self._nodes[arn] for arn in arns if arn in self._nodes]

        if "SELECT" in query and "FROM code_graph_edges" in query:
            if "WHERE from_arn = $1" in query:
                from_arn = args[0]
                if "AND type = $2" in query:
                    edge_type = args[1]
                    return [e for e in self._edges if e["from_arn"] == from_arn and e["type"] == edge_type]
                return [e for e in self._edges if e["from_arn"] == from_arn]
            elif "WHERE to_arn = $1" in query:
                to_arn = args[0]
                if "AND type = $2" in query:
                    edge_type = args[1]
                    return [e for e in self._edges if e["to_arn"] == to_arn and e["type"] == edge_type]
                return [e for e in self._edges if e["to_arn"] == to_arn]
        return []

    async def fetchval(self, query: str, *args):
        """Mock fetchval - returns a single value."""
        return 1

    async def execute(self, query: str, *args) -> str:
        """Mock execute - returns command tag."""
        return "DELETE 0"

    def acquire(self):
        """Return a context manager for acquiring a connection."""
        return MockConnectionContextForTraversal(self)


class MockConnectionContextForTraversal:
    """Mock connection context manager for traversal tests."""

    def __init__(self, pool: MockAsyncpgPoolForTraversal):
        self._pool = pool
        self._conn = MockConnectionForTraversal(pool)

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnectionForTraversal:
    """Mock database connection for traversal tests."""

    def __init__(self, pool: MockAsyncpgPoolForTraversal):
        self._pool = pool

    def transaction(self):
        """Return a transaction context manager."""
        return MockTransactionContext()

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow for upsert operations."""
        if "INSERT INTO code_graph_nodes" in query and "ON CONFLICT" in query:
            arn = args[0]
            node_type = args[1]
            workspace = args[2]
            package = args[3]
            path = args[4]
            symbol = args[5]
            kind = args[6]
            name = args[7]
            signature = args[8]
            documentation = args[9]
            file_path = args[10]
            line_number = args[11]
            index_hash = args[12]

            was_inserted = arn not in self._pool._nodes

            self._pool._nodes[arn] = {
                "arn": arn,
                "type": node_type,
                "workspace": workspace,
                "package": package,
                "path": path,
                "symbol": symbol,
                "kind": kind,
                "name": name,
                "signature": signature,
                "documentation": documentation,
                "file_path": file_path,
                "line_number": line_number,
                "index_hash": index_hash,
            }

            return {"inserted": was_inserted}
        return None

    async def execute(self, query: str, *args) -> str:
        """Mock execute for edge operations."""
        if "INSERT INTO code_graph_edges" in query:
            from_arn = args[0]
            to_arn = args[1]
            edge_type = args[2]

            existing_edge = next(
                (e for e in self._pool._edges
                 if e["from_arn"] == from_arn and e["to_arn"] == to_arn and e["type"] == edge_type),
                None
            )

            if existing_edge is None:
                self._pool._edges.append({
                    "from_arn": from_arn,
                    "to_arn": to_arn,
                    "type": edge_type,
                })
                return "INSERT 0 1"
            return "INSERT 0 0"

        return "INSERT 0 0"


def compute_expected_reachable_nodes(
    start_arn: str,
    edges: list[dict],
    edge_types: list[str],
    depth: int,
) -> set[str]:
    """Compute expected reachable nodes using BFS.

    This is a reference implementation to verify the repository's traverse method.
    It performs BFS from the start node, following edges of specified types
    up to the given depth.

    Args:
        start_arn: Starting node ARN
        edges: List of edge dictionaries with from_arn, to_arn, type
        edge_types: List of edge type values to follow
        depth: Maximum traversal depth

    Returns:
        Set of ARNs reachable from start_arn (excluding start_arn itself)
    """
    if depth <= 0:
        return set()

    if not edge_types:
        return set()

    visited: set[str] = {start_arn}
    result: set[str] = set()
    current_frontier: set[str] = {start_arn}

    for _ in range(depth):
        if not current_frontier:
            break

        next_frontier: set[str] = set()

        for edge in edges:
            if (edge["from_arn"] in current_frontier and
                edge["type"] in edge_types and
                edge["to_arn"] not in visited):
                visited.add(edge["to_arn"])
                next_frontier.add(edge["to_arn"])
                result.add(edge["to_arn"])

        current_frontier = next_frontier

    return result


@st.composite
def chain_graph_strategy(
    draw: st.DrawFn,
    min_length: int = 2,
    max_length: int = 6,
) -> tuple[list[GraphNode], list[GraphEdge], str]:
    """Strategy for generating chain graphs (A -> B -> C -> D).

    Creates a linear chain of nodes connected by edges of a single type.
    Returns the nodes, edges, and the ARN of the first node in the chain.

    Feature: code-graph-storage, Property 24: Graph Traversal Correctness
    """
    length = draw(st.integers(min_value=min_length, max_value=max_length))
    edge_type = draw(valid_edge_types)

    workspace = draw(workspace_strategy)
    package = draw(package_strategy)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for i in range(length):
        path = f"chain_{i}.py"
        symbol = f"node_{i}"
        arn = build_arn(NodeType.CODE, workspace, package, path, symbol)

        node = GraphNode(
            arn=arn,
            type=NodeType.CODE,
            workspace=workspace,
            package=package,
            path=path,
            name=f"Node{i}",
            symbol=symbol,
            kind=SymbolKind.FUNCTION,
        )
        nodes.append(node)

        if i > 0:
            edge = GraphEdge(
                from_arn=nodes[i - 1].arn,
                to_arn=nodes[i].arn,
                type=edge_type,
            )
            edges.append(edge)

    return (nodes, edges, nodes[0].arn)


@st.composite
def tree_graph_strategy(
    draw: st.DrawFn,
    min_children: int = 2,
    max_children: int = 4,
    max_depth: int = 2,
) -> tuple[list[GraphNode], list[GraphEdge], str]:
    """Strategy for generating tree graphs (A -> B, A -> C, B -> D, B -> E).

    Creates a tree structure with a root node and children at each level.
    Returns the nodes, edges, and the ARN of the root node.

    Feature: code-graph-storage, Property 24: Graph Traversal Correctness
    """
    edge_type = draw(valid_edge_types)

    workspace = draw(workspace_strategy)
    package = draw(package_strategy)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    node_counter = 0

    def create_node(level: int, index: int) -> GraphNode:
        nonlocal node_counter
        path = f"tree_l{level}_i{index}.py"
        symbol = f"node_{node_counter}"
        arn = build_arn(NodeType.CODE, workspace, package, path, symbol)
        node_counter += 1

        return GraphNode(
            arn=arn,
            type=NodeType.CODE,
            workspace=workspace,
            package=package,
            path=path,
            name=f"TreeNode_L{level}_I{index}",
            symbol=symbol,
            kind=SymbolKind.CLASS,
        )

    root = create_node(0, 0)
    nodes.append(root)

    current_level_nodes = [root]

    for level in range(1, max_depth + 1):
        next_level_nodes: list[GraphNode] = []

        for parent_idx, parent in enumerate(current_level_nodes):
            num_children = draw(st.integers(min_value=min_children, max_value=max_children))

            for child_idx in range(num_children):
                child = create_node(level, len(next_level_nodes))
                nodes.append(child)
                next_level_nodes.append(child)

                edge = GraphEdge(
                    from_arn=parent.arn,
                    to_arn=child.arn,
                    type=edge_type,
                )
                edges.append(edge)

        current_level_nodes = next_level_nodes

    return (nodes, edges, root.arn)


@st.composite
def cycle_graph_strategy(
    draw: st.DrawFn,
    min_length: int = 3,
    max_length: int = 6,
) -> tuple[list[GraphNode], list[GraphEdge], str]:
    """Strategy for generating cycle graphs (A -> B -> C -> A).

    Creates a cycle of nodes where the last node connects back to the first.
    Returns the nodes, edges, and the ARN of the first node in the cycle.

    Feature: code-graph-storage, Property 24: Graph Traversal Correctness
    """
    length = draw(st.integers(min_value=min_length, max_value=max_length))
    edge_type = draw(valid_edge_types)

    workspace = draw(workspace_strategy)
    package = draw(package_strategy)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for i in range(length):
        path = f"cycle_{i}.py"
        symbol = f"cycle_node_{i}"
        arn = build_arn(NodeType.CODE, workspace, package, path, symbol)

        node = GraphNode(
            arn=arn,
            type=NodeType.CODE,
            workspace=workspace,
            package=package,
            path=path,
            name=f"CycleNode{i}",
            symbol=symbol,
            kind=SymbolKind.METHOD,
        )
        nodes.append(node)

    for i in range(length):
        next_idx = (i + 1) % length
        edge = GraphEdge(
            from_arn=nodes[i].arn,
            to_arn=nodes[next_idx].arn,
            type=edge_type,
        )
        edges.append(edge)

    return (nodes, edges, nodes[0].arn)


@st.composite
def mixed_edge_type_graph_strategy(
    draw: st.DrawFn,
    min_nodes: int = 4,
    max_nodes: int = 8,
) -> tuple[list[GraphNode], list[GraphEdge], str, list[EdgeType]]:
    """Strategy for generating graphs with multiple edge types.

    Creates a graph with nodes connected by edges of different types.
    Returns the nodes, edges, start ARN, and a subset of edge types to traverse.

    Feature: code-graph-storage, Property 24: Graph Traversal Correctness
    """
    num_nodes = draw(st.integers(min_value=min_nodes, max_value=max_nodes))

    workspace = draw(workspace_strategy)
    package = draw(package_strategy)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for i in range(num_nodes):
        path = f"mixed_{i}.py"
        symbol = f"mixed_node_{i}"
        arn = build_arn(NodeType.CODE, workspace, package, path, symbol)

        node = GraphNode(
            arn=arn,
            type=NodeType.CODE,
            workspace=workspace,
            package=package,
            path=path,
            name=f"MixedNode{i}",
            symbol=symbol,
            kind=draw(valid_symbol_kinds),
        )
        nodes.append(node)

    num_edges = draw(st.integers(min_value=num_nodes - 1, max_value=num_nodes * 2))
    for _ in range(num_edges):
        from_idx = draw(st.integers(min_value=0, max_value=num_nodes - 1))
        to_idx = draw(st.integers(min_value=0, max_value=num_nodes - 1))
        if from_idx != to_idx:
            edge_type = draw(valid_edge_types)
            edge = GraphEdge(
                from_arn=nodes[from_idx].arn,
                to_arn=nodes[to_idx].arn,
                type=edge_type,
            )
            edges.append(edge)

    all_edge_types = list(EdgeType)
    num_types_to_use = draw(st.integers(min_value=1, max_value=len(all_edge_types)))
    edge_types_to_traverse = draw(st.lists(
        st.sampled_from(all_edge_types),
        min_size=num_types_to_use,
        max_size=num_types_to_use,
        unique=True,
    ))

    return (nodes, edges, nodes[0].arn, edge_types_to_traverse)


class TestProperty24GraphTraversalCorrectness:
    """Property 24: Graph Traversal Correctness.

    For any traversal query starting from a valid ARN, the result SHALL
    include all nodes reachable via the specified edge types up to the
    specified depth.

    Feature: code-graph-storage, Property 24: Graph Traversal Correctness
    **Validates: Requirement 5.6**
    """

    @settings(max_examples=100, deadline=None)
    @given(data=chain_graph_strategy(min_length=3, max_length=6))
    def test_chain_graph_traversal_depth_limiting(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str],
    ) -> None:
        """Test traversal on chain graphs respects depth limiting.

        Property: For a chain graph A -> B -> C -> D, traversing from A
        with depth=1 should return only B, depth=2 should return B and C, etc.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, start_arn = data
        if len(nodes) < 2 or not edges:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            edge_type = edges[0].type
            edge_types = [edge_type]
            edge_type_values = [et.value for et in edge_types]

            for depth in range(0, len(nodes) + 1):
                result = await repo.traverse(start_arn, edge_types, depth)

                expected = compute_expected_reachable_nodes(
                    start_arn,
                    mock_pool._edges,
                    edge_type_values,
                    depth,
                )

                result_arns = set(node.arn for node in result)

                assert result_arns == expected, (
                    f"Depth {depth}: Expected {expected}, got {result_arns}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=tree_graph_strategy(min_children=2, max_children=3, max_depth=2))
    def test_tree_graph_traversal_breadth(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str],
    ) -> None:
        """Test traversal on tree graphs finds all children at each level.

        Property: For a tree graph, traversing from root with depth=1 should
        return all direct children, depth=2 should return children and grandchildren.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, root_arn = data
        if len(nodes) < 2 or not edges:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            edge_type = edges[0].type
            edge_types = [edge_type]
            edge_type_values = [et.value for et in edge_types]

            for depth in [1, 2, 3]:
                result = await repo.traverse(root_arn, edge_types, depth)

                expected = compute_expected_reachable_nodes(
                    root_arn,
                    mock_pool._edges,
                    edge_type_values,
                    depth,
                )

                result_arns = set(node.arn for node in result)

                assert result_arns == expected, (
                    f"Depth {depth}: Expected {len(expected)} nodes, got {len(result_arns)}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=cycle_graph_strategy(min_length=3, max_length=5))
    def test_cycle_graph_traversal_handles_cycles(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str],
    ) -> None:
        """Test traversal on cycle graphs handles cycles correctly.

        Property: For a cycle graph A -> B -> C -> A, traversal should not
        loop infinitely and should return each node at most once.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, start_arn = data
        if len(nodes) < 3 or not edges:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            edge_type = edges[0].type
            edge_types = [edge_type]
            edge_type_values = [et.value for et in edge_types]

            large_depth = len(nodes) * 2
            result = await repo.traverse(start_arn, edge_types, large_depth)

            expected = compute_expected_reachable_nodes(
                start_arn,
                mock_pool._edges,
                edge_type_values,
                large_depth,
            )

            result_arns = set(node.arn for node in result)

            assert result_arns == expected, (
                f"Expected {expected}, got {result_arns}"
            )

            assert start_arn not in result_arns, (
                "Start node should not be in traversal results"
            )

            assert len(result_arns) == len(result), (
                "Traversal should not return duplicate nodes"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=chain_graph_strategy(min_length=2, max_length=5))
    def test_depth_zero_returns_empty_list(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str],
    ) -> None:
        """Test that depth=0 returns an empty list.

        Property: Traversing with depth=0 should return no nodes,
        as no traversal steps are taken.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, start_arn = data
        if not nodes:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            if edges:
                await repo.upsert_edges(edges)

            edge_type = edges[0].type if edges else EdgeType.CONTAINS
            edge_types = [edge_type]

            result = await repo.traverse(start_arn, edge_types, depth=0)

            assert result == [], (
                f"Expected empty list for depth=0, got {len(result)} nodes"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=mixed_edge_type_graph_strategy(min_nodes=4, max_nodes=6))
    def test_unreachable_nodes_not_included(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str, list[EdgeType]],
    ) -> None:
        """Test that unreachable nodes are not included in traversal results.

        Property: Nodes that are not connected to the start node via the
        specified edge types should not appear in traversal results.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, start_arn, edge_types_to_traverse = data
        if len(nodes) < 2:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            if edges:
                await repo.upsert_edges(edges)

            edge_type_values = [et.value for et in edge_types_to_traverse]

            result = await repo.traverse(start_arn, edge_types_to_traverse, depth=10)

            expected = compute_expected_reachable_nodes(
                start_arn,
                mock_pool._edges,
                edge_type_values,
                10,
            )

            result_arns = set(node.arn for node in result)

            assert result_arns == expected, (
                f"Expected {expected}, got {result_arns}"
            )

            for node in result:
                assert node.arn in expected, (
                    f"Node {node.arn} should not be reachable"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=mixed_edge_type_graph_strategy(min_nodes=5, max_nodes=8))
    def test_mixed_edge_types_traversal(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str, list[EdgeType]],
    ) -> None:
        """Test traversal with multiple edge type filters.

        Property: When traversing with multiple edge types, only edges
        matching those types should be followed.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, start_arn, edge_types_to_traverse = data
        if len(nodes) < 2 or not edges:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            edge_type_values = [et.value for et in edge_types_to_traverse]

            for depth in [1, 2, 3]:
                result = await repo.traverse(start_arn, edge_types_to_traverse, depth)

                expected = compute_expected_reachable_nodes(
                    start_arn,
                    mock_pool._edges,
                    edge_type_values,
                    depth,
                )

                result_arns = set(node.arn for node in result)

                assert result_arns == expected, (
                    f"Depth {depth}: Expected {expected}, got {result_arns}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(node=graph_node_strategy())
    def test_empty_edge_types_returns_empty_list(
        self,
        node: GraphNode,
    ) -> None:
        """Test that empty edge types list returns empty result.

        Property: Traversing with an empty edge types list should return
        no nodes, as there are no edge types to follow.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes([node])

            result = await repo.traverse(node.arn, [], depth=5)

            assert result == [], (
                f"Expected empty list for empty edge types, got {len(result)} nodes"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    @settings(max_examples=100, deadline=None)
    @given(data=chain_graph_strategy(min_length=4, max_length=6))
    def test_start_node_not_in_results(
        self,
        data: tuple[list[GraphNode], list[GraphEdge], str],
    ) -> None:
        """Test that the start node is never included in traversal results.

        Property: The traversal should return nodes reachable FROM the start
        node, but not the start node itself.

        Feature: code-graph-storage, Property 24: Graph Traversal Correctness
        **Validates: Requirement 5.6**
        """
        nodes, edges, start_arn = data
        if len(nodes) < 2 or not edges:
            return

        mock_pool = MockAsyncpgPoolForTraversal()
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool

        async def run_test():
            await repo.upsert_nodes(nodes)
            await repo.upsert_edges(edges)

            edge_type = edges[0].type
            edge_types = [edge_type]

            for depth in range(1, len(nodes) + 2):
                result = await repo.traverse(start_arn, edge_types, depth)

                result_arns = [node.arn for node in result]

                assert start_arn not in result_arns, (
                    f"Start node {start_arn} should not be in traversal results at depth {depth}"
                )

        asyncio.get_event_loop().run_until_complete(run_test())


# Run tests with pytest
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
