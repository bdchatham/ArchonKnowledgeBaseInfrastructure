"""Configuration management for Knowledge Base services.

All configuration is loaded from environment variables, making the services
container-friendly and configurable via Kubernetes ConfigMaps and Secrets.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class EmbeddingConfiguration(BaseSettings):
    """Configuration for embedding service connection.
    
    The embedding_service_url points to the Agent's model server,
    which provides the /v1/embeddings endpoint for vector generation.
    This is a required dependency - the Knowledge Base cannot function
    without access to an embedding service.
    """
    
    embedding_service_url: str = Field(
        description="URL of the embedding service (e.g., http://vllm.archon-system.svc.cluster.local:8000)"
    )
    embedding_model: str = Field(
        default="BAAI/bge-base-en-v1.5",
        description="Name of the embedding model to use"
    )


class StorageConfiguration(BaseSettings):
    """Configuration for storage backends."""
    
    vector_db_url: str = Field(
        default="http://qdrant:6333",
        description="URL of the Qdrant vector database"
    )
    collection_name: str = Field(
        default="archon-docs",
        description="Name of the Qdrant collection for document embeddings"
    )
    tracker_db_url: str = Field(
        default="postgresql://archon:password@postgres:5432/archon",
        description="PostgreSQL connection URL for state tracking"
    )


class RetrievalConfiguration(BaseSettings):
    """Configuration for retrieval behavior."""
    
    retrieval_k: int = Field(
        default=5,
        description="Number of top results to return from vector search"
    )
    chunk_size: int = Field(
        default=1000,
        description="Maximum size of document chunks in characters"
    )
    chunk_overlap: int = Field(
        default=200,
        description="Overlap between consecutive chunks in characters"
    )


class MonitorConfiguration(BaseSettings):
    """Configuration for document monitoring."""
    
    github_token: str | None = Field(
        default=None,
        description="GitHub personal access token for API access"
    )
    repositories: str = Field(
        default="[]",
        description="JSON array of repository configurations to monitor"
    )


class Settings(
    EmbeddingConfiguration,
    StorageConfiguration,
    RetrievalConfiguration,
    MonitorConfiguration
):
    """Combined settings loaded from environment variables.
    
    Usage:
        settings = Settings()
        # All configuration is loaded from environment variables
        # e.g., EMBEDDING_SERVICE_URL, VECTOR_DB_URL, etc.
    """
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
