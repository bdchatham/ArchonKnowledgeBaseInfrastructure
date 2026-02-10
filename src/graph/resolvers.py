"""GraphQL query and mutation resolvers for the Code Graph.

This module implements the GraphQL resolvers that fetch and modify data in
the Code Graph using the GraphRepository. Each resolver maps a GraphQL
operation to the corresponding repository method.

The resolvers handle:
- Converting repository data types to GraphQL types
- Mapping enum values between repository and GraphQL layers
- Error handling and null safety
- Bulk upsert operations for sync from SCIP parse results

Source:
- .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
- .kiro/specs/code-graph-storage/requirements.md (Requirements 5.1-5.6, 6.1-6.6)
"""

import logging
from typing import Optional

import strawberry

from src.graph.models import EdgeType as ModelEdgeType
from src.graph.models import NodeType as ModelNodeType
from src.graph.models import SymbolKind as ModelSymbolKind
from src.graph.repository import GraphEdge as RepoGraphEdge
from src.graph.repository import GraphNode as RepoGraphNode
from src.graph.repository import GraphRepository
from src.graph.schema import (
    EdgeType,
    Node,
    NodeType,
    RelationshipInput,
    SymbolInput,
    SymbolKind,
    SyncResult,
    _repo_node_to_graphql,
)

logger = logging.getLogger(__name__)


def _map_symbol_kind_to_model(kind: Optional[SymbolKind]) -> Optional[ModelSymbolKind]:
    """Map GraphQL SymbolKind enum to repository model enum.

    Args:
        kind: GraphQL SymbolKind enum value or None

    Returns:
        Corresponding ModelSymbolKind enum value or None
    """
    if kind is None:
        return None

    mapping = {
        SymbolKind.FUNCTION: ModelSymbolKind.FUNCTION,
        SymbolKind.CLASS: ModelSymbolKind.CLASS,
        SymbolKind.METHOD: ModelSymbolKind.METHOD,
        SymbolKind.VARIABLE: ModelSymbolKind.VARIABLE,
        SymbolKind.TYPE: ModelSymbolKind.TYPE,
        SymbolKind.MODULE: ModelSymbolKind.MODULE,
        SymbolKind.FILE: ModelSymbolKind.FILE,
        SymbolKind.PACKAGE: ModelSymbolKind.PACKAGE,
    }
    return mapping.get(kind)


def _map_node_type_to_model(node_type: NodeType) -> ModelNodeType:
    """Map GraphQL NodeType enum to repository model enum.

    Args:
        node_type: GraphQL NodeType enum value

    Returns:
        Corresponding ModelNodeType enum value
    """
    mapping = {
        NodeType.CODE: ModelNodeType.CODE,
        NodeType.DOC: ModelNodeType.DOC,
        NodeType.K8S: ModelNodeType.K8S,
        NodeType.INFRA: ModelNodeType.INFRA,
    }
    return mapping[node_type]


def _map_edge_type_to_model(edge_type: EdgeType) -> ModelEdgeType:
    """Map GraphQL EdgeType enum to repository model enum.

    Args:
        edge_type: GraphQL EdgeType enum value

    Returns:
        Corresponding ModelEdgeType enum value
    """
    mapping = {
        EdgeType.CONTAINS: ModelEdgeType.CONTAINS,
        EdgeType.REFERENCES: ModelEdgeType.REFERENCES,
        EdgeType.IMPLEMENTS: ModelEdgeType.IMPLEMENTS,
        EdgeType.EXTENDS: ModelEdgeType.EXTENDS,
        EdgeType.IMPORTS: ModelEdgeType.IMPORTS,
        EdgeType.DOCUMENTS: ModelEdgeType.DOCUMENTS,
    }
    return mapping[edge_type]


def _map_edge_types_to_model(edge_types: list[EdgeType]) -> list[ModelEdgeType]:
    """Map GraphQL EdgeType enums to repository model enums.

    Args:
        edge_types: List of GraphQL EdgeType enum values

    Returns:
        List of corresponding ModelEdgeType enum values
    """
    mapping = {
        EdgeType.CONTAINS: ModelEdgeType.CONTAINS,
        EdgeType.REFERENCES: ModelEdgeType.REFERENCES,
        EdgeType.IMPLEMENTS: ModelEdgeType.IMPLEMENTS,
        EdgeType.EXTENDS: ModelEdgeType.EXTENDS,
        EdgeType.IMPORTS: ModelEdgeType.IMPORTS,
        EdgeType.DOCUMENTS: ModelEdgeType.DOCUMENTS,
    }
    return [mapping[et] for et in edge_types if et in mapping]


