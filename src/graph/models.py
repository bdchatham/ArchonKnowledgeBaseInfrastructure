"""SQLAlchemy models for the Code Graph.

This module defines the SQLAlchemy ORM models that map to the PostgreSQL
tables for the Code Graph. These models represent code symbols (nodes)
and their relationships (edges).

The models map to the schema defined in migrations/001_code_graph_tables.sql.

Source:
- migrations/001_code_graph_tables.sql
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class NodeType(Enum):
    """Resource type classification for graph nodes.

    Defines the type of resource a node represents in the Code Graph.

    Values:
        CODE: Source code symbols (functions, classes, methods, etc.)
        DOC: Documentation files (.archon.md, README, etc.)
        K8S: Kubernetes resources (deployments, services, etc.)
        INFRA: Infrastructure definitions (CDK, Terraform, etc.)
    """

    CODE = "code"
    DOC = "doc"
    K8S = "k8s"
    INFRA = "infra"


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
    """

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    TYPE = "type"
    MODULE = "module"
    FILE = "file"
    PACKAGE = "package"


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
    """

    CONTAINS = "contains"
    REFERENCES = "references"
    IMPLEMENTS = "implements"
    EXTENDS = "extends"
    IMPORTS = "imports"
    DOCUMENTS = "documents"


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models in the Code Graph."""

    pass


class GraphNodeModel(Base):
    """SQLAlchemy model for code graph nodes.

    Represents a node in the Code Graph, which can be a code symbol,
    file, package, or documentation. Each node is uniquely identified
    by its ARN (Archon Resource Name).

    ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
    Example: arn:archon:code:personal-work/ArchonKnowledgeBaseInfrastructure/src/graph/schema.py#GraphQLService

    Attributes:
        arn: Archon Resource Name - unique identifier for the node
        type: Resource type (code, doc, k8s, infra)
        workspace: Workspace identifier (e.g., 'personal-work')
        package: Package/repository name
        path: File path relative to package
        symbol: Symbol name within the file (nullable)
        kind: Symbol kind (function, class, method, etc.) (nullable)
        name: Human-readable name
        signature: Function/method signature (nullable)
        documentation: Documentation string (nullable)
        file_path: Absolute file path for resolution (nullable)
        line_number: Line number in source file (nullable)
        created_at: Timestamp when node was created
        updated_at: Timestamp when node was last updated
        index_hash: SCIP index hash for change detection (nullable)

    Relationships:
        outgoing_edges: Edges where this node is the source
        incoming_edges: Edges where this node is the target
    """

    __tablename__ = "code_graph_nodes"

    # Primary key: Archon Resource Name
    arn: Mapped[str] = mapped_column(Text, primary_key=True)

    # Resource type classification
    type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Location identifiers
    workspace: Mapped[str] = mapped_column(Text, nullable=False)
    package: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)

    # Symbol information
    symbol: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kind: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Human-readable name
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional metadata
    signature: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    documentation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    line_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # SCIP index hash for change detection
    index_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    outgoing_edges: Mapped[list["GraphEdgeModel"]] = relationship(
        "GraphEdgeModel",
        foreign_keys="GraphEdgeModel.from_arn",
        back_populates="source_node",
        cascade="all, delete-orphan",
    )
    incoming_edges: Mapped[list["GraphEdgeModel"]] = relationship(
        "GraphEdgeModel",
        foreign_keys="GraphEdgeModel.to_arn",
        back_populates="target_node",
        cascade="all, delete-orphan",
    )

    # Table constraints
    __table_args__ = (
        CheckConstraint(
            "type IN ('code', 'doc', 'k8s', 'infra')",
            name="check_node_type",
        ),
        CheckConstraint(
            "kind IN ('function', 'class', 'method', 'variable', 'type', 'module', 'file', 'package') OR kind IS NULL",
            name="check_node_kind",
        ),
        Index("idx_nodes_package", "package"),
        Index("idx_nodes_kind", "kind"),
        Index("idx_nodes_path", "path"),
    )

    def __repr__(self) -> str:
        return f"<GraphNodeModel(arn={self.arn!r}, type={self.type!r}, name={self.name!r})>"


class GraphEdgeModel(Base):
    """SQLAlchemy model for code graph edges.

    Represents a directed relationship between two nodes in the Code Graph.
    Each edge connects a source node (from_arn) to a target node (to_arn)
    with a specific relationship type.

    Relationship types:
        - contains: Parent contains child (e.g., class contains method)
        - references: Source references target (e.g., function calls another)
        - implements: Source implements target interface/type
        - extends: Source extends target class/type
        - imports: Source imports target module
        - documents: Documentation node documents code node

    Attributes:
        id: Auto-incrementing edge identifier
        from_arn: Source node ARN (foreign key)
        to_arn: Target node ARN (foreign key)
        type: Relationship type
        created_at: Timestamp when edge was created

    Relationships:
        source_node: The node this edge originates from
        target_node: The node this edge points to
    """

    __tablename__ = "code_graph_edges"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign keys with cascade delete
    from_arn: Mapped[str] = mapped_column(
        Text,
        ForeignKey("code_graph_nodes.arn", ondelete="CASCADE"),
        nullable=False,
    )
    to_arn: Mapped[str] = mapped_column(
        Text,
        ForeignKey("code_graph_nodes.arn", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationship type
    type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Relationships
    source_node: Mapped["GraphNodeModel"] = relationship(
        "GraphNodeModel",
        foreign_keys=[from_arn],
        back_populates="outgoing_edges",
    )
    target_node: Mapped["GraphNodeModel"] = relationship(
        "GraphNodeModel",
        foreign_keys=[to_arn],
        back_populates="incoming_edges",
    )

    # Table constraints
    __table_args__ = (
        CheckConstraint(
            "type IN ('contains', 'references', 'implements', 'extends', 'imports', 'documents')",
            name="check_edge_type",
        ),
        UniqueConstraint("from_arn", "to_arn", "type", name="unique_edge"),
        Index("idx_edges_from", "from_arn"),
        Index("idx_edges_to", "to_arn"),
        Index("idx_edges_type", "type"),
    )

    def __repr__(self) -> str:
        return f"<GraphEdgeModel(id={self.id!r}, from_arn={self.from_arn!r}, to_arn={self.to_arn!r}, type={self.type!r})>"
