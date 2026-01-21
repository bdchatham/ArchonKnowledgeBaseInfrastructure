# API

## Overview

The Query Service exposes a REST API for semantic document retrieval. The primary endpoint accepts natural language queries and returns relevant document chunks ranked by similarity score.

The API is designed for internal use by the Agent for RAG augmentation, but can also be called directly by other services.

## Base URL

- **Internal**: `http://query.archon-knowledge-base.svc.cluster.local:8080`
- **External**: `https://archon-kb.home.local` (via Ingress)

## Endpoints

### POST /v1/retrieve

Retrieve relevant document chunks for a natural language query.

**Request Body**:
```json
{
  "query": "How does the deployment pipeline work?",
  "k": 5
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | Yes | - | Natural language query |
| `k` | integer | No | 5 | Number of results to return |

**Response** (200 OK):
```json
{
  "chunks": [
    {
      "content": "The deployment pipeline uses ArgoCD...",
      "source": "https://github.com/org/repo/.kiro/docs/operations.md",
      "chunk_index": 2,
      "score": 0.89
    }
  ],
  "query": "How does the deployment pipeline work?"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `chunks` | array | Retrieved document chunks |
| `chunks[].content` | string | Chunk text content |
| `chunks[].source` | string | Source file path (repo URL + file path) |
| `chunks[].chunk_index` | integer | Position of chunk within source document |
| `chunks[].score` | float | Similarity score (0-1, higher is better) |
| `query` | string | Echo of the input query |

**Error Responses**:

| Status | Condition | Response |
|--------|-----------|----------|
| 503 | Embedding service unavailable | `{"detail": "Embedding service unavailable"}` |
| 503 | Vector store unavailable | `{"detail": "Vector store unavailable"}` |
| 503 | Service not initialized | `{"detail": "Service not initialized"}` |

### GET /health

Liveness check endpoint. Returns 200 if the service process is running.

**Response** (200 OK):
```json
{
  "status": "healthy"
}
```

### GET /ready

Readiness check endpoint. Returns 200 only if the service can handle requests (embedding service and vector store are reachable).

**Response** (200 OK):
```json
{
  "status": "ready",
  "embedding_service": "healthy",
  "vector_store": "healthy"
}
```

**Error Response** (503):
```json
{
  "detail": "Embedding service not ready"
}
```

### GET /metrics

Basic service metrics endpoint.

**Response** (200 OK):
```json
{
  "service": "archon-knowledge-base-query",
  "version": "1.0.0"
}
```

## Authentication

The API currently does not require authentication. Access control is managed at the network level:
- Internal cluster access via Kubernetes Service
- External access via Ingress with rate limiting (10 requests/minute)

## Rate Limiting

The Ingress applies rate limiting:
- **Limit**: 10 requests per minute per client IP
- **Window**: 1 minute sliding window

## CORS Configuration

CORS is enabled via Ingress annotations:
- **Allowed Methods**: GET, POST, OPTIONS
- **Allowed Headers**: Content-Type, Authorization
- **Allowed Origins**: * (all origins)

## Integration Examples

### Python Client

```python
import httpx

async def retrieve_context(query: str, k: int = 5) -> list:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://query.archon-knowledge-base.svc.cluster.local:8080/v1/retrieve",
            json={"query": query, "k": k}
        )
        response.raise_for_status()
        return response.json()["chunks"]
```

### curl

```bash
# Retrieve documents
curl -X POST http://localhost:8080/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I deploy?", "k": 3}'

# Health check
curl http://localhost:8080/health

# Readiness check
curl http://localhost:8080/ready
```

### Agent Integration

The Agent calls the Knowledge Base during inference to augment prompts with relevant context:

```python
# Simplified Agent RAG flow
context_chunks = await kb_client.retrieve(user_query, k=5)
augmented_prompt = f"""Context:
{format_chunks(context_chunks)}

Question: {user_query}
"""
response = await llm.generate(augmented_prompt)
```

**Source**
- `src/query/main.py` - API implementation
- `src/query/retriever.py` - Retrieval logic
- `manifests/ingress.yaml` - Ingress configuration with CORS and rate limiting
