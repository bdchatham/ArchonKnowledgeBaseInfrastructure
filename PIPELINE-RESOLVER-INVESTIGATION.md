# Pipeline Resolver Investigation Summary

## Problem Statement

When deploying the Knowledge Base pipeline via AphexCLI, the `taskRef` resolver configuration is being stripped. The pipeline should reference the `argocd-deployment` task in `tekton-pipelines` namespace using a cluster resolver, but the deployed pipeline only has a simple `taskRef` with `kind: Task` and `name: argocd-deployment`.

## Expected vs Actual

**Expected taskRef (in source YAML):**
```yaml
taskRef:
  resolver: cluster
  params:
    - name: kind
      value: task
    - name: name
      value: argocd-deployment
    - name: namespace
      value: tekton-pipelines
```

**Actual taskRef (in deployed Pipeline):**
```yaml
taskRef:
  kind: Task
  name: argocd-deployment
```

## Evidence

### Working Example: archon-agent pipeline
The `archon-agent` pipeline in the cluster correctly has the resolver format:
```yaml
taskRef:
  params:
  - name: kind
    value: task
  - name: name
    value: git-clone
  - name: namespace
    value: tekton-pipelines
  resolver: cluster
```

### RepoBinding Comparison

**archon-agent-binding pipelineSpec** contains:
```yaml
taskRef:
  resolver: cluster
  params:
    - name: kind
      value: task
    ...
```

**archon-knowledge-base-binding pipelineSpec** contains:
```yaml
taskRef:
  name: argocd-deployment
```

This proves the Tekton SDK and platform controller DO support the resolver format - the archon-agent pipeline works correctly.

## Investigation Steps Taken

1. **Checked deployed pipeline** - Confirmed resolver is missing
2. **Checked platform controller code** (`repobinding_provisioners.go`) - The `provisionPipeline` function uses standard Kubernetes YAML decoder with Tekton SDK types. It does NOT modify the taskRef - it just decodes and re-encodes.
3. **Compared RepoBindings** - Found that archon-agent has resolver in its pipelineSpec, archon-knowledge-base does not
4. **Deleted and recreated pipeline** - Same result, resolver still stripped
5. **Verified source file** - The file in the editor shows resolver format, but `cat` command shows it without resolver

## Root Cause Hypothesis

There appears to be a **file synchronization issue** between:
- What the editor/Kiro sees (has resolver)
- What the filesystem actually contains (no resolver)

When the CLI reads the file, it reads the filesystem version which lacks the resolver configuration.

## Verification Needed

1. Manually verify file contents: `cat ArchonKnowledgeBaseInfrastructure/pipeline/deploy-knowledge-base.yaml`
2. If file lacks resolver, manually edit it to add the resolver format
3. Re-run the CLI command after confirming file is correct

## Correct taskRef Format

Based on the working archon-agent pipeline, the correct format is:
```yaml
tasks:
  - name: deploy-to-argocd
    taskRef:
      resolver: cluster
      params:
        - name: kind
          value: task
        - name: name
          value: argocd-deployment
        - name: namespace
          value: tekton-pipelines
    params:
      - name: app-name
        value: $(params.app-name)
      # ... rest of params
```

## Next Steps

1. Ensure the source file on disk has the correct resolver format
2. Delete existing pipeline: `AphexCLI/bin/aphex pipeline delete archon-knowledge-base --aphex-org=archon`
3. Recreate pipeline: `AphexCLI/bin/aphex pipeline create -f ArchonKnowledgeBaseInfrastructure/pipeline/deploy-knowledge-base.yaml --aphex-org=archon --repo-org=bdchatham --repo-name=ArchonKnowledgeBaseInfrastructure archon-knowledge-base`
4. Verify: `kubectl get pipeline archon-knowledge-base -n archon-knowledge-base -o yaml | grep -A 10 taskRef`

## Key Finding

The platform controller is NOT the problem. The Tekton SDK correctly handles resolver fields (proven by archon-agent). The issue is that the source file being read by the CLI does not contain the resolver configuration.
