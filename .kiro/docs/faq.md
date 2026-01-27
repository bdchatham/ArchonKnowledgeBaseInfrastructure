# FAQ

## General Questions

### What is this repository for?

ArchonKnowledgeBaseInfrastructure provides a fully self-contained RAG (Retrieval-Augmented Generation) knowledge base system. It ingests documentation from GitHub repositories, generates embeddings using its own Embedding Service, stores them in a vector database, and provides a semantic search API for retrieval.

### How does this fit into the larger system?

The Knowledge Base is one component of the Archon system:
- **Knowledge Base** (this repo): Document storage, embedding generation, retrieval, and optional MCP server
- **Agent** (ArchonAgent): LLM model server providing inference
- **Platform** (AphexPlatformInfrastructure): GitOps infrastructure with ArgoCD and KnowledgeBase CRD controller

The Agent can call the Knowledge Base during inference to retrieve relevant context for RAG-augmented responses. AI assistants like Kiro can use the MCP server to search documentation when enabled.

### Why is the Knowledge Base separate from the Agent?

Separation provides flexibility:
- Knowledge Base can be deployed without Agent running
- Multiple Agents can share one Knowledge Base
- Knowledge bases can be updated without Agent downtime
- Different teams can manage their own knowledge bases
- Storage and compute can scale independently

## Deployment Questions

### What must be running before I deploy the Knowledge Base?

Nothing. The Knowledge Base is fully self-contained with its own Embedding Service. You only need:
- ArgoCD installed in the cluster
- Secrets configured (GitHub token, PostgreSQL password)

### How do I deploy the Knowledge Base?

Use the Tekton pipeline:
```bash
kubectl apply -f pipeline/deploy-knowledge-base.yaml
kubectl create -f - <<EOF
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: deploy-knowledge-base-
  namespace: pipeline-system
spec:
  pipelineRef:
    name: deploy-knowledge-base
EOF
```

Or apply manifests directly:
```bash
kubectl apply -k manifests/
```

### How do I add a new repository to monitor?

Edit the ConfigMap to add the repository:
```bash
kubectl edit configmap knowledge-base-config -n archon-knowledge-base
```

Add to the `repositories` JSON array:
```json
{"url": "https://github.com/org/repo", "branch": "mainline", "paths": [".kiro/docs"]}
```

The Monitor CronJob will pick up the new repository on its next run (every 15 minutes).


## Operational Questions

### How do I check if the Knowledge Base is healthy?

```bash
# Check pod status
kubectl get pods -n archon-knowledge-base

# Check Embedding service readiness
kubectl port-forward svc/embedding-svc 8000:8000 -n archon-knowledge-base
curl http://localhost:8000/ready

# Check Query service readiness
kubectl port-forward svc/query 8080:8080 -n archon-knowledge-base
curl http://localhost:8080/ready
```

A healthy Query response shows:
```json
{"status": "ready", "embedding_service": "healthy", "vector_store": "healthy"}
```

### Why is the Query service returning 503?

The `/ready` endpoint returns 503 when dependencies are unavailable:
- **"Embedding service not ready"**: Internal Embedding Service is down
- **"Vector store not ready"**: Qdrant is down or collection doesn't exist

Check logs for details:
```bash
kubectl logs -l app=query -n archon-knowledge-base
kubectl logs -l app=embedding -n archon-knowledge-base
```

### Why is the Embedding service returning 503?

The Embedding `/ready` endpoint returns 503 when the model isn't loaded:
- Model loading takes ~60 seconds on startup
- Check memory allocation (model requires ~2GB)

Check logs:
```bash
kubectl logs -l app=embedding -n archon-knowledge-base
```

### How do I manually trigger document ingestion?

Create a one-off Job from the CronJob spec:
```bash
kubectl create job --from=cronjob/monitor manual-sync -n archon-knowledge-base
kubectl logs -l job-name=manual-sync -n archon-knowledge-base -f
```

### How do I see what documents are indexed?

Query PostgreSQL for tracked documents:
```bash
kubectl exec -it postgres-0 -n archon-knowledge-base -- \
  psql -U archon -d archon -c "SELECT repo_file_path, last_modified FROM document_state;"
```

### How do I force re-ingestion of all documents?

Delete the state tracking data and run Monitor:
```bash
# Clear PostgreSQL state
kubectl exec -it postgres-0 -n archon-knowledge-base -- \
  psql -U archon -d archon -c "TRUNCATE document_state;"

# Trigger Monitor
kubectl create job --from=cronjob/monitor full-reindex -n archon-knowledge-base
```

## Development Questions

### How do I run the services locally?

1. Set environment variables (see `manifests/configmap.yaml` for required vars)
2. Start dependencies (Qdrant, PostgreSQL)
3. Run the services:

```bash
# Embedding service
PYTHONPATH=. uvicorn src.embedding.main:app --port 8000 --reload

# Query service
PYTHONPATH=. uvicorn src.query.main:app --port 8080 --reload

# Monitor (one-shot)
PYTHONPATH=. python -m src.monitor.main
```

### How do I test the retrieval API?

```bash
curl -X POST http://localhost:8080/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "How does deployment work?", "k": 3}'
```

## Archon-Specific Questions

### How is this repository ingested by Archon?

Archon reads all Markdown files under `.kiro/docs/` from this public GitHub repository. The Monitor service fetches these files, chunks them, generates embeddings via the internal Embedding Service, and stores them in Qdrant.

### How do I update documentation?

1. Edit files under `.kiro/docs/`
2. Ensure changes are grounded in code with "Source" references
3. Commit and push to the monitored branch (mainline)
4. Monitor will detect changes within 15 minutes

### What is the MCP server and how do I enable it?

The MCP (Model Context Protocol) server exposes knowledge base tools for AI assistants like Kiro CLI. It's automatically provisioned by the platform controller when `mcpServer` is set in the KnowledgeBase CRD:

```yaml
apiVersion: aphex.io/v1alpha1
kind: KnowledgeBase
metadata:
  name: my-kb
spec:
  mcpServer: {}  # Presence enables MCP server with defaults
  # Or customize:
  # mcpServer:
  #   image: custom-mcp:v1.0
  #   port: 9090
  #   queryServiceURL: http://my-query:8080
```

**Tools exposed:**
- `{kb-name}.search` - Search documentation with ranked results
- `{kb-name}.get_document` - Retrieve full document text
- `{kb-name}.list_sources` - List available repositories

**Integration with Kiro:**
Repositories using ArchonKiroTemplate include `.kiro/steering/archon-rag.md`, which instructs Kiro to discover and use MCP tools automatically.

### What embedding model is used?

The system uses `BAAI/bge-base-en-v1.5`, which produces 768-dimensional vectors. This model is served by the internal Embedding Service using sentence-transformers.

### How are documents chunked?

Documents are split into overlapping chunks:
- **Chunk size**: 1000 characters (configurable)
- **Overlap**: 200 characters (configurable)
- **Boundaries**: Chunks break at sentence or word boundaries when possible

**Source**
- `CLAUDE.md` - Repository contract
- `manifests/configmap.yaml` - Configuration reference
- `src/embedding/main.py` - Embedding service implementation
- `src/monitor/chunker.py` - Chunking implementation
- `pipeline/README.md` - Deployment guide
