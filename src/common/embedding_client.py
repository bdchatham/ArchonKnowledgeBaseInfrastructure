"""Client for generating embeddings via OpenAI-compatible API.

This client connects to the Agent's model server to generate embeddings
for documents and queries. It implements retry logic with exponential
backoff for resilience against transient failures.
"""

import logging
from typing import List

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)


class EmbeddingServiceError(Exception):
    """Raised when the embedding service is unavailable or returns an error."""
    pass


class EmbeddingClient:
    """Client for generating embeddings via OpenAI-compatible API.
    
    The client connects to an embedding service (e.g., vLLM) that implements
    the OpenAI embeddings API format. It handles retries with exponential
    backoff for transient failures.
    
    Usage:
        client = EmbeddingClient(
            base_url="http://vllm.archon-system.svc.cluster.local:8000",
            model="BAAI/bge-base-en-v1.5"
        )
        embeddings = await client.embed(["Hello world", "Another text"])
    """
    
    def __init__(self, base_url: str, model: str, timeout: float = 30.0):
        """Initialize the embedding client.
        
        Args:
            base_url: Base URL of the embedding service
            model: Name of the embedding model to use
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts.
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            List of embedding vectors (each vector is a list of floats)
            
        Raises:
            EmbeddingServiceError: If the embedding service is unavailable
                or returns an error after all retries
        """
        if not texts:
            return []
        
        client = await self._get_client()
        url = f"{self.base_url}/v1/embeddings"
        
        payload = {
            "model": self.model,
            "input": texts,
        }
        
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            
            data = response.json()
            embeddings = [item["embedding"] for item in data["data"]]
            
            logger.debug(f"Generated {len(embeddings)} embeddings")
            return embeddings
            
        except httpx.ConnectError as e:
            logger.error(f"Failed to connect to embedding service at {url}: {e}")
            raise EmbeddingServiceError(
                f"Embedding service unavailable at {self.base_url}"
            ) from e
            
        except httpx.TimeoutException as e:
            logger.error(f"Timeout connecting to embedding service at {url}: {e}")
            raise EmbeddingServiceError(
                f"Embedding service timeout at {self.base_url}"
            ) from e
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Embedding service returned error: {e.response.status_code}")
            raise EmbeddingServiceError(
                f"Embedding service error: {e.response.status_code}"
            ) from e
    
    async def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text.
        
        Convenience method for embedding a single text string.
        
        Args:
            text: Text string to embed
            
        Returns:
            Embedding vector as a list of floats
        """
        embeddings = await self.embed([text])
        return embeddings[0]
    
    async def health_check(self) -> bool:
        """Check if the embedding service is healthy.
        
        Returns:
            True if the service is reachable and responding
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception:
            return False
