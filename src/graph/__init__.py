"""Code Graph module for GraphQL Code Graph Storage.

This module provides a queryable representation of code structure and
relationships for the Archon Knowledge Base. It stores code symbols
(functions, classes, methods, etc.) and their relationships (contains,
references, implements, etc.) in PostgreSQL, exposing them through a
GraphQL API.

Components:
- models: SQLAlchemy models for graph nodes and edges
- schema: GraphQL schema definition (strawberry-graphql types)
- resolvers: GraphQL resolvers
- service: Graph service layer
- repository: Data access layer
"""

from src.graph.models import (
    EdgeType,
    GraphEdgeModel,
    GraphNodeModel,
    NodeType,
    SymbolKind,
)
from src.graph.repository import (
    GraphEdge,
    GraphNode,
    GraphRepository,
    GraphRepositoryError,
)
from src.graph.schema import (
    EdgeType as GQLEdgeType,
    Node as GQLNode,
    NodeType as GQLNodeType,
    SymbolKind as GQLSymbolKind,
)
from src.graph.resolvers import Mutation, Query
from src.graph.service import GraphQLService, GraphQLServiceConfig

__all__ = [
    # Enums (SQLAlchemy/Python)
    "NodeType",
    "SymbolKind",
    "EdgeType",
    # SQLAlchemy models
    "GraphNodeModel",
    "GraphEdgeModel",
    # Repository layer
    "GraphRepository",
    "GraphRepositoryError",
    "GraphNode",
    "GraphEdge",
    # GraphQL types (strawberry)
    "GQLNode",
    "GQLNodeType",
    "GQLSymbolKind",
    "GQLEdgeType",
    # GraphQL resolvers
    "Query",
    "Mutation",
    # Service layer
    "GraphQLService",
    "GraphQLServiceConfig",
]
