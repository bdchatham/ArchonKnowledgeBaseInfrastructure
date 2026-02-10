# Data Models

## Overview

The Knowledge Base uses three storage systems with distinct schemas:
- **Qdrant**: Vector embeddings with document and ARN metadata
- **PostgreSQL**: Document state for change detection and Code Graph storage
- **State Files**: Sync state for change detection

Both database schemas are initialized by the `knowledge-base-init` Job on first deployment.

## Code Graph PostgreSQL Schema

### Table: code_graph_nodes

Stores code symbols extracted from SCIP indexes with ARN as primary key.

**Schema:**
```sql
CREATE TABLE code_graph_nodes (
    arn TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('code', 'doc', 'k8s', 'infra')),
    workspace TEXT NOT NULL,
    package TEXT NOT NULL,
    path TEXT NOT NULL,
    symbol TEXT,
    kind TEXT CHECK (kind IN ('function', 'class', 'method', 'variable', 'type', 'module', 'file', 'package')),
    name TEXT NOT NULL,
    signature TEXT,
    documentation TEXT,
    file_path TEXT,
    line_number INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    index_hash TEXT
);

CREATE INDEX idx_nodes_package ON code_graph_nodes(package);
CREATE INDEX idx_nodes_kind ON code_graph_nodes(kind);
CREATE INDEX idx_nodes_path ON code_graph_nodes(path);
```

**Columns:**
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `arn` | TEXT | PRIMARY KEY | Archon Resource Name (unique identifier) |
| `type` | TEXT | NOT NULL, CHECK | Resource type: code, doc, k8s, infra |
| `workspace` | TEXT | NOT NULL | Workspace identifier |
| `package` | TEXT | NOT NULL | Package/repository name |
| `path` | TEXT | NOT NULL | File path relative to package |
| `symbol` | TEXT | NULLABLE | Symbol name (optional) |
| `kind` | TEXT | CHECK | Symbol kind: function, class, method, variable, type, module, file, package |
| `name` | TEXT | NOT NULL | Human-readable name |
| `signature` | TEXT | NULLABLE | Function/method signature |
| `documentation` | TEXT | NULLABLE | Documentation string |
| `file_path` | TEXT | NULLABLE | Absolute file path |
| `line_number` | INTEGER | NULLABLE | Line number in source file |
| `created_at` | TIMESTAMP | DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMP | DEFAULT NOW() | Last update timestamp |
| `index_hash` | TEXT | NULLABLE | SCIP index hash for change detection |

### Table: code_graph_edges

Stores relationships between code symbols with foreign key constraints.

**Schema:**
```sql
CREATE TABLE code_graph_edges (
    id SERIAL PRIMARY KEY,
    from_arn TEXT NOT NULL REFERENCES code_graph_nodes(arn) ON DELETE CASCADE,
    to_arn TEXT NOT NULL REFERENCES code_graph_nodes(arn) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('contains', 'references', 'implements', 'extends', 'imports', 'documents')),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (from_arn, to_arn, type)
);

CREATE INDEX idx_edges_from ON code_graph_edges(from_arn);
CREATE INDEX idx_edges_to ON code_graph_edges(to_arn);
CREATE INDEX idx_edges_type ON code_graph_edges(type);
```

**Columns:**
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing ID |
| `from_arn` | TEXT | NOT NULL, FK | Source node ARN (cascade delete) |
| `to_arn` | TEXT | NOT NULL, FK | Target node ARN (cascade delete) |
| `type` | TEXT | NOT NULL, CHECK | Edge type: contains, references, implements, extends, imports, documents |
| `created_at` | TIMESTAMP | DEFAULT NOW() | Creation timestamp |

**Edge Types:**
| Type | Description |
|------|-------------|
| `contains` | Parent contains child (e.g., class contains method) |
| `references` | Symbol references another symbol |
| `implements` | Class/type implements interface |
| `extends` | Class extends another class |
| `imports` | Module imports another module |
| `documents` | Documentation node documents code node |

### ARN Format

ARNs (Archon Resource Names) follow this format:

```
arn:archon:<type>:<workspace>/<package>/<path>#<symbol>
```

| Component | Description | Example |
|-----------|-------------|---------|
| `type` | Resource type | `code`, `doc`, `k8s`, `infra` |
| `workspace` | Workspace identifier | `personal-work` |
| `package` | Package/repository name | `ArchonKnowledgeBaseInfrastructure` |
| `path` | File path relative to package | `src/graph/schema.py` |
| `symbol` | Symbol name (optional) | `GraphQLService.execute` |

