"""Embedding service module.

Provides a FastAPI application that serves embeddings using
the BAAI/bge-base-en-v1.5 model via sentence-transformers.
"""

from .main import app

__all__ = ["app"]
