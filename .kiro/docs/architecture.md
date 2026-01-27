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
- Generate query embeddings via the internal Embedding Service using `EmbeddingClient` from AphexServiceClients
- Search Qdrant for similar document chunks using `VectorStore` wrapper
- Return ranked results with source metadata

The service uses `AphexServiceClients` for resilient HTTP communication with automatic retry, exponential backoff, and jitter.

The service exposes health (`/health`) and readiness (`/ready`) endpoints. Readiness checks verify connectivity to both the embedding service and Qdrant.

### Monitor Service

A batch job that runs as a Kubernetes CronJob every 15 minutes. Processes configured GitHub repositories to detect and ingest documentation changes.

Responsibilities:
- Fetch file listings from GitHub repositories
- Compare SHA hashes against tracked state in PostgreSQL
- Chunk new/modified documents and generate embeddings via `EmbeddingClient` from AphexServiceClients
- Store embeddings in Qdrant with source metadata
- Remove embeddings for deleted documents

The service uses `AphexServiceClients` for resilient communication with the embedding service.

### MCP Server (Optional)

A Model Context Protocol server that exposes knowledge base tools for AI assistants. Automatically provisioned when `spec.mcpServer` is set (non-nil) in the KnowledgeBase CRD.

Responsibilities:
- Expose MCP protocol endpoints (`/mcp/tools/list`, `/mcp/tools/call`)
- Provide `search` tool that wraps Query Service
- Provide `get_document` tool for full document retrieval
- Provide `list_sources` tool for repository discovery

The MCP server is provisioned by the platform controller in AphexPlatformInfrastructure:
- **Deployment**: `mcp-server-{kb-name}` with configurable replicas
- **Service**: `mcp-server-{kb-name}` on configurable port (default: 8090)
- **Image**: `ghcr.io/bdchatham/archon-mcp-server:latest`
- **Resources**: 128-256Mi memory, 50-200m CPU

The server uses `AphexServiceClients.QueryClient` for resilient communication with the Query Service.

**Integration with Kiro:**
Repositories using ArchonKiroTemplate include `.kiro/steering/archon-rag.md`, which instructs Kiro to discover and use MCP tools automatically when working on platform code.


### Qdrant Vector Store

Stores document embeddings for similarity search. Deployed as a StatefulSet with persistent storage.

Configuration:
- Collection: `archon-docs`
- Vector size: 768 (matches BAAI/bge-base-en-v1.5 embedding model)
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
| MCP Server | FastAPI + AphexServiceClients | latest |
| HTTP Client | httpx | 0.26+ |
| Service Clients | AphexServiceClients | latest |
| Vector Database | Qdrant | 1.16.3 |
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

### MCP Tool Invocation Flow (Optional)

```
Kiro CLI → MCP Server → Query Service → Embedding Service
                                     ↓
                             Vector Store (Qdrant)
                                     ↓
                             Ranked Results → MCP Server → Kiro CLI
```

1. Kiro discovers MCP tools via `/mcp/tools/list`
2. Kiro invokes `{kb-name}.search` tool via `/mcp/tools/call`
3. MCP Server calls Query Service `/v1/retrieve`
4. Query Service generates embedding and searches Qdrant
5. Results are formatted as MCP response with provenance
6. Kiro uses results to ground decisions in actual documentation

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

| Consumer | Integration Point | Pattern |
|----------|-------------------|---------|
| Agent (Model Only) | No integration | Direct model inference |
| Agent (Model + KB) | Calls `/v1/retrieve` for RAG context | Manual orchestration |
| Agent (Full) | Orchestrator calls `/v1/retrieve` | Automatic RAG |
| Kiro CLI | MCP Server tools | Tool-based retrieval |
| External Clients | REST API via Ingress | Direct API access |

**Agent Integration Patterns:**

1. **Model Only**: Agent provisions model server without KB reference
   - No Knowledge Base integration
   - Direct inference via `{agent-name}-model` service

2. **Model + KB**: Agent references Knowledge Base
   - Model server: `{agent-name}-model`
   - User manually calls Query Service for context
   - Flexible but requires orchestration

3. **Full (Model + KB + Orchestration)**: Agent provisions orchestrator
   - Orchestrator: `{agent-name}` service
   - Unified `/v1/chat` endpoint
   - Orchestrator automatically calls Query Service for RAG context
   - Simplest RAG experience

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
