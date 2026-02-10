"""Unit tests for the two-phase retriever.

Source: src/query/retriever.py, src/common/vector_store.py
"""
from __future__ import annotations
import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

if "aphex_clients" not in sys.modules:
    _aphex_stub = types.ModuleType("aphex_clients")
    _aphex_stub.EmbeddingClient = type("EmbeddingClient", (), {})
    sys.modules["aphex_clients"] = _aphex_stub

from src.graph.models import EdgeType, NodeType, SymbolKind
from src.common.vector_store import SearchResult
from src.query.retriever import Retriever, RetrievalResult


@dataclass
class FakeGraphNode:
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


def _build_retriever(search_results, graph_repository=None, default_k=5):
    embedding_client = AsyncMock()
    embedding_client.embed_single = AsyncMock(return_value=[0.1, 0.2, 0.3])
    vector_store = AsyncMock()
    vector_store.search = AsyncMock(return_value=search_results)
    return Retriever(
        embedding_client=embedding_client,
        vector_store=vector_store,
        default_k=default_k,
        graph_repository=graph_repository,
    )


class TestSemanticOnlyRetrieval:
    def test_returns_semantic_results_when_no_graph(self):
        search_results = [
            SearchResult(content="Function foo does bar", source="pkg/file.py",
                         chunk_index=0, score=0.95,
                         metadata={"arn": "arn:archon:code:ws/pkg/file.py#foo"}),
            SearchResult(content="Class Baz handles qux", source="pkg/baz.py",
                         chunk_index=1, score=0.85,
                         metadata={"arn": "arn:archon:code:ws/pkg/baz.py#Baz"}),
        ]
        retriever = _build_retriever(search_results, graph_repository=None)
        async def run():
            results = await retriever.retrieve("how does foo work?")
            assert len(results) == 2
            assert results[0].content == "Function foo does bar"
            assert results[0].score == 0.95
            assert results[1].content == "Class Baz handles qux"
        asyncio.get_event_loop().run_until_complete(run())

    def test_returns_empty_for_no_results(self):
        retriever = _build_retriever([], graph_repository=None)
        async def run():
            results = await retriever.retrieve("nonexistent topic")
            assert results == []
        asyncio.get_event_loop().run_until_complete(run())


class TestCombinedRetrieval:
    def test_enriches_with_graph_results(self):
        search_results = [
            SearchResult(content="Function foo does bar", source="pkg/file.py",
                         chunk_index=0, score=0.95,
                         metadata={"arn": "arn:archon:code:ws/pkg/file.py#foo"}),
        ]
        related_node = FakeGraphNode(
            arn="arn:archon:code:ws/pkg/file.py#helper",
            type=NodeType.CODE, workspace="ws", package="pkg",
            path="file.py", name="helper", kind=SymbolKind.FUNCTION,
            signature="def helper(x: int) -> str",
            documentation="Helper function for foo", file_path="pkg/file.py",
        )
        graph_repo = AsyncMock()
        graph_repo.traverse = AsyncMock(return_value=[related_node])
        retriever = _build_retriever(search_results, graph_repository=graph_repo)
        async def run():
            results = await retriever.retrieve("how does foo work?")
            assert len(results) == 2
            assert results[0].content == "Function foo does bar"
            graph_result = results[1]
            assert "helper" in graph_result.content
            assert graph_result.metadata.get("origin") == "graph"
            assert graph_result.metadata.get("arn") == related_node.arn
        asyncio.get_event_loop().run_until_complete(run())

    def test_deduplicates_graph_results_by_arn(self):
        arn = "arn:archon:code:ws/pkg/file.py#foo"
        search_results = [
            SearchResult(content="Function foo", source="pkg/file.py",
                         chunk_index=0, score=0.95, metadata={"arn": arn}),
        ]
        duplicate_node = FakeGraphNode(
            arn=arn, type=NodeType.CODE, workspace="ws", package="pkg",
            path="file.py", name="foo", kind=SymbolKind.FUNCTION,
            file_path="pkg/file.py",
        )
        graph_repo = AsyncMock()
        graph_repo.traverse = AsyncMock(return_value=[duplicate_node])
        retriever = _build_retriever(search_results, graph_repository=graph_repo)
        async def run():
            results = await retriever.retrieve("foo")
            assert len(results) == 1
            assert results[0].content == "Function foo"
        asyncio.get_event_loop().run_until_complete(run())