**Examples:**
```
arn:archon:code:personal-work/ArchonKnowledgeBaseInfrastructure/src/graph/schema.py#GraphQLService
arn:archon:doc:personal-work/ArchonAgent/src/orchestrator/main.py
arn:archon:code:personal-work/AphexCLI/cmd/root.go#Execute
```

**Source**
- `migrations/001_code_graph_tables.sql` - Database schema migration
- `src/graph/models.py` - SQLAlchemy models

## Qdrant Collection Schema

### Collection: archon-docs

Stores document chunk embeddings for similarity search with ARN metadata.

**Vector Configuration**:
| Property | Value |
|----------|-------|
| Collection Name | `archon-docs` |
| Vector Size | 768 |
| Distance Metric | Cosine |

**Point Structure**:
```json
{
  "id": 1234567890,
  "vector": [0.123, -0.456, ...],
  "payload": {
    "source": "https://github.com/org/repo/.kiro/docs/overview.md",
    "chunk_index": 0,
    "content": "Document chunk text content...",
    "arn": "arn:archon:doc:personal-work/ArchonAgent/src/orchestrator/main.py",
    "related_arns": [
      "arn:archon:code:personal-work/ArchonAgent/src/orchestrator/main.py#Orchestrator"
    ],
    "symbol_name": "Orchestrator",
    "symbol_kind": "class",
    "package": "ArchonAgent"
  }
}
```

**Payload Fields**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | Full path: repo URL + file path |
| `chunk_index` | integer | Yes | Position of chunk within document (0-indexed) |
| `content` | string | Yes | Chunk text content |
| `arn` | string | Yes | ARN of the documented symbol/file |
| `related_arns` | array | Yes | ARNs referenced in this chunk (can be empty) |
| `symbol_name` | string | No | Name of the symbol being documented |
| `symbol_kind` | string | No | Kind of symbol (function, class, method, etc.) |
| `package` | string | Yes | Package name for filtering |

**Point ID Generation**:
Point IDs are generated by hashing the string ID (`{arn}:{chunk_index}`) to a 64-bit integer:
```python
id = hash(f"{arn}:{chunk_index}") & 0x7FFFFFFFFFFFFFFF
```

### Collection Initialization

Created by init job:
```bash
curl -X PUT http://qdrant:6333/collections/archon-docs \
  -H "Content-Type: application/json" \
  -d '{"vectors": {"size": 768, "distance": "Cosine"}}'
```

## Enhanced Chunk Metadata

### ArchonChunk Dataclass

Python representation of a document chunk with ARN metadata:

```python
@dataclass
class ArchonChunk:
    """Document chunk with ARN metadata for graph traversal."""
    
    content: str              # Chunk text content
    source: str               # File path (e.g., "ArchonAgent/src/orchestrator/main.py")
    chunk_index: int          # Index of chunk within the source file
    
    # ARN metadata for graph integration
    arn: str                  # ARN of the documented symbol/file
    related_arns: list[str]   # ARNs referenced in this chunk
    
    # Additional context
    symbol_name: str | None   # Name of the symbol being documented
    symbol_kind: str | None   # Kind of symbol (function, class, etc.)
    package: str              # Package name
```

### SearchResult Dataclass

Python representation of a search result with ARN metadata:

```python
@dataclass
class SearchResult:
    """Search result with ARN metadata for graph traversal."""
    
    content: str              # Chunk text content
    source: str               # File path
    chunk_index: int          # Index of chunk within the source file
    score: float              # Similarity score (0.0 to 1.0)
    
    # ARN metadata
    arn: str                  # ARN of the documented symbol/file
    related_arns: list[str]   # ARNs referenced in this chunk
    symbol_name: str | None   # Name of the symbol
    symbol_kind: str | None   # Kind of symbol
    package: str              # Package name
```

**Source**
- `src/vector/models.py` - ArchonChunk, SearchResult dataclasses
- `src/vector/store.py` - Vector store service

## PostgreSQL Document State Schema

### Table: document_state

Tracks document versions for efficient change detection during monitoring.

**Schema:**
```sql
CREATE TABLE document_state (
    repo_file_path VARCHAR(512) PRIMARY KEY,
    sha VARCHAR(64) NOT NULL,
    last_modified TIMESTAMP NOT NULL,
    last_checked TIMESTAMP NOT NULL,
    content_hash VARCHAR(64)
);

CREATE INDEX idx_last_checked ON document_state(last_checked);
```

