"""Unit tests for KnowledgeBaseSyncService._load_package_data edge cases.

Tests missing SCIP index files, corrupted protobuf data, empty indexes,
and valid indexes with symbols. Uses tmp_path fixture for filesystem setup.

Feature: scip-sync-pipeline

Source:
- src/sync/service.py (_load_package_data method)
- .kiro/specs/scip-sync-pipeline/requirements.md (Requirements 5.4, 5.5)

Validates:
    Requirements 5.4, 5.5
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.models import GeneratedDoc, ScipParseResult
from src.sync.proto import scip_pb2
from src.sync.service import KnowledgeBaseSyncService


def _create_service() -> KnowledgeBaseSyncService:
    return KnowledgeBaseSyncService(
        graph_adapter=MagicMock(),
        vector_adapter=MagicMock(),
        change_detector=MagicMock(),
    )


class TestLoadPackageDataMissingSCIPIndex:
    """Tests for _load_package_data when no SCIP index file exists.

    Validates: Requirement 5.4
    """

    def test_missing_scip_index_returns_none_and_empty_list(self, tmp_path: Path) -> None:
        """Package directory exists but has no .scip/index.scip file.

        Validates: Requirement 5.4
        """
        service = _create_service()
        package = "my-package"
        (tmp_path / package).mkdir()

        async def run():
            return await service._load_package_data(package, str(tmp_path))

        result, docs = asyncio.get_event_loop().run_until_complete(run())

        assert result is None
        assert docs == []

    def test_missing_scip_directory_returns_none_and_empty_list(self, tmp_path: Path) -> None:
        """Package directory exists but .scip directory is absent entirely.

        Validates: Requirement 5.4
        """
        service = _create_service()
        package = "no-scip-dir"
        pkg_dir = tmp_path / package
        pkg_dir.mkdir()
        (pkg_dir / "src").mkdir()
        (pkg_dir / "src" / "main.py").write_text("print('hello')")

        async def run():
            return await service._load_package_data(package, str(tmp_path))

        result, docs = asyncio.get_event_loop().run_until_complete(run())

        assert result is None
        assert docs == []


class TestLoadPackageDataCorruptedProtobuf:
    """Tests for _load_package_data when the SCIP index is corrupted.

    Validates: Requirement 5.5
    """

    def test_corrupted_protobuf_returns_none_and_empty_list(self, tmp_path: Path) -> None:
        """Random bytes in index.scip that cannot be parsed as protobuf.

        Validates: Requirement 5.5
        """
        service = _create_service()
        package = "corrupt-pkg"
        scip_dir = tmp_path / package / ".scip"
        scip_dir.mkdir(parents=True)
        (scip_dir / "index.scip").write_bytes(b"\xff\xfe\xfd\x00\x01\x02\x03\x80\x81")

        async def run():
            return await service._load_package_data(package, str(tmp_path))

        result, docs = asyncio.get_event_loop().run_until_complete(run())

        assert result is None
        assert docs == []


class TestLoadPackageDataEmptyIndex:
    """Tests for _load_package_data with a valid but empty SCIP index.

    Validates: Requirement 5.4
    """

    def test_empty_index_returns_parse_result_with_no_symbols(self, tmp_path: Path) -> None:
        """Valid protobuf Index with zero documents produces empty symbols."""
        service = _create_service()
        package = "empty-pkg"
        scip_dir = tmp_path / package / ".scip"
        scip_dir.mkdir(parents=True)

        index = scip_pb2.Index()
        (scip_dir / "index.scip").write_bytes(index.SerializeToString())

        async def run():
            return await service._load_package_data(package, str(tmp_path))

        result, docs = asyncio.get_event_loop().run_until_complete(run())

        assert result is not None
        assert isinstance(result, ScipParseResult)
        assert result.symbols == []
        assert result.relationships == []
        assert len(result.hash) == 64
        assert docs == []


class TestLoadPackageDataValidIndex:
    """Tests for _load_package_data with a valid SCIP index containing symbols.

    Validates: Requirements 5.1, 5.2, 5.3
    """

    def test_valid_index_returns_parse_result_and_generated_docs(self, tmp_path: Path) -> None:
        """Index with one document and one symbol produces ScipParseResult and docs."""
        service = _create_service()
        package = "valid-pkg"
        scip_dir = tmp_path / package / ".scip"
        scip_dir.mkdir(parents=True)

        index = scip_pb2.Index()
        doc = index.documents.add()
        doc.relative_path = "src/main.py"

        sym = doc.symbols.add()
        sym.symbol = "local my_function"
        sym.display_name = "my_function"
        sym.kind = scip_pb2.Function

        occ = doc.occurrences.add()
        occ.symbol = "local my_function"
        occ.symbol_roles = 0x1
        occ.range.extend([10, 4, 10, 15])

        (scip_dir / "index.scip").write_bytes(index.SerializeToString())

        async def run():
            return await service._load_package_data(package, str(tmp_path))

        result, docs = asyncio.get_event_loop().run_until_complete(run())

        assert result is not None
        assert isinstance(result, ScipParseResult)
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "my_function"
        assert result.symbols[0].kind == "function"
        assert result.symbols[0].arn.startswith("arn:archon:code:")
        assert len(result.hash) == 64

        assert len(docs) == 1
        assert isinstance(docs[0], GeneratedDoc)
        assert docs[0].source_path == "src/main.py"
        assert docs[0].doc_path.endswith(".archon.md")
        assert "my_function" in docs[0].content
