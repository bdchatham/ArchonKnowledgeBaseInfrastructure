# Architecture

## System Design

The Archon Knowledge Base uses a **declarative CRD-based architecture** with two deployment layers:

1. **Infrastructure Layer** - Core services (Qdrant, PostgreSQL, Query, Embedding, Monitor)
2. **Configuration Layer** - KnowledgeBase CRD specifies repositories and MCP settings

### KnowledgeBase CRD Model

```yaml
apiVersion: aphex.io/v1alpha1
kind: KnowledgeBase
metadata:
  name: platform-docs
  namespace: archon-knowledge-base
spec:
  displayName: "Aphex Platform Documentation"
  repositories:
    - url: https://github.com/bdchatham/AphexPlatformInfrastructure
      branch: mainline
      paths: [".kiro/docs"]
  mcp:
    image: ghcr.io/bdchatham/archon-mcp-server:latest
    port: 3000
    replicas: 1
```

The platform controller watches this CRD and provisions:
- MCP server deployment (if `spec.mcp` is set)
- MCP server service
- Configuration for Monitor service to track repositories

The architecture follows a clear separation of concerns:
- **Embedding Service**: Generates vector embeddings using sentence-transformers
- **Query Service**: Handles retrieval requests synchronously
- **Monitor Service**: Handles document ingestion asynchronously via CronJob
- **MCP Server**: Exposes knowledge base tools for AI assistants (optional)
- **Storage Layer**: Provides persistence for vectors and state

## Components

### KnowledgeBase CRD

Declarative configuration for the knowledge base:

**Required fields:**
- `spec.displayName` - Human-readable name
- `spec.repositories[]` - List of repositories to track
  - `url` - Repository URL (required)
  - `branch` - Git branch (optional, default: main)
  - `paths[]` - Documentation paths (optional, default: [".kiro/docs"])

**Optional fields:**
- `spec.description` - Description of the knowledge base
- `spec.mcp` - MCP server configuration
  - `image` - Container image (required if mcp set)
  - `port` - Server port (required if mcp set)
  - `queryServiceURL` - Query service URL (optional, auto-computed)
  - `replicas` - Number of replicas (optional, default: 1)

**Status tracking:**
- `status.phase` - Current state (Pending, Ready, Failed)
- `status.mcp.deployed` - MCP server deployment status
- `status.mcp.serviceURL` - Internal service URL

**Source**
- `manifests/knowledgebase.yaml` - KnowledgeBase CRD manifest
- Platform controller API types (in AphexPlatformInfrastructure)

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

A batch job that runs as a Kubernetes CronJob every 15 minutes. Processes repositories configured in the KnowledgeBase CRD to detect and ingest documentation changes.

Responsibilities:
- Read repository configuration from KnowledgeBase CRD
- Fetch file listings from GitHub repositories
- Compare SHA hashes against tracked state in PostgreSQL
- Chunk new/modified documents and generate embeddings via `EmbeddingClient` from AphexServiceClients
- Store embeddings in Qdrant with source metadata
- Remove embeddings for deleted documents

The service uses `AphexServiceClients` for resilient communication with the embedding service.

### MCP Server (Optional)

A Model Context Protocol server that exposes knowledge base tools for AI assistants. Automatically provisioned by the platform controller when `spec.mcp` is set in the KnowledgeBase CRD.

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

## Code Graph Module

The Code Graph provides a GraphQL-queryable representation of code structure and relationships. It stores code symbols (functions, classes, methods) and their relationships (contains, references, implements) in PostgreSQL, enabling graph traversal for code navigation.

### Purpose

- Store code symbols extracted from SCIP indexes with ARN-based identification
- Store relationships between symbols (contains, references, implements, extends, imports, documents)
- Expose GraphQL API for querying code structure and traversing relationships
- Support bulk sync operations from SCIP parse results

### Components

**PostgreSQL Tables:**
- `code_graph_nodes`: Stores code symbols with ARN as primary key
- `code_graph_edges`: Stores relationships with foreign key constraints and cascade delete

**GraphQL API:**
- Query operations: `node`, `searchNodes`, `findReferences`, `symbolsInFile`, `symbolsInPackage`, `traverse`
- Mutation operations: `syncFromScip`, `prunePackage`

**Source**
- `src/graph/schema.py` - GraphQL schema definition
- `src/graph/resolvers.py` - GraphQL resolvers
- `src/graph/models.py` - SQLAlchemy models
- `migrations/001_code_graph_tables.sql` - Database schema

## Enhanced Vector Store

The Vector Store is enhanced with ARN metadata to enable navigation between semantic search results and the Code Graph. Each chunk includes ARN references for graph traversal.

### Purpose

