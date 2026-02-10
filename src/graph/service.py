"""GraphQL service for executing queries against the Code Graph.

This module provides the main entry point for graph operations, exposing
the GraphQL API. It creates a strawberry schema from Query and Mutation
classes and handles query execution with proper context setup.

The service:
- Creates a strawberry schema from Query and Mutation classes
- Creates a GraphRepository instance for database access
- Passes the repository to resolvers via context
- Executes GraphQL queries using strawberry's execute method

Source:
- .kiro/specs/code-graph-storage/design.md (GraphQL Service section)
- .kiro/specs/code-graph-storage/requirements.md (Requirements 4.1, 5.1)
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

import strawberry
from graphql import GraphQLSchema

from src.graph.repository import GraphRepository
from src.graph.resolvers import Mutation, Query

logger = logging.getLogger(__name__)


@dataclass
class GraphQLServiceConfig:
    """Configuration for the GraphQL service.

    Provides configuration options for the GraphQL service including
    database connection settings and query limits.

    Attributes:
        database_url: PostgreSQL connection URL
                     (e.g., "postgresql://user:pass@host:port/db")
        max_traversal_depth: Maximum depth for traverse queries (default: 5)
        default_search_limit: Default limit for search queries (default: 20)
        max_search_limit: Maximum allowed limit for search queries (default: 100)

    Example:
        config = GraphQLServiceConfig(
            database_url="postgresql://localhost:5432/archon",
            max_traversal_depth=5,
            default_search_limit=20,
            max_search_limit=100,
        )
        service = GraphQLService(config)

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Service section)
    """

    database_url: str
    max_traversal_depth: int = 5
    default_search_limit: int = 20
    max_search_limit: int = 100


class GraphQLService:
    """Service for executing GraphQL queries against the Code Graph.

    The GraphQLService is the main entry point for graph operations. It
    creates a strawberry GraphQL schema from the Query and Mutation classes,
    manages the GraphRepository for database access, and executes GraphQL
    queries with proper context setup.

    Usage:
        config = GraphQLServiceConfig(database_url="postgresql://...")
        service = GraphQLService(config)

        # Execute a query
        result = await service.execute('''
            query {
                node(arn: "arn:archon:code:ws/pkg/file.py#func") {
                    name
                    kind
                }
            }
        ''')

        # Get schema for introspection
        schema = service.get_schema()

    Attributes:
        config: GraphQLServiceConfig with service settings
        repository: GraphRepository for database access
        schema: Strawberry GraphQL schema

    Validates: Requirements 4.1, 5.1

    Source:
    - .kiro/specs/code-graph-storage/design.md (GraphQL Service section)
    - .kiro/specs/code-graph-storage/requirements.md (Requirements 4.1, 5.1)
    """

    def __init__(self, config: GraphQLServiceConfig) -> None:
        """Initialize the GraphQL service with configuration.

        Creates the strawberry GraphQL schema from Query and Mutation classes
        and initializes the GraphRepository for database access.

        Args:
            config: GraphQLServiceConfig with database URL and query limits

        Example:
            config = GraphQLServiceConfig(
                database_url="postgresql://localhost:5432/archon",
            )
            service = GraphQLService(config)
        """
        self.config = config
        self.repository = GraphRepository(db_url=config.database_url)
        self._schema = strawberry.Schema(query=Query, mutation=Mutation)

        logger.info(
            f"GraphQLService initialized with max_traversal_depth={config.max_traversal_depth}, "
            f"default_search_limit={config.default_search_limit}, "
            f"max_search_limit={config.max_search_limit}"
        )

    async def execute(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query and return the result.

        Executes the provided GraphQL query string against the Code Graph
        schema. The repository is passed to resolvers via the context,
        allowing them to access the database.

        Args:
            query: GraphQL query string
            variables: Optional query variables as a dictionary

        Returns:
            GraphQL execution result with 'data' and optional 'errors' keys.
            The 'data' key contains the query result, and 'errors' contains
            any errors that occurred during execution.

        Example:
            result = await service.execute('''
                query GetNode($arn: ID!) {
                    node(arn: $arn) {
                        name
                        kind
                        documentation
                    }
                }
            ''', variables={"arn": "arn:archon:code:ws/pkg/file.py#func"})

            if result.get("errors"):
                print(f"Errors: {result['errors']}")
            else:
                print(f"Node: {result['data']['node']}")

        Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
        """
        logger.debug(f"Executing GraphQL query: {query[:100]}...")

        result = await self._schema.execute(
            query,
            variable_values=variables,
            context_value={"repository": self.repository},
        )

        response: dict[str, Any] = {"data": result.data}

        if result.errors:
            response["errors"] = [
                {
                    "message": str(error.message),
                    "locations": [
                        {"line": loc.line, "column": loc.column}
                        for loc in (error.locations or [])
                    ],
                    "path": error.path,
                }
                for error in result.errors
            ]
            logger.warning(
                f"GraphQL execution completed with {len(result.errors)} error(s)"
            )
        else:
            logger.debug("GraphQL execution completed successfully")

        return response

    def get_schema(self) -> GraphQLSchema:
        """Return the GraphQL schema for introspection.

        Returns the underlying graphql-core GraphQLSchema object, which
        can be used for schema introspection queries or integration with
        other GraphQL tools.

        Returns:
            GraphQL schema object (graphql-core GraphQLSchema)

        Example:
            schema = service.get_schema()
            # Use for introspection or integration with GraphQL tools

        Validates: Requirement 4.1

        Source:
        - .kiro/specs/code-graph-storage/design.md (GraphQL Service section)
        """
        return self._schema._schema

    async def connect(self) -> None:
        """Connect to the database.

        Establishes the database connection pool for the repository.
        This method should be called before executing queries.

        Raises:
            GraphRepositoryError: If connection fails
        """
        await self.repository.connect()
        logger.info("GraphQLService connected to database")

    async def close(self) -> None:
        """Close the database connection.

        Closes the database connection pool and releases resources.
        This method should be called when the service is no longer needed.
        """
        await self.repository.close()
        logger.info("GraphQLService disconnected from database")

    async def health_check(self) -> bool:
        """Check if the service is healthy.

        Performs a health check on the database connection to verify
        that the service can execute queries.

        Returns:
            True if the service is healthy, False otherwise
        """
        return await self.repository.health_check()
