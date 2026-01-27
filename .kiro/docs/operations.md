# Operations

## Deployment

### Prerequisites

Before deploying the Knowledge Base:

1. **ArgoCD** must be installed in the cluster
2. **Secrets must be configured** with actual values (GitHub token, PostgreSQL password)

The Knowledge Base is fully self-contained. No external services are required.

### Deployment via Tekton Pipeline

```bash
# Install the pipeline
kubectl apply -f pipeline/deploy-knowledge-base.yaml

# Run the pipeline
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

### Manual Deployment via Kustomize

```bash
# Apply all manifests
kubectl apply -k manifests/
```

### Deployment Order

The Kustomization applies resources in this order:
1. Namespace (`archon-knowledge-base`)
2. ConfigMap and Secrets
3. StatefulSets (Qdrant, PostgreSQL)
4. Init Job (creates schemas)
5. Embedding Deployment and Service
6. Query Deployment and Service
7. Monitor CronJob
8. Ingress

### Post-Deployment Verification

```bash
# Check all pods are running
kubectl get pods -n archon-knowledge-base

# Expected pods:
# - embedding-*
# - query-* (2 replicas)
# - qdrant-0
# - postgres-0
# - mcp-server-* (if MCP enabled in KnowledgeBase CRD)

# Verify Embedding service health
kubectl port-forward svc/embedding-svc 8000:8000 -n archon-knowledge-base
curl http://localhost:8000/ready

# Verify Query service health
kubectl port-forward svc/query 8080:8080 -n archon-knowledge-base
curl http://localhost:8080/ready

# Verify MCP server health (if enabled)
kubectl port-forward svc/mcp-server-{kb-name} 8090:8090 -n archon-knowledge-base
curl http://localhost:8090/health

# Check via Ingress
curl -k https://archon-kb.home.local/health
```

### Enabling MCP Server

The MCP server is provisioned automatically by the platform controller when enabled in the KnowledgeBase CRD:

```yaml
apiVersion: aphex.io/v1alpha1
kind: KnowledgeBase
metadata:
  name: platform-docs
  namespace: archon-knowledge-base
spec:
  displayName: "Platform Documentation"
  repositories:
    - url: https://github.com/bdchatham/ArchonAgent
    - url: https://github.com/bdchatham/AphexPlatformInfrastructure
  mcpServer: {}  # Presence enables MCP server with defaults
  # Or customize:
  # mcpServer:
  #   port: 8090                                              # optional, default: 8090
  #   image: ghcr.io/bdchatham/archon-mcp-server:latest     # optional
  #   queryServiceURL: http://query.archon-knowledge-base:8080  # optional
```

**What gets provisioned:**
- Deployment: `mcp-server-platform-docs`
- Service: `mcp-server-platform-docs:8090`
- Service URL: `http://mcp-server-platform-docs.archon-knowledge-base:8090`

**Check MCP server status:**
```bash
kubectl get knowledgebase platform-docs -n archon-knowledge-base -o jsonpath='{.status.mcpServer}'
```

**Expected output:**
```json
{
  "deployed": true,
  "serviceName": "mcp-server-platform-docs",
  "serviceURL": "http://mcp-server-platform-docs.archon-knowledge-base:8090",
  "readyReplicas": 1
}
```


## Monitoring

### Health Endpoints

| Service | Endpoint | Purpose | Success Response |
|---------|----------|---------|------------------|
| Embedding | `/health` | Liveness check | `{"status": "healthy"}` |
| Embedding | `/ready` | Model loaded check | `{"status": "ready", "model": "..."}` |
| Query | `/health` | Liveness check | `{"status": "healthy"}` |
| Query | `/ready` | Readiness check | `{"status": "ready", ...}` |
| MCP Server | `/health` | Liveness check | `{"status": "healthy"}` |

### Key Metrics to Watch

- Embedding service pod restarts and memory usage
- Query service pod restarts
- MCP server pod restarts (if enabled)
- Monitor CronJob success/failure rate
- Qdrant collection size and query latency
- PostgreSQL connection pool usage

### Kubernetes Probes

Embedding service probes:
- **Liveness**: `/health` every 30s, initial delay 30s
- **Readiness**: `/ready` every 10s, initial delay 60s

MCP server probes (if enabled):
- **Liveness**: `/health` every 30s, initial delay 10s
- **Readiness**: `/health` every 10s, initial delay 5s

Query service probes:
- **Liveness**: `/health` every 30s, initial delay 10s
- **Readiness**: `/ready` every 10s, initial delay 5s

### Logs