- Store document embeddings with ARN metadata for graph integration
- Enable filtering by package and symbol kind in similarity search
- Return ARN metadata with search results for Code Graph navigation
- Support bulk sync operations from Archon documentation

### Enhancements

**ARN Metadata Fields:**
- `arn`: ARN of the documented symbol/file
- `related_arns`: ARNs referenced in the chunk content
- `symbol_name`: Name of the symbol being documented
- `symbol_kind`: Kind of symbol (function, class, method, etc.)
- `package`: Package name for filtering

**Source**
- `src/vector/models.py` - ArchonChunk, SearchResult dataclasses
- `src/vector/store.py` - Vector store service with ARN support
- `src/vector/filters.py` - Qdrant filter builders

## Sync Service

The Sync Service orchestrates synchronization of code intelligence data (SCIP parse results and Archon documentation) to the knowledge base. It ensures the Code Graph and Vector Store reflect the current state of the codebase.

### Purpose

- Transform SCIP symbols to Code Graph nodes using ARN as primary key
- Transform SCIP relationships to Code Graph edges
- Chunk and embed Archon docs with ARN metadata
- Upsert chunks to Vector Store
- Prune stale nodes and chunks when symbols are removed
- Skip synchronization when SCIP index hash hasn't changed

### Components

**Sync Orchestrator:**
- `sync_package()`: Sync a single package's code intelligence
- `sync_workspace()`: Sync all packages in a workspace

**Adapters:**
- `GraphSyncAdapter`: Transforms SCIP data to Code Graph operations
- `VectorSyncAdapter`: Chunks, embeds, and syncs Archon docs

**Change Detection:**
- Hash-based change detection to skip unchanged packages
- State stored in `.archon/sync-state.json`

**Source**
- `src/sync/service.py` - KnowledgeBaseSyncService
- `src/sync/graph_adapter.py` - Graph sync adapter
- `src/sync/vector_adapter.py` - Vector sync adapter
- `src/sync/change_detector.py` - Hash-based change detection

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Embedding Service | FastAPI + sentence-transformers | 0.109+ |
| Query Service | FastAPI + Uvicorn | 0.109+ |
| MCP Server | FastAPI + AphexServiceClients | latest |
| Code Graph | PostgreSQL + GraphQL | 15-alpine |
| GraphQL Library | graphql-core or strawberry-graphql | latest |
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

## Phase 2 System Context

The following diagram shows the complete system with Phase 2 components (Code Graph, Enhanced Vector Store, Sync Service):

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ArchonKnowledgeBaseInfrastructure                        │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    Knowledge Base Sync Service                        │  │
│  │  ┌─────────────────┐              ┌─────────────────┐                │  │
│  │  │ Graph Sync      │              │ Vector Sync     │                │  │
│  │  │ Adapter         │              │ Adapter         │                │  │
│  │  └────────┬────────┘              └────────┬────────┘                │  │
│  └───────────┼────────────────────────────────┼──────────────────────────┘  │
│              │                                │                              │
│              ▼                                ▼                              │
│  ┌─────────────────────────┐    ┌─────────────────────────┐                │
│  │    PostgreSQL           │    │    Qdrant               │                │
│  │    Code Graph           │    │    Vector Store         │                │
│  │    - code_graph_nodes   │    │    - embeddings         │                │
│  │    - code_graph_edges   │    │    - ARN metadata       │                │
│  └─────────────────────────┘    └─────────────────────────┘                │
│              │                                │                              │
│              ▼                                ▼                              │
│  ┌─────────────────────────┐    ┌─────────────────────────┐                │
│  │    GraphQL API          │    │    Query Service        │                │
│  │    - node queries       │    │    - semantic search    │                │
│  │    - traversal          │    │    - ARN-enriched       │                │
│  └─────────────────────────┘    └─────────────────────────┘                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│                    External Consumers                                        │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │ ArchonMCPServer │    │ ArchonDocTools  │    │ Kiro Agent      │         │
│  │ - archon.search │    │ - sync trigger  │    │ - queries       │         │
│  │ - archon.graph  │    │ - SCIP indexes  │    │ - navigation    │         │
│  │ - archon.resolve│    │ - Archon docs   │    │                 │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
```

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
- `src/common/embedding_client.py` - Embedding client
- `src/common/vector_store.py` - Qdrant wrapper
- `src/common/state_tracker.py` - PostgreSQL state tracker
- `manifests/embedding-deployment.yaml` - Embedding deployment
- `manifests/query-deployment.yaml` - Query deployment
- `manifests/qdrant-statefulset.yaml` - Qdrant deployment
- `manifests/postgres-statefulset.yaml` - PostgreSQL deployment