**Columns**:
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `repo_file_path` | VARCHAR(512) | PRIMARY KEY | Unique document identifier (repo URL + file path) |
| `sha` | VARCHAR(64) | NOT NULL | Git blob SHA from GitHub API |
| `last_modified` | TIMESTAMP | NOT NULL | When document was last ingested |
| `last_checked` | TIMESTAMP | NOT NULL | When document was last checked for changes |
| `content_hash` | VARCHAR(64) | NULLABLE | SHA-256 hash of document content |

**Example Row**:
```
repo_file_path: https://github.com/bdchatham/ArchonAgent/.kiro/docs/overview.md
sha: a1b2c3d4e5f6...
last_modified: 2025-01-21 10:30:00
last_checked: 2025-01-21 10:45:00
content_hash: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
```

### DocumentState Dataclass

Python representation used by StateTracker:
```python
@dataclass
class DocumentState:
    repo_file_path: str      # Primary key
    sha: str                 # Git SHA for change detection
    last_modified: datetime  # Last ingestion time
    last_checked: datetime   # Last check time
    content_hash: str | None # Content SHA-256
```

## Sync State Storage

### State File Location

`<workspace>/.archon/sync-state.json`

### State File Schema

```json
{
  "packages": {
    "ArchonDocumentationMCPTools": {
      "index_hash": "abc123def456...",
      "last_synced": "2024-01-15T10:30:00Z"
    },
    "ArchonKnowledgeBaseInfrastructure": {
      "index_hash": "def456abc123...",
      "last_synced": "2024-01-15T10:35:00Z"
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `packages` | object | Map of package name to sync state |
| `index_hash` | string | SHA-256 hash of the SCIP index content |
| `last_synced` | string | ISO 8601 timestamp of last successful sync |

**Source**
- `src/sync/change_detector.py` - Hash-based change detection

## ConfigMap Structure

Configuration stored in Kubernetes ConfigMap `knowledge-base-config`:

| Key | Type | Example |
|-----|------|---------|
| `embedding_service_url` | string | `http://embedding-svc:8000` |
| `embedding_model` | string | `BAAI/bge-base-en-v1.5` |
| `vector_db_url` | string | `http://qdrant:6333` |
| `collection_name` | string | `archon-docs` |
| `tracker_db_url` | string | `postgresql://archon:password@postgres:5432/archon` |
| `retrieval_k` | string | `5` |
| `chunk_size` | string | `1000` |
| `chunk_overlap` | string | `200` |
| `repositories` | JSON string | See below |

### Repository Configuration

The `repositories` field contains a JSON array of repository configurations:
```json
[
  {
    "url": "https://github.com/bdchatham/ArchonAgent",
    "branch": "mainline",
    "paths": [".kiro/docs"]
  }
]
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | Yes | - | GitHub repository URL |
| `branch` | string | No | `main` | Branch to monitor |
| `paths` | array | No | `[".kiro/docs"]` | Paths to scan for Markdown files |

## Secrets Structure

Sensitive configuration in Kubernetes Secret `knowledge-base-secrets`:

| Key | Description |
|-----|-------------|
| `github_token` | GitHub personal access token for API access |
| `postgres_user` | PostgreSQL username |
| `postgres_password` | PostgreSQL password |

## Data Flow

### Document Identification

Documents are uniquely identified by `repo_file_path`, which combines:
- Repository URL: `https://github.com/owner/repo`
- File path: `.kiro/docs/overview.md`
- Result: `https://github.com/owner/repo/.kiro/docs/overview.md`

This identifier is used consistently across:
- PostgreSQL `document_state.repo_file_path`
- Qdrant payload `source` field
- Vector point ID generation

### Chunking Parameters

Documents are split into chunks with configurable parameters:
- **chunk_size**: Maximum characters per chunk (default: 1000)
- **chunk_overlap**: Overlap between consecutive chunks (default: 200)

Chunks are created with clean boundaries at sentence or word breaks when possible.

**Source**
- `src/common/state_tracker.py` - DocumentState dataclass and PostgreSQL operations
- `src/common/vector_store.py` - Qdrant operations and SearchResult dataclass
- `src/monitor/chunker.py` - Chunk dataclass and chunking logic
- `manifests/init-job.yaml` - Schema initialization
- `manifests/configmap.yaml` - Configuration structure
- `manifests/secrets.yaml` - Secrets template
