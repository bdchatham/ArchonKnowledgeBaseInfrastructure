"""Embedding service client for generating text embeddings.

Provides an async HTTP client for the embedding service with retry logic,
connection error handling, and batch processing support.
"""

import logging
from typing import Any, List, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSION = 768
DEFAULT_BATCH_SIZE = 32
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 5


class EmbeddingServiceError(Exception):
    """Base exception for embedding service errors."""

    pass


class EmbeddingServiceUnavailable(EmbeddingServiceError):
    """Raised when the embedding service is unavailable (503)."""

    pass


class EmbeddingValidationError(EmbeddingServiceError):
    """Raised when input validation fails (400)."""

    pass


class EmbeddingTimeoutError(EmbeddingServiceError):
    """Raised when embedding request times out (504)."""

    pass


class EmbeddingConfigurationError(EmbeddingServiceError):
    """Raised when there's a configuration error (500)."""

    pass


def _create_retry_decorator(max_attempts: int = DEFAULT_MAX_RETRIES):
    """Create a retry decorator with exponential backoff and jitter."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=1.0, max=60.0, jitter=5.0),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )


class EmbeddingServiceClient:
    """Client for the embedding service.

    Provides async methods for generating embeddings via the embedding service.
    Includes retry logic with exponential backoff for transient failures.

    Usage:
        async with EmbeddingServiceClient("http://embedding-svc:8000") as client:
            embeddings = await client.embed(["Hello world", "Another text"])
            single_embedding = await client.embed_single("Single text")
    """

    def __init__(
        self,
        service_url: str,
        model: str = "BAAI/bge-base-en-v1.5",
        timeout: float = DEFAULT_TIMEOUT,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Initialize the embedding service client.

        Args:
            service_url: URL of the embedding service
            model: Name of the embedding model to use
            timeout: Request timeout in seconds
            batch_size: Maximum texts per batch request
            max_retries: Maximum retry attempts for transient failures
        """
        self.service_url = service_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "EmbeddingServiceClient":
        """Enter async context manager."""
        self._client = httpx.AsyncClient(
            base_url=self.service_url,
            timeout=httpx.Timeout(self.timeout),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context manager."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get the HTTP client, creating one if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.service_url,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts.

        Processes texts in batches for optimal throughput. Empty input
        returns an empty list.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (one per input text)

        Raises:
            EmbeddingValidationError: If any text is empty
            EmbeddingServiceUnavailable: If the service is unavailable
            EmbeddingTimeoutError: If the request times out
            EmbeddingConfigurationError: If there's a configuration error
        """
        if not texts:
            return []

        for i, text in enumerate(texts):
            if not text or not text.strip():
                raise EmbeddingValidationError(
                    f"Text at index {i} is empty. Provide non-empty content."
                )

        embeddings: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_embeddings = await self._embed_batch(batch)
            embeddings.extend(batch_embeddings)

        return embeddings

    async def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text string to embed

        Returns:
            Embedding vector

        Raises:
            EmbeddingValidationError: If text is empty
            EmbeddingServiceUnavailable: If the service is unavailable
            EmbeddingTimeoutError: If the request times out
        """
        embeddings = await self.embed([text])
        return embeddings[0]

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed (should be <= batch_size)

        Returns:
            List of embedding vectors
        """
        return await self._embed_batch_with_retry(texts)

    @_create_retry_decorator()
    async def _embed_batch_with_retry(self, texts: List[str]) -> List[List[float]]:
        """Make the embedding request with retry logic.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors

        Raises:
            EmbeddingServiceUnavailable: If the service is unavailable after retries
            EmbeddingTimeoutError: If the request times out after retries
            EmbeddingConfigurationError: If there's a server error
        """
        client = self._get_client()

        try:
            response = await client.post(
                "/v1/embeddings",
                json={
                    "input": texts,
                    "model": self.model,
                },
            )
        except httpx.ConnectError as e:
            logger.error(f"Failed to connect to embedding service: {e}")
            raise EmbeddingServiceUnavailable(
                f"Embedding service unavailable at {self.service_url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            logger.error(f"Embedding request timed out: {e}")
            raise EmbeddingTimeoutError(
                f"Embedding request timed out after {self.timeout}s. "
                "Consider reducing batch size and retrying."
            ) from e

        if response.status_code == 503:
            raise EmbeddingServiceUnavailable(
                f"Embedding service unavailable (503): {response.text}"
            )
        elif response.status_code == 504:
            raise EmbeddingTimeoutError(
                f"Embedding request timed out (504): {response.text}"
            )
        elif response.status_code >= 500:
            raise EmbeddingConfigurationError(
                f"Embedding service error ({response.status_code}): {response.text}"
            )
        elif response.status_code >= 400:
            raise EmbeddingValidationError(
                f"Invalid request ({response.status_code}): {response.text}"
            )

        data = response.json()
        embeddings = [item["embedding"] for item in data["data"]]

        if embeddings and len(embeddings[0]) != EMBEDDING_DIMENSION:
            raise EmbeddingConfigurationError(
                f"Embedding dimension mismatch: expected {EMBEDDING_DIMENSION}, "
                f"got {len(embeddings[0])}. Verify embedding model configuration."
            )

        return embeddings

    async def health_check(self) -> bool:
        """Check if the embedding service is healthy.

        Returns:
            True if the service is healthy, False otherwise
        """
        client = self._get_client()
        try:
            response = await client.get("/health")
            return response.status_code == 200
        except Exception:
            return False
