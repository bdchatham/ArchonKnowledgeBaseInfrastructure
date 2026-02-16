"""Repository for Code Graph data access.

This module provides the data access layer for the Code Graph, handling
all database operations for nodes and edges. It uses asyncpg for async
PostgreSQL access with connection pooling.

The repository pattern separates data access logic from business logic,
making the code more testable and maintainable.

Source:
- src/graph/models.py (SQLAlchemy models and enums)
- migrations/001_code_graph_tables.sql (database schema)
"""

import logging
from dataclasses import dataclass
from typing import Optional

import asyncpg

from src.graph.models import EdgeType, NodeType, SymbolKind

logger = logging.getLogger(__name__)


@dataclass
class GraphNode:
    """Represents a node in the Code Graph.

    This is a data transfer object (DTO) for graph nodes, separate from
    the SQLAlchemy model. It provides a clean interface for the repository
    layer without ORM dependencies.

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    Example: arn:archon:code:personal-work/ArchonKnowledgeBaseInfrastructure/src/graph/schema.py#GraphQLService

    Attributes:
        arn: Archon Resource Name - unique identifier for the node
        type: Resource type (code, doc, k8s, infra)
        workspace: Workspace identifier (e.g., 'personal-work')
        package: Package/repository name
        path: File path relative to package
        name: Human-readable name
        symbol: Symbol name within the file (optional)
        kind: Symbol kind (function, class, method, etc.) (optional)
        signature: Function/method signature (optional)
        documentation: Documentation string (optional)
        file_path: Absolute file path for resolution (optional)
        line_number: Line number in source file (optional)
        index_hash: SCIP index hash for change detection (optional)
    """

    arn: str
    type: NodeType
    workspace: str
    package: str
    path: str
    name: str
    symbol: Optional[str] = None
    kind: Optional[SymbolKind] = None
    signature: Optional[str] = None
    documentation: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    index_hash: Optional[str] = None


@dataclass
class GraphEdge:
    """Represents an edge in the Code Graph.

    This is a data transfer object (DTO) for graph edges, separate from
    the SQLAlchemy model. It provides a clean interface for the repository
    layer without ORM dependencies.

    Relationship types:
        - contains: Parent contains child (e.g., class contains method)
        - references: Source references target (e.g., function calls another)
        - implements: Source implements target interface/type
        - extends: Source extends target class/type
        - imports: Source imports target module
        - documents: Documentation node documents code node

    Attributes:
        from_arn: Source node ARN
        to_arn: Target node ARN
        type: Relationship type
    """

    from_arn: str
    to_arn: str
    type: EdgeType


class GraphRepositoryError(Exception):
    """Raised when graph repository operations fail."""

    pass


