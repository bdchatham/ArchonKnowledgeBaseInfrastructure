"""Integration tests for Code Graph sync flow.

This module contains end-to-end integration tests for the Code Graph
sync operations, testing the GraphQL mutations through the GraphQLService.

Feature: code-graph-storage
Task: 6.1 End-to-end sync flow tests

Test Scenarios:
1. syncFromScip creates nodes and edges
2. syncFromScip updates existing nodes
3. prunePackage removes stale nodes
4. Cascade delete removes edges when nodes are deleted

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6**

Source:
- src/graph/service.py
- src/graph/resolvers.py
- src/graph/repository.py
- .kiro/specs/code-graph-storage/design.md
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

import pytest

# Add the parent directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import directly from specific modules to avoid schema.py import issues
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


class MockAsyncpgPoolForIntegration:
    """Mock asyncpg pool for integration testing.

    This mock simulates PostgreSQL behavior including:
    - Node storage with upsert semantics
    - Edge storage with foreign key constraints
    - Cascade delete behavior when nodes are removed
    - Package-scoped pruning operations
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
        """Mock execute - handles DELETE operations with cascade."""
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

            # Cascade delete: remove edges referencing deleted nodes
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
        return MockConnectionContextForIntegration(self)


class MockConnectionContextForIntegration:
    """Mock connection context manager for integration tests."""

    def __init__(self, pool: MockAsyncpgPoolForIntegration):
        self._pool = pool
        self._conn = MockConnectionForIntegration(pool)

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockTransactionContextForIntegration:
    """Mock transaction context manager for integration tests."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnectionForIntegration:
    """Mock database connection for integration tests with full functionality."""

    def __init__(self, pool: MockAsyncpgPoolForIntegration):
        self._pool = pool

    def transaction(self):
        """Return a transaction context manager."""
        return MockTransactionContextForIntegration()

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

            # Enforce foreign key constraints
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

            # Check for duplicate edge
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


class TestSyncFromScipCreatesNodesAndEdges:
    """Test that syncFromScip mutation creates nodes and edges correctly.

    Feature: code-graph-storage, Task 6.1: End-to-end sync flow tests
    **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForIntegration:
        """Create a mock asyncpg pool for testing."""
        return MockAsyncpgPoolForIntegration()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForIntegration) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_sync_creates_nodes_from_symbols(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that syncFromScip creates nodes from symbol inputs.

        Verifies that calling the sync operation with symbol data
        results in nodes being created in the database.

        **Validates: Requirements 6.1, 6.2**
        """
        async def run_test():
            # Create sample nodes
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/main.py#main",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/main.py",
                    name="main",
                    symbol="main",
                    kind=SymbolKind.FUNCTION,
                    signature="def main() -> None",
                    documentation="Main entry point",
                    file_path="/home/user/test-pkg/src/main.py",
                    line_number=10,
                    index_hash="abc123",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/main.py#helper",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/main.py",
                    name="helper",
                    symbol="helper",
                    kind=SymbolKind.FUNCTION,
                    signature="def helper(x: int) -> int",
                    documentation="Helper function",
                    file_path="/home/user/test-pkg/src/main.py",
                    line_number=20,
                    index_hash="abc123",
                ),
            ]

            # Upsert nodes
            created, updated = await repository.upsert_nodes(nodes)

            # Verify counts
            assert created == 2, f"Expected 2 nodes created, got {created}"
            assert updated == 0, f"Expected 0 nodes updated, got {updated}"

            # Verify nodes exist in mock database
            assert len(mock_pool._nodes) == 2
            assert "arn:archon:code:test-ws/test-pkg/src/main.py#main" in mock_pool._nodes
            assert "arn:archon:code:test-ws/test-pkg/src/main.py#helper" in mock_pool._nodes

            # Verify node properties
            main_node = mock_pool._nodes["arn:archon:code:test-ws/test-pkg/src/main.py#main"]
            assert main_node["name"] == "main"
            assert main_node["kind"] == "function"
            assert main_node["package"] == "test-pkg"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_creates_edges_from_relationships(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that syncFromScip creates edges from relationship inputs.

        Verifies that calling the sync operation with relationship data
        results in edges being created in the database.

        **Validates: Requirements 6.1, 6.3**
        """
        async def run_test():
            # First create nodes (edges require existing nodes)
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/main.py#MyClass",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/main.py",
                    name="MyClass",
                    symbol="MyClass",
                    kind=SymbolKind.CLASS,
                    index_hash="abc123",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/main.py#MyClass.method",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/main.py",
                    name="method",
                    symbol="MyClass.method",
                    kind=SymbolKind.METHOD,
                    index_hash="abc123",
                ),
            ]
            await repository.upsert_nodes(nodes)

            # Create edges
            edges = [
                GraphEdge(
                    from_arn="arn:archon:code:test-ws/test-pkg/src/main.py#MyClass",
                    to_arn="arn:archon:code:test-ws/test-pkg/src/main.py#MyClass.method",
                    type=EdgeType.CONTAINS,
                ),
            ]
            edges_created = await repository.upsert_edges(edges)

            # Verify edge count
            assert edges_created == 1, f"Expected 1 edge created, got {edges_created}"

            # Verify edge exists in mock database
            assert len(mock_pool._edges) == 1
            edge = mock_pool._edges[0]
            assert edge["from_arn"] == "arn:archon:code:test-ws/test-pkg/src/main.py#MyClass"
            assert edge["to_arn"] == "arn:archon:code:test-ws/test-pkg/src/main.py#MyClass.method"
            assert edge["type"] == "contains"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_returns_correct_counts(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that syncFromScip returns correct SyncResult counts.

        Verifies that the sync operation returns accurate counts for
        nodes created, nodes updated, and edges created.

        **Validates: Requirements 6.4**
        """
        async def run_test():
            # Create initial nodes
            initial_nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/file.py#func1",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/file.py",
                    name="func1",
                    symbol="func1",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
            ]
            created1, updated1 = await repository.upsert_nodes(initial_nodes)
            assert created1 == 1
            assert updated1 == 0

            # Sync with mix of new and existing nodes
            sync_nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/file.py#func1",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/file.py",
                    name="func1_updated",  # Updated name
                    symbol="func1",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash2",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/test-pkg/src/file.py#func2",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="test-pkg",
                    path="src/file.py",
                    name="func2",
                    symbol="func2",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash2",
                ),
            ]
            created2, updated2 = await repository.upsert_nodes(sync_nodes)

            # Verify counts: 1 new node, 1 updated node
            assert created2 == 1, f"Expected 1 node created, got {created2}"
            assert updated2 == 1, f"Expected 1 node updated, got {updated2}"

            # Verify total nodes in database
            assert len(mock_pool._nodes) == 2

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSyncFromScipUpdatesExistingNodes:
    """Test that syncFromScip mutation updates existing nodes correctly.

    Feature: code-graph-storage, Task 6.1: End-to-end sync flow tests
    **Validates: Requirements 6.2, 6.4**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForIntegration:
        """Create a mock asyncpg pool for testing."""
        return MockAsyncpgPoolForIntegration()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForIntegration) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_sync_updates_node_properties(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that syncing with same ARN updates node properties.

        Verifies that when a node with an existing ARN is synced,
        its properties are updated rather than creating a duplicate.

        **Validates: Requirements 6.2**
        """
        async def run_test():
            # Create initial node
            initial_node = GraphNode(
                arn="arn:archon:code:test-ws/test-pkg/src/module.py#process",
                type=NodeType.CODE,
                workspace="test-ws",
                package="test-pkg",
                path="src/module.py",
                name="process",
                symbol="process",
                kind=SymbolKind.FUNCTION,
                signature="def process(data: dict) -> None",
                documentation="Original documentation",
                line_number=15,
                index_hash="original_hash",
            )
            await repository.upsert_nodes([initial_node])

            # Verify initial state
            stored = mock_pool._nodes["arn:archon:code:test-ws/test-pkg/src/module.py#process"]
            assert stored["documentation"] == "Original documentation"
            assert stored["line_number"] == 15

            # Update with new properties
            updated_node = GraphNode(
                arn="arn:archon:code:test-ws/test-pkg/src/module.py#process",
                type=NodeType.CODE,
                workspace="test-ws",
                package="test-pkg",
                path="src/module.py",
                name="process",
                symbol="process",
                kind=SymbolKind.FUNCTION,
                signature="def process(data: dict, options: dict) -> bool",
                documentation="Updated documentation with more details",
                line_number=20,
                index_hash="updated_hash",
            )
            created, updated = await repository.upsert_nodes([updated_node])

            # Verify update counts
            assert created == 0, f"Expected 0 nodes created, got {created}"
            assert updated == 1, f"Expected 1 node updated, got {updated}"

            # Verify only one node exists
            assert len(mock_pool._nodes) == 1

            # Verify properties were updated
            stored = mock_pool._nodes["arn:archon:code:test-ws/test-pkg/src/module.py#process"]
            assert stored["documentation"] == "Updated documentation with more details"
            assert stored["line_number"] == 20
            assert stored["signature"] == "def process(data: dict, options: dict) -> bool"
            assert stored["index_hash"] == "updated_hash"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_twice_with_same_data_reports_updates(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that syncing the same data twice reports updates correctly.

        Verifies that re-syncing identical data is treated as an update
        operation, not a create operation.

        **Validates: Requirements 6.2, 6.4**
        """
        async def run_test():
            node = GraphNode(
                arn="arn:archon:code:test-ws/test-pkg/src/utils.py#format_string",
                type=NodeType.CODE,
                workspace="test-ws",
                package="test-pkg",
                path="src/utils.py",
                name="format_string",
                symbol="format_string",
                kind=SymbolKind.FUNCTION,
                index_hash="hash123",
            )

            # First sync - should create
            created1, updated1 = await repository.upsert_nodes([node])
            assert created1 == 1
            assert updated1 == 0

            # Second sync with same data - should update
            created2, updated2 = await repository.upsert_nodes([node])
            assert created2 == 0, f"Expected 0 nodes created on re-sync, got {created2}"
            assert updated2 == 1, f"Expected 1 node updated on re-sync, got {updated2}"

            # Still only one node
            assert len(mock_pool._nodes) == 1

        asyncio.get_event_loop().run_until_complete(run_test())


class TestPrunePackageRemovesStaleNodes:
    """Test that prunePackage mutation removes stale nodes correctly.

    Feature: code-graph-storage, Task 6.1: End-to-end sync flow tests
    **Validates: Requirements 6.5, 6.6**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForIntegration:
        """Create a mock asyncpg pool for testing."""
        return MockAsyncpgPoolForIntegration()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForIntegration) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_prune_removes_nodes_not_in_keep_list(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that prunePackage removes nodes not in keepArns list.

        Verifies that nodes belonging to the package but not in the
        keepArns list are removed from the database.

        **Validates: Requirements 6.5, 6.6**
        """
        async def run_test():
            # Create multiple nodes in the same package
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/prune-pkg/src/keep1.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="prune-pkg",
                    path="src/keep1.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/prune-pkg/src/keep2.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="prune-pkg",
                    path="src/keep2.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/prune-pkg/src/remove.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="prune-pkg",
                    path="src/remove.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)
            assert len(mock_pool._nodes) == 3

            # Prune, keeping only first two nodes
            keep_arns = [
                "arn:archon:code:test-ws/prune-pkg/src/keep1.py#func",
                "arn:archon:code:test-ws/prune-pkg/src/keep2.py#func",
            ]
            removed_count = await repository.prune_package("prune-pkg", keep_arns)

            # Verify removal count
            assert removed_count == 1, f"Expected 1 node removed, got {removed_count}"

            # Verify correct nodes remain
            assert len(mock_pool._nodes) == 2
            assert "arn:archon:code:test-ws/prune-pkg/src/keep1.py#func" in mock_pool._nodes
            assert "arn:archon:code:test-ws/prune-pkg/src/keep2.py#func" in mock_pool._nodes
            assert "arn:archon:code:test-ws/prune-pkg/src/remove.py#func" not in mock_pool._nodes

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_only_affects_specified_package(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that prunePackage only removes nodes from specified package.

        Verifies that nodes from other packages are not affected by
        the prune operation.

        **Validates: Requirements 6.5**
        """
        async def run_test():
            # Create nodes in different packages
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/pkg-a/src/file.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="pkg-a",
                    path="src/file.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/pkg-b/src/file.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="pkg-b",
                    path="src/file.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)
            assert len(mock_pool._nodes) == 2

            # Prune pkg-a with empty keep list (remove all from pkg-a)
            removed_count = await repository.prune_package("pkg-a", [])

            # Verify only pkg-a node was removed
            assert removed_count == 1
            assert len(mock_pool._nodes) == 1
            assert "arn:archon:code:test-ws/pkg-b/src/file.py#func" in mock_pool._nodes
            assert "arn:archon:code:test-ws/pkg-a/src/file.py#func" not in mock_pool._nodes

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_returns_zero_when_no_stale_nodes(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that prunePackage returns 0 when all nodes are in keep list.

        Verifies that when all nodes are in the keepArns list,
        no nodes are removed and the count is 0.

        **Validates: Requirements 6.6**
        """
        async def run_test():
            # Create nodes
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/keep-all-pkg/src/a.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="keep-all-pkg",
                    path="src/a.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/keep-all-pkg/src/b.py#func",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="keep-all-pkg",
                    path="src/b.py",
                    name="func",
                    symbol="func",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)

            # Prune with all ARNs in keep list
            keep_arns = [
                "arn:archon:code:test-ws/keep-all-pkg/src/a.py#func",
                "arn:archon:code:test-ws/keep-all-pkg/src/b.py#func",
            ]
            removed_count = await repository.prune_package("keep-all-pkg", keep_arns)

            # Verify no nodes removed
            assert removed_count == 0
            assert len(mock_pool._nodes) == 2

        asyncio.get_event_loop().run_until_complete(run_test())


class TestCascadeDeleteBehavior:
    """Test that deleting nodes cascades to remove associated edges.

    Feature: code-graph-storage, Task 6.1: End-to-end sync flow tests
    **Validates: Requirements 2.3, 6.5, 6.6**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForIntegration:
        """Create a mock asyncpg pool for testing."""
        return MockAsyncpgPoolForIntegration()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForIntegration) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_deleting_node_removes_outgoing_edges(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that deleting a node removes its outgoing edges.

        Verifies that when a node is deleted via prune, all edges
        where that node is the source (from_arn) are also deleted.

        **Validates: Requirements 2.3, 6.5**
        """
        async def run_test():
            # Create nodes
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/parent.py#Parent",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/parent.py",
                    name="Parent",
                    symbol="Parent",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/parent.py#Parent.method",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/parent.py",
                    name="method",
                    symbol="Parent.method",
                    kind=SymbolKind.METHOD,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)

            # Create edge: Parent CONTAINS method
            edges = [
                GraphEdge(
                    from_arn="arn:archon:code:test-ws/cascade-pkg/src/parent.py#Parent",
                    to_arn="arn:archon:code:test-ws/cascade-pkg/src/parent.py#Parent.method",
                    type=EdgeType.CONTAINS,
                ),
            ]
            await repository.upsert_edges(edges)
            assert len(mock_pool._edges) == 1

            # Delete the Parent node (keep only the method)
            keep_arns = ["arn:archon:code:test-ws/cascade-pkg/src/parent.py#Parent.method"]
            removed = await repository.prune_package("cascade-pkg", keep_arns)

            # Verify node was removed
            assert removed == 1
            assert len(mock_pool._nodes) == 1

            # Verify edge was cascade deleted
            assert len(mock_pool._edges) == 0, (
                f"Expected 0 edges after cascade delete, got {len(mock_pool._edges)}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_deleting_node_removes_incoming_edges(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that deleting a node removes its incoming edges.

        Verifies that when a node is deleted via prune, all edges
        where that node is the target (to_arn) are also deleted.

        **Validates: Requirements 2.3, 6.5**
        """
        async def run_test():
            # Create nodes
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/caller.py#caller",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/caller.py",
                    name="caller",
                    symbol="caller",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/callee.py#callee",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/callee.py",
                    name="callee",
                    symbol="callee",
                    kind=SymbolKind.FUNCTION,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)

            # Create edge: caller REFERENCES callee
            edges = [
                GraphEdge(
                    from_arn="arn:archon:code:test-ws/cascade-pkg/src/caller.py#caller",
                    to_arn="arn:archon:code:test-ws/cascade-pkg/src/callee.py#callee",
                    type=EdgeType.REFERENCES,
                ),
            ]
            await repository.upsert_edges(edges)
            assert len(mock_pool._edges) == 1

            # Delete the callee node (keep only the caller)
            keep_arns = ["arn:archon:code:test-ws/cascade-pkg/src/caller.py#caller"]
            removed = await repository.prune_package("cascade-pkg", keep_arns)

            # Verify node was removed
            assert removed == 1
            assert len(mock_pool._nodes) == 1

            # Verify edge was cascade deleted
            assert len(mock_pool._edges) == 0, (
                f"Expected 0 edges after cascade delete, got {len(mock_pool._edges)}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_deleting_node_removes_all_connected_edges(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that deleting a node removes all connected edges.

        Verifies that when a node is deleted, both incoming and outgoing
        edges are removed via cascade delete.

        **Validates: Requirements 2.3, 6.5**
        """
        async def run_test():
            # Create a graph: A -> B -> C (B has both incoming and outgoing edges)
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/a.py#A",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/a.py",
                    name="A",
                    symbol="A",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/b.py#B",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/b.py",
                    name="B",
                    symbol="B",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/c.py#C",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/c.py",
                    name="C",
                    symbol="C",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)

            # Create edges: A -> B and B -> C
            edges = [
                GraphEdge(
                    from_arn="arn:archon:code:test-ws/cascade-pkg/src/a.py#A",
                    to_arn="arn:archon:code:test-ws/cascade-pkg/src/b.py#B",
                    type=EdgeType.REFERENCES,
                ),
                GraphEdge(
                    from_arn="arn:archon:code:test-ws/cascade-pkg/src/b.py#B",
                    to_arn="arn:archon:code:test-ws/cascade-pkg/src/c.py#C",
                    type=EdgeType.REFERENCES,
                ),
            ]
            await repository.upsert_edges(edges)
            assert len(mock_pool._edges) == 2

            # Delete node B (keep A and C)
            keep_arns = [
                "arn:archon:code:test-ws/cascade-pkg/src/a.py#A",
                "arn:archon:code:test-ws/cascade-pkg/src/c.py#C",
            ]
            removed = await repository.prune_package("cascade-pkg", keep_arns)

            # Verify B was removed
            assert removed == 1
            assert len(mock_pool._nodes) == 2
            assert "arn:archon:code:test-ws/cascade-pkg/src/b.py#B" not in mock_pool._nodes

            # Verify both edges were cascade deleted
            assert len(mock_pool._edges) == 0, (
                f"Expected 0 edges after deleting middle node, got {len(mock_pool._edges)}"
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_edges_preserved_when_both_nodes_kept(
        self,
        mock_pool: MockAsyncpgPoolForIntegration,
        repository: GraphRepository,
    ) -> None:
        """Test that edges are preserved when both connected nodes are kept.

        Verifies that edges are only deleted when one of their connected
        nodes is deleted, not when unrelated nodes are pruned.

        **Validates: Requirements 2.3, 6.5**
        """
        async def run_test():
            # Create nodes
            nodes = [
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/keep1.py#Keep1",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/keep1.py",
                    name="Keep1",
                    symbol="Keep1",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/keep2.py#Keep2",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/keep2.py",
                    name="Keep2",
                    symbol="Keep2",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
                GraphNode(
                    arn="arn:archon:code:test-ws/cascade-pkg/src/remove.py#Remove",
                    type=NodeType.CODE,
                    workspace="test-ws",
                    package="cascade-pkg",
                    path="src/remove.py",
                    name="Remove",
                    symbol="Remove",
                    kind=SymbolKind.CLASS,
                    index_hash="hash1",
                ),
            ]
            await repository.upsert_nodes(nodes)

            # Create edge between Keep1 and Keep2 (not involving Remove)
            edges = [
                GraphEdge(
                    from_arn="arn:archon:code:test-ws/cascade-pkg/src/keep1.py#Keep1",
                    to_arn="arn:archon:code:test-ws/cascade-pkg/src/keep2.py#Keep2",
                    type=EdgeType.REFERENCES,
                ),
            ]
            await repository.upsert_edges(edges)
            assert len(mock_pool._edges) == 1

            # Delete Remove node (keep Keep1 and Keep2)
            keep_arns = [
                "arn:archon:code:test-ws/cascade-pkg/src/keep1.py#Keep1",
                "arn:archon:code:test-ws/cascade-pkg/src/keep2.py#Keep2",
            ]
            removed = await repository.prune_package("cascade-pkg", keep_arns)

            # Verify Remove was deleted
            assert removed == 1
            assert len(mock_pool._nodes) == 2

            # Verify edge between Keep1 and Keep2 is preserved
            assert len(mock_pool._edges) == 1, (
                f"Expected 1 edge preserved, got {len(mock_pool._edges)}"
            )
            edge = mock_pool._edges[0]
            assert edge["from_arn"] == "arn:archon:code:test-ws/cascade-pkg/src/keep1.py#Keep1"
            assert edge["to_arn"] == "arn:archon:code:test-ws/cascade-pkg/src/keep2.py#Keep2"

        asyncio.get_event_loop().run_until_complete(run_test())


# =============================================================================
# Task 6.2: GraphQL Query Execution Tests
# =============================================================================
#
# This section contains integration tests for GraphQL query operations,
# testing all query operations with sample data through the GraphQLService.
#
# Feature: code-graph-storage
# Task: 6.2 GraphQL query execution tests
#
# Test Scenarios:
# 1. node(arn): Query existing node, query non-existent node
# 2. searchNodes: Search by name, filter by kind, filter by package, test limit
# 3. findReferences: Find nodes that reference a given node
# 4. symbolsInFile: Get all symbols in a specific file
# 5. symbolsInPackage: Get all symbols in a package, filter by kind
# 6. traverse: Test depth limiting, test with different edge types
# 7. Error handling: Invalid ARN format, invalid enum values
# 8. Relationship fields: Test contains, containedBy, references, referencedBy
#
# **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**
#
# Source:
# - src/graph/service.py
# - src/graph/resolvers.py
# - src/graph/schema.py
# - .kiro/specs/code-graph-storage/design.md
# =============================================================================


class MockAsyncpgPoolForGraphQL:
    """Mock asyncpg pool for GraphQL query testing.

    This mock simulates PostgreSQL behavior for GraphQL query operations:
    - Node lookup by ARN
    - Node search by name with filters
    - Edge traversal for relationship fields
    - Symbols in file/package queries
    - Graph traversal with depth limiting
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    def add_node(self, node_data: dict) -> None:
        """Add a node to the mock database."""
        self._nodes[node_data["arn"]] = node_data

    def add_edge(self, edge_data: dict) -> None:
        """Add an edge to the mock database."""
        self._edges.append(edge_data)


    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Mock fetchrow - returns a single row or None."""
        if "SELECT" in query and "FROM code_graph_nodes" in query and "WHERE arn = $1" in query:
            arn = args[0]
            return self._nodes.get(arn)
        return None

    async def fetch(self, query: str, *args) -> list[dict]:
        """Mock fetch - returns multiple rows."""
        if "SELECT" in query and "FROM code_graph_edges" in query:
            return self._handle_edge_query(query, args)
        if "SELECT" in query and "FROM code_graph_nodes" in query:
            return self._handle_node_query(query, args)
        return []


    def _handle_edge_query(self, query: str, args) -> list[dict]:
        """Handle edge-related queries."""
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
        elif "WHERE e.from_arn = ANY($1)" in query:
            from_arns = args[0]
            edge_types = args[1]
            visited = args[2] if len(args) > 2 else []
            return [
                {"to_arn": e["to_arn"]}
                for e in self._edges
                if e["from_arn"] in from_arns and e["type"] in edge_types and e["to_arn"] not in visited
            ]
        return []


    def _handle_node_query(self, query: str, args) -> list[dict]:
        """Handle node-related queries."""
        if "WHERE arn = ANY($1)" in query:
            arns = args[0]
            return [self._nodes[arn] for arn in arns if arn in self._nodes]
        elif "WHERE LOWER(name) LIKE LOWER($1)" in query:
            return self._handle_search_query(query, args)
        elif "WHERE path = $1 AND package = $2" in query:
            path, package = args[0], args[1]
            return [
                n for n in self._nodes.values()
                if n["path"] == path and n["package"] == package and n.get("symbol")
            ]
        elif "WHERE package = $1" in query:
            return self._handle_package_query(query, args)
        return []


    def _handle_search_query(self, query: str, args) -> list[dict]:
        """Handle search nodes query."""
        name_pattern = args[0].strip("%").lower()
        results = [n for n in self._nodes.values() if name_pattern in n["name"].lower()]
        param_idx = 1
        if "AND kind = $" in query:
            kind = args[param_idx]
            results = [n for n in results if n.get("kind") == kind]
            param_idx += 1
        if "AND package = $" in query:
            package = args[param_idx]
            results = [n for n in results if n["package"] == package]
            param_idx += 1
        limit = args[param_idx] if param_idx < len(args) else 20
        return results[:limit]

    def _handle_package_query(self, query: str, args) -> list[dict]:
        """Handle symbols in package query."""
        package = args[0]
        results = [n for n in self._nodes.values() if n["package"] == package and n.get("symbol")]
        if "AND kind = $2" in query:
            kind = args[1]
            results = [n for n in results if n.get("kind") == kind]
        return results


    async def fetchval(self, query: str, *args):
        """Mock fetchval - returns a single value."""
        return 1

    async def execute(self, query: str, *args) -> str:
        """Mock execute - handles DELETE operations."""
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


class MockTransactionContextForGraphQL:
    """Mock transaction context manager for GraphQL tests."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnectionForGraphQL:
    """Mock database connection for GraphQL tests."""

    def __init__(self, pool: MockAsyncpgPoolForGraphQL):
        self._pool = pool

    def transaction(self):
        """Return a transaction context manager."""
        return MockTransactionContextForGraphQL()

    async def fetchrow(self, query: str, *args) -> Optional[dict]:
        """Delegate to pool."""
        return await self._pool.fetchrow(query, *args)

    async def fetch(self, query: str, *args) -> list[dict]:
        """Delegate to pool."""
        return await self._pool.fetch(query, *args)

    async def execute(self, query: str, *args) -> str:
        """Delegate to pool."""
        return await self._pool.execute(query, *args)


def create_test_node(
    arn: str,
    name: str,
    package: str = "test-pkg",
    path: str = "src/file.py",
    kind: str = "function",
    node_type: str = "code",
    workspace: str = "test-ws",
    symbol: Optional[str] = None,
    signature: Optional[str] = None,
    documentation: Optional[str] = None,
    file_path: Optional[str] = None,
    line_number: Optional[int] = None,
) -> dict:
    """Create a test node dictionary for the mock database."""
    return {
        "arn": arn,
        "type": node_type,
        "workspace": workspace,
        "package": package,
        "path": path,
        "symbol": symbol or name,
        "kind": kind,
        "name": name,
        "signature": signature,
        "documentation": documentation,
        "file_path": file_path,
        "line_number": line_number,
        "index_hash": "test-hash",
    }


class TestNodeQueryOperation:
    """Test the node(arn) GraphQL query operation.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 5.1**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with test data."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/main.py#main_func",
            name="main_func",
            kind="function",
            signature="def main_func() -> None",
            documentation="Main entry point function",
            line_number=10,
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/utils.py#Helper",
            name="Helper",
            kind="class",
            path="src/utils.py",
            documentation="Helper class for utilities",
            line_number=5,
        ))
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_node_query_returns_existing_node(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that node(arn) returns an existing node with all properties.

        **Validates: Requirement 5.1**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNode($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        kind
                        signature
                        documentation
                        lineNumber
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/main.py#main_func"}
            )

            assert "errors" not in result or result["errors"] is None
            assert result["data"]["node"] is not None
            node = result["data"]["node"]
            assert node["arn"] == "arn:archon:code:test-ws/test-pkg/src/main.py#main_func"
            assert node["name"] == "main_func"
            assert node["kind"] == "FUNCTION"
            assert node["signature"] == "def main_func() -> None"
            assert node["documentation"] == "Main entry point function"
            assert node["lineNumber"] == 10

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_node_query_returns_null_for_nonexistent_node(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that node(arn) returns null for non-existent ARN.

        **Validates: Requirement 5.1**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNode($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/nonexistent.py#missing"}
            )

            assert "errors" not in result or result["errors"] is None
            assert result["data"]["node"] is None

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSearchNodesQueryOperation:
    """Test the searchNodes GraphQL query operation.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 5.2**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with test data for search."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/pkg-a/src/repo.py#Repository",
            name="Repository",
            package="pkg-a",
            kind="class",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/pkg-a/src/repo.py#RepositoryError",
            name="RepositoryError",
            package="pkg-a",
            kind="class",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/pkg-b/src/service.py#ServiceRepository",
            name="ServiceRepository",
            package="pkg-b",
            kind="class",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/pkg-a/src/utils.py#get_repository",
            name="get_repository",
            package="pkg-a",
            kind="function",
        ))
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_search_nodes_by_name(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test searchNodes returns nodes matching name pattern.

        **Validates: Requirement 5.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SearchNodes($name: String!) {
                    searchNodes(name: $name) {
                        arn
                        name
                        kind
                    }
                }
            """
            result = await service.execute(query, variables={"name": "Repository"})

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["searchNodes"]
            assert len(nodes) == 4
            names = [n["name"] for n in nodes]
            assert "Repository" in names
            assert "RepositoryError" in names
            assert "ServiceRepository" in names
            assert "get_repository" in names

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_search_nodes_filter_by_kind(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test searchNodes filters by symbol kind.

        **Validates: Requirement 5.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SearchNodes($name: String!, $kind: SymbolKind) {
                    searchNodes(name: $name, kind: $kind) {
                        arn
                        name
                        kind
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"name": "Repository", "kind": "CLASS"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["searchNodes"]
            assert len(nodes) == 3
            for node in nodes:
                assert node["kind"] == "CLASS"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_search_nodes_filter_by_package(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test searchNodes filters by package.

        **Validates: Requirement 5.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SearchNodes($name: String!, $package: String) {
                    searchNodes(name: $name, package: $package) {
                        arn
                        name
                        package
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"name": "Repository", "package": "pkg-a"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["searchNodes"]
            assert len(nodes) == 3
            for node in nodes:
                assert node["package"] == "pkg-a"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_search_nodes_respects_limit(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test searchNodes respects the limit parameter.

        **Validates: Requirement 5.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SearchNodes($name: String!, $limit: Int) {
                    searchNodes(name: $name, limit: $limit) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"name": "Repository", "limit": 2}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["searchNodes"]
            assert len(nodes) == 2

        asyncio.get_event_loop().run_until_complete(run_test())


class TestFindReferencesQueryOperation:
    """Test the findReferences GraphQL query operation.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 5.3**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with test data for references."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/target.py#target_func",
            name="target_func",
            kind="function",
            path="src/target.py",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/caller1.py#caller1",
            name="caller1",
            kind="function",
            path="src/caller1.py",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/caller2.py#caller2",
            name="caller2",
            kind="function",
            path="src/caller2.py",
        ))
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/caller1.py#caller1",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/target.py#target_func",
            "type": "references",
        })
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/caller2.py#caller2",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/target.py#target_func",
            "type": "references",
        })
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_find_references_returns_referencing_nodes(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test findReferences returns all nodes that reference the target.

        **Validates: Requirement 5.3**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query FindRefs($arn: ID!) {
                    findReferences(arn: $arn) {
                        arn
                        name
                        path
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/target.py#target_func"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["findReferences"]
            assert len(nodes) == 2
            names = [n["name"] for n in nodes]
            assert "caller1" in names
            assert "caller2" in names

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_find_references_returns_empty_for_unreferenced_node(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test findReferences returns empty list for unreferenced node.

        **Validates: Requirement 5.3**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query FindRefs($arn: ID!) {
                    findReferences(arn: $arn) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/caller1.py#caller1"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["findReferences"]
            assert len(nodes) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSymbolsInFileQueryOperation:
    """Test the symbolsInFile GraphQL query operation.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 5.4**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with test data for file symbols."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/module.py#MyClass",
            name="MyClass",
            path="src/module.py",
            kind="class",
            line_number=10,
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/module.py#MyClass.method",
            name="method",
            path="src/module.py",
            kind="method",
            symbol="MyClass.method",
            line_number=15,
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/module.py#helper_func",
            name="helper_func",
            path="src/module.py",
            kind="function",
            line_number=30,
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/other.py#other_func",
            name="other_func",
            path="src/other.py",
            kind="function",
        ))
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_symbols_in_file_returns_all_symbols(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test symbolsInFile returns all symbols in the specified file.

        **Validates: Requirement 5.4**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SymbolsInFile($path: String!, $package: String!) {
                    symbolsInFile(path: $path, package: $package) {
                        arn
                        name
                        kind
                        lineNumber
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"path": "src/module.py", "package": "test-pkg"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["symbolsInFile"]
            assert len(nodes) == 3
            names = [n["name"] for n in nodes]
            assert "MyClass" in names
            assert "method" in names
            assert "helper_func" in names
            assert "other_func" not in names

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_symbols_in_file_returns_empty_for_nonexistent_file(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test symbolsInFile returns empty list for non-existent file.

        **Validates: Requirement 5.4**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SymbolsInFile($path: String!, $package: String!) {
                    symbolsInFile(path: $path, package: $package) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"path": "src/nonexistent.py", "package": "test-pkg"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["symbolsInFile"]
            assert len(nodes) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSymbolsInPackageQueryOperation:
    """Test the symbolsInPackage GraphQL query operation.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 5.5**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with test data for package symbols."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/my-pkg/src/a.py#ClassA",
            name="ClassA",
            package="my-pkg",
            path="src/a.py",
            kind="class",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/my-pkg/src/b.py#func_b",
            name="func_b",
            package="my-pkg",
            path="src/b.py",
            kind="function",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/my-pkg/src/c.py#ClassC",
            name="ClassC",
            package="my-pkg",
            path="src/c.py",
            kind="class",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/other-pkg/src/d.py#ClassD",
            name="ClassD",
            package="other-pkg",
            path="src/d.py",
            kind="class",
        ))
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_symbols_in_package_returns_all_symbols(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test symbolsInPackage returns all symbols in the package.

        **Validates: Requirement 5.5**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SymbolsInPackage($package: String!) {
                    symbolsInPackage(package: $package) {
                        arn
                        name
                        kind
                        path
                    }
                }
            """
            result = await service.execute(query, variables={"package": "my-pkg"})

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["symbolsInPackage"]
            assert len(nodes) == 3
            names = [n["name"] for n in nodes]
            assert "ClassA" in names
            assert "func_b" in names
            assert "ClassC" in names
            assert "ClassD" not in names

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_symbols_in_package_filter_by_kind(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test symbolsInPackage filters by symbol kind.

        **Validates: Requirement 5.5**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SymbolsInPackage($package: String!, $kind: SymbolKind) {
                    symbolsInPackage(package: $package, kind: $kind) {
                        arn
                        name
                        kind
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"package": "my-pkg", "kind": "CLASS"}
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["symbolsInPackage"]
            assert len(nodes) == 2
            for node in nodes:
                assert node["kind"] == "CLASS"
            names = [n["name"] for n in nodes]
            assert "ClassA" in names
            assert "ClassC" in names

        asyncio.get_event_loop().run_until_complete(run_test())


class TestTraverseQueryOperation:
    """Test the traverse GraphQL query operation.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 5.6**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with test data for traversal."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/root.py#Root",
            name="Root",
            kind="class",
            path="src/root.py",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/root.py#Root.method1",
            name="method1",
            kind="method",
            path="src/root.py",
            symbol="Root.method1",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/root.py#Root.method2",
            name="method2",
            kind="method",
            path="src/root.py",
            symbol="Root.method2",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/helper.py#helper",
            name="helper",
            kind="function",
            path="src/helper.py",
        ))
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root.method1",
            "type": "contains",
        })
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root.method2",
            "type": "contains",
        })
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root.method1",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/helper.py#helper",
            "type": "references",
        })
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_traverse_depth_one(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test traverse with depth=1 returns immediate neighbors.

        **Validates: Requirement 5.6**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query Traverse($startArn: ID!, $edgeTypes: [EdgeType!]!, $depth: Int) {
                    traverse(startArn: $startArn, edgeTypes: $edgeTypes, depth: $depth) {
                        arn
                        name
                        kind
                    }
                }
            """
            result = await service.execute(
                query,
                variables={
                    "startArn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root",
                    "edgeTypes": ["CONTAINS"],
                    "depth": 1
                }
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["traverse"]
            assert len(nodes) == 2
            names = [n["name"] for n in nodes]
            assert "method1" in names
            assert "method2" in names
            assert "helper" not in names

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_traverse_depth_two(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test traverse with depth=2 returns nodes two hops away.

        **Validates: Requirement 5.6**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query Traverse($startArn: ID!, $edgeTypes: [EdgeType!]!, $depth: Int) {
                    traverse(startArn: $startArn, edgeTypes: $edgeTypes, depth: $depth) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={
                    "startArn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root",
                    "edgeTypes": ["CONTAINS", "REFERENCES"],
                    "depth": 2
                }
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["traverse"]
            assert len(nodes) == 3
            names = [n["name"] for n in nodes]
            assert "method1" in names
            assert "method2" in names
            assert "helper" in names

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_traverse_with_specific_edge_type(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test traverse filters by edge type.

        **Validates: Requirement 5.6**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query Traverse($startArn: ID!, $edgeTypes: [EdgeType!]!, $depth: Int) {
                    traverse(startArn: $startArn, edgeTypes: $edgeTypes, depth: $depth) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={
                    "startArn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root.method1",
                    "edgeTypes": ["REFERENCES"],
                    "depth": 1
                }
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["traverse"]
            assert len(nodes) == 1
            assert nodes[0]["name"] == "helper"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_traverse_depth_zero_returns_empty(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test traverse with depth=0 returns empty list.

        **Validates: Requirement 5.6**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query Traverse($startArn: ID!, $edgeTypes: [EdgeType!]!, $depth: Int) {
                    traverse(startArn: $startArn, edgeTypes: $edgeTypes, depth: $depth) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={
                    "startArn": "arn:archon:code:test-ws/test-pkg/src/root.py#Root",
                    "edgeTypes": ["CONTAINS"],
                    "depth": 0
                }
            )

            assert "errors" not in result or result["errors"] is None
            nodes = result["data"]["traverse"]
            assert len(nodes) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestGraphQLErrorHandling:
    """Test GraphQL error handling for invalid inputs.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool."""
        return MockAsyncpgPoolForGraphQL()

    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_invalid_enum_value_returns_error(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that invalid enum values return GraphQL validation errors.

        **Validates: Error handling for invalid enum values**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query SearchNodes($name: String!, $kind: SymbolKind) {
                    searchNodes(name: $name, kind: $kind) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"name": "test", "kind": "INVALID_KIND"}
            )

            assert "errors" in result and result["errors"] is not None
            assert len(result["errors"]) > 0

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_missing_required_argument_returns_error(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that missing required arguments return GraphQL errors.

        **Validates: Error handling for missing required arguments**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNode {
                    node {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(query)

            assert "errors" in result and result["errors"] is not None
            assert len(result["errors"]) > 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_invalid_query_syntax_returns_error(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that invalid GraphQL syntax returns parse errors.

        **Validates: Error handling for invalid query syntax**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNode($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                    
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/file.py#func"}
            )

            assert "errors" in result and result["errors"] is not None
            assert len(result["errors"]) > 0

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_invalid_edge_type_enum_returns_error(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that invalid EdgeType enum values return errors.

        **Validates: Error handling for invalid enum values**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query Traverse($startArn: ID!, $edgeTypes: [EdgeType!]!, $depth: Int) {
                    traverse(startArn: $startArn, edgeTypes: $edgeTypes, depth: $depth) {
                        arn
                        name
                    }
                }
            """
            result = await service.execute(
                query,
                variables={
                    "startArn": "arn:archon:code:test-ws/test-pkg/src/file.py#func",
                    "edgeTypes": ["INVALID_EDGE_TYPE"],
                    "depth": 1
                }
            )

            assert "errors" in result and result["errors"] is not None
            assert len(result["errors"]) > 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestRelationshipFieldResolvers:
    """Test Node relationship field resolvers.

    Feature: code-graph-storage, Task 6.2: GraphQL query execution tests
    **Validates: Requirement 4.2**
    """

    @pytest.fixture
    def mock_pool(self) -> MockAsyncpgPoolForGraphQL:
        """Create a mock asyncpg pool with relationship test data."""
        pool = MockAsyncpgPoolForGraphQL()
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass",
            name="ParentClass",
            kind="class",
            path="src/parent.py",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass.method",
            name="method",
            kind="method",
            path="src/parent.py",
            symbol="ParentClass.method",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/caller.py#caller_func",
            name="caller_func",
            kind="function",
            path="src/caller.py",
        ))
        pool.add_node(create_test_node(
            arn="arn:archon:code:test-ws/test-pkg/src/target.py#target_func",
            name="target_func",
            kind="function",
            path="src/target.py",
        ))
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass.method",
            "type": "contains",
        })
        pool.add_edge({
            "from_arn": "arn:archon:code:test-ws/test-pkg/src/caller.py#caller_func",
            "to_arn": "arn:archon:code:test-ws/test-pkg/src/target.py#target_func",
            "type": "references",
        })
        return pool


    @pytest.fixture
    def repository(self, mock_pool: MockAsyncpgPoolForGraphQL) -> GraphRepository:
        """Create a GraphRepository with mocked pool."""
        repo = GraphRepository(db_url="postgresql://test:test@localhost/test")
        repo._pool = mock_pool
        return repo

    def test_contains_field_returns_contained_nodes(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that contains field returns nodes contained by this node.

        **Validates: Requirement 4.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNodeWithContains($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        contains {
                            arn
                            name
                            kind
                        }
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass"}
            )

            assert "errors" not in result or result["errors"] is None
            node = result["data"]["node"]
            assert node is not None
            assert len(node["contains"]) == 1
            assert node["contains"][0]["name"] == "method"
            assert node["contains"][0]["kind"] == "METHOD"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_contained_by_field_returns_parent_node(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that containedBy field returns the containing node.

        **Validates: Requirement 4.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNodeWithContainedBy($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        containedBy {
                            arn
                            name
                            kind
                        }
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass.method"}
            )

            assert "errors" not in result or result["errors"] is None
            node = result["data"]["node"]
            assert node is not None
            assert node["containedBy"] is not None
            assert node["containedBy"]["name"] == "ParentClass"
            assert node["containedBy"]["kind"] == "CLASS"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_references_field_returns_referenced_nodes(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that references field returns nodes this node references.

        **Validates: Requirement 4.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

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
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/caller.py#caller_func"}
            )

            assert "errors" not in result or result["errors"] is None
            node = result["data"]["node"]
            assert node is not None
            assert len(node["references"]) == 1
            assert node["references"][0]["name"] == "target_func"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_referenced_by_field_returns_referencing_nodes(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that referencedBy field returns nodes that reference this node.

        **Validates: Requirement 4.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

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
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/target.py#target_func"}
            )

            assert "errors" not in result or result["errors"] is None
            node = result["data"]["node"]
            assert node is not None
            assert len(node["referencedBy"]) == 1
            assert node["referencedBy"][0]["name"] == "caller_func"

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_empty_relationship_fields(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that relationship fields return empty lists when no relationships exist.

        **Validates: Requirement 4.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

            query = """
                query GetNodeWithAllRelationships($arn: ID!) {
                    node(arn: $arn) {
                        arn
                        name
                        contains {
                            arn
                        }
                        references {
                            arn
                        }
                        implements {
                            arn
                        }
                        extends {
                            arn
                        }
                        imports {
                            arn
                        }
                    }
                }
            """
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/target.py#target_func"}
            )

            assert "errors" not in result or result["errors"] is None
            node = result["data"]["node"]
            assert node is not None
            assert len(node["contains"]) == 0
            assert len(node["references"]) == 0
            assert len(node["implements"]) == 0
            assert len(node["extends"]) == 0
            assert len(node["imports"]) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_contained_by_returns_null_for_top_level_node(
        self,
        mock_pool: MockAsyncpgPoolForGraphQL,
        repository: GraphRepository,
    ) -> None:
        """Test that containedBy returns null for nodes not contained by anything.

        **Validates: Requirement 4.2**
        """
        async def run_test():
            from src.graph.service import GraphQLService, GraphQLServiceConfig
            config = GraphQLServiceConfig(database_url="postgresql://test:test@localhost/test")
            service = GraphQLService(config)
            service.repository = repository

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
            result = await service.execute(
                query,
                variables={"arn": "arn:archon:code:test-ws/test-pkg/src/parent.py#ParentClass"}
            )

            assert "errors" not in result or result["errors"] is None
            node = result["data"]["node"]
            assert node is not None
            assert node["containedBy"] is None

        asyncio.get_event_loop().run_until_complete(run_test())