```bash
# Embedding service logs
kubectl logs -l app=embedding -n archon-knowledge-base

# Query service logs
kubectl logs -l app=query -n archon-knowledge-base

# Monitor job logs (most recent)
kubectl logs -l app=monitor -n archon-knowledge-base --tail=100

# Qdrant logs
kubectl logs -l app=qdrant -n archon-knowledge-base

# PostgreSQL logs
kubectl logs -l app=postgres -n archon-knowledge-base
```

## Runbooks

### Embedding Service Not Ready

**Symptom**: Embedding `/ready` returns 503

**Diagnosis**:
```bash
kubectl logs -l app=embedding -n archon-knowledge-base | grep -i error
```

**Common Causes**:
1. Model not loaded - Check memory allocation (requires ~2GB)
2. OOM killed - Check pod events and increase memory limits

**Resolution**:
```bash
# Check pod events
kubectl describe pod -l app=embedding -n archon-knowledge-base

# Restart embedding service
kubectl rollout restart deployment/embedding -n archon-knowledge-base
```

### Query Service Returns 503

**Symptom**: `/ready` returns 503, `/v1/retrieve` fails

**Diagnosis**:
```bash
kubectl logs -l app=query -n archon-knowledge-base | grep -i error
```

**Common Causes**:
1. Embedding service unavailable - Check embedding pod status
2. Qdrant unreachable - Check Qdrant pod status
3. Collection doesn't exist - Re-run init job

**Resolution**:
```bash
# Verify Embedding service is healthy
kubectl port-forward svc/embedding-svc 8000:8000 -n archon-knowledge-base
curl http://localhost:8000/ready

# Verify Qdrant is healthy
kubectl exec -it qdrant-0 -n archon-knowledge-base -- curl localhost:6333/health

# Re-run init job if needed
kubectl delete job knowledge-base-init -n archon-knowledge-base
kubectl apply -f manifests/init-job.yaml
```

### Monitor Job Failing

**Symptom**: CronJob pods show Error or CrashLoopBackOff

**Diagnosis**:
```bash
kubectl logs job/monitor-<timestamp> -n archon-knowledge-base
```

**Common Causes**:
1. Invalid GitHub token - Check secret value
2. Embedding service timeout - Check embedding pod health
3. PostgreSQL connection failed - Check postgres pod

**Resolution**:
```bash
# Update GitHub token
kubectl create secret generic knowledge-base-secrets \
  --from-literal=github_token=<new-token> \
  --from-literal=postgres_user=archon \
  --from-literal=postgres_password=<password> \
  -n archon-knowledge-base \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Qdrant Collection Missing

**Symptom**: Vector search returns errors about missing collection

**Resolution**:
```bash
# Re-create collection via init job
kubectl delete job knowledge-base-init -n archon-knowledge-base --ignore-not-found
kubectl apply -f manifests/init-job.yaml

# Or manually create
kubectl exec -it qdrant-0 -n archon-knowledge-base -- \
  curl -X PUT http://localhost:6333/collections/archon-docs \
  -H "Content-Type: application/json" \
  -d '{"vectors": {"size": 768, "distance": "Cosine"}}'