@strawberry.type
class Query:
    """GraphQL Query type for Code Graph operations.

    Provides query operations for retrieving nodes from the Code Graph.
    All queries use the GraphRepository for data access.

    Query Operations:
        - node(arn): Direct ARN lookup
        - searchNodes(name, kind, package, limit): Name-based search
        - findReferences(arn): Find all nodes referencing a symbol
        - symbolsInFile(path, package): List symbols in a file
        - symbolsInPackage(package, kind): List symbols in a package
        - traverse(startArn, edgeTypes, depth): Relationship traversal

    Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirement 5)
    """

    @strawberry.field
    async def node(
        self,
        info: strawberry.Info,
        arn: strawberry.ID,
    ) -> Optional[Node]:
        """Get a node by ARN.

        Performs a direct lookup of a node by its Archon Resource Name.
        Returns None if no node exists with the specified ARN.

        Args:
            info: Strawberry context info containing the repository
            arn: Archon Resource Name of the node to retrieve

        Returns:
            Node if found, None otherwise

        Validates: Requirement 5.1

        Example GraphQL Query:
            query {
              node(arn: "arn:archon:code:personal-work/pkg/file.py#func") {
                name
                kind
                documentation
              }
            }
        """
        repository: GraphRepository = info.context["repository"]
        repo_node = await repository.get_node(str(arn))

        if repo_node is None:
            return None

        return _repo_node_to_graphql(repo_node)

    @strawberry.field
    async def search_nodes(
        self,
        info: strawberry.Info,
        name: str,
        kind: Optional[SymbolKind] = None,
        package: Optional[str] = None,
        limit: int = 20,
    ) -> list[Node]:
        """Search nodes by name with optional filters.

        Performs a case-insensitive search for nodes matching the name
        pattern. Results can be filtered by symbol kind and/or package.

        Args:
            info: Strawberry context info containing the repository
            name: Name pattern to search for (case-insensitive)
            kind: Optional symbol kind filter
            package: Optional package filter
            limit: Maximum number of results (default: 20)

        Returns:
            List of matching Node objects

        Validates: Requirement 5.2

        Example GraphQL Query:
            query {
              searchNodes(name: "Repository", kind: CLASS, limit: 10) {
                arn
                name
                package
              }
            }
        """
        repository: GraphRepository = info.context["repository"]
        model_kind = _map_symbol_kind_to_model(kind)

        repo_nodes = await repository.search_nodes(
            name=name,
            kind=model_kind,
            package=package,
            limit=limit,
        )

        return [_repo_node_to_graphql(node) for node in repo_nodes]

    @strawberry.field
    async def find_references(
        self,
        info: strawberry.Info,
        arn: strawberry.ID,
    ) -> list[Node]:
        """Find all nodes that reference the specified symbol.

        Retrieves all nodes that have a REFERENCES edge pointing to
        the node with the specified ARN. This is useful for finding
        all callers of a function or all usages of a class.

        Args:
            info: Strawberry context info containing the repository
            arn: ARN of the symbol to find references for

        Returns:
            List of Node objects that reference the specified symbol

        Validates: Requirement 5.3

        Example GraphQL Query:
            query {
              findReferences(arn: "arn:archon:code:ws/pkg/file.py#func") {
                arn
                name
                path
              }
            }
        """
        repository: GraphRepository = info.context["repository"]

        edges = await repository.get_edges_to(
            arn=str(arn),
            edge_type=ModelEdgeType.REFERENCES,
        )

        result_nodes: list[Node] = []
        for edge in edges:
            repo_node = await repository.get_node(edge.from_arn)
            if repo_node is not None:
                result_nodes.append(_repo_node_to_graphql(repo_node))

        return result_nodes

    @strawberry.field
    async def symbols_in_file(
        self,
        info: strawberry.Info,
        path: str,
        package: str,
    ) -> list[Node]:
        """Get all symbols defined in a file.

        Retrieves all code symbols (functions, classes, methods, etc.)
        defined in the specified file. Results are ordered by line number.

        Args:
            info: Strawberry context info containing the repository
            path: File path relative to package
            package: Package name

        Returns:
            List of Node objects for symbols in the file

        Validates: Requirement 5.4

        Example GraphQL Query:
            query {
              symbolsInFile(path: "src/graph/schema.py", package: "ArchonKnowledgeBaseInfrastructure") {
                name
                kind
                lineNumber
              }
            }
        """
        repository: GraphRepository = info.context["repository"]

        repo_nodes = await repository.get_symbols_in_file(
            path=path,
            package=package,
        )

        return [_repo_node_to_graphql(node) for node in repo_nodes]

    @strawberry.field
    async def symbols_in_package(
        self,
        info: strawberry.Info,
        package: str,
        kind: Optional[SymbolKind] = None,
    ) -> list[Node]:
        """Get all symbols in a package.

        Retrieves all code symbols in the specified package, optionally
        filtered by symbol kind. Results are ordered by path and name.

        Args:
            info: Strawberry context info containing the repository
            package: Package name
            kind: Optional symbol kind filter

        Returns:
            List of Node objects for symbols in the package

        Validates: Requirement 5.5

        Example GraphQL Query:
            query {
              symbolsInPackage(package: "ArchonKnowledgeBaseInfrastructure", kind: CLASS) {
                arn
                name
                path
              }
            }
        """
        repository: GraphRepository = info.context["repository"]
        model_kind = _map_symbol_kind_to_model(kind)

        repo_nodes = await repository.get_symbols_in_package(
            package=package,
            kind=model_kind,
        )

        return [_repo_node_to_graphql(node) for node in repo_nodes]

    @strawberry.field
    async def traverse(
        self,
        info: strawberry.Info,
        start_arn: strawberry.ID,
        edge_types: list[EdgeType],
        depth: int = 1,
    ) -> list[Node]:
        """Traverse relationships from a starting node.

        Performs a breadth-first traversal from the starting node,
        following edges of the specified types up to the given depth.
        The traversal handles cycles by tracking visited nodes.

        Args:
            info: Strawberry context info containing the repository
            start_arn: Starting node ARN
            edge_types: List of edge types to follow
            depth: Maximum traversal depth (default: 1)

        Returns:
            List of Node objects reachable from the start node
            (does not include the start node itself)

        Validates: Requirement 5.6

        Example GraphQL Query:
            query {
              traverse(
                startArn: "arn:archon:code:ws/pkg/file.py#Class"
                edgeTypes: [CONTAINS]
                depth: 1
              ) {
                name
                kind
              }
            }
        """
        repository: GraphRepository = info.context["repository"]
        model_edge_types = _map_edge_types_to_model(edge_types)

        repo_nodes = await repository.traverse(
            start_arn=str(start_arn),
            edge_types=model_edge_types,
            depth=depth,
        )

        return [_repo_node_to_graphql(node) for node in repo_nodes]