class GraphRepository:
    """Repository for Code Graph data access.

    Provides async data access methods for graph nodes and edges using
    asyncpg connection pooling. This class handles all database operations
    for the Code Graph, including CRUD operations, traversals, and bulk
    upserts.

    Usage:
        repository = GraphRepository(db_url="postgresql://...")
        await repository.connect()

        # Get a node by ARN
        node = await repository.get_node("arn:archon:code:...")

        # Search nodes
        nodes = await repository.search_nodes("GraphRepository")

        await repository.close()

    The repository methods will be implemented in subsequent tasks:
        - Task 2.2: Node CRUD operations (get_node, search_nodes, etc.)
        - Task 2.3: Edge CRUD operations (get_edges_from, get_edges_to)
        - Task 2.4: Traversal operations (traverse)
        - Task 2.5: Bulk upsert operations (upsert_nodes, upsert_edges, prune_package)

    Attributes:
        db_url: PostgreSQL connection URL
    """

    def __init__(self, db_url: str) -> None:
        """Initialize the graph repository.

        Args:
            db_url: PostgreSQL connection URL
                    (e.g., "postgresql://user:pass@host:port/db")
        """
        self.db_url = db_url
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Connect to the database and create connection pool.

        Creates an asyncpg connection pool for efficient database access.
        This method is idempotent - calling it multiple times has no effect
        if already connected.

        Raises:
            GraphRepositoryError: If connection fails
        """
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(self.db_url)
                logger.info("Connected to graph repository database")
            except asyncpg.PostgresError as e:
                logger.error(f"Failed to connect to database: {e}")
                raise GraphRepositoryError(f"Failed to connect to database: {e}") from e

    async def close(self) -> None:
        """Close the database connection pool.

        Closes all connections in the pool and releases resources.
        This method is idempotent - calling it multiple times has no effect
        if already closed.
        """
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Closed graph repository database connection")

    async def _get_pool(self) -> asyncpg.Pool:
        """Get the connection pool, connecting if necessary.

        Returns:
            The asyncpg connection pool

        Raises:
            GraphRepositoryError: If connection fails
        """
        if self._pool is None:
            await self.connect()
        return self._pool

    async def health_check(self) -> bool:
        """Check if the database is healthy.

        Performs a simple query to verify database connectivity and
        that the required tables exist.

        Returns:
            True if database is reachable and tables exist, False otherwise
        """
        try:
            pool = await self._get_pool()
            await pool.fetchval("SELECT 1 FROM code_graph_nodes LIMIT 1")
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    # Node CRUD operations (Task 2.2)

    def _row_to_graph_node(self, row: asyncpg.Record) -> GraphNode:
        """Convert a database row to a GraphNode object.

        Args:
            row: asyncpg Record from a query

        Returns:
            GraphNode object populated from the row
        """
        return GraphNode(
            arn=row["arn"],
            type=NodeType(row["type"]),
            workspace=row["workspace"],
            package=row["package"],
            path=row["path"],
            name=row["name"],
            symbol=row["symbol"],
            kind=SymbolKind(row["kind"]) if row["kind"] else None,
            signature=row["signature"],
            documentation=row["documentation"],
            file_path=row["file_path"],
            line_number=row["line_number"],
            index_hash=row["index_hash"],
        )

    async def get_node(self, arn: str) -> Optional[GraphNode]:
        """Get a node by ARN.

        Args:
            arn: Archon Resource Name of the node

        Returns:
            GraphNode if found, None otherwise

        Raises:
            GraphRepositoryError: If database operation fails
        """
        try:
            pool = await self._get_pool()
            row = await pool.fetchrow(
                """
                SELECT arn, type, workspace, package, path, symbol, kind,
                       name, signature, documentation, file_path, line_number,
                       index_hash
                FROM code_graph_nodes
                WHERE arn = $1
                """,
                arn,
            )
            if row is None:
                return None
            return self._row_to_graph_node(row)
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get node {arn}: {e}")
            raise GraphRepositoryError(f"Failed to get node: {e}") from e

    async def search_nodes(
        self,
        name: str,
        kind: Optional[SymbolKind] = None,
        package: Optional[str] = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        """Search nodes by name with optional filters.

        Args:
            name: Name pattern to search for (case-insensitive)
            kind: Optional symbol kind filter
            package: Optional package filter
            limit: Maximum number of results (default: 20)

        Returns:
            List of matching GraphNode objects

        Raises:
            GraphRepositoryError: If database operation fails
        """
        try:
            pool = await self._get_pool()
            query_parts = [
                """
                SELECT arn, type, workspace, package, path, symbol, kind,
                       name, signature, documentation, file_path, line_number,
                       index_hash
                FROM code_graph_nodes
                WHERE LOWER(name) LIKE LOWER($1)
                """
            ]
            params: list = [f"%{name}%"]
            param_index = 2

            if kind is not None:
                query_parts.append(f"AND kind = ${param_index}")
                params.append(kind.value)
                param_index += 1

            if package is not None:
                query_parts.append(f"AND package = ${param_index}")
                params.append(package)
                param_index += 1

            query_parts.append(f"ORDER BY name LIMIT ${param_index}")
            params.append(limit)

            query = " ".join(query_parts)
            rows = await pool.fetch(query, *params)
            return [self._row_to_graph_node(row) for row in rows]
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to search nodes with name '{name}': {e}")
            raise GraphRepositoryError(f"Failed to search nodes: {e}") from e

    async def get_symbols_in_file(
        self,
        path: str,
        package: str,
    ) -> list[GraphNode]:
        """Get all symbols defined in a file.

        Args:
            path: File path relative to package
            package: Package name

        Returns:
            List of GraphNode objects for symbols in the file

        Raises:
            GraphRepositoryError: If database operation fails
        """
        try:
            pool = await self._get_pool()
            rows = await pool.fetch(
                """
                SELECT arn, type, workspace, package, path, symbol, kind,
                       name, signature, documentation, file_path, line_number,
                       index_hash
                FROM code_graph_nodes
                WHERE path = $1 AND package = $2 AND symbol IS NOT NULL
                ORDER BY line_number NULLS LAST, name
                """,
                path,
                package,
            )
            return [self._row_to_graph_node(row) for row in rows]
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get symbols in file {path}: {e}")
            raise GraphRepositoryError(f"Failed to get symbols in file: {e}") from e

    async def get_symbols_in_package(
        self,
        package: str,
        kind: Optional[SymbolKind] = None,
    ) -> list[GraphNode]:
        """Get all symbols in a package.

        Args:
            package: Package name
            kind: Optional symbol kind filter

        Returns:
            List of GraphNode objects for symbols in the package

        Raises:
            GraphRepositoryError: If database operation fails
        """
        try:
            pool = await self._get_pool()
            if kind is not None:
                rows = await pool.fetch(
                    """
                    SELECT arn, type, workspace, package, path, symbol, kind,
                           name, signature, documentation, file_path, line_number,
                           index_hash
                    FROM code_graph_nodes
                    WHERE package = $1 AND kind = $2 AND symbol IS NOT NULL
                    ORDER BY path, name
                    """,
                    package,
                    kind.value,
                )
            else:
                rows = await pool.fetch(
                    """
                    SELECT arn, type, workspace, package, path, symbol, kind,
                           name, signature, documentation, file_path, line_number,
                           index_hash
                    FROM code_graph_nodes
                    WHERE package = $1 AND symbol IS NOT NULL
                    ORDER BY path, name
                    """,
                    package,
                )
            return [self._row_to_graph_node(row) for row in rows]
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get symbols in package {package}: {e}")
            raise GraphRepositoryError(f"Failed to get symbols in package: {e}") from e

    # Edge CRUD operations (Task 2.3)

    def _row_to_graph_edge(self, row: asyncpg.Record) -> GraphEdge:
        """Convert a database row to a GraphEdge object.

        Args:
            row: asyncpg Record from a query

        Returns:
            GraphEdge object populated from the row
        """
        return GraphEdge(
            from_arn=row["from_arn"],
            to_arn=row["to_arn"],
            type=EdgeType(row["type"]),
        )

    async def get_edges_from(
        self,
        arn: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[GraphEdge]:
        """Get outgoing edges from a node.

        Args:
            arn: Source node ARN
            edge_type: Optional edge type filter

        Returns:
            List of GraphEdge objects for outgoing edges

        Raises:
            GraphRepositoryError: If database operation fails
        """
        try:
            pool = await self._get_pool()
            if edge_type is not None:
                rows = await pool.fetch(
                    """
                    SELECT from_arn, to_arn, type
                    FROM code_graph_edges
                    WHERE from_arn = $1 AND type = $2
                    ORDER BY to_arn
                    """,
                    arn,
                    edge_type.value,
                )
            else:
                rows = await pool.fetch(
                    """
                    SELECT from_arn, to_arn, type
                    FROM code_graph_edges
                    WHERE from_arn = $1
                    ORDER BY type, to_arn
                    """,
                    arn,
                )
            return [self._row_to_graph_edge(row) for row in rows]
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get edges from {arn}: {e}")
            raise GraphRepositoryError(f"Failed to get edges from node: {e}") from e

    async def get_edges_to(
        self,
        arn: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[GraphEdge]:
        """Get incoming edges to a node.

        Args:
            arn: Target node ARN
            edge_type: Optional edge type filter

        Returns:
            List of GraphEdge objects for incoming edges

        Raises:
            GraphRepositoryError: If database operation fails
        """
        try:
            pool = await self._get_pool()
            if edge_type is not None:
                rows = await pool.fetch(
                    """
                    SELECT from_arn, to_arn, type
                    FROM code_graph_edges
                    WHERE to_arn = $1 AND type = $2
                    ORDER BY from_arn
                    """,
                    arn,
                    edge_type.value,
                )
            else:
                rows = await pool.fetch(
                    """
                    SELECT from_arn, to_arn, type
                    FROM code_graph_edges
                    WHERE to_arn = $1
                    ORDER BY type, from_arn
                    """,
                    arn,
                )
            return [self._row_to_graph_edge(row) for row in rows]
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get edges to {arn}: {e}")
            raise GraphRepositoryError(f"Failed to get edges to node: {e}") from e

    # Traversal operations (Task 2.4)

    async def traverse(
        self,
        start_arn: str,
        edge_types: list[EdgeType],
        depth: int = 1,
    ) -> list[GraphNode]:
        """Traverse the graph from a starting node.

        Performs a breadth-first traversal from the starting node,
        following edges of the specified types up to the given depth.
        Uses a visited set for cycle detection to avoid infinite loops.

        The traversal returns all nodes reachable from the start node
        via the specified edge types, excluding the start node itself.

        Args:
            start_arn: Starting node ARN
            edge_types: List of edge types to follow
            depth: Maximum traversal depth (default: 1, must be >= 0)

        Returns:
            List of GraphNode objects reachable from the start node
            (does not include the start node itself)

        Raises:
            GraphRepositoryError: If database operation fails

        Example:
            # Traverse from a class to find all contained methods (depth 1)
            methods = await repo.traverse(
                "arn:archon:code:ws/pkg/file.py#MyClass",
                [EdgeType.CONTAINS],
                depth=1
            )

            # Traverse references up to 2 levels deep
            refs = await repo.traverse(
                "arn:archon:code:ws/pkg/file.py#func",
                [EdgeType.REFERENCES],
                depth=2
            )
        """
        if depth < 0:
            return []

        if depth == 0:
            return []

        if not edge_types:
            return []

        try:
            pool = await self._get_pool()

            visited: set[str] = {start_arn}
            result_nodes: list[GraphNode] = []
            current_frontier: set[str] = {start_arn}

            edge_type_values = [et.value for et in edge_types]

            for _ in range(depth):
                if not current_frontier:
                    break

                frontier_list = list(current_frontier)
                rows = await pool.fetch(
                    """
                    SELECT DISTINCT e.to_arn
                    FROM code_graph_edges e
                    WHERE e.from_arn = ANY($1)
                      AND e.type = ANY($2)
                      AND e.to_arn != ALL($3)
                    """,
                    frontier_list,
                    edge_type_values,
                    list(visited),
                )

                next_frontier: set[str] = set()
                new_arns: list[str] = []

                for row in rows:
                    to_arn = row["to_arn"]
                    if to_arn not in visited:
                        visited.add(to_arn)
                        next_frontier.add(to_arn)
                        new_arns.append(to_arn)

                if new_arns:
                    node_rows = await pool.fetch(
                        """
                        SELECT arn, type, workspace, package, path, symbol, kind,
                               name, signature, documentation, file_path, line_number,
                               index_hash
                        FROM code_graph_nodes
                        WHERE arn = ANY($1)
                        """,
                        new_arns,
                    )
                    for node_row in node_rows:
                        result_nodes.append(self._row_to_graph_node(node_row))

                current_frontier = next_frontier

            return result_nodes

        except asyncpg.PostgresError as e:
            logger.error(f"Failed to traverse from {start_arn}: {e}")
            raise GraphRepositoryError(f"Failed to traverse graph: {e}") from e

    # Bulk upsert operations (Task 2.5)

    async def upsert_nodes(self, nodes: list[GraphNode]) -> tuple[int, int]:
        """Bulk upsert nodes.

        Inserts new nodes or updates existing nodes based on ARN.
        Uses PostgreSQL's ON CONFLICT clause for efficient upserts.
        The operation is performed within a transaction for atomicity.

        For each node:
        - If the ARN doesn't exist, a new node is created
        - If the ARN exists, the existing node is updated with new values

        Args:
            nodes: List of GraphNode objects to upsert

        Returns:
            Tuple of (created_count, updated_count)

        Raises:
            GraphRepositoryError: If database operation fails

        Example:
            nodes = [
                GraphNode(
                    arn="arn:archon:code:ws/pkg/file.py#func",
                    type=NodeType.CODE,
                    workspace="ws",
                    package="pkg",
                    path="file.py",
                    name="func",
                    kind=SymbolKind.FUNCTION,
                ),
            ]
            created, updated = await repo.upsert_nodes(nodes)
        """
        if not nodes:
            return (0, 0)

        try:
            pool = await self._get_pool()

            created_count = 0
            updated_count = 0

            async with pool.acquire() as conn:
                async with conn.transaction():
                    for node in nodes:
                        result = await conn.fetchrow(
                            """
                            INSERT INTO code_graph_nodes (
                                arn, type, workspace, package, path, symbol, kind,
                                name, signature, documentation, file_path, line_number,
                                index_hash, updated_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
                            ON CONFLICT (arn) DO UPDATE SET
                                type = EXCLUDED.type,
                                workspace = EXCLUDED.workspace,
                                package = EXCLUDED.package,
                                path = EXCLUDED.path,
                                symbol = EXCLUDED.symbol,
                                kind = EXCLUDED.kind,
                                name = EXCLUDED.name,
                                signature = EXCLUDED.signature,
                                documentation = EXCLUDED.documentation,
                                file_path = EXCLUDED.file_path,
                                line_number = EXCLUDED.line_number,
                                index_hash = EXCLUDED.index_hash,
                                updated_at = NOW()
                            RETURNING (xmax = 0) AS inserted
                            """,
                            node.arn,
                            node.type.value,
                            node.workspace,
                            node.package,
                            node.path,
                            node.symbol,
                            node.kind.value if node.kind else None,
                            node.name,
                            node.signature,
                            node.documentation,
                            node.file_path,
                            node.line_number,
                            node.index_hash,
                        )

                        if result["inserted"]:
                            created_count += 1
                        else:
                            updated_count += 1

            logger.info(
                f"Upserted {len(nodes)} nodes: {created_count} created, {updated_count} updated"
            )
            return (created_count, updated_count)

        except asyncpg.PostgresError as e:
            logger.error(f"Failed to upsert nodes: {e}")
            raise GraphRepositoryError(f"Failed to upsert nodes: {e}") from e

    async def upsert_edges(self, edges: list[GraphEdge]) -> int:
        """Bulk upsert edges, skipping any referencing non-existent nodes."""
        if not edges:
            return 0

        try:
            pool = await self._get_pool()
            created_count = 0
            skipped_fk = 0

            async with pool.acquire() as conn:
                for edge in edges:
                    try:
                        result = await conn.execute(
                            """
                            INSERT INTO code_graph_edges (from_arn, to_arn, type)
                            VALUES ($1, $2, $3)
                            ON CONFLICT (from_arn, to_arn, type) DO NOTHING
                            """,
                            edge.from_arn,
                            edge.to_arn,
                            edge.type.value,
                        )
                        if result == "INSERT 0 1":
                            created_count += 1
                    except asyncpg.ForeignKeyViolationError:
                        skipped_fk += 1

            logger.info(
                f"Upserted {len(edges)} edges: {created_count} created, "
                f"{skipped_fk} skipped (missing nodes), "
                f"{len(edges) - created_count - skipped_fk} skipped (duplicates)"
            )
            return created_count

        except asyncpg.PostgresError as e:
            logger.error(f"Failed to upsert edges: {e}")
            raise GraphRepositoryError(f"Failed to upsert edges: {e}") from e

    async def prune_package(
        self,
        package: str,
        keep_arns: list[str],
    ) -> int:
        """Remove nodes not in keep_arns list for a package.

        Removes stale nodes from a package that are no longer present
        in the SCIP index. Edges are automatically removed via CASCADE DELETE
        when their source or target nodes are deleted.

        This is typically called after a sync operation to clean up nodes
        that no longer exist in the source code.

        Args:
            package: Package name to prune
            keep_arns: List of ARNs to keep (nodes with these ARNs will not
                      be deleted, even if they belong to the specified package)

        Returns:
            Number of nodes removed

        Raises:
            GraphRepositoryError: If database operation fails

        Example:
            # After syncing, keep only the nodes that were in the SCIP index
            current_arns = ["arn:archon:code:ws/pkg/file.py#func1", ...]
            removed = await repo.prune_package("pkg", current_arns)
            print(f"Removed {removed} stale nodes")
        """
        try:
            pool = await self._get_pool()

            if keep_arns:
                result = await pool.execute(
                    """
                    DELETE FROM code_graph_nodes
                    WHERE package = $1
                      AND arn != ALL($2)
                    """,
                    package,
                    keep_arns,
                )
            else:
                result = await pool.execute(
                    """
                    DELETE FROM code_graph_nodes
                    WHERE package = $1
                    """,
                    package,
                )

            removed_count = int(result.split()[-1])

            logger.info(
                f"Pruned package '{package}': removed {removed_count} stale nodes"
            )
            return removed_count

        except asyncpg.PostgresError as e:
            logger.error(f"Failed to prune package {package}: {e}")
            raise GraphRepositoryError(f"Failed to prune package: {e}") from e