```

### PostgreSQL Schema Missing

**Symptom**: Monitor fails with "relation document_state does not exist"

**Resolution**:
```bash
kubectl exec -it postgres-0 -n archon-knowledge-base -- psql -U archon -d archon -c "
CREATE TABLE IF NOT EXISTS document_state (
    repo_file_path VARCHAR(512) PRIMARY KEY,
    sha VARCHAR(64) NOT NULL,
    last_modified TIMESTAMP NOT NULL,
    last_checked TIMESTAMP NOT NULL,
    content_hash VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_last_checked ON document_state(last_checked);
"
```

## Configuration Management

### ConfigMap Settings

| Key | Default | Description |
|-----|---------|-------------|
| `embedding_service_url` | `http://embedding-svc:8000` | Internal Embedding Service endpoint |
| `embedding_model` | `BAAI/bge-base-en-v1.5` | Embedding model name |
| `vector_db_url` | `http://qdrant:6333` | Qdrant URL |
| `collection_name` | `archon-docs` | Qdrant collection |
| `tracker_db_url` | `postgresql://...` | PostgreSQL connection |
| `retrieval_k` | `5` | Default results count |
| `chunk_size` | `1000` | Chunk size in characters |
| `chunk_overlap` | `200` | Overlap between chunks |
| `repositories` | JSON array | Repositories to monitor |

### Adding a Repository

Edit the ConfigMap to add repositories:
```yaml
repositories: |
  [
    {"url": "https://github.com/org/repo", "branch": "mainline", "paths": [".kiro/docs"]}
  ]
```

Apply and restart Monitor:
```bash
kubectl apply -f manifests/configmap.yaml
kubectl delete job -l app=monitor -n archon-knowledge-base
```

### Manually Triggering Document Ingestion

To immediately ingest documents without waiting for the CronJob schedule:

```bash
# Create a one-time job from the CronJob
kubectl create job monitor-manual --from=cronjob/monitor -n archon-knowledge-base

# Watch the job progress
kubectl logs -f job/monitor-manual -n archon-knowledge-base

# Clean up after completion
kubectl delete job monitor-manual -n archon-knowledge-base
```

## Agent Deployment

The Agent CRD provisions model servers (vLLM) and optionally references Knowledge Bases for RAG capabilities.

### Deployment Patterns

**1. Model Only (No RAG)**

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

**What gets provisioned:**
- Deployment: `llama-70b-model` (vLLM server)
- Service: `llama-70b-model:8000`

**Access:**
```bash
curl http://llama-70b-model.agents:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "meta-llama/Llama-3.1-70B-Instruct", "messages": [{"role": "user", "content": "Hello"}]}'
```

**2. Model + Knowledge Base (Manual RAG)**

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

**What gets provisioned:**
- Deployment: `llama-70b-rag-model` (vLLM server)
- Service: `llama-70b-rag-model:8000`

**Access (manual orchestration):**
```bash
# 1. Get context from Knowledge Base
CONTEXT=$(curl http://query.archon-knowledge-base:8080/v1/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I deploy an Agent?", "k": 3}')

# 2. Call model with context
curl http://llama-70b-rag-model.agents:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"meta-llama/Llama-3.1-70B-Instruct\", \"messages\": [{\"role\": \"system\", \"content\": \"$CONTEXT\"}, {\"role\": \"user\", \"content\": \"How do I deploy an Agent?\"}]}"
```

**3. Full (Model + KB + Orchestration)**

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
  orchestration: {}  # Enables orchestrator with defaults
```

**What gets provisioned:**
- Deployment: `llama-70b-unified-model` (vLLM server)
- Service: `llama-70b-unified-model:8000`
- Deployment: `llama-70b-unified` (orchestrator)
- Service: `llama-70b-unified:8000` (unified endpoint)

**Access (automatic RAG):**
```bash
# Single endpoint handles RAG automatically
curl http://llama-70b-unified.agents:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "meta-llama/Llama-3.1-70B-Instruct", "messages": [{"role": "user", "content": "How do I deploy an Agent?"}]}'
```

### Checking Agent Status

```bash
# Get Agent status
kubectl get agent llama-70b-unified -n agents -o yaml

# Check model server status
kubectl get agent llama-70b-unified -n agents -o jsonpath='{.status.modelServer}'

# Check orchestrator status (if provisioned)
kubectl get agent llama-70b-unified -n agents -o jsonpath='{.status.orchestrator}'
```

**Expected status:**
```yaml
status:
  phase: Ready
  message: "Agent is ready"
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

### Troubleshooting Agent Deployment

**Model server not ready:**
```bash
# Check model server pods
kubectl get pods -l agent=llama-70b-unified,app=model-server -n agents

# Check logs
kubectl logs -l agent=llama-70b-unified,app=model-server -n agents

# Common issues:
# - Insufficient GPU resources
# - Model download timeout
# - OOM (increase memory limits)
```

**Orchestrator not ready:**
```bash
# Check orchestrator pods
kubectl get pods -l agent=llama-70b-unified,app=orchestrator -n agents

# Check logs
kubectl logs -l agent=llama-70b-unified,app=orchestrator -n agents

# Common issues:
# - Model server URL unreachable
# - Query service URL unreachable
# - KnowledgeBase not found
```

**Source**
- `manifests/configmap.yaml` - Configuration
- `manifests/secrets.yaml` - Secrets template
- `manifests/embedding-deployment.yaml` - Embedding deployment with probes
- `manifests/query-deployment.yaml` - Query deployment with probes
- `manifests/monitor-cronjob.yaml` - Monitor CronJob
- `manifests/init-job.yaml` - Schema initialization
- `pipeline/README.md` - Pipeline documentation
- AphexPlatformInfrastructure: `platform/base/platform-controller/controller/controllers/agent_controller.go` - Agent controller
- AphexPlatformInfrastructure: `platform/base/platform-controller/controller/api/v1alpha1/agent_types.go` - Agent CRD
