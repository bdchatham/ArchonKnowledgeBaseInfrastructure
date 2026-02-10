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

## GraphQL Code Graph API

The Code Graph exposes a GraphQL API for querying code structure and relationships.

### Base URL

- **Internal**: `http://graph.archon-knowledge-base.svc.cluster.local:8080/graphql`

### GraphQL Schema

#### Types

```graphql
type Node {
  arn: ID!
  type: NodeType!
  workspace: String!
  package: String!
  path: String!
  symbol: String
  kind: SymbolKind
  name: String!
  signature: String
  documentation: String
  filePath: String
  lineNumber: Int
  
  # Relationship traversal
  contains: [Node!]!
  containedBy: Node
  references: [Node!]!
  referencedBy: [Node!]!
  implements: [Node!]!
  implementedBy: [Node!]!
  extends: [Node!]!
  extendedBy: [Node!]!
  imports: [Node!]!
  importedBy: [Node!]!
  documentationNode: Node
}

enum NodeType {
  CODE
  DOC
  K8S
  INFRA
}

enum SymbolKind {
  FUNCTION
  CLASS
  METHOD
  VARIABLE
  TYPE
  MODULE
  FILE
  PACKAGE
}

enum EdgeType {
  CONTAINS
  REFERENCES
  IMPLEMENTS
  EXTENDS
  IMPORTS
  DOCUMENTS
}
```

#### Query Operations

| Operation | Description | Parameters |
|-----------|-------------|------------|
| `node(arn: ID!)` | Lookup node by ARN | `arn`: ARN of the node |
| `searchNodes(name: String!, kind: SymbolKind, package: String, limit: Int)` | Search by name pattern | `name`: search pattern, `kind`: optional filter, `package`: optional filter, `limit`: max results (default 20) |
| `findReferences(arn: ID!)` | Find all references to a symbol | `arn`: ARN of the symbol |
| `symbolsInFile(path: String!, package: String!)` | List symbols in a file | `path`: file path, `package`: package name |
| `symbolsInPackage(package: String!, kind: SymbolKind)` | List symbols in a package | `package`: package name, `kind`: optional filter |
| `traverse(startArn: ID!, edgeTypes: [EdgeType!]!, depth: Int)` | Traverse relationships | `startArn`: starting node, `edgeTypes`: edge types to follow, `depth`: max depth (default 1) |

#### Mutation Operations

| Operation | Description | Parameters |
|-----------|-------------|------------|
| `syncFromScip(packagePath: String!, symbols: [SymbolInput!]!, relationships: [RelationshipInput!]!, indexHash: String!)` | Bulk upsert from SCIP | `packagePath`: package path, `symbols`: symbol data, `relationships`: relationship data, `indexHash`: SCIP index hash |
| `prunePackage(package: String!, keepArns: [ID!]!)` | Remove stale nodes | `package`: package name, `keepArns`: ARNs to keep |

### Example Queries

**Lookup by ARN:**
```graphql
query {
  node(arn: "arn:archon:code:personal-work/ArchonAgent/src/orchestrator/main.py#Orchestrator") {
    name
    kind
    signature
    documentation
    references {
      arn
      name
    }
  }
}
```

**Search by name:**
```graphql
query {
  searchNodes(name: "Orchestrator", kind: CLASS, limit: 10) {
    arn
    name
    package
    filePath
    lineNumber
  }
}
```

**Traverse relationships:**
```graphql
query {
  traverse(
    startArn: "arn:archon:code:personal-work/ArchonAgent/src/orchestrator/main.py#Orchestrator"
    edgeTypes: [REFERENCES, IMPLEMENTS]
    depth: 2
  ) {
    arn
    name
    kind
  }
}
```

**Source**
- `src/graph/schema.py` - GraphQL schema definition
- `src/graph/resolvers.py` - GraphQL resolvers

## Enhanced Search Results

The Query Service returns ARN-enriched search results for graph traversal.

### Enhanced Response Format

