# CLAUDE.md — Archon Knowledge Base Infrastructure Contract

You are working in a repository that participates in the **Archon** RAG knowledge base.

Your primary responsibility in this repo:

> Keep `.kiro/docs` accurate, structured, and grounded in the actual code and infrastructure, so Archon can answer questions about this system reliably.

This file defines how you should behave when editing code or documentation in this repository.

---

## 1. Repository Scope

- **Service name:** `ArchonKnowledgeBaseInfrastructure`
- **High-level purpose:**  
  `Standalone RAG knowledge base system that manages document ingestion, vector storage, and retrieval. Provides a Query service for semantic search and a Monitor service for keeping embeddings synchronized with source repositories.`
- **Type:** `infrastructure + service`
- **Primary runtime:** `Python containerized services on Kubernetes`

When documenting, focus on this service's role as a standalone knowledge base that depends on an external Agent (model server) for embedding generation.

---

## 2. Archon Documentation Layout

All documentation that Archon ingests for this repo MUST live under `.kiro/docs/`.

Use these files and purposes:

1. `.kiro/docs/overview.md`  
   - High-level summary of the knowledge base system
   - Relationship to Agent (model server) for embeddings
   - RAG workflow explanation

2. `.kiro/docs/architecture.md`  
   - Query service and Monitor service components
   - Storage layer (Qdrant, PostgreSQL)
   - Data flows for ingestion and retrieval
   - Dependency on external embedding service

3. `.kiro/docs/operations.md`  
   - Kubernetes deployment procedures
   - Prerequisite: Agent must be running first
   - Monitoring and troubleshooting
   - Configuration management

4. `.kiro/docs/api.md`  
   - Query service REST API (`/v1/retrieve`, `/health`, `/ready`)
   - Request/response formats
   - Error codes and handling

5. `.kiro/docs/data-models.md`  
   - Qdrant collection schema (vectors, metadata)
   - PostgreSQL document_state table schema
   - ConfigMap and Secrets structure

6. `.kiro/docs/faq.md`  
   - Common questions about deployment
   - Troubleshooting embedding service connectivity
   - Scaling and performance

---

## 3. Grounding and Provenance

All non-trivial statements in `.kiro/docs` must be grounded in this repository or clearly labeled as TODO.

**Acceptable sources:**
- Code in this repository (`src/`, `manifests/`, `pipeline/`)
- Existing `.kiro/docs/*` files
- Explicit specs under `.kiro/specs/` if present

**Unacceptable sources:**
- Guesses or prior knowledge that cannot be confirmed from this repo
- Generic knowledge of Kubernetes or Python frameworks

Include a "Source" subsection for significant sections:

**Source**
- `src/query/main.py`
- `manifests/query-deployment.yaml`

---

## 4. RAG-Friendly Writing Principles

1. **Short, focused sections** (400-800 tokens each)
2. **Direct, factual tone**
3. **Explicit structure** with numbered lists and bullet points
4. **Avoid noise** - no generic tutorials

---

## 5. Behavior When Coding vs Documenting

### 5.1 When Implementing or Refactoring Code

Update `.kiro/docs` when:
- Adding new API endpoints → update `api.md`
- Changing data schemas → update `data-models.md`
- Modifying deployment → update `operations.md`
- Changing architecture → update `architecture.md`

### 5.2 When Answering Questions

1. Check `.kiro/docs` first
2. If docs are missing or wrong, correct them
3. Then answer by referencing the docs

---

## 6. What Not To Do

- Do not store secrets, tokens, or credentials in `.kiro/` or this file
- Do not describe security-sensitive details unless explicitly allowed
- Do not use `.kiro/docs` as a scratchpad
- Do not contradict this file unless the repo owner updates it

---

## 7. Archon Integration Notes

- **Archon ingestion paths:** `.kiro/docs/` only
- **Docs that should not be ingested:** None - all docs are public
- **Special instructions:** This repo provides the knowledge base that Archon uses for RAG. Meta-documentation about Archon itself should be accurate and up-to-date.

---

By following this contract, you ensure that:
- Engineers and agents get accurate, inspectable answers
- Archon's RAG index reflects the real system architecture
- Documentation remains maintainable and tightly coupled to the codebase
