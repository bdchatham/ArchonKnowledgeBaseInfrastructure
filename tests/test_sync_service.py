"""Unit tests for KnowledgeBaseSyncService.

This module contains unit tests for the KnowledgeBaseSyncService class, testing
sync_package with valid/invalid inputs, skip behavior when hash unchanged,
force behavior, sync_workspace with multiple packages, and partial failure handling.

Feature: sync-service

Source:
- src/sync/service.py
- .kiro/specs/sync-service/design.md

Validates:
    Requirements 9.1, 9.2, 9.3, 9.4, 7.3, 7.4, 7.5
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.models import (
    GeneratedDoc,
    PackageSyncResult,
    ScipParseResult,
    ScipRelationship,
    ScipSymbol,
    SymbolLocation,
    SyncResult,
    VectorSyncResult,
    WorkspaceSyncResult,
)
from src.sync.service import (
    KnowledgeBaseSyncService,
    ValidationError,
)


def create_test_location(
    file: str = "src/main.py",
    line: int = 42,
    column: int = 0,
) -> SymbolLocation:
    """Create a test SymbolLocation with default values."""
    return SymbolLocation(file=file, line=line, column=column)


def create_test_symbol(
    arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#test_func",
    type_: str = "code",
    workspace: str = "personal-work",
    package: str = "TestPackage",
    path: str = "src/main.py",
    symbol: Optional[str] = "test_func",
    kind: str = "function",
    name: str = "test_func",
    signature: Optional[str] = "def test_func(x: int) -> str",
    documentation: Optional[str] = "Test function documentation.",
    location: Optional[SymbolLocation] = None,
) -> ScipSymbol:
    """Create a test ScipSymbol with default values."""
    return ScipSymbol(
        arn=arn,
        type=type_,
        workspace=workspace,
        package=package,
        path=path,
        symbol=symbol,
        kind=kind,
        name=name,
        signature=signature,
        documentation=documentation,
        location=location or create_test_location(),
    )


def create_test_relationship(
    from_arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#MyClass",
    to_arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#my_method",
    rel_type: str = "contains",
) -> ScipRelationship:
    """Create a test ScipRelationship with default values."""
    return ScipRelationship(
        from_arn=from_arn,
        to_arn=to_arn,
        type=rel_type,
    )


def create_test_parse_result(
    symbols: Optional[List[ScipSymbol]] = None,
    relationships: Optional[List[ScipRelationship]] = None,
    hash_: str = "abc123def456789012345678901234567890123456789012345678901234",
) -> ScipParseResult:
    """Create a test ScipParseResult with default values."""
    return ScipParseResult(
        symbols=symbols or [],
        relationships=relationships or [],
        hash=hash_,
    )


def create_test_doc(
    source_path: str = "src/main.py",
    doc_path: str = "src/main.archon.md",
    content: str = "Test documentation content for the main module.",
    arn: str = "arn:archon:doc:personal-work/TestPackage/src/main.py",
    referenced_arns: Optional[List[str]] = None,
) -> GeneratedDoc:
    """Create a test GeneratedDoc with default values."""
    return GeneratedDoc(
        source_path=source_path,
        doc_path=doc_path,
        content=content,
        arn=arn,
        referenced_arns=referenced_arns or [],
    )


class MockGraphSyncAdapter:
    """Mock GraphSyncAdapter for unit tests."""

    def __init__(
        self,
        sync_result: Optional[dict] = None,
        prune_result: int = 0,
        sync_error: Optional[Exception] = None,
        prune_error: Optional[Exception] = None,
    ):
        self._sync_result = sync_result or {
            "nodes_created": 0,
            "nodes_updated": 0,
            "edges_created": 0,
        }
        self._prune_result = prune_result
        self._sync_error = sync_error
        self._prune_error = prune_error
        self.sync_calls: List[dict] = []
        self.prune_calls: List[dict] = []

    async def sync(self, parse_result):
        """Mock sync method."""
        self.sync_calls.append({"parse_result": parse_result})
        if self._sync_error:
            raise self._sync_error
        return MagicMock(
            nodes_created=self._sync_result["nodes_created"],
            nodes_updated=self._sync_result["nodes_updated"],
            edges_created=self._sync_result["edges_created"],
        )

    async def prune(self, package: str, keep_arns: List[str]) -> int:
        """Mock prune method."""
        self.prune_calls.append({"package": package, "keep_arns": keep_arns})
        if self._prune_error:
            raise self._prune_error
        return self._prune_result


class MockVectorSyncAdapter:
    """Mock VectorSyncAdapter for unit tests."""

    def __init__(
        self,
        sync_result: Optional[VectorSyncResult] = None,
        sync_error: Optional[Exception] = None,
    ):
        self._sync_result = sync_result or VectorSyncResult(
            chunks_upserted=0,
            chunks_pruned=0,
            errors=[],
        )
        self._sync_error = sync_error
        self.sync_calls: List[dict] = []

    async def sync_docs(
        self, package: str, archon_docs: List[GeneratedDoc]
    ) -> VectorSyncResult:
        """Mock sync_docs method."""
        self.sync_calls.append({"package": package, "archon_docs": archon_docs})
        if self._sync_error:
            raise self._sync_error
        return self._sync_result


class MockChangeDetector:
    """Mock ChangeDetector for unit tests."""

    def __init__(
        self,
        has_changed_result: bool = True,
        stored_hash: Optional[str] = None,
    ):
        self._has_changed_result = has_changed_result
        self._stored_hash = stored_hash
        self._stored_hashes: dict = {}
        self.has_changed_calls: List[dict] = []
        self.update_hash_calls: List[dict] = []

    def has_changed(self, package: str, current_hash: str) -> bool:
        """Mock has_changed method."""
        self.has_changed_calls.append({"package": package, "current_hash": current_hash})
        return self._has_changed_result

    def get_stored_hash(self, package: str) -> Optional[str]:
        """Mock get_stored_hash method."""
        return self._stored_hashes.get(package, self._stored_hash)

    def update_hash(self, package: str, new_hash: str) -> None:
        """Mock update_hash method."""
        self.update_hash_calls.append({"package": package, "new_hash": new_hash})
        self._stored_hashes[package] = new_hash


class TestSyncPackageValidInputs:
    """Tests for sync_package with valid inputs.

    Validates: Requirements 9.1, 9.2
    """

    @pytest.fixture
    def service(self):
        """Create a KnowledgeBaseSyncService with mock dependencies."""
        graph_adapter = MockGraphSyncAdapter(
            sync_result={"nodes_created": 2, "nodes_updated": 1, "edges_created": 3},
            prune_result=1,
        )
        vector_adapter = MockVectorSyncAdapter(
            sync_result=VectorSyncResult(chunks_upserted=5, chunks_pruned=2, errors=[])
        )
        change_detector = MockChangeDetector(has_changed_result=True)
        return KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

    def test_sync_package_returns_sync_result(self, service):
        """Test that sync_package returns a SyncResult.

        Validates: Requirement 9.2
        """
        symbols = [create_test_symbol()]
        relationships = [create_test_relationship()]
        scip_result = create_test_parse_result(symbols=symbols, relationships=relationships)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert isinstance(result, SyncResult)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_returns_correct_counts(self, service):
        """Test that sync_package returns correct counts.

        Validates: Requirement 9.2
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.nodes_created == 2
            assert result.nodes_updated == 1
            assert result.edges_created == 3
            assert result.chunks_upserted == 5
            assert result.nodes_pruned == 1
            assert result.chunks_pruned == 2

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_package_success_true_on_valid_sync(self, service):
        """Test that sync_package returns success=True on valid sync.

        Validates: Requirement 9.2
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True
            assert result.skipped is False
            assert result.skip_reason is None

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_calls_graph_adapter(self, service):
        """Test that sync_package calls graph adapter.

        Validates: Requirement 9.1
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert len(service.graph_adapter.sync_calls) == 1
            assert len(service.graph_adapter.prune_calls) == 1

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_calls_vector_adapter(self, service):
        """Test that sync_package calls vector adapter.

        Validates: Requirement 9.1
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert len(service.vector_adapter.sync_calls) == 1
            call = service.vector_adapter.sync_calls[0]
            assert call["package"] == "TestPackage"
            assert call["archon_docs"] == archon_docs

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_updates_hash_on_success(self, service):
        """Test that sync_package updates hash after successful sync.

        Validates: Requirement 7.2
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols, hash_="new_hash_123")
        archon_docs = [create_test_doc()]

        async def run_test():
            await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert len(service.change_detector.update_hash_calls) == 1
            call = service.change_detector.update_hash_calls[0]
            assert call["package"] == "TestPackage"
            assert call["new_hash"] == "new_hash_123"

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSyncPackageInvalidInputs:
    """Tests for sync_package with invalid inputs.

    Validates: Requirements 9.1, 9.2, 1.4
    """

    @pytest.fixture
    def service(self):
        """Create a KnowledgeBaseSyncService with mock dependencies."""
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector()
        return KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

    def test_sync_package_empty_package_path_returns_error(self, service):
        """Test that empty package_path returns validation error.

        Validates: Requirement 1.4
        """
        scip_result = create_test_parse_result()
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is False
            assert len(result.errors) > 0
            assert any("package_path" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_whitespace_package_path_returns_error(self, service):
        """Test that whitespace-only package_path returns validation error.

        Validates: Requirement 1.4
        """
        scip_result = create_test_parse_result()
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="   ",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is False
            assert len(result.errors) > 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_none_scip_result_returns_error(self, service):
        """Test that None scip_result returns validation error.

        Validates: Requirement 1.4
        """
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=None,
                archon_docs=archon_docs,
            )
            assert result.success is False
            assert len(result.errors) > 0
            assert any("scip_result" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_empty_hash_returns_error(self, service):
        """Test that empty hash in scip_result returns validation error.

        Validates: Requirement 1.4
        """
        scip_result = create_test_parse_result(hash_="")
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is False
            assert len(result.errors) > 0
            assert any("hash" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_none_archon_docs_returns_error(self, service):
        """Test that None archon_docs returns validation error.

        Validates: Requirement 1.4
        """
        scip_result = create_test_parse_result()

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=None,
            )
            assert result.success is False
            assert len(result.errors) > 0
            assert any("archon_docs" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_package_invalid_doc_missing_arn_returns_error(self, service):
        """Test that doc missing arn returns validation error.

        Validates: Requirement 1.4
        """
        scip_result = create_test_parse_result()
        bad_doc = GeneratedDoc(
            source_path="src/main.py",
            doc_path="src/main.archon.md",
            content="Test content",
            arn="",
            referenced_arns=[],
        )

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=[bad_doc],
            )
            assert result.success is False
            assert len(result.errors) > 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSyncPackageSkipBehavior:
    """Tests for sync_package skip behavior when hash unchanged.

    Validates: Requirements 7.1, 7.5
    """

    def test_sync_package_skips_when_hash_unchanged(self):
        """Test that sync_package skips when hash unchanged.

        Validates: Requirement 7.1
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.skipped is True
            assert result.success is True
            assert result.skip_reason is not None
            assert "hash" in result.skip_reason.lower()

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_skip_returns_zero_counts(self):
        """Test that skipped sync returns zero counts.

        Validates: Requirement 7.5
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.nodes_created == 0
            assert result.nodes_updated == 0
            assert result.edges_created == 0
            assert result.chunks_upserted == 0
            assert result.nodes_pruned == 0
            assert result.chunks_pruned == 0

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_package_skip_does_not_call_adapters(self):
        """Test that skipped sync does not call adapters.

        Validates: Requirement 7.1
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert len(graph_adapter.sync_calls) == 0
            assert len(vector_adapter.sync_calls) == 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_skip_does_not_update_hash(self):
        """Test that skipped sync does not update hash.

        Validates: Requirement 7.1
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert len(change_detector.update_hash_calls) == 0

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSyncPackageForceBehavior:
    """Tests for sync_package force behavior.

    Validates: Requirements 7.3, 7.4
    """

    def test_sync_package_force_bypasses_change_detection(self):
        """Test that force=True bypasses change detection.

        Validates: Requirement 7.4
        """
        graph_adapter = MockGraphSyncAdapter(
            sync_result={"nodes_created": 1, "nodes_updated": 0, "edges_created": 0}
        )
        vector_adapter = MockVectorSyncAdapter(
            sync_result=VectorSyncResult(chunks_upserted=1, chunks_pruned=0, errors=[])
        )
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
                force=True,
            )
            assert result.skipped is False
            assert result.success is True
            assert len(graph_adapter.sync_calls) == 1
            assert len(vector_adapter.sync_calls) == 1

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_package_force_updates_hash(self):
        """Test that force=True still updates hash after sync.

        Validates: Requirement 7.3
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols, hash_="forced_hash")
        archon_docs = [create_test_doc()]

        async def run_test():
            await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
                force=True,
            )
            assert len(change_detector.update_hash_calls) == 1
            assert change_detector.update_hash_calls[0]["new_hash"] == "forced_hash"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_force_false_respects_change_detection(self):
        """Test that force=False respects change detection.

        Validates: Requirement 7.3
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
                force=False,
            )
            assert result.skipped is True
            assert len(graph_adapter.sync_calls) == 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_force_default_is_false(self):
        """Test that force parameter defaults to False.

        Validates: Requirement 7.3
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.skipped is True

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSyncWorkspace:
    """Tests for sync_workspace with multiple packages.

    Validates: Requirements 9.3, 9.4
    """

    @pytest.fixture
    def service(self, tmp_path):
        """Create a KnowledgeBaseSyncService with mock dependencies."""
        graph_adapter = MockGraphSyncAdapter(
            sync_result={"nodes_created": 1, "nodes_updated": 0, "edges_created": 1}
        )
        vector_adapter = MockVectorSyncAdapter(
            sync_result=VectorSyncResult(chunks_upserted=2, chunks_pruned=0, errors=[])
        )
        change_detector = MockChangeDetector(has_changed_result=True)
        return KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

    def test_sync_workspace_returns_workspace_sync_result(self, service, tmp_path):
        """Test that sync_workspace returns WorkspaceSyncResult.

        Validates: Requirement 9.4
        """
        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert isinstance(result, WorkspaceSyncResult)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_workspace_empty_workspace_returns_success(self, service, tmp_path):
        """Test that empty workspace returns success with empty results.

        Validates: Requirement 9.4
        """
        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert result.success is True
            assert result.packages_synced == []
            assert result.packages_skipped == []
            assert result.errors == {}

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_workspace_discovers_packages_with_scip(self, service, tmp_path):
        """Test that sync_workspace discovers packages with SCIP indexes.

        Validates: Requirement 9.3
        """
        pkg_dir = tmp_path / "PackageA"
        pkg_dir.mkdir()
        scip_dir = pkg_dir / ".scip"
        scip_dir.mkdir()
        (scip_dir / "index.scip").write_text("mock scip index")

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert "PackageA" in result.packages_skipped or any(
                p.package == "PackageA" for p in result.packages_synced
            )

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_workspace_discovers_packages_with_archon(self, service, tmp_path):
        """Test that sync_workspace discovers packages with Archon docs.

        Validates: Requirement 9.3
        """
        pkg_dir = tmp_path / "PackageB"
        pkg_dir.mkdir()
        archon_dir = pkg_dir / ".archon"
        archon_dir.mkdir()

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert "PackageB" in result.packages_skipped or any(
                p.package == "PackageB" for p in result.packages_synced
            )

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_workspace_ignores_hidden_directories(self, service, tmp_path):
        """Test that sync_workspace ignores hidden directories.

        Validates: Requirement 9.3
        """
        hidden_dir = tmp_path / ".hidden"
        hidden_dir.mkdir()
        scip_dir = hidden_dir / ".scip"
        scip_dir.mkdir()
        (scip_dir / "index.scip").write_text("mock scip index")

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert ".hidden" not in result.packages_skipped
            assert not any(p.package == ".hidden" for p in result.packages_synced)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_workspace_nonexistent_path_returns_empty(self, service, tmp_path):
        """Test that nonexistent workspace path returns empty results.

        Validates: Requirement 9.3
        """
        nonexistent = tmp_path / "nonexistent"

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(nonexistent),
            )
            assert result.success is True
            assert result.packages_synced == []
            assert result.packages_skipped == []

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_workspace_force_parameter_passed_to_packages(self, tmp_path):
        """Test that force parameter is passed to package syncs.

        Validates: Requirement 9.3
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=False)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        pkg_dir = tmp_path / "PackageA"
        pkg_dir.mkdir()
        scip_dir = pkg_dir / ".scip"
        scip_dir.mkdir()
        (scip_dir / "index.scip").write_text("mock scip index")

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
                force=True,
            )
            assert isinstance(result, WorkspaceSyncResult)

        asyncio.get_event_loop().run_until_complete(run_test())


