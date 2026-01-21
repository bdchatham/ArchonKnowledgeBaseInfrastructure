# FAQ

## General Questions

### What is this repository for?

ArchonKnowledgeBaseInfrastructure provides a standalone RAG (Retrieval-Augmented Generation) knowledge base system. It ingests documentation from GitHub repositories, generates embeddings, stores them in a vector database, and provides a semantic search API for retrieval.

### How does this fit into the larger system?

The Knowledge Base is one component of the Archon system:
- **Agent** (ArchonAgent): LLM model server providing embeddings and inference
- **Knowledge Base** (this repo): Document storage and retrieval
- **Platform** (AphexPlatformInfrastructure): GitOps infrastructure with ArgoCD

The Agent calls the Knowledge Base during inference to retrieve relevant context for RAG-augmented responses.

### Why is the Knowledge Base separate from the Agent?

Separation provides flexibility:
- Multiple knowledge bases can share one Agent
- Knowledge bases can be updated without Agent downtime
- Different teams can manage their own knowledge bases
- Storage and compute can scale independently

## Deployment Questions

### What must be running before I deploy the Knowledge Base?

The Agent (vLLM model server) must be running in `archon-system` namespace. The Knowledge Base depends on the Agent's `/v1/embeddings` endpoint for generating vectors.

Verify Agent is ready:
```bash
kubectl get pods -n archon-system
curl http://vllm.archon-system.svc.cluster.local:8000/health
```

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
{"url": "https://github.com/org/repo", "branch": "main", "paths": [".kiro/docs"]}
```

The Monitor CronJob will pick up the new repository on its next run (every 15 minutes).

## Operational Questions

### How do I check if the Knowledge Base is healthy?

```bash
# Check pod status
kubectl get pods -n archon-knowledge-base

# Check Query service readiness
kubectl port-forward svc/query 8080:8080 -n archon-knowledge-base
curl http://localhost:8080/ready
```

A healthy response shows:
```json
{"status": "ready", "embedding_service": "healthy", "vector_store": "healthy"}
```

### Why is the Query service returning 503?

The `/ready` endpoint returns 503 when dependencies are unavailable:
- **"Embedding service not ready"**: Agent is down or unreachable
- **"Vector store not ready"**: Qdrant is down or collection doesn't exist
- **"Service not initialized"**: Query service is still starting

Check logs for details:
```bash
kubectl logs -l app=query -n archon-knowledge-base
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
3. Run the service:

```bash
# Query service
PYTHONPATH=. uvicorn src.query.main:app --reload

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

Archon reads all Markdown files under `.kiro/docs/` from this public GitHub repository. The Monitor service fetches these files, chunks them, generates embeddings, and stores them in Qdrant.

### How do I update documentation?

1. Edit files under `.kiro/docs/`
2. Ensure changes are grounded in code with "Source" references
3. Commit and push to the monitored branch
4. Monitor will detect changes within 15 minutes

### What embedding model is used?

The system uses `BAAI/bge-base-en-v1.5`, which produces 384-dimensional vectors. This model is served by the Agent's vLLM instance.

### How are documents chunked?

Documents are split into overlapping chunks:
- **Chunk size**: 1000 characters (configurable)
- **Overlap**: 200 characters (configurable)
- **Boundaries**: Chunks break at sentence or word boundaries when possible

**Source**
- `CLAUDE.md` - Repository contract
- `manifests/configmap.yaml` - Configuration reference
- `src/monitor/chunker.py` - Chunking implementation
- `pipeline/README.md` - Deployment guide
