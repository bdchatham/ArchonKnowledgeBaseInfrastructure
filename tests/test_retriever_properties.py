"""Property-based tests for combined retriever.

Feature: scip-sync-pipeline, Property 11: Combined retrieval

For any query where the vector store returns results containing ARN metadata,
the retriever SHALL extract those ARNs and issue a query to the code graph
for related symbols. The number of graph queries SHALL be greater than zero
when ARNs are present in semantic results.

**Validates: Requirements 7.1, 7.2**

Source:
- src/query/retriever.py
- src/common/vector_store.py
- .kiro/specs/scip-sync-pipeline/design.md (Property 11)
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

if "aphex_clients" not in sys.modules:
    _aphex_stub = types.ModuleType("aphex_clients")
    _aphex_stub.EmbeddingClient = type("EmbeddingClient", (), {})
    sys.modules["aphex_clients"] = _aphex_stub

from unittest.mock import AsyncMock

from src.common.vector_store import SearchResult
from src.graph.models import EdgeType, NodeType, SymbolKind
from src.query.retriever import Retriever, RetrievalResult


@dataclass
class FakeGraphNode:
    """Lightweight stand-in for GraphNode used in property tests."""

    arn: str
    type: NodeType
    workspace: str
    package: str
    path: str
    name: str
    symbol: Optional[str] = None
    kind: Optional[SymbolKind] = None
    signature: Optional[str] = None
    documentation: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    index_hash: Optional[str] = None


def _safe_identifier(min_size: int = 2, max_size: int = 15) -> st.SearchStrategy[str]:
    return st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu"), whitelist_characters="_"),
        min_size=min_size,
        max_size=max_size,
    ).filter(lambda s: s[0].isalpha())


def _arn_strategy() -> st.SearchStrategy[str]:
    """Generate valid ARN strings following the Archon format."""
    workspace = _safe_identifier(min_size=2, max_size=10)
    package = _safe_identifier(min_size=2, max_size=10)
    path_segment = _safe_identifier(min_size=2, max_size=8)
    extension = st.sampled_from([".py", ".ts", ".go", ".rs"])
    symbol = _safe_identifier(min_size=2, max_size=12)
    return st.builds(
        lambda w, p, ps, ext, s: f"arn:archon:code:{w}/{p}/{ps}{ext}#{s}",
        workspace,
        package,
        path_segment,
        extension,
        symbol,
    )


def _search_result_with_arn(arn: str) -> st.SearchStrategy[SearchResult]:
    """Generate a SearchResult that carries the given ARN in metadata."""
    content = _safe_identifier(min_size=5, max_size=40)
    source = _safe_identifier(min_size=3, max_size=15)
    chunk_index = st.integers(min_value=0, max_value=10)
    score = st.floats(min_value=0.1, max_value=1.0, allow_nan=False)
    return st.builds(
        lambda c, s, ci, sc: SearchResult(
            content=c, source=s, chunk_index=ci, score=sc, metadata={"arn": arn}
        ),
        content,
        source,
        chunk_index,
        score,
    )


def _search_result_without_arn() -> st.SearchStrategy[SearchResult]:
    """Generate a SearchResult with no ARN in metadata."""
    content = _safe_identifier(min_size=5, max_size=40)
    source = _safe_identifier(min_size=3, max_size=15)
    chunk_index = st.integers(min_value=0, max_value=10)
    score = st.floats(min_value=0.1, max_value=1.0, allow_nan=False)
    metadata_choice = st.sampled_from([{}, None, {"topic": "docs"}])
    return st.builds(
        lambda c, s, ci, sc, m: SearchResult(
            content=c, source=s, chunk_index=ci, score=sc, metadata=m
        ),
        content,
        source,
        chunk_index,
        score,
        metadata_choice,
    )


@st.composite
def search_results_with_arns(draw: st.DrawFn):
    """Generate a list of SearchResults where at least one has an ARN.

    Returns (search_results, unique_arns) so tests can verify graph query counts.
    """
    num_arn_results = draw(st.integers(min_value=1, max_value=5))
    num_plain_results = draw(st.integers(min_value=0, max_value=3))

    arns = draw(
        st.lists(_arn_strategy(), min_size=num_arn_results, max_size=num_arn_results, unique=True)
    )

    arn_results = []
    for arn in arns:
        result = draw(_search_result_with_arn(arn))
        arn_results.append(result)

    plain_results = draw(
        st.lists(_search_result_without_arn(), min_size=num_plain_results, max_size=num_plain_results)
    )

    all_results = arn_results + plain_results
    draw(st.randoms()).shuffle(all_results)

    return all_results, set(arns)


@st.composite
def search_results_without_arns(draw: st.DrawFn):
    """Generate a list of SearchResults with no ARN metadata."""
    num_results = draw(st.integers(min_value=1, max_value=5))
    return draw(
        st.lists(_search_result_without_arn(), min_size=num_results, max_size=num_results)
    )


def _build_retriever_with_tracking(search_results, graph_repository=None):
    """Build a Retriever with mocked embedding client and vector store."""
    embedding_client = AsyncMock()
    embedding_client.embed_single = AsyncMock(return_value=[0.1, 0.2, 0.3])
    vector_store = AsyncMock()
    vector_store.search = AsyncMock(return_value=search_results)
    return Retriever(
        embedding_client=embedding_client,
        vector_store=vector_store,
        default_k=10,
        graph_repository=graph_repository,
    )


def _make_graph_repo_mock():
    """Create a graph repository mock that returns empty traversal results."""
    graph_repo = AsyncMock()
    graph_repo.traverse = AsyncMock(return_value=[])
    return graph_repo


class TestCombinedRetrieverQueriesBothStores:
    """Property-based tests for combined retrieval.

    Feature: scip-sync-pipeline, Property 11: Combined retrieval

    **Validates: Requirements 7.1, 7.2**
    """

    @given(data=search_results_with_arns(), query=_safe_identifier(min_size=3, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_graph_queries_issued_when_arns_present(
        self, data: tuple, query: str
    ) -> None:
        """When semantic results contain ARN metadata and a graph repository
        is available, the number of graph queries is greater than zero.

        **Validates: Requirements 7.1, 7.2**
        """
        search_results, unique_arns = data
        graph_repo = _make_graph_repo_mock()
        retriever = _build_retriever_with_tracking(search_results, graph_repository=graph_repo)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        assert graph_repo.traverse.call_count > 0, (
            f"Expected graph queries > 0 when {len(unique_arns)} ARNs present, "
            f"but traverse was called {graph_repo.traverse.call_count} times"
        )

    @given(data=search_results_with_arns(), query=_safe_identifier(min_size=3, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_graph_query_count_matches_unique_arns(
        self, data: tuple, query: str
    ) -> None:
        """The retriever issues exactly one graph traversal per unique ARN
        extracted from semantic results.

        **Validates: Requirements 7.1, 7.2**
        """
        search_results, unique_arns = data
        graph_repo = _make_graph_repo_mock()
        retriever = _build_retriever_with_tracking(search_results, graph_repository=graph_repo)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        assert graph_repo.traverse.call_count == len(unique_arns), (
            f"Expected {len(unique_arns)} graph queries (one per unique ARN), "
            f"but got {graph_repo.traverse.call_count}"
        )

    @given(data=search_results_with_arns(), query=_safe_identifier(min_size=3, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_graph_queries_use_extracted_arns(
        self, data: tuple, query: str
    ) -> None:
        """Each graph traversal call uses an ARN extracted from the semantic
        results.

        **Validates: Requirements 7.1, 7.2**
        """
        search_results, unique_arns = data
        graph_repo = _make_graph_repo_mock()
        retriever = _build_retriever_with_tracking(search_results, graph_repository=graph_repo)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        queried_arns = {call.args[0] for call in graph_repo.traverse.call_args_list}
        assert queried_arns == unique_arns, (
            f"Queried ARNs {queried_arns} do not match extracted ARNs {unique_arns}"
        )

    @given(results=search_results_without_arns(), query=_safe_identifier(min_size=3, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_no_graph_queries_when_no_arns(
        self, results: list, query: str
    ) -> None:
        """When no ARNs are present in semantic results, no graph queries
        are made.

        **Validates: Requirements 7.1, 7.2**
        """
        graph_repo = _make_graph_repo_mock()
        retriever = _build_retriever_with_tracking(results, graph_repository=graph_repo)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        assert graph_repo.traverse.call_count == 0, (
            f"Expected zero graph queries when no ARNs present, "
            f"but traverse was called {graph_repo.traverse.call_count} times"
        )

    @given(data=search_results_with_arns(), query=_safe_identifier(min_size=3, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_no_graph_queries_when_graph_repo_is_none(
        self, data: tuple, query: str
    ) -> None:
        """When graph_repository is None, no graph queries are made
        regardless of ARN presence (semantic-only fallback).

        **Validates: Requirements 7.1, 7.2**
        """
        search_results, _ = data
        retriever = _build_retriever_with_tracking(search_results, graph_repository=None)

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        assert len(results) == len(search_results), (
            f"Expected {len(search_results)} semantic-only results, got {len(results)}"
        )


# ---------------------------------------------------------------------------
# Property 12: Combined query merge includes all results
# ---------------------------------------------------------------------------


def _fake_graph_node(arn: str) -> FakeGraphNode:
    """Build a FakeGraphNode with the given ARN and sensible defaults."""
    return FakeGraphNode(
        arn=arn,
        type=NodeType.CODE,
        workspace="ws",
        package="pkg",
        path="src/mod.py",
        name=arn.rsplit("#", 1)[-1] if "#" in arn else "symbol",
        kind=SymbolKind.FUNCTION,
        signature="def f(): ...",
        file_path="src/mod.py",
    )


@st.composite
def _disjoint_graph_arns(draw: st.DrawFn, semantic_arns: set[str]):
    """Generate a list of unique ARNs guaranteed disjoint from *semantic_arns*."""
    count = draw(st.integers(min_value=1, max_value=5))
    arns = draw(
        st.lists(
            _arn_strategy().filter(lambda a: a not in semantic_arns),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    return arns


@st.composite
def _overlapping_graph_arns(draw: st.DrawFn, semantic_arns: set[str]):
    """Generate graph ARNs where at least one overlaps with *semantic_arns*.

    Returns (all_graph_arns, overlapping_arns, unique_arns).
    """
    overlap_list = sorted(semantic_arns)
    overlap_count = draw(st.integers(min_value=1, max_value=max(1, len(overlap_list))))
    overlapping = draw(
        st.lists(
            st.sampled_from(overlap_list),
            min_size=overlap_count,
            max_size=overlap_count,
            unique=True,
        )
    )

    unique_count = draw(st.integers(min_value=0, max_value=3))
    unique = draw(
        st.lists(
            _arn_strategy().filter(lambda a: a not in semantic_arns),
            min_size=unique_count,
            max_size=unique_count,
            unique=True,
        )
    )

    all_arns = list(overlapping) + unique
    return all_arns, set(overlapping), set(unique)


def _make_graph_repo_returning_nodes(nodes_by_arn: dict[str, list[FakeGraphNode]]):
    """Create a graph repository mock that returns specific nodes per ARN."""
    graph_repo = AsyncMock()

    async def _traverse(arn, edge_types, depth=1):
        return nodes_by_arn.get(arn, [])

    graph_repo.traverse = AsyncMock(side_effect=_traverse)
    return graph_repo


class TestMergeCompleteness:
    """Property-based tests for merge completeness.

    Feature: scip-sync-pipeline, Property 12: Merge completeness

    For any query where semantic search returns S results and graph
    traversal returns G related symbols, the merged response SHALL
    contain at least S results. The merged result count SHALL be
    greater than or equal to S.

    **Validates: Requirements 7.3**
    """

    @given(
        data=search_results_with_arns(),
        query=_safe_identifier(min_size=3, max_size=20),
    )
    @settings(max_examples=100, deadline=None)
    def test_merged_count_gte_semantic_count(self, data: tuple, query: str) -> None:
        """The merged result count is always >= the semantic result count,
        regardless of what the graph returns.

        **Validates: Requirements 7.3**
        """
        search_results, semantic_arns = data
        graph_repo = _make_graph_repo_mock()
        retriever = _build_retriever_with_tracking(
            search_results, graph_repository=graph_repo
        )

        loop = asyncio.new_event_loop()
        try:
            merged = loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        assert len(merged) >= len(search_results), (
            f"Merged count {len(merged)} < semantic count {len(search_results)}"
        )

    @given(
        data=search_results_with_arns(),
        query=_safe_identifier(min_size=3, max_size=20),
    )
    @settings(max_examples=100, deadline=None)
    def test_unique_graph_results_added_to_merge(
        self, data: tuple, query: str
    ) -> None:
        """When graph traversal returns symbols with ARNs disjoint from
        semantic results, merged count equals semantic count + graph count.

        **Validates: Requirements 7.3**
        """
        search_results, semantic_arns = data

        graph_arns = set()
        nodes_by_arn: dict[str, list[FakeGraphNode]] = {}
        counter = 0
        for arn in sorted(semantic_arns):
            unique_arn = f"arn:archon:code:ws/pkg/graph{counter}.py#sym{counter}"
            counter += 1
            while unique_arn in semantic_arns or unique_arn in graph_arns:
                counter += 1
                unique_arn = f"arn:archon:code:ws/pkg/graph{counter}.py#sym{counter}"
            graph_arns.add(unique_arn)
            nodes_by_arn[arn] = [_fake_graph_node(unique_arn)]

        graph_repo = _make_graph_repo_returning_nodes(nodes_by_arn)
        retriever = _build_retriever_with_tracking(
            search_results, graph_repository=graph_repo
        )

        loop = asyncio.new_event_loop()
        try:
            merged = loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        expected_count = len(search_results) + len(graph_arns)
        assert len(merged) == expected_count, (
            f"Expected {expected_count} (semantic={len(search_results)} + "
            f"graph={len(graph_arns)}), got {len(merged)}"
        )

    @given(
        data=search_results_with_arns(),
        query=_safe_identifier(min_size=3, max_size=20),
    )
    @settings(max_examples=100, deadline=None)
    def test_duplicate_graph_arns_excluded_from_merge(
        self, data: tuple, query: str
    ) -> None:
        """When graph traversal returns symbols whose ARNs already appear
        in semantic results, those duplicates are excluded from the merge.

        **Validates: Requirements 7.3**
        """
        search_results, semantic_arns = data

        nodes_by_arn: dict[str, list[FakeGraphNode]] = {}
        sorted_arns = sorted(semantic_arns)
        for i, arn in enumerate(sorted_arns):
            duplicate_target = sorted_arns[(i + 1) % len(sorted_arns)]
            nodes_by_arn[arn] = [_fake_graph_node(duplicate_target)]

        graph_repo = _make_graph_repo_returning_nodes(nodes_by_arn)
        retriever = _build_retriever_with_tracking(
            search_results, graph_repository=graph_repo
        )

        loop = asyncio.new_event_loop()
        try:
            merged = loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        assert len(merged) == len(search_results), (
            f"Expected {len(search_results)} (no new graph results), "
            f"got {len(merged)} — duplicate ARNs should be excluded"
        )

    @given(
        data=search_results_with_arns(),
        query=_safe_identifier(min_size=3, max_size=20),
    )
    @settings(max_examples=100, deadline=None)
    def test_all_semantic_content_preserved_in_merge(
        self, data: tuple, query: str
    ) -> None:
        """Every semantic result's content appears in the merged output,
        preserving the original ordering.

        **Validates: Requirements 7.3**
        """
        search_results, semantic_arns = data

        graph_arns = set()
        nodes_by_arn: dict[str, list[FakeGraphNode]] = {}
        counter = 0
        for arn in sorted(semantic_arns):
            unique_arn = f"arn:archon:code:ws/pkg/extra{counter}.py#extra{counter}"
            counter += 1
            while unique_arn in semantic_arns or unique_arn in graph_arns:
                counter += 1
                unique_arn = f"arn:archon:code:ws/pkg/extra{counter}.py#extra{counter}"
            graph_arns.add(unique_arn)
            nodes_by_arn[arn] = [_fake_graph_node(unique_arn)]

        graph_repo = _make_graph_repo_returning_nodes(nodes_by_arn)
        retriever = _build_retriever_with_tracking(
            search_results, graph_repository=graph_repo
        )

        loop = asyncio.new_event_loop()
        try:
            merged = loop.run_until_complete(retriever.retrieve(query))
        finally:
            loop.close()

        semantic_contents = [sr.content for sr in search_results]
        merged_contents = [r.content for r in merged[: len(search_results)]]
        assert merged_contents == semantic_contents, (
            "Semantic results not preserved at the start of merged output"
        )
