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

# Verify Embedding service health
kubectl port-forward svc/embedding-svc 8000:8000 -n archon-knowledge-base
curl http://localhost:8000/ready

# Verify Query service health
kubectl port-forward svc/query 8080:8080 -n archon-knowledge-base
curl http://localhost:8080/ready

# Check via Ingress
curl -k https://archon-kb.home.local/health
```


## Monitoring

### Health Endpoints

| Service | Endpoint | Purpose | Success Response |
|---------|----------|---------|------------------|
| Embedding | `/health` | Liveness check | `{"status": "healthy"}` |
| Embedding | `/ready` | Model loaded check | `{"status": "ready", "model": "..."}` |
| Query | `/health` | Liveness check | `{"status": "healthy"}` |
| Query | `/ready` | Readiness check | `{"status": "ready", ...}` |

### Key Metrics to Watch

- Embedding service pod restarts and memory usage
- Query service pod restarts
- Monitor CronJob success/failure rate
- Qdrant collection size and query latency
- PostgreSQL connection pool usage

### Kubernetes Probes

Embedding service probes:
- **Liveness**: `/health` every 30s, initial delay 30s
- **Readiness**: `/ready` every 10s, initial delay 60s

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
  -d '{"vectors": {"size": 384, "distance": "Cosine"}}'
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

**Source**
- `manifests/configmap.yaml` - Configuration
- `manifests/secrets.yaml` - Secrets template
- `manifests/embedding-deployment.yaml` - Embedding deployment with probes
- `manifests/query-deployment.yaml` - Query deployment with probes
- `manifests/monitor-cronjob.yaml` - Monitor CronJob
- `manifests/init-job.yaml` - Schema initialization
- `pipeline/README.md` - Pipeline documentation
