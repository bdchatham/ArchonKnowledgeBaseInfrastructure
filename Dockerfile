# Multi-stage Dockerfile for Knowledge Base services
# Supports both Query and Monitor services via build targets

# Base stage with Python and dependencies
FROM python:3.11-slim as base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
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

# Monitor service target
FROM base as monitor

ENV PYTHONPATH=/app

CMD ["python", "-m", "src.monitor.main"]