```json
{
  "chunks": [
    {
      "content": "The deployment pipeline uses ArgoCD...",
      "source": "https://github.com/org/repo/.kiro/docs/operations.md",
      "chunk_index": 2,
      "score": 0.89,
      "arn": "arn:archon:doc:personal-work/ArchonAgent/src/orchestrator/main.py",
      "related_arns": [
        "arn:archon:code:personal-work/ArchonAgent/src/orchestrator/main.py#Orchestrator"
      ],
      "symbol_name": "Orchestrator",
      "symbol_kind": "class",
      "package": "ArchonAgent"
    }
  ],
  "query": "How does the deployment pipeline work?"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `arn` | string | ARN of the documented symbol/file |
| `related_arns` | array | ARNs referenced in the chunk content |
| `symbol_name` | string | Name of the symbol being documented (optional) |
| `symbol_kind` | string | Kind of symbol (optional) |
| `package` | string | Package name for filtering |

### Filtering Parameters

The `/v1/retrieve` endpoint supports additional filtering:

| Parameter | Type | Description |
|-----------|------|-------------|
| `package` | string | Filter results by package name |
| `symbol_kind` | string | Filter results by symbol kind |

**Example with filters:**
```bash
curl -X POST http://localhost:8080/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "orchestrator", "k": 5, "package": "ArchonAgent", "symbol_kind": "class"}'
```

## Sync Service API

The Sync Service is exposed via MCP tool in ArchonDocumentationMCPTools.

### sync_to_knowledge_base Tool

**Input Schema:**
```json
{
  "workspacePath": "/path/to/workspace",
  "packages": ["ArchonAgent", "ArchonMCPServer"],
  "force": false
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `workspacePath` | string | Yes | Path to workspace root |
| `packages` | array | No | Specific packages to sync (all if omitted) |
| `force` | boolean | No | Bypass change detection (default: false) |

**Output Schema:**
```json
{
  "success": true,
  "packagesSynced": [
    {
      "package": "ArchonAgent",
      "nodesCreated": 150,
      "nodesUpdated": 25,
      "chunksUpserted": 45
    }
  ],
  "packagesSkipped": ["ArchonMCPServer"],
  "errors": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Overall sync success |
| `packagesSynced` | array | Packages that were synchronized |
| `packagesSkipped` | array | Packages skipped (unchanged hash) |
| `errors` | array | Errors by package |

**Source**
- `ArchonDocumentationMCPTools/src/tools/knowledge_base_sync.ts` - MCP tool implementation
- `src/sync/service.py` - KnowledgeBaseSyncService

## Agent CRD API

The Agent CRD provisions model servers and optionally orchestrators for RAG-augmented inference.

### Agent Resource

**API Group**: `aphex.io/v1alpha1`  
**Kind**: `Agent`

### Spec Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `displayName` | string | Yes | Human-readable name |
| `model` | ModelSpec | Yes | Model server configuration |
| `knowledgeBase` | KnowledgeBaseConfig | No | KB reference (nil = no RAG) |
| `orchestration` | OrchestrationConfig | No | Orchestrator config (nil = no orchestrator) |

### ModelSpec

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `provider` | string | Yes | - | Model provider (vllm, openai, anthropic, bedrock) |
| `name` | string | Yes | - | Model name (e.g., meta-llama/Llama-3.1-70B-Instruct) |
| `quantization` | string | No | - | Quantization method (e.g., awq, gptq) |
| `gpuCount` | int32 | No | 0 | Number of GPUs to allocate |
| `image` | string | No | vllm/vllm-openai:v0.14.1-cu130 | Container image |
| `port` | int32 | No | 8000 | Service port |

### KnowledgeBaseConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | - | KnowledgeBase resource name |
| `namespace` | string | No | Agent namespace | KnowledgeBase namespace |

### OrchestrationConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `image` | string | No | ghcr.io/bdchatham/archon-orchestrator:latest | Orchestrator image |
| `port` | int32 | No | 8000 | Service port |

### Status Fields

| Field | Type | Description |
|-------|------|-------------|
| `phase` | string | Pending, Ready, or Failed |
| `message` | string | Human-readable status message |
| `lastReconcileTime` | metav1.Time | Last reconciliation timestamp |
| `modelServer` | ModelServerStatus | Model server deployment status |
| `orchestrator` | OrchestratorStatus | Orchestrator status (nil if not provisioned) |

### ModelServerStatus

| Field | Type | Description |
|-------|------|-------------|
| `deployed` | bool | Whether model server is deployed |
| `serviceName` | string | Kubernetes Service name |
| `serviceURL` | string | Internal service URL |
| `readyReplicas` | int32 | Number of ready replicas |

### OrchestratorStatus

| Field | Type | Description |
|-------|------|-------------|
| `deployed` | bool | Whether orchestrator is deployed |
| `serviceName` | string | Kubernetes Service name |
| `serviceURL` | string | Internal service URL |
| `readyReplicas` | int32 | Number of ready replicas |

### Example: Minimal Agent

```yaml
apiVersion: aphex.io/v1alpha1
kind: Agent
metadata:
  name: llama-70b
  namespace: agents
spec:
  displayName: "Llama 3.1 70B"
  model:
    provider: vllm
    name: meta-llama/Llama-3.1-70B-Instruct
    gpuCount: 4
```

**Provisions:**
- Deployment: `llama-70b-model`
- Service: `llama-70b-model:8000`

### Example: Agent with KB Reference

```yaml
apiVersion: aphex.io/v1alpha1
kind: Agent
metadata:
  name: llama-70b-rag
  namespace: agents
spec:
  displayName: "Llama 3.1 70B with RAG"
  model:
    provider: vllm
    name: meta-llama/Llama-3.1-70B-Instruct
    gpuCount: 4
  knowledgeBase:
    name: platform-docs
    namespace: archon-knowledge-base
```

**Provisions:**
- Deployment: `llama-70b-rag-model`
- Service: `llama-70b-rag-model:8000`

**User must manually orchestrate RAG** (call Query Service, then model server).

### Example: Full Agent with Orchestration

```yaml
apiVersion: aphex.io/v1alpha1
kind: Agent
metadata:
  name: llama-70b-unified
  namespace: agents
spec:
  displayName: "Llama 3.1 70B Unified"
  model:
    provider: vllm
    name: meta-llama/Llama-3.1-70B-Instruct
    gpuCount: 4
  knowledgeBase:
    name: platform-docs
    namespace: archon-knowledge-base
  orchestration: {}
```

**Provisions:**
- Deployment: `llama-70b-unified-model`
- Service: `llama-70b-unified-model:8000`
- Deployment: `llama-70b-unified` (orchestrator)
- Service: `llama-70b-unified:8000` (unified endpoint)

**Orchestrator automatically handles RAG** (retrieves context, calls model).

### Checking Agent Status

```bash
kubectl get agent llama-70b-unified -n agents -o yaml
```

**Expected status:**
```yaml
status:
  phase: Ready
  message: "Agent is ready"
  lastReconcileTime: "2026-01-27T10:00:00Z"
  modelServer:
    deployed: true
    serviceName: llama-70b-unified-model
    serviceURL: http://llama-70b-unified-model.agents:8000
    readyReplicas: 1
  orchestrator:
    deployed: true
    serviceName: llama-70b-unified
    serviceURL: http://llama-70b-unified.agents:8000
    readyReplicas: 1
```

**Source**
- AphexPlatformInfrastructure: `platform/base/platform-controller/controller/api/v1alpha1/agent_types.go` - Agent CRD definition
- AphexPlatformInfrastructure: `platform/base/platform-controller/controller/controllers/agent_controller.go` - Agent controller
- AphexPlatformInfrastructure: `platform/base/crds/aphex.io_agents.yaml` - Generated CRD manifest
