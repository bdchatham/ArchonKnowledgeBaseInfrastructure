"""Graph service FastAPI application.

This module provides the GraphQL API for the Code Graph. It exposes
the strawberry GraphQL schema at /graphql and health/readiness endpoints.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from strawberry.asgi import GraphQL as BaseGraphQL

from src.graph.service import GraphQLService, GraphQLServiceConfig


class GraphQL(BaseGraphQL):
    """GraphQL ASGI app that injects the repository into resolver context."""

    def __init__(self, schema, repository):
        super().__init__(schema)
        self._repository = repository

    async def get_context(self, request, response=None):
        return {"repository": self._repository}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


service: GraphQLService | None = None


def build_database_url(host: str, port: str, db: str, user: str, password: str) -> str:
    """Construct a PostgreSQL connection URL from components.

    Args:
        host: Database hostname
        port: Database port
        db: Database name
        user: Database username
        password: Database password

    Returns:
        PostgreSQL connection URL in the format
        ``postgresql://{user}:{password}@{host}:{port}/{db}``
    """
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    global service

    logger.info("Starting Graph service")

    db_url = build_database_url(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        db=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )

    config = GraphQLServiceConfig(database_url=db_url)
    service = GraphQLService(config)
    await service.connect()

    graphql_app = GraphQL(service._schema, service.repository)
    app.mount("/graphql", graphql_app)

    logger.info("Graph service started")

    yield

    logger.info("Shutting down Graph service")
    if service:
        await service.close()


app = FastAPI(
    title="Archon Code Graph Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint.

    Returns 200 if the service is running.
    """
    return {"status": "healthy"}


@app.get("/ready")
async def ready():
    """Readiness check endpoint.

    Returns 200 if the database connection is healthy, 503 otherwise.
    """
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized",
        )

    is_healthy = await service.health_check()

    if not is_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection unhealthy",
        )

    return {"status": "ready", "database": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081)