def _symbol_input_to_repo_node(symbol: SymbolInput, index_hash: str) -> RepoGraphNode:
    """Convert a GraphQL SymbolInput to a repository GraphNode.

    Maps the GraphQL input type to the repository data transfer object,
    converting enum values and setting the index hash for change detection.

    Args:
        symbol: GraphQL SymbolInput from mutation input
        index_hash: SCIP index hash for change detection

    Returns:
        Repository GraphNode object ready for upsert
    """
    return RepoGraphNode(
        arn=str(symbol.arn),
        type=_map_node_type_to_model(symbol.type),
        workspace=symbol.workspace,
        package=symbol.package,
        path=symbol.path,
        name=symbol.name,
        symbol=symbol.symbol,
        kind=_map_symbol_kind_to_model(symbol.kind),
        signature=symbol.signature,
        documentation=symbol.documentation,
        file_path=symbol.file_path,
        line_number=symbol.line_number,
        index_hash=index_hash,
    )


def _relationship_input_to_repo_edge(relationship: RelationshipInput) -> RepoGraphEdge:
    """Convert a GraphQL RelationshipInput to a repository GraphEdge.

    Maps the GraphQL input type to the repository data transfer object,
    converting enum values.

    Args:
        relationship: GraphQL RelationshipInput from mutation input

    Returns:
        Repository GraphEdge object ready for upsert
    """
    return RepoGraphEdge(
        from_arn=str(relationship.from_arn),
        to_arn=str(relationship.to_arn),
        type=_map_edge_type_to_model(relationship.type),
    )