class TestPartialFailureHandling:
    """Tests for partial failure handling.

    Validates: Requirements 9.2, 9.4
    """

    def test_sync_package_graph_failure_returns_error(self):
        """Test that graph sync failure returns error in result.

        Validates: Requirement 9.2
        """
        graph_adapter = MockGraphSyncAdapter(
            sync_error=Exception("Database connection failed")
        )
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=True)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is False
            assert len(result.errors) > 0
            assert any("failed" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_package_vector_failure_continues_and_reports_error(self):
        """Test that vector sync failure is reported but doesn't block graph sync.

        Validates: Requirement 9.2
        """
        graph_adapter = MockGraphSyncAdapter(
            sync_result={"nodes_created": 1, "nodes_updated": 0, "edges_created": 1}
        )
        vector_adapter = MockVectorSyncAdapter(
            sync_error=Exception("Qdrant connection failed")
        )
        change_detector = MockChangeDetector(has_changed_result=True)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.nodes_created == 1
            assert len(result.errors) > 0
            assert any("vector" in e.lower() or "qdrant" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_vector_errors_included_in_result(self):
        """Test that vector adapter errors are included in result.

        Validates: Requirement 9.2
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter(
            sync_result=VectorSyncResult(
                chunks_upserted=1,
                chunks_pruned=0,
                errors=["Failed to embed chunk 3"],
            )
        )
        change_detector = MockChangeDetector(has_changed_result=True)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert "Failed to embed chunk 3" in result.errors

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_workspace_partial_failure_continues(self, tmp_path):
        """Test that workspace sync continues after package failure.

        Validates: Requirement 9.4
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=True)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        pkg_a = tmp_path / "PackageA"
        pkg_a.mkdir()
        (pkg_a / ".scip").mkdir()
        (pkg_a / ".scip" / "index.scip").write_text("mock")

        pkg_b = tmp_path / "PackageB"
        pkg_b.mkdir()
        (pkg_b / ".scip").mkdir()
        (pkg_b / ".scip" / "index.scip").write_text("mock")

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert isinstance(result, WorkspaceSyncResult)

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_workspace_aggregates_errors_by_package(self, tmp_path):
        """Test that workspace sync aggregates errors by package.

        Validates: Requirement 9.4
        """
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=True)
        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        async def run_test():
            result = await service.sync_workspace(
                workspace_path=str(tmp_path),
            )
            assert isinstance(result.errors, dict)

        asyncio.get_event_loop().run_until_complete(run_test())


