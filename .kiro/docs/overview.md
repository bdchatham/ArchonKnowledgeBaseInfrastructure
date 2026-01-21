# Overview

## Purpose

ArchonKnowledgeBaseInfrastructure is a standalone RAG (Retrieval-Augmented Generation) knowledge base system designed to be deployed alongside an Agent (LLM model server). It provides document ingestion, vector storage, and semantic retrieval capabilities that enable RAG workflows.

The system is intentionally decoupled from the model server - it requires an external embedding service endpoint but doesn't bundle its own LLM. This allows teams to deploy knowledge bases independently and connect them to their existing Agent infrastructure.

## Key Components

**Query Service**: A FastAPI application that accepts natural language queries, generates embeddings via the Agent, performs vector similarity search in Qdrant, and returns relevant document chunks.

**Monitor Service**: A scheduled job that watches configured GitHub repositories for documentation changes, detects modifications via SHA comparison, and keeps the vector store synchronized with source content.

**Vector Store (Qdrant)**: Stores document embeddings for fast similarity search. Each chunk is stored with metadata including source file path and chunk index.

**State Tracker (PostgreSQL)**: Tracks document versions to enable efficient change detection. Stores SHA hashes and timestamps for each monitored document.

## RAG Workflow

From the client's perspective, RAG is transparent - they make a single OpenAI-compatible API call to the Agent:

1. Client sends query to Agent
2. Agent internally calls Knowledge Base Query service
3. Query service generates embedding via Agent's embedding endpoint
4. Query service searches Qdrant for similar chunks
5. Agent receives context and performs inference
6. Client receives response with RAG-augmented context

## Deployment Model

1. **Deploy Agent** (model server with embedding + inference capabilities)
2. **Deploy Knowledge Base** (configured with Agent's embedding endpoint URL)
3. **RAG is enabled** - Agent can now retrieve context from Knowledge Base

## Relationship to Agent

The Knowledge Base depends on the Agent for embedding generation but operates independently for storage and retrieval. This separation allows:

- Multiple knowledge bases to share one Agent
- Knowledge bases to be updated without Agent downtime
- Different teams to manage their own knowledge bases
- Flexible scaling of storage vs. compute

## Terminology

| Term | Definition |
|------|------------|
| Agent | The vLLM model server providing embedding and inference capabilities |
| Knowledge Base | This system - document storage and retrieval infrastructure |
| Query Service | FastAPI application handling retrieval requests |
| Monitor Service | CronJob that syncs documents from GitHub |
| Chunk | A segment of a document stored with its embedding |
| repo_file_path | Unique document identifier combining repo URL and file path |

**Source**
- `src/query/main.py` - Query service implementation
- `src/monitor/main.py` - Monitor service implementation
- `manifests/` - Kubernetes deployment manifests
