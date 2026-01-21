# Overview

## Purpose

ArchonKnowledgeBaseInfrastructure is a fully self-contained RAG (Retrieval-Augmented Generation) knowledge base system. It provides document ingestion, embedding generation, vector storage, and semantic retrieval capabilities that enable RAG workflows.

The system includes its own Embedding Service using the BAAI/bge-base-en-v1.5 model via sentence-transformers. No external dependencies are required for deployment.

## Key Components

**Embedding Service**: A FastAPI application that generates vector embeddings using the BAAI/bge-base-en-v1.5 model. Provides an OpenAI-compatible `/v1/embeddings` endpoint.

**Query Service**: A FastAPI application that accepts natural language queries, generates embeddings via the internal Embedding Service, performs vector similarity search in Qdrant, and returns relevant document chunks.

**Monitor Service**: A scheduled job that watches configured GitHub repositories for documentation changes, detects modifications via SHA comparison, and keeps the vector store synchronized with source content.

**Vector Store (Qdrant)**: Stores document embeddings for fast similarity search. Each chunk is stored with metadata including source file path and chunk index.

**State Tracker (PostgreSQL)**: Tracks document versions to enable efficient change detection. Stores SHA hashes and timestamps for each monitored document.

## RAG Workflow

The Knowledge Base provides context retrieval for RAG-augmented responses:

1. Client sends query to Agent (or directly to Knowledge Base)
2. Query Service generates embedding via internal Embedding Service
3. Query Service searches Qdrant for similar chunks
4. Relevant context is returned to the caller
5. Agent uses context to augment LLM inference

## Deployment Model

The Knowledge Base is fully self-contained:

1. **Deploy Knowledge Base** - All components deploy together
2. **RAG is enabled** - Query Service is ready to serve retrieval requests
3. **Optional: Deploy Agent** - For LLM inference with RAG augmentation

## Relationship to Agent

The Knowledge Base operates independently from the Agent (vLLM model server). This separation allows:

- Knowledge Base to be deployed without Agent running
- Multiple Agents to share one Knowledge Base
- Knowledge bases to be updated without Agent downtime
- Different teams to manage their own knowledge bases
- Flexible scaling of storage vs. compute

## Terminology

| Term | Definition |
|------|------------|
| Agent | The vLLM model server providing LLM inference capabilities |
| Knowledge Base | This system - document storage and retrieval infrastructure |
| Embedding Service | FastAPI application generating vector embeddings |
| Query Service | FastAPI application handling retrieval requests |
| Monitor Service | CronJob that syncs documents from GitHub |
| Chunk | A segment of a document stored with its embedding |
| repo_file_path | Unique document identifier combining repo URL and file path |

**Source**
- `src/embedding/main.py` - Embedding service implementation
- `src/query/main.py` - Query service implementation
- `src/monitor/main.py` - Monitor service implementation
- `manifests/` - Kubernetes deployment manifests