class TestGraphFallback:
    def test_falls_back_on_graph_exception(self):
        search_results = [
            SearchResult(content="Function foo", source="pkg/file.py",
                         chunk_index=0, score=0.95,
                         metadata={"arn": "arn:archon:code:ws/pkg/file.py#foo"}),
        ]
        graph_repo = AsyncMock()
        graph_repo.traverse = AsyncMock(side_effect=ConnectionError("db down"))
        retriever = _build_retriever(search_results, graph_repository=graph_repo)
        async def run():
            results = await retriever.retrieve("foo")
            assert len(results) == 1
            assert results[0].content == "Function foo"
        asyncio.get_event_loop().run_until_complete(run())


class TestARNExtraction:
    def test_no_arns_skips_graph_query(self):
        search_results = [
            SearchResult(content="Some doc content", source="docs/readme.md",
                         chunk_index=0, score=0.90, metadata={}),
        ]
        graph_repo = AsyncMock()
        graph_repo.traverse = AsyncMock(return_value=[])
        retriever = _build_retriever(search_results, graph_repository=graph_repo)
        async def run():
            results = await retriever.retrieve("readme")
            assert len(results) == 1
            graph_repo.traverse.assert_not_called()
        asyncio.get_event_loop().run_until_complete(run())

    def test_extracts_unique_arns_from_multiple_results(self):
        arn1 = "arn:archon:code:ws/pkg/a.py#A"
        arn2 = "arn:archon:code:ws/pkg/b.py#B"
        search_results = [
            SearchResult(content="A", source="a.py", chunk_index=0, score=0.9, metadata={"arn": arn1}),
            SearchResult(content="A2", source="a.py", chunk_index=1, score=0.8, metadata={"arn": arn1}),
            SearchResult(content="B", source="b.py", chunk_index=0, score=0.7, metadata={"arn": arn2}),
        ]
        graph_repo = AsyncMock()
        graph_repo.traverse = AsyncMock(return_value=[])
        retriever = _build_retriever(search_results, graph_repository=graph_repo)
        async def run():
            results = await retriever.retrieve("query")
            assert len(results) == 3
            assert graph_repo.traverse.call_count == 2
        asyncio.get_event_loop().run_until_complete(run())

    def test_none_metadata_treated_as_no_arn(self):
        search_results = [
            SearchResult(content="Content", source="file.py",
                         chunk_index=0, score=0.9, metadata=None),
        ]
        graph_repo = AsyncMock()
        graph_repo.traverse = AsyncMock(return_value=[])
        retriever = _build_retriever(search_results, graph_repository=graph_repo)
        async def run():
            results = await retriever.retrieve("query")
            assert len(results) == 1
            graph_repo.traverse.assert_not_called()
        asyncio.get_event_loop().run_until_complete(run())


class TestRetrieveWithContext:
    def test_formats_context_string(self):
        search_results = [
            SearchResult(content="First chunk", source="file1.py",
                         chunk_index=0, score=0.9, metadata={}),
            SearchResult(content="Second chunk", source="file2.py",
                         chunk_index=0, score=0.8, metadata={}),
        ]
        retriever = _build_retriever(search_results, graph_repository=None)
        async def run():
            context = await retriever.retrieve_with_context("query")
            assert "[Source 1: file1.py]" in context
            assert "First chunk" in context
            assert "[Source 2: file2.py]" in context
            assert "Second chunk" in context
            assert "---" in context
        asyncio.get_event_loop().run_until_complete(run())

    def test_returns_empty_string_for_no_results(self):
        retriever = _build_retriever([], graph_repository=None)
        async def run():
            context = await retriever.retrieve_with_context("query")
            assert context == ""
        asyncio.get_event_loop().run_until_complete(run())
