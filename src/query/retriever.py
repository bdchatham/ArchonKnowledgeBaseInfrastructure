"""Retrieval logic for semantic search with optional code graph enrichment.

This module provides two-phase retrieval: semantic search on the vector
store followed by code graph traversal for related symbols. When the
code graph is unavailable, it falls back to semantic-only results.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from aphex_clients import EmbeddingClient
from ..common.vector_store import VectorStore, SearchResult

logger = logging.getLogger(__name__)

GRAPH_RESULT_SCORE_DISCOUNT = 0.8
DEFAULT_GRAPH_TRAVERSAL_DEPTH = 1


@dataclass
class RetrievalResult:
    """Result from a retrieval query."""

    content: str
    source: str
    chunk_index: int
    score: float
    metadata: Optional[dict] = field(default_factory=dict)


class Retriever:
    """Retrieves relevant document chunks for queries.

    Supports two-phase retrieval: semantic search on the vector store,
    then code graph traversal for related symbols when a graph repository
    is available. Falls back to semantic-only when the graph is unavailable.

    Usage:
        retriever = Retriever(
            embedding_client=EmbeddingClient(...),
            vector_store=VectorStore(...),
            default_k=5,
            graph_repository=graph_repo,
        )

        results = await retriever.retrieve("How does the system work?")
    """

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        default_k: int = 5,
        graph_repository=None,
    ):
        """Initialize the retriever.

        Args:
            embedding_client: Client for generating embeddings
            vector_store: Qdrant vector store wrapper
            default_k: Default number of results to return
            graph_repository: Optional GraphRepository for code graph traversal
        """
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        self.default_k = default_k
        self.graph_repository = graph_repository

    async def retrieve(
        self,
        query: str,
        k: Optional[int] = None,
    ) -> List[RetrievalResult]:
        """Retrieve relevant document chunks for a query.

        Performs semantic search, then enriches with graph-traversed
        context when a graph repository is available and ARNs are found.

        Args:
            query: Natural language query string
            k: Number of results to return (uses default_k if not specified)

        Returns:
            List of RetrievalResult objects ordered by relevance
        """
        if k is None:
            k = self.default_k

        query_embedding = await self.embedding_client.embed_single(query)
        search_results = await self.vector_store.search(query_embedding, k=k)

        semantic_results = self._to_retrieval_results(search_results)

        graph_results = await self._enrich_with_graph(semantic_results)

        merged = self._merge_results(semantic_results, graph_results)

        logger.debug(
            f"Retrieved {len(semantic_results)} semantic + "
            f"{len(graph_results)} graph results for query: {query[:50]}..."
        )
        return merged

    async def retrieve_with_context(
        self,
        query: str,
        k: Optional[int] = None,
    ) -> str:
        """Retrieve and format context for LLM augmentation.

        Args:
            query: Natural language query string
            k: Number of results to return

        Returns:
            Formatted context string for LLM prompt augmentation
        """
        results = await self.retrieve(query, k)

        if not results:
            return ""

        context_parts = []
        for i, result in enumerate(results, 1):
            context_parts.append(
                f"[Source {i}: {result.source}]\n{result.content}"
            )

        return "\n\n---\n\n".join(context_parts)

    def _to_retrieval_results(
        self, search_results: List[SearchResult]
    ) -> List[RetrievalResult]:
        """Convert SearchResult objects to RetrievalResult objects."""
        return [
            RetrievalResult(
                content=result.content,
                source=result.source,
                chunk_index=result.chunk_index,
                score=result.score,
                metadata=result.metadata or {},
            )
            for result in search_results
        ]

    def _extract_arns(self, results: List[RetrievalResult]) -> list[str]:
        """Extract unique ARNs from retrieval result metadata."""
        arns = []
        seen = set()
        for result in results:
            arn = (result.metadata or {}).get("arn", "")
            if arn and arn not in seen:
                arns.append(arn)
                seen.add(arn)
        return arns

    async def _query_related_symbols(self, arns: list[str]) -> list:
        """Query the code graph for symbols related to the given ARNs.

        Returns a list of GraphNode objects from graph traversal.
        """
        from ..graph.models import EdgeType

        related_nodes = []
        seen_arns = set(arns)

        for arn in arns:
            traversal_types = [
                EdgeType.REFERENCES,
                EdgeType.CONTAINS,
                EdgeType.IMPLEMENTS,
                EdgeType.EXTENDS,
            ]
            nodes = await self.graph_repository.traverse(
                arn, traversal_types, depth=DEFAULT_GRAPH_TRAVERSAL_DEPTH
            )
            for node in nodes:
                if node.arn not in seen_arns:
                    related_nodes.append(node)
                    seen_arns.add(node.arn)

        return related_nodes

    def _graph_nodes_to_results(self, nodes: list) -> List[RetrievalResult]:
        """Convert GraphNode objects to RetrievalResult objects.

        Graph-traversed results receive a discounted score since they
        are contextually related but not direct semantic matches.
        """
        results = []
        for node in nodes:
            content_parts = [f"# {node.name}"]
            if node.kind:
                content_parts.append(f"Kind: {node.kind.value}")
            if node.signature:
                content_parts.append(f"Signature: {node.signature}")
            if node.documentation:
                content_parts.append(node.documentation)

            content = "\n".join(content_parts)
            source = node.file_path or node.path

            results.append(
                RetrievalResult(
                    content=content,
                    source=source,
                    chunk_index=0,
                    score=GRAPH_RESULT_SCORE_DISCOUNT,
                    metadata={"arn": node.arn, "origin": "graph"},
                )
            )
        return results

    async def _enrich_with_graph(
        self, semantic_results: List[RetrievalResult]
    ) -> List[RetrievalResult]:
        """Enrich semantic results with graph-traversed context.

        Returns an empty list when the graph repository is unavailable
        or no ARNs are found in the semantic results.
        """
        if self.graph_repository is None:
            return []

        arns = self._extract_arns(semantic_results)
        if not arns:
            return []

        try:
            related_nodes = await self._query_related_symbols(arns)
            return self._graph_nodes_to_results(related_nodes)
        except Exception:
            logger.warning(
                "Code graph query failed, falling back to semantic-only",
                exc_info=True,
            )
            return []

    def _merge_results(
        self,
        semantic_results: List[RetrievalResult],
        graph_results: List[RetrievalResult],
    ) -> List[RetrievalResult]:
        """Merge semantic and graph results, deduplicating by ARN.

        Semantic results take priority. Graph results are appended
        only if their ARN is not already present in semantic results.
        """
        merged = list(semantic_results)

        semantic_arns = set()
        for result in semantic_results:
            arn = (result.metadata or {}).get("arn", "")
            if arn:
                semantic_arns.add(arn)

        for result in graph_results:
            arn = (result.metadata or {}).get("arn", "")
            if arn not in semantic_arns:
                merged.append(result)

        return merged
