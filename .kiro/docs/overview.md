# Overview

## Purpose

ArchonKnowledgeBaseInfrastructure is a fully self-contained RAG (Retrieval-Augmented Generation) knowledge base system. It provides document ingestion, embedding generation, vector storage, semantic retrieval, and MCP (Model Context Protocol) server capabilities that enable RAG workflows and tool-based AI interactions.

The system includes its own Embedding Service using the BAAI/bge-base-en-v1.5 model via sentence-transformers. No external dependencies are required for deployment.

## Key Components

**Embedding Service**: A FastAPI application that generates vector embeddings using the BAAI/bge-base-en-v1.5 model. Provides an OpenAI-compatible `/v1/embeddings` endpoint.

**Query Service**: A FastAPI application that accepts natural language queries, generates embeddings via the internal Embedding Service, performs vector similarity search in Qdrant, and returns relevant document chunks.

**MCP Server** (optional): A Model Context Protocol server that exposes knowledge base tools (`search`, `get_document`, `list_sources`) for AI assistants like Kiro CLI. Automatically provisioned when enabled in the KnowledgeBase CRD.

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

## MCP Integration

When MCP server is enabled in the KnowledgeBase CRD, the platform controller automatically provisions:

1. **MCP Server Deployment** - Runs archon-mcp-server image
2. **MCP Server Service** - Exposes tools at `http://mcp-server-{kb-name}.{namespace}:8090`
3. **Tool Discovery** - AI assistants can discover and invoke knowledge base tools

**Tools exposed:**
- `{kb-name}.search` - Search documentation with ranked results
- `{kb-name}.get_document` - Retrieve full document text
- `{kb-name}.list_sources` - List available repositories

**Integration with Kiro:**
Repositories using ArchonKiroTemplate include `.kiro/steering/archon-rag.md`, which instructs Kiro to discover and use MCP tools automatically.

## Deployment Model

The Knowledge Base is fully self-contained and can be deployed independently or integrated with Agents:

1. **Deploy Knowledge Base** - All components deploy together
2. **RAG is enabled** - Query Service is ready to serve retrieval requests
3. **Optional: Enable MCP Server** - Add `spec.mcpServer: {}` to KnowledgeBase CRD
4. **Optional: Deploy Agent** - Reference Knowledge Base in Agent CRD for RAG-augmented inference

## Relationship to Agent

The Knowledge Base operates independently from Agents. The Agent CRD provisions model servers (vLLM) and optionally references a Knowledge Base for RAG capabilities.

**Three Agent deployment patterns:**

1. **Model only** - Agent provisions model server without Knowledge Base reference
   - Direct model inference via `{agent-name}-model` service
   - No RAG capabilities

2. **Model + Knowledge Base** - Agent references existing Knowledge Base
   - Model server at `{agent-name}-model` service
   - User manually calls both services for RAG workflow
   - Flexible but requires orchestration

3. **Model + Knowledge Base + Orchestration** - Agent provisions orchestrator
   - Unified `/v1/chat` endpoint at `{agent-name}` service
   - Orchestrator automatically combines model inference + KB retrieval
   - Simplest RAG experience

**Benefits of separation:**
- Knowledge Base deployed without Agent running
- Multiple Agents share one Knowledge Base
- Knowledge bases updated without Agent downtime
- Different teams manage their own knowledge bases
- Flexible scaling of storage vs. compute
- MCP server provides tool-based access for AI assistants

## Terminology

| Term | Definition |
|------|------------|
| Agent | CRD that provisions model server (vLLM) and optionally orchestrator for RAG |
| Knowledge Base | This system - document storage and retrieval infrastructure |
| Embedding Service | FastAPI application generating vector embeddings |
| Query Service | FastAPI application handling retrieval requests |
| Monitor Service | CronJob that syncs documents from GitHub |
| Orchestrator | Optional component that combines model inference + KB retrieval into unified endpoint |
| Chunk | A segment of a document stored with its embedding |
| repo_file_path | Unique document identifier combining repo URL and file path |

**Source**
- `src/embedding/main.py` - Embedding service implementation
- `src/query/main.py` - Query service implementation
- `src/monitor/main.py` - Monitor service implementation
- `manifests/` - Kubernetes deployment manifests
