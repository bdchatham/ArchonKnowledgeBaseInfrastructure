# Multi-stage Dockerfile for Knowledge Base services
# Supports Query and Embedding services via build targets

# Base stage with Python and core dependencies
FROM python:3.11-slim as base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Query service target
FROM base as query

EXPOSE 8080

ENV PYTHONPATH=/app

CMD ["python", "-m", "uvicorn", "src.query.main:app", "--host", "0.0.0.0", "--port", "8080"]

# Graph service target
FROM base as graph

EXPOSE 8081

ENV PYTHONPATH=/app

CMD ["python", "-m", "uvicorn", "src.graph.main:app", "--host", "0.0.0.0", "--port", "8081"]

# Embedding service target
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04 as embedding-base

WORKDIR /app

# Install Python and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install embedding-specific dependencies
COPY requirements-embedding.txt .
RUN pip install --no-cache-dir -r requirements-embedding.txt

# Pre-download the model during build for faster startup
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-base-en-v1.5')"

# Copy application code
COPY src/embedding/ ./src/embedding/

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

FROM embedding-base as embedding

EXPOSE 8000

ENV PYTHONPATH=/app

CMD ["python", "-m", "uvicorn", "src.embedding.main:app", "--host", "0.0.0.0", "--port", "8000"]
