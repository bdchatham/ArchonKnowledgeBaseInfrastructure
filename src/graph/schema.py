"""GraphQL schema definitions for the Code Graph.

This module defines the GraphQL types, enums, and schema for querying
the Code Graph. It uses strawberry-graphql for type-safe GraphQL
implementation.

The schema exposes:
- Node type with all properties and relationship traversal fields
- NodeType, SymbolKind, EdgeType enums
- Query operations (to be implemented in resolvers.py)
- Mutation operations (to be implemented in resolvers.py)

Source:
- .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
- .kiro/specs/code-graph-storage/requirements.md (Requirements 4.1, 4.3, 4.4, 4.5)
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

import strawberry

if TYPE_CHECKING:
    from src.graph.repository import GraphRepository


@strawberry.enum
class NodeType(Enum):
    """Resource type classification for graph nodes.

    Defines the type of resource a node represents in the Code Graph.

    Values:
        CODE: Source code symbols (functions, classes, methods, etc.)
        DOC: Documentation files (.archon.md, README, etc.)
        K8S: Kubernetes resources (deployments, services, etc.)
        INFRA: Infrastructure definitions (CDK, Terraform, etc.)

    Validates: Requirement 4.3
    """

    CODE = "code"
    DOC = "doc"
    K8S = "k8s"
    INFRA = "infra"


@strawberry.enum
class SymbolKind(Enum):
    """Symbol kind classification for code nodes.

    Defines the kind of code symbol a node represents.

    Values:
        FUNCTION: Standalone function definition
        CLASS: Class definition
        METHOD: Method within a class
        VARIABLE: Variable or constant definition
        TYPE: Type alias or interface definition
        MODULE: Module or namespace
        FILE: Source file
        PACKAGE: Package or repository

    Validates: Requirement 4.4
    """

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    TYPE = "type"
    MODULE = "module"
    FILE = "file"
    PACKAGE = "package"


@strawberry.enum
class EdgeType(Enum):
    """Relationship type classification for graph edges.

    Defines the type of relationship between two nodes.

    Values:
        CONTAINS: Parent contains child (e.g., class contains method)
        REFERENCES: Source references target (e.g., function calls another)
        IMPLEMENTS: Source implements target interface/type
        EXTENDS: Source extends target class/type
        IMPORTS: Source imports target module
        DOCUMENTS: Documentation node documents code node

    Validates: Requirement 4.5
    """

    CONTAINS = "contains"
    REFERENCES = "references"
    IMPLEMENTS = "implements"
    EXTENDS = "extends"
    IMPORTS = "imports"
    DOCUMENTS = "documents"


@strawberry.type
class Node:
    """GraphQL type representing a node in the Code Graph.

    A node can represent a code symbol (function, class, method, etc.),
    a file, a package, or documentation. Each node is uniquely identified
    by its ARN (Archon Resource Name).

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    Example: arn:archon:code:personal-work/ArchonKnowledgeBaseInfrastructure/src/graph/schema.py#Node

    Attributes:
        arn: Archon Resource Name - unique identifier for the node
        type: Resource type (CODE, DOC, K8S, INFRA)
        workspace: Workspace identifier (e.g., 'personal-work')
        package: Package/repository name
        path: File path relative to package
        symbol: Symbol name within the file (nullable)
        kind: Symbol kind (FUNCTION, CLASS, METHOD, etc.) (nullable)
        name: Human-readable name
        signature: Function/method signature (nullable)
        documentation: Documentation string (nullable)
        file_path: Absolute file path for resolution (nullable)
        line_number: Line number in source file (nullable)

    Relationship Fields:
        contains: Nodes contained by this node
        contained_by: Node that contains this node
        references: Nodes this node references
        referenced_by: Nodes that reference this node
        implements: Interfaces/types this node implements
        implemented_by: Nodes that implement this node
        extends: Types this node extends
        extended_by: Nodes that extend this node
        imports: Modules this node imports
        imported_by: Nodes that import this node
        documentation_node: Link to associated .archon.md doc node

    Validates: Requirements 4.1, 4.2
    """

    # Primary identifier
    arn: strawberry.ID

    # Resource type classification
    type: NodeType

    # Location identifiers
    workspace: str
    package: str
    path: str

    # Symbol information (nullable)
    symbol: Optional[str] = None
    kind: Optional[SymbolKind] = None

    # Human-readable name
    name: str

    # Optional metadata
    signature: Optional[str] = None
    documentation: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None

    @strawberry.field
    async def contains(self, info: strawberry.Info) -> list["Node"]:
        """Nodes contained by this node (e.g., methods in a class).

        Traverses CONTAINS edges where this node is the source.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects contained by this node
        """
        return await _resolve_outgoing_edges(
            info, str(self.arn), "contains"
        )

    @strawberry.field
    async def contained_by(self, info: strawberry.Info) -> Optional["Node"]:
        """Node that contains this node (e.g., class containing a method).

        Traverses CONTAINS edges where this node is the target.
        Returns the first containing node, or None if not contained.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            Node that contains this node, or None
        """
        nodes = await _resolve_incoming_edges(
            info, str(self.arn), "contains"
        )
        return nodes[0] if nodes else None

    @strawberry.field
    async def references(self, info: strawberry.Info) -> list["Node"]:
        """Nodes this node references (e.g., functions called by this function).

        Traverses REFERENCES edges where this node is the source.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects referenced by this node
        """
        return await _resolve_outgoing_edges(
            info, str(self.arn), "references"
        )

    @strawberry.field
    async def referenced_by(self, info: strawberry.Info) -> list["Node"]:
        """Nodes that reference this node (e.g., callers of this function).

        Traverses REFERENCES edges where this node is the target.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that reference this node
        """
        return await _resolve_incoming_edges(
            info, str(self.arn), "references"
        )

    @strawberry.field
    async def implements(self, info: strawberry.Info) -> list["Node"]:
        """Interfaces/types this node implements.

        Traverses IMPLEMENTS edges where this node is the source.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that this node implements
        """
        return await _resolve_outgoing_edges(
            info, str(self.arn), "implements"
        )

    @strawberry.field
    async def implemented_by(self, info: strawberry.Info) -> list["Node"]:
        """Nodes that implement this node (e.g., classes implementing an interface).

        Traverses IMPLEMENTS edges where this node is the target.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that implement this node
        """
        return await _resolve_incoming_edges(
            info, str(self.arn), "implements"
        )

    @strawberry.field
    async def extends(self, info: strawberry.Info) -> list["Node"]:
        """Types this node extends (e.g., parent classes).

        Traverses EXTENDS edges where this node is the source.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that this node extends
        """
        return await _resolve_outgoing_edges(
            info, str(self.arn), "extends"
        )

    @strawberry.field
    async def extended_by(self, info: strawberry.Info) -> list["Node"]:
        """Nodes that extend this node (e.g., child classes).

        Traverses EXTENDS edges where this node is the target.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that extend this node
        """
        return await _resolve_incoming_edges(
            info, str(self.arn), "extends"
        )

    @strawberry.field
    async def imports(self, info: strawberry.Info) -> list["Node"]:
        """Modules this node imports.

        Traverses IMPORTS edges where this node is the source.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that this node imports
        """
        return await _resolve_outgoing_edges(
            info, str(self.arn), "imports"
        )

    @strawberry.field
    async def imported_by(self, info: strawberry.Info) -> list["Node"]:
        """Nodes that import this node.

        Traverses IMPORTS edges where this node is the target.

        Args:
            info: Strawberry context info containing the repository

        Returns:
            List of Node objects that import this node
        """
        return await _resolve_incoming_edges(
            info, str(self.arn), "imports"
        )

    @strawberry.field
    async def documentation_node(self, info: strawberry.Info) -> Optional["Node"]:
        """Link to associated .archon.md documentation node.

        Traverses DOCUMENTS edges where this node is the target
        (i.e., finds the doc node that documents this code node).

        Args:
            info: Strawberry context info containing the repository

        Returns:
            Documentation Node that documents this node, or None
        """
        nodes = await _resolve_incoming_edges(
            info, str(self.arn), "documents"
        )
        return nodes[0] if nodes else None


async def _resolve_outgoing_edges(
    info: strawberry.Info,
    arn: str,
    edge_type_value: str,
) -> list[Node]:
    """Resolve outgoing edges from a node to connected nodes.

    Helper function for Node relationship field resolvers that traverse
    outgoing edges (where this node is the source).

    Args:
        info: Strawberry context info containing the repository
        arn: ARN of the source node
        edge_type_value: Edge type value string (e.g., "contains", "references")

    Returns:
        List of Node objects connected via outgoing edges of the specified type
    """
    from src.graph.models import EdgeType as ModelEdgeType

    repository: GraphRepository = info.context["repository"]
    model_edge_type = ModelEdgeType(edge_type_value)

    edges = await repository.get_edges_from(arn, model_edge_type)

    result_nodes: list[Node] = []
    for edge in edges:
        repo_node = await repository.get_node(edge.to_arn)
        if repo_node is not None:
            result_nodes.append(_repo_node_to_graphql(repo_node))

    return result_nodes


async def _resolve_incoming_edges(
    info: strawberry.Info,
    arn: str,
    edge_type_value: str,
) -> list[Node]:
    """Resolve incoming edges to a node from connected nodes.

    Helper function for Node relationship field resolvers that traverse
    incoming edges (where this node is the target).

    Args:
        info: Strawberry context info containing the repository
        arn: ARN of the target node
        edge_type_value: Edge type value string (e.g., "contains", "references")

    Returns:
        List of Node objects connected via incoming edges of the specified type
    """
    from src.graph.models import EdgeType as ModelEdgeType

    repository: GraphRepository = info.context["repository"]
    model_edge_type = ModelEdgeType(edge_type_value)

    edges = await repository.get_edges_to(arn, model_edge_type)

    result_nodes: list[Node] = []
    for edge in edges:
        repo_node = await repository.get_node(edge.from_arn)
        if repo_node is not None:
            result_nodes.append(_repo_node_to_graphql(repo_node))

    return result_nodes


def _repo_node_to_graphql(repo_node) -> Node:
    """Convert a repository GraphNode to a GraphQL Node.

    Maps the repository data transfer object to the GraphQL type,
    converting enum values and handling nullable fields.

    Args:
        repo_node: GraphNode from the repository layer

    Returns:
        GraphQL Node type with all properties populated
    """
    node_type_mapping = {
        "code": NodeType.CODE,
        "doc": NodeType.DOC,
        "k8s": NodeType.K8S,
        "infra": NodeType.INFRA,
    }

    symbol_kind_mapping = {
        "function": SymbolKind.FUNCTION,
        "class": SymbolKind.CLASS,
        "method": SymbolKind.METHOD,
        "variable": SymbolKind.VARIABLE,
        "type": SymbolKind.TYPE,
        "module": SymbolKind.MODULE,
        "file": SymbolKind.FILE,
        "package": SymbolKind.PACKAGE,
    }

    graphql_type = node_type_mapping.get(repo_node.type.value, NodeType.CODE)

    graphql_kind = None
    if repo_node.kind is not None:
        graphql_kind = symbol_kind_mapping.get(repo_node.kind.value)

    return Node(
        arn=strawberry.ID(repo_node.arn),
        type=graphql_type,
        workspace=repo_node.workspace,
        package=repo_node.package,
        path=repo_node.path,
        symbol=repo_node.symbol,
        kind=graphql_kind,
        name=repo_node.name,
        signature=repo_node.signature,
        documentation=repo_node.documentation,
        file_path=repo_node.file_path,
        line_number=repo_node.line_number,
    )


@strawberry.input
class SymbolInput:
    """GraphQL input type for symbol definitions in sync operations.

    Used by the `syncFromScip` mutation to accept symbol data from SCIP
    parse results. Each symbol represents a code element (function, class,
    method, etc.) to be upserted into the Code Graph.

    Attributes:
        arn: Archon Resource Name - unique identifier for the symbol
        type: Resource type (CODE, DOC, K8S, INFRA)
        workspace: Workspace identifier (e.g., 'personal-work')
        package: Package/repository name
        path: File path relative to package
        symbol: Symbol name within the file (nullable)
        kind: Symbol kind (FUNCTION, CLASS, METHOD, etc.)
        name: Human-readable name
        signature: Function/method signature (nullable)
        documentation: Documentation string (nullable)
        file_path: Absolute file path for resolution (nullable)
        line_number: Line number in source file (nullable)

    Validates: Requirement 7.1

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirement 7.1)
    """

    # Required fields
    arn: strawberry.ID
    type: NodeType
    workspace: str
    package: str
    path: str
    kind: SymbolKind
    name: str

    # Optional fields
    symbol: Optional[str] = None
    signature: Optional[str] = None
    documentation: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None


@strawberry.input
class RelationshipInput:
    """GraphQL input type for relationship definitions in sync operations.

    Used by the `syncFromScip` mutation to accept relationship data from
    SCIP parse results. Each relationship represents a directed edge
    between two nodes in the Code Graph.

    Attributes:
        from_arn: Source node ARN (the node where the relationship originates)
        to_arn: Target node ARN (the node where the relationship points)
        type: Relationship type (CONTAINS, REFERENCES, IMPLEMENTS, etc.)

    Validates: Requirement 7.2

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirement 7.2)
    """

    from_arn: strawberry.ID
    to_arn: strawberry.ID
    type: EdgeType


@strawberry.type
class SyncResult:
    """GraphQL type representing the result of a sync operation.

    Returned by the `syncFromScip` mutation to report the outcome of
    synchronizing SCIP parse results with the Code Graph. Provides
    counts for nodes and edges that were created, updated, or removed.

    Attributes:
        nodes_created: Count of new nodes inserted into the graph
        nodes_updated: Count of existing nodes that were updated
        edges_created: Count of new edges inserted into the graph
        edges_removed: Count of stale edges that were removed

    Validates: Requirement 7.3

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Schema section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirement 7.3)
    """

    nodes_created: int
    nodes_updated: int
    edges_created: int
    edges_removed: int
