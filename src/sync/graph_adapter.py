"""Graph sync adapter for syncing SCIP parse results to the Code Graph.

This module provides an adapter that bridges SCIP indexing output (from
ArchonDocumentationMCPTools) with the Code Graph storage. It transforms
SCIP parse results into GraphQL mutation inputs and calls the syncFromScip
and prunePackage mutations.

The adapter:
- Transforms ScipSymbol objects to SymbolInput GraphQL inputs
- Transforms ScipRelationship objects to RelationshipInput GraphQL inputs
- Calls the syncFromScip mutation to upsert nodes and edges
- Calls the prunePackage mutation to remove stale nodes

Source:
- .kiro/specs/code-graph-storage/design.md (Sync Adapter section)
- .kiro/specs/code-graph-storage/requirements.md (Requirements 6.1-6.4)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from src.graph.service import GraphQLService

logger = logging.getLogger(__name__)


# SCIP symbol kind to GraphQL SymbolKind enum mapping
SCIP_KIND_TO_GRAPHQL: dict[str, str] = {
    "function": "FUNCTION",
    "class": "CLASS",
    "method": "METHOD",
    "variable": "VARIABLE",
    "type": "TYPE",
    "module": "MODULE",
    "file": "FILE",
    "package": "PACKAGE",
}

# SCIP relationship type to GraphQL EdgeType enum mapping
SCIP_TYPE_TO_GRAPHQL: dict[str, str] = {
    "contains": "CONTAINS",
    "references": "REFERENCES",
    "implements": "IMPLEMENTS",
    "extends": "EXTENDS",
    "imports": "IMPORTS",
    "documents": "DOCUMENTS",
}


@dataclass
class ScipSymbol:
    """Represents a symbol from SCIP parse output.

    This dataclass captures the essential information about a code symbol
    extracted from a SCIP index. It is used as input to the GraphSyncAdapter
    for transformation into GraphQL mutation inputs.

    Attributes:
        arn: Archon Resource Name uniquely identifying the symbol
        name: Human-readable name of the symbol
        kind: Symbol kind (function, class, method, variable, type, module, file, package)
        signature: Function/method signature (optional)
        documentation: Documentation string (optional)
        file_path: File path relative to package
        line_number: Line number in source file (optional)

    Example:
        symbol = ScipSymbol(
            arn="arn:archon:code:personal-work/MyPackage/src/main.py#my_function",
            name="my_function",
            kind="function",
            signature="def my_function(x: int) -> str",
            documentation="Converts an integer to a string.",
            file_path="src/main.py",
            line_number=42,
        )
    """

    arn: str
    name: str
    kind: str
    signature: Optional[str]
    documentation: Optional[str]
    file_path: str
    line_number: Optional[int]


@dataclass
class ScipRelationship:
    """Represents a relationship from SCIP parse output.

    This dataclass captures a directed relationship between two symbols
    extracted from a SCIP index. It is used as input to the GraphSyncAdapter
    for transformation into GraphQL mutation inputs.

    Attributes:
        from_arn: Source node ARN
        to_arn: Target node ARN
        type: Relationship type (contains, references, implements, extends, imports, documents)

    Example:
        relationship = ScipRelationship(
            from_arn="arn:archon:code:personal-work/MyPackage/src/main.py#MyClass",
            to_arn="arn:archon:code:personal-work/MyPackage/src/main.py#my_method",
            type="contains",
        )
    """

    from_arn: str
    to_arn: str
    type: str


@dataclass
class ScipParseResult:
    """Result from SCIP parsing.

    This dataclass captures the complete output from parsing a SCIP index
    for a package. It contains all symbols and relationships discovered
    during indexing, along with metadata for change detection.

    Attributes:
        package_path: Path to the package being synced
        workspace: Workspace identifier (e.g., 'personal-work')
        package: Package/repository name
        symbols: List of symbols discovered in the package
        relationships: List of relationships between symbols
        index_hash: SHA-256 hash of SCIP index content for change detection

    Example:
        result = ScipParseResult(
            package_path="/home/user/projects/MyPackage",
            workspace="personal-work",
            package="MyPackage",
            symbols=[...],
            relationships=[...],
            index_hash="abc123...",
        )
    """

    package_path: str
    workspace: str
    package: str
    symbols: list[ScipSymbol]
    relationships: list[ScipRelationship]
    index_hash: str


@dataclass
class SyncResult:
    """Result of a sync operation.

    This dataclass captures the outcome of syncing SCIP parse results
    to the Code Graph, including counts of created, updated, and removed
    nodes and edges.

    Attributes:
        nodes_created: Count of new nodes inserted
        nodes_updated: Count of existing nodes updated
        edges_created: Count of new edges inserted
        edges_removed: Count of stale edges removed

    Example:
        result = SyncResult(
            nodes_created=10,
            nodes_updated=5,
            edges_created=20,
            edges_removed=3,
        )
    """

    nodes_created: int
    nodes_updated: int
    edges_created: int
    edges_removed: int


class GraphSyncAdapterError(Exception):
    """Raised when graph sync adapter operations fail."""

    pass


class GraphSyncAdapter:
    """Adapter for syncing SCIP parse results to the Code Graph.

    The GraphSyncAdapter bridges the SCIP indexing output (from
    ArchonDocumentationMCPTools) with the Code Graph storage. It transforms
    SCIP parse results into GraphQL mutation inputs and calls the appropriate
    mutations to sync the data.

    The adapter performs two main operations:
    1. sync(): Transforms SCIP symbols and relationships into GraphQL inputs
       and calls the syncFromScip mutation to upsert nodes and edges.
    2. prune(): Calls the prunePackage mutation to remove stale nodes that
       are no longer present in the SCIP index.

    Usage:
        service = GraphQLService(config)
        adapter = GraphSyncAdapter(service)

        # Sync SCIP parse results
        result = await adapter.sync(parse_result)
        print(f"Created {result.nodes_created} nodes")

        # Prune stale nodes
        removed = await adapter.prune("MyPackage", current_arns)
        print(f"Removed {removed} stale nodes")

    Validates: Requirements 6.1, 6.2, 6.3, 6.4

    Source:
    - .kiro/specs/code-graph-storage/design.md (Sync Adapter section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirements 6.1-6.4)
    """

    def __init__(self, graph_service: GraphQLService) -> None:
        """Initialize with a GraphQL service instance.

        Args:
            graph_service: GraphQLService instance for executing mutations
        """
        self._service = graph_service

    def _transform_symbol_to_input(
        self,
        symbol: ScipSymbol,
        workspace: str,
        package: str,
    ) -> dict:
        """Transform a ScipSymbol to a SymbolInput GraphQL input.

        Converts a ScipSymbol dataclass into a dictionary suitable for
        use as a SymbolInput in the syncFromScip GraphQL mutation.

        Args:
            symbol: ScipSymbol to transform
            workspace: Workspace identifier
            package: Package name

        Returns:
            Dictionary with SymbolInput fields

        Raises:
            GraphSyncAdapterError: If symbol kind is invalid
        """
        graphql_kind = SCIP_KIND_TO_GRAPHQL.get(symbol.kind.lower())
        if graphql_kind is None:
            raise GraphSyncAdapterError(
                f"Invalid symbol kind '{symbol.kind}'. "
                f"Valid kinds: {list(SCIP_KIND_TO_GRAPHQL.keys())}"
            )

        return {
            "arn": symbol.arn,
            "type": "CODE",
            "workspace": workspace,
            "package": package,
            "path": symbol.file_path,
            "symbol": symbol.name,
            "kind": graphql_kind,
            "name": symbol.name,
            "signature": symbol.signature,
            "documentation": symbol.documentation,
            "filePath": symbol.file_path,
            "lineNumber": symbol.line_number,
        }

    def _transform_relationship_to_input(
        self,
        relationship: ScipRelationship,
    ) -> dict:
        """Transform a ScipRelationship to a RelationshipInput GraphQL input.

        Converts a ScipRelationship dataclass into a dictionary suitable for
        use as a RelationshipInput in the syncFromScip GraphQL mutation.

        Args:
            relationship: ScipRelationship to transform

        Returns:
            Dictionary with RelationshipInput fields

        Raises:
            GraphSyncAdapterError: If relationship type is invalid
        """
        graphql_type = SCIP_TYPE_TO_GRAPHQL.get(relationship.type.lower())
        if graphql_type is None:
            raise GraphSyncAdapterError(
                f"Invalid relationship type '{relationship.type}'. "
                f"Valid types: {list(SCIP_TYPE_TO_GRAPHQL.keys())}"
            )

        return {
            "fromArn": relationship.from_arn,
            "toArn": relationship.to_arn,
            "type": graphql_type,
        }

    async def sync(self, parse_result: ScipParseResult) -> SyncResult:
        """Sync SCIP parse result to the Code Graph.

        Transforms SCIP symbols and relationships into GraphQL inputs
        and calls the syncFromScip mutation to upsert nodes and edges.

        The sync operation:
        1. Transforms all ScipSymbol objects to SymbolInput dictionaries
        2. Transforms all ScipRelationship objects to RelationshipInput dictionaries
        3. Calls the syncFromScip GraphQL mutation with the transformed data
        4. Returns a SyncResult with counts of created/updated/removed items

        Args:
            parse_result: ScipParseResult containing symbols and relationships

        Returns:
            SyncResult with counts of nodes/edges created, updated, and removed

        Raises:
            GraphSyncAdapterError: If transformation or mutation fails

        Example:
            result = await adapter.sync(parse_result)
            print(f"Synced: {result.nodes_created} created, {result.nodes_updated} updated")

        Validates: Requirements 6.1, 6.2, 6.3, 6.4
        """
        logger.info(
            f"Syncing SCIP parse result for package '{parse_result.package}' "
            f"with {len(parse_result.symbols)} symbols and "
            f"{len(parse_result.relationships)} relationships"
        )

        symbol_inputs = [
            self._transform_symbol_to_input(
                symbol,
                parse_result.workspace,
                parse_result.package,
            )
            for symbol in parse_result.symbols
        ]

        relationship_inputs = [
            self._transform_relationship_to_input(rel)
            for rel in parse_result.relationships
        ]

        mutation = """
            mutation SyncFromScip(
                $packagePath: String!
                $symbols: [SymbolInput!]!
                $relationships: [RelationshipInput!]!
                $indexHash: String!
            ) {
                syncFromScip(
                    packagePath: $packagePath
                    symbols: $symbols
                    relationships: $relationships
                    indexHash: $indexHash
                ) {
                    nodesCreated
                    nodesUpdated
                    edgesCreated
                    edgesRemoved
                }
            }
        """

        variables = {
            "packagePath": parse_result.package_path,
            "symbols": symbol_inputs,
            "relationships": relationship_inputs,
            "indexHash": parse_result.index_hash,
        }

        result = await self._service.execute(mutation, variables)

        if result.get("errors"):
            error_messages = [e.get("message", str(e)) for e in result["errors"]]
            logger.error(f"GraphQL errors during sync: {error_messages}")
            raise GraphSyncAdapterError(
                f"Failed to sync SCIP parse result: {error_messages}"
            )

        sync_data = result["data"]["syncFromScip"]

        sync_result = SyncResult(
            nodes_created=sync_data["nodesCreated"],
            nodes_updated=sync_data["nodesUpdated"],
            edges_created=sync_data["edgesCreated"],
            edges_removed=sync_data["edgesRemoved"],
        )

        logger.info(
            f"Sync completed for package '{parse_result.package}': "
            f"{sync_result.nodes_created} nodes created, "
            f"{sync_result.nodes_updated} nodes updated, "
            f"{sync_result.edges_created} edges created, "
            f"{sync_result.edges_removed} edges removed"
        )

        return sync_result

    async def prune(self, package: str, keep_arns: list[str]) -> int:
        """Prune stale nodes from a package.

        Calls the prunePackage mutation to remove nodes that are no longer
        present in the SCIP index. This is typically called after a sync
        operation to clean up nodes that no longer exist in the source code.

        Args:
            package: Package name to prune
            keep_arns: List of ARNs to keep (nodes with these ARNs will not
                      be deleted)

        Returns:
            Count of nodes removed

        Raises:
            GraphSyncAdapterError: If mutation fails

        Example:
            # After syncing, prune nodes not in the current index
            current_arns = [symbol.arn for symbol in parse_result.symbols]
            removed = await adapter.prune("MyPackage", current_arns)
            print(f"Removed {removed} stale nodes")

        Validates: Requirements 6.5, 6.6
        """
        logger.info(
            f"Pruning package '{package}', keeping {len(keep_arns)} ARNs"
        )

        mutation = """
            mutation PrunePackage($package: String!, $keepArns: [ID!]!) {
                prunePackage(package: $package, keepArns: $keepArns)
            }
        """

        variables = {
            "package": package,
            "keepArns": keep_arns,
        }

        result = await self._service.execute(mutation, variables)

        if result.get("errors"):
            error_messages = [e.get("message", str(e)) for e in result["errors"]]
            logger.error(f"GraphQL errors during prune: {error_messages}")
            raise GraphSyncAdapterError(
                f"Failed to prune package '{package}': {error_messages}"
            )

        removed_count = result["data"]["prunePackage"]

        logger.info(
            f"Prune completed for package '{package}': {removed_count} nodes removed"
        )

        return removed_count
