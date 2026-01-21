# Architecture

## System Design

The Archon Knowledge Base is a fully self-contained RAG infrastructure system. It consists of three primary services (Embedding, Query, and Monitor) backed by two storage systems (Qdrant and PostgreSQL). The system is designed for Kubernetes deployment with no external dependencies.

The architecture follows a clear separation of concerns:
- **Embedding Service**: Generates vector embeddings using sentence-transformers
- **Query Service**: Handles retrieval requests synchronously
- **Monitor Service**: Handles document ingestion asynchronously via CronJob
- **Storage Layer**: Provides persistence for vectors and state

## Components

### Embedding Service

A FastAPI application that provides OpenAI-compatible embedding generation using the BAAI/bge-base-en-v1.5 model via sentence-transformers. Runs as a Kubernetes Deployment.

Responsibilities:
- Load and serve the BGE embedding model
- Accept text input via `/v1/embeddings` endpoint
- Return normalized embeddings in OpenAI-compatible format

The service exposes health (`/health`) and readiness (`/ready`) endpoints. Readiness checks verify the model is loaded and ready to serve.

### Query Service

A FastAPI application that provides the semantic search API. Runs as a Kubernetes Deployment with 2 replicas for availability.

Responsibilities:
- Accept natural language queries via REST API
- Generate query embeddings via the internal Embedding Service
- Search Qdrant for similar document chunks
- Return ranked results with source metadata

The service exposes health (`/health`) and readiness (`/ready`) endpoints. Readiness checks verify connectivity to both the embedding service and Qdrant.

### Monitor Service

A batch job that runs as a Kubernetes CronJob every 15 minutes. Processes configured GitHub repositories to detect and ingest documentation changes.

Responsibilities:
- Fetch file listings from GitHub repositories
- Compare SHA hashes against tracked state in PostgreSQL
- Chunk new/modified documents and generate embeddings
- Store embeddings in Qdrant with source metadata
- Remove embeddings for deleted documents


### Qdrant Vector Store

Stores document embeddings for similarity search. Deployed as a StatefulSet with persistent storage.

Configuration:
- Collection: `archon-docs`
- Vector size: 384 (matches BGE-base embedding model)
- Distance metric: Cosine similarity
- Storage: 10Gi PersistentVolumeClaim

### PostgreSQL State Tracker

Tracks document versions for efficient change detection. Deployed as a StatefulSet with persistent storage.

Stores:
- Document paths and SHA hashes
- Last modified and last checked timestamps
- Content hashes for additional verification

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Embedding Service | FastAPI + sentence-transformers | 0.109+ |
| Query Service | FastAPI + Uvicorn | 0.109+ |
| HTTP Client | httpx | 0.26+ |
| Vector Database | Qdrant | 1.7.0 |
| State Database | PostgreSQL | 15-alpine |
| Embedding Model | BAAI/bge-base-en-v1.5 | - |
| Container Runtime | Python | 3.11-slim |
| Orchestration | Kubernetes | - |
| GitOps | ArgoCD | - |

## Data Flow

### Retrieval Flow

```
Client → Query Service → Embedding Service
                      ↓
              Vector Store (Qdrant)
                      ↓
              Ranked Results → Client
```

1. Client sends query to `/v1/retrieve`
2. Query Service calls internal Embedding Service's `/v1/embeddings` endpoint
3. Query embedding is used to search Qdrant
4. Top-k similar chunks are returned with scores

### Ingestion Flow

```
Monitor CronJob → GitHub API → Document Content
                            ↓
                    State Tracker (PostgreSQL)
                            ↓
                    Chunker → Embedding Service
                            ↓
                    Vector Store (Qdrant)
```

1. Monitor fetches file listings from configured repositories
2. SHA comparison against PostgreSQL identifies changes
3. Changed documents are chunked (1000 chars, 200 overlap)
4. Chunks are embedded via internal Embedding Service
5. Embeddings are upserted to Qdrant with metadata

## Dependencies

### Internal Services

| Service | Purpose | Endpoint |
|---------|---------|----------|
| Embedding Service | Vector generation | `http://embedding-svc:8000/v1/embeddings` |
| Qdrant | Vector storage | `http://qdrant:6333` |
| PostgreSQL | State tracking | `postgresql://postgres:5432/archon` |

### External Dependencies

| Dependency | Purpose | Required |
|------------|---------|----------|
| GitHub API | Document source for Monitor | Yes |

The Knowledge Base is fully self-contained. No external embedding service is required.

### Downstream Consumers

| Consumer | Integration Point |
|----------|-------------------|
| Agent (vLLM) | Calls `/v1/retrieve` for RAG context |
| External Clients | REST API via Ingress |

**Source**
- `src/embedding/main.py` - Embedding service implementation
- `src/query/main.py` - Query service implementation
- `src/monitor/main.py` - Monitor service implementation
- `src/common/embedding_client.py` - Embedding client
- `src/common/vector_store.py` - Qdrant wrapper
- `src/common/state_tracker.py` - PostgreSQL state tracker
- `manifests/embedding-deployment.yaml` - Embedding deployment
- `manifests/query-deployment.yaml` - Query deployment
- `manifests/monitor-cronjob.yaml` - Monitor CronJob
- `manifests/qdrant-statefulset.yaml` - Qdrant deployment
- `manifests/postgres-statefulset.yaml` - PostgreSQL deployment