@strawberry.type
class Mutation:
    """GraphQL Mutation type for Code Graph operations.

    Provides mutation operations for modifying the Code Graph.
    All mutations use the GraphRepository for data access.

    Mutation Operations:
        - syncFromScip(packagePath, symbols, relationships, indexHash):
          Bulk upsert nodes and edges from SCIP parse results
        - prunePackage(package, keepArns):
          Remove stale nodes not in the keepArns list

    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirement 6)
    """

    @strawberry.mutation
    async def sync_from_scip(
        self,
        info: strawberry.Info,
        package_path: str,
        symbols: list[SymbolInput],
        relationships: list[RelationshipInput],
        index_hash: str,
    ) -> SyncResult:
        """Sync nodes and edges from SCIP parse results.

        Performs a bulk upsert of nodes and edges from SCIP parse results.
        Nodes are inserted or updated based on ARN conflict. Edges are
        inserted or skipped if they already exist.

        The operation is performed within database transactions for atomicity.
        The index_hash is stored on each node for change detection in
        subsequent syncs.

        Args:
            info: Strawberry context info containing the repository
            package_path: Path to the package being synced
            symbols: Array of symbol definitions to upsert
            relationships: Array of relationships to upsert
            index_hash: SCIP index hash for change detection

        Returns:
            SyncResult with counts of nodes created/updated and edges
            created/removed

        Validates: Requirements 6.1, 6.2, 6.3, 6.4

        Example GraphQL Mutation:
            mutation {
              syncFromScip(
                packagePath: "ArchonKnowledgeBaseInfrastructure"
                symbols: [
                  {
                    arn: "arn:archon:code:ws/pkg/file.py#func"
                    type: CODE
                    workspace: "ws"
                    package: "pkg"
                    path: "file.py"
                    kind: FUNCTION
                    name: "func"
                  }
                ]
                relationships: [
                  {
                    fromArn: "arn:archon:code:ws/pkg/file.py#Class"
                    toArn: "arn:archon:code:ws/pkg/file.py#method"
                    type: CONTAINS
                  }
                ]
                indexHash: "abc123"
              ) {
                nodesCreated
                nodesUpdated
                edgesCreated
                edgesRemoved
              }
            }
        """
        repository: GraphRepository = info.context["repository"]

        repo_nodes = [
            _symbol_input_to_repo_node(symbol, index_hash) for symbol in symbols
        ]
        nodes_created, nodes_updated = await repository.upsert_nodes(repo_nodes)

        repo_edges = [
            _relationship_input_to_repo_edge(relationship)
            for relationship in relationships
        ]
        edges_created = await repository.upsert_edges(repo_edges)

        logger.info(
            f"syncFromScip completed for {package_path}: "
            f"{nodes_created} nodes created, {nodes_updated} nodes updated, "
            f"{edges_created} edges created"
        )

        return SyncResult(
            nodes_created=nodes_created,
            nodes_updated=nodes_updated,
            edges_created=edges_created,
            edges_removed=0,
        )

    @strawberry.mutation
    async def prune_package(
        self,
        info: strawberry.Info,
        package: str,
        keep_arns: list[strawberry.ID],
    ) -> int:
        """Remove stale nodes not in the keepArns list.

        Removes nodes from a package that are no longer present in the
        SCIP index. Edges are automatically removed via CASCADE DELETE
        when their source or target nodes are deleted.

        This is typically called after a sync operation to clean up nodes
        that no longer exist in the source code.

        Args:
            info: Strawberry context info containing the repository
            package: Package name to prune
            keep_arns: List of ARNs to keep (nodes with these ARNs will
                      not be deleted)

        Returns:
            Count of nodes removed

        Validates: Requirements 6.5, 6.6

        Example GraphQL Mutation:
            mutation {
              prunePackage(
                package: "ArchonKnowledgeBaseInfrastructure"
                keepArns: [
                  "arn:archon:code:ws/pkg/file.py#func1"
                  "arn:archon:code:ws/pkg/file.py#func2"
                ]
              )
            }
        """
        repository: GraphRepository = info.context["repository"]

        keep_arn_strings = [str(arn) for arn in keep_arns]
        removed_count = await repository.prune_package(package, keep_arn_strings)

        logger.info(
            f"prunePackage completed for {package}: {removed_count} nodes removed"
        )

        return removed_count
