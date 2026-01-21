# Archon Knowledge Base Deployment Pipeline

This directory contains the Tekton Pipeline for deploying the Archon Knowledge Base infrastructure.

## Overview

The Knowledge Base is fully self-contained with its own embedding service. No external dependencies are required for deployment.

## Prerequisites

1. **ArgoCD is Installed**
   ```bash
   kubectl get pods -n argocd
   ```

2. **argocd-deployment Task is Available**
   ```bash
   kubectl get task argocd-deployment -n pipeline-system
   ```

3. **Secrets are Configured**
   Update `manifests/secrets.yaml` with actual values before deployment:
   - `github_token`: GitHub personal access token for repository access
   - `postgres_password`: Secure password for PostgreSQL

## Pipeline Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `git-url` | `https://github.com/bdchatham/ArchonKnowledgeBaseInfrastructure` | Repository URL |
| `git-revision` | `mainline` | Branch, tag, or commit SHA |
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
   - `embedding-*` (Deployment)
   - `query-*` (Deployment, 2 replicas)
   - `knowledge-base-init-*` (Job, Completed)

2. **Verify Embedding Service Health**
   ```bash
   kubectl port-forward svc/embedding-svc 8000:8000 -n archon-knowledge-base
   curl http://localhost:8000/health
   ```

3. **Verify Query Service Health**
   ```bash
   kubectl port-forward svc/query 8080:8080 -n archon-knowledge-base
   curl http://localhost:8080/health
   ```

4. **Check Ingress**
   ```bash
   curl -k https://archon-kb.home.local/health
   ```

5. **Verify Monitor CronJob**
   ```bash
   kubectl get cronjob monitor -n archon-knowledge-base
   ```

## Troubleshooting

### Embedding Service Not Ready

If embedding `/ready` returns 503:
- Check model loading: `kubectl logs -l app=embedding -n archon-knowledge-base`
- Verify memory allocation is sufficient (model requires ~2GB)

### Query Service Not Ready

If `/ready` returns 503:
- Check embedding service connectivity: `kubectl logs -l app=query -n archon-knowledge-base`
- Verify embedding service is running: `kubectl get pods -l app=embedding -n archon-knowledge-base`

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
│  │                   Embedding Service                     ││
│  │           (BAAI/bge-base-en-v1.5 model)                ││
│  └─────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              Monitor CronJob (every 15 min)             ││
│  │         Syncs documents from GitHub repositories        ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```
