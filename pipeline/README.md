# Archon Knowledge Base Deployment Pipeline

This directory contains the Tekton Pipeline for deploying the Archon Knowledge Base infrastructure.

## Prerequisites

Before deploying the Knowledge Base, ensure:

1. **Agent (Model Server) is Running**
   ```bash
   kubectl get pods -n archon-system
   # Verify vLLM pod is Running and Ready
   ```

2. **ArgoCD is Installed**
   ```bash
   kubectl get pods -n argocd
   ```

3. **argocd-deployment Task is Available**
   ```bash
   kubectl get task argocd-deployment -n pipeline-system
   ```

4. **Secrets are Configured**
   Update `manifests/secrets.yaml` with actual values before deployment:
   - `github_token`: GitHub personal access token for repository access
   - `postgres_password`: Secure password for PostgreSQL

## Pipeline Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `git-url` | `https://github.com/bdchatham/ArchonKnowledgeBaseInfrastructure` | Repository URL |
| `git-revision` | `main` | Branch, tag, or commit SHA |
| `manifest-path` | `manifests` | Path to Kubernetes manifests |
| `app-name` | `archon-knowledge-base` | ArgoCD Application name |
| `target-namespace` | `archon-knowledge-base` | Deployment namespace |

## Deployment

### Install the Pipeline

```bash
kubectl apply -f pipeline/deploy-knowledge-base.yaml
```

### Run the Pipeline

```bash
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

### Monitor Progress

```bash
# Watch PipelineRun status
kubectl get pipelinerun -n pipeline-system -w

# Check ArgoCD Application status
kubectl get application archon-knowledge-base -n argocd

# Watch pods come up
kubectl get pods -n archon-knowledge-base -w
```

## Verification

After deployment completes:

1. **Check All Pods are Running**
   ```bash
   kubectl get pods -n archon-knowledge-base
   ```
   Expected pods:
   - `qdrant-0` (StatefulSet)
   - `postgres-0` (StatefulSet)
   - `query-*` (Deployment, 2 replicas)
   - `knowledge-base-init-*` (Job, Completed)

2. **Verify Query Service Health**
   ```bash
   kubectl port-forward svc/query 8080:8080 -n archon-knowledge-base
   curl http://localhost:8080/health
   ```

3. **Check Ingress**
   ```bash
   curl -k https://archon-kb.home.local/health
   ```

4. **Verify Monitor CronJob**
   ```bash
   kubectl get cronjob monitor -n archon-knowledge-base
   ```

## Troubleshooting

### Query Service Not Ready

If `/ready` returns 503:
- Check embedding service connectivity: `kubectl logs -l app=query -n archon-knowledge-base`
- Verify Agent is running: `kubectl get pods -n archon-system`
- Check ConfigMap `embedding_service_url` is correct

### Monitor Job Failing

Check logs:
```bash
kubectl logs -l app=monitor -n archon-knowledge-base
```

Common issues:
- GitHub token invalid or missing
- Embedding service unavailable
- PostgreSQL connection failed

### Init Job Stuck

Check init container logs:
```bash
kubectl logs job/knowledge-base-init -c init-postgres -n archon-knowledge-base
kubectl logs job/knowledge-base-init -c init-qdrant -n archon-knowledge-base
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    archon-knowledge-base                     │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Qdrant    │  │  PostgreSQL │  │    Query Service    │  │
│  │ (vectors)   │  │   (state)   │  │   (retrieval API)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              Monitor CronJob (every 15 min)             ││
│  │         Syncs documents from GitHub repositories        ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
                              │ embeddings
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      archon-system                           │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                   vLLM Model Server                     ││
│  │              (embeddings + inference)                   ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```
