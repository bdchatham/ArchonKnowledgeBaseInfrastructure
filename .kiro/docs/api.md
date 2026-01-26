# API

## Overview

The Knowledge Base exposes two REST APIs:
- **Query Service**: Semantic document retrieval for RAG workflows
- **Embedding Service**: OpenAI-compatible embedding generation

Both APIs are designed for internal use but can be accessed externally via Ingress.

## Base URLs

### Query Service
- **Internal**: `http://query.archon-knowledge-base.svc.cluster.local:8080`
- **External**: `https://archon-kb.home.local` (via Ingress)

### Embedding Service
- **Internal**: `http://embedding-svc.archon-knowledge-base.svc.cluster.local:8000`

## Query Service Endpoints

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

### GET /health

Liveness check endpoint.

**Response** (200 OK):
```json
{"status": "healthy"}
```

### GET /ready

Readiness check endpoint. Returns 200 only if dependencies are reachable.

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
{"detail": "Embedding service not ready"}
```


## Embedding Service Endpoints

### POST /v1/embeddings

Generate embeddings for input text(s). OpenAI-compatible format.

**Request Body**:
```json
{
  "input": "How does deployment work?",
  "model": "BAAI/bge-base-en-v1.5"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input` | string or array | Yes | Text(s) to embed |
| `model` | string | No | Model name (ignored, uses configured model) |

**Response** (200 OK):
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.123, -0.456, ...],
      "index": 0
    }
  ],
  "model": "BAAI/bge-base-en-v1.5",
  "usage": {
    "prompt_tokens": 5,
    "total_tokens": 5
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `data` | array | Embedding results |
| `data[].embedding` | array | 768-dimensional vector |
| `data[].index` | integer | Index of input text |
| `model` | string | Model used |
| `usage` | object | Token usage (approximate) |

### GET /health

Liveness check endpoint.

**Response** (200 OK):
```json
{"status": "healthy"}
```

### GET /ready

Readiness check. Returns 200 only if model is loaded.

**Response** (200 OK):
```json
{
  "status": "ready",
  "model": "BAAI/bge-base-en-v1.5"
}
```

**Error Response** (503):
```json
{"detail": "Model not loaded"}
```

## Authentication

The APIs currently do not require authentication. Access control is managed at the network level:
- Internal cluster access via Kubernetes Service
- External access via Ingress with rate limiting

## Rate Limiting

The Ingress applies rate limiting to external requests:
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

async def generate_embedding(text: str) -> list:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://embedding-svc.archon-knowledge-base.svc.cluster.local:8000/v1/embeddings",
            json={"input": text}
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]
```

### curl

```bash
# Retrieve documents
curl -X POST http://localhost:8080/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I deploy?", "k": 3}'

# Generate embedding
curl -X POST http://localhost:8000/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": "Sample text to embed"}'

# Health checks
curl http://localhost:8080/health
curl http://localhost:8000/health
```

**Source**
- `src/query/main.py` - Query API implementation
- `src/query/retriever.py` - Retrieval logic
- `src/embedding/main.py` - Embedding API implementation
- `manifests/ingress.yaml` - Ingress configuration with CORS and rate limiting