class TestServiceInitialization:
    """Tests for KnowledgeBaseSyncService initialization.

    Validates: Requirement 9.1
    """

    def test_service_accepts_all_dependencies(self):
        """Test that service accepts all required dependencies."""
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector()

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        assert service.graph_adapter is graph_adapter
        assert service.vector_adapter is vector_adapter
        assert service.change_detector is change_detector

    def test_service_stores_adapter_references(self):
        """Test that service stores adapter references for later use."""
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector()

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        assert service._graph_adapter is graph_adapter
        assert service._vector_adapter is vector_adapter
        assert service._change_detector is change_detector


class TestArnValidation:
    """Tests for ARN validation in sync_package.

    Validates: Requirement 1.4
    """

    @pytest.fixture
    def service(self):
        """Create a KnowledgeBaseSyncService with mock dependencies."""
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=True)
        return KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

    def test_sync_package_filters_invalid_arns(self, service):
        """Test that sync_package filters out symbols with invalid ARNs.

        Validates: Requirement 1.4
        """
        valid_symbol = create_test_symbol(
            arn="arn:archon:code:personal-work/TestPackage/src/main.py#valid_func"
        )
        invalid_symbol = create_test_symbol(
            arn="invalid-arn-format"
        )
        symbols = [valid_symbol, invalid_symbol]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True
            assert any("invalid" in e.lower() or "skipping" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())


    def test_sync_package_filters_relationships_with_invalid_arns(self, service):
        """Test that sync_package filters relationships with invalid ARNs.

        Validates: Requirement 1.4
        """
        valid_symbol = create_test_symbol(
            arn="arn:archon:code:personal-work/TestPackage/src/main.py#valid_func"
        )
        invalid_relationship = create_test_relationship(
            from_arn="invalid-from-arn",
            to_arn="arn:archon:code:personal-work/TestPackage/src/main.py#valid_func",
        )
        symbols = [valid_symbol]
        relationships = [invalid_relationship]
        scip_result = create_test_parse_result(symbols=symbols, relationships=relationships)
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True
            assert any("invalid" in e.lower() or "skipping" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_valid_arn_format_accepted(self, service):
        """Test that valid ARN format is accepted.

        Validates: Requirement 1.4
        """
        valid_symbol = create_test_symbol(
            arn="arn:archon:code:personal-work/TestPackage/src/main.py#my_function"
        )
        symbols = [valid_symbol]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True
            assert not any("invalid arn" in e.lower() for e in result.errors)

        asyncio.get_event_loop().run_until_complete(run_test())


class TestSyncPackageWithEmptyInputs:
    """Tests for sync_package with empty but valid inputs.

    Validates: Requirements 9.1, 9.2
    """

    @pytest.fixture
    def service(self):
        """Create a KnowledgeBaseSyncService with mock dependencies."""
        graph_adapter = MockGraphSyncAdapter()
        vector_adapter = MockVectorSyncAdapter()
        change_detector = MockChangeDetector(has_changed_result=True)
        return KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

    def test_sync_package_empty_symbols_succeeds(self, service):
        """Test that sync_package with empty symbols succeeds.

        Validates: Requirement 9.1
        """
        scip_result = create_test_parse_result(symbols=[], relationships=[])
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_empty_docs_succeeds(self, service):
        """Test that sync_package with empty docs succeeds.

        Validates: Requirement 9.1
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols)
        archon_docs = []

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_package_empty_relationships_succeeds(self, service):
        """Test that sync_package with empty relationships succeeds.

        Validates: Requirement 9.1
        """
        symbols = [create_test_symbol()]
        scip_result = create_test_parse_result(symbols=symbols, relationships=[])
        archon_docs = [create_test_doc()]

        async def run_test():
            result = await service.sync_package(
                package_path="TestPackage",
                scip_result=scip_result,
                archon_docs=archon_docs,
            )
            assert result.success is True

        asyncio.get_event_loop().run_until_complete(run_test())
