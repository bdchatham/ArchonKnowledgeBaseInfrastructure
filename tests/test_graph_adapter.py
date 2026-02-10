"""Unit tests for GraphSyncAdapter.

This module contains unit tests for the GraphSyncAdapter class, testing
symbol to node transformation, relationship to edge transformation,
batch upsert operations, stale node pruning, and error handling.

Feature: sync-service

Source:
- src/sync/graph_adapter.py
- .kiro/specs/sync-service/design.md

Validates:
    Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 6.1, 6.2
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.graph_adapter import (
    GraphSyncAdapter,
    GraphSyncAdapterError,
    ScipParseResult,
    ScipRelationship,
    ScipSymbol,
    SyncResult,
    SCIP_KIND_TO_GRAPHQL,
    SCIP_TYPE_TO_GRAPHQL,
)
from src.graph.service import GraphQLService


def create_test_symbol(
    arn: str = "arn:archon:code:personal-work/TestPackage/src/main.py#test_func",
    name: str = "test_func",
    kind: str = "function",
    signature: Optional[str] = "def test_func(x: int) -> str",
    documentation: Optional[str] = "Test function documentation.",
    file_path: str = "src/main.py",
    line_number: Optional[int] = 42,
) -> ScipSymbol:
    """Create a test ScipSymbol with default values."""
    return ScipSymbol(
        arn=arn,
        name=name,
        kind=kind,
        signature=signature,
        documentation=documentation,
        file_path=file_path,
        line_number=line_number,
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
    package_path: str = "/home/user/projects/TestPackage",
    workspace: str = "personal-work",
    package: str = "TestPackage",
    symbols: Optional[List[ScipSymbol]] = None,
    relationships: Optional[List[ScipRelationship]] = None,
    index_hash: str = "abc123def456",
) -> ScipParseResult:
    """Create a test ScipParseResult with default values."""
    return ScipParseResult(
        package_path=package_path,
        workspace=workspace,
        package=package,
        symbols=symbols or [],
        relationships=relationships or [],
        index_hash=index_hash,
    )


class MockGraphQLService:
    """Mock GraphQL service for unit tests."""

    def __init__(
        self,
        sync_result: Optional[Dict[str, Any]] = None,
        prune_result: Optional[int] = None,
        errors: Optional[List[Dict[str, str]]] = None,
    ):
        self._sync_result = sync_result or {
            "nodesCreated": 0,
            "nodesUpdated": 0,
            "edgesCreated": 0,
            "edgesRemoved": 0,
        }
        self._prune_result = prune_result if prune_result is not None else 0
        self._errors = errors
        self.execute_calls: List[Dict[str, Any]] = []

    async def execute(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Mock execute method that records calls and returns configured results."""
        self.execute_calls.append({"query": query, "variables": variables})

        if self._errors:
            return {"errors": self._errors}

        if "syncFromScip" in query:
            return {"data": {"syncFromScip": self._sync_result}}
        elif "prunePackage" in query:
            return {"data": {"prunePackage": self._prune_result}}
        else:
            return {"data": None}


class TestTransformSymbolToInput:
    """Tests for _transform_symbol_to_input method.

    Validates: Requirements 2.1, 2.2
    """

    @pytest.fixture
    def adapter(self):
        """Create a GraphSyncAdapter with mock service."""
        mock_service = MockGraphQLService()
        return GraphSyncAdapter(mock_service)

    def test_transform_symbol_maps_all_fields(self, adapter):
        """Test that symbol transformation maps all required fields.

        Validates: Requirement 2.2
        """
        symbol = create_test_symbol(
            arn="arn:archon:code:personal-work/TestPackage/src/main.py#my_func",
            name="my_func",
            kind="function",
            signature="def my_func(x: int) -> str",
            documentation="My function docs.",
            file_path="src/main.py",
            line_number=42,
        )

        result = adapter._transform_symbol_to_input(
            symbol,
            workspace="personal-work",
            package="TestPackage",
        )

        assert result["arn"] == symbol.arn
        assert result["type"] == "CODE"
        assert result["workspace"] == "personal-work"
        assert result["package"] == "TestPackage"
        assert result["path"] == symbol.file_path
        assert result["symbol"] == symbol.name
        assert result["kind"] == "FUNCTION"
        assert result["name"] == symbol.name
        assert result["signature"] == symbol.signature
        assert result["documentation"] == symbol.documentation
        assert result["filePath"] == symbol.file_path
        assert result["lineNumber"] == symbol.line_number

    def test_transform_symbol_handles_optional_fields_as_none(self, adapter):
        """Test that optional fields can be None.

        Validates: Requirement 2.2
        """
        symbol = create_test_symbol(
            signature=None,
            documentation=None,
            line_number=None,
        )

        result = adapter._transform_symbol_to_input(
            symbol,
            workspace="personal-work",
            package="TestPackage",
        )

        assert result["signature"] is None
        assert result["documentation"] is None
        assert result["lineNumber"] is None

    @pytest.mark.parametrize(
        "kind,expected_graphql_kind",
        [
            ("function", "FUNCTION"),
            ("class", "CLASS"),
            ("method", "METHOD"),
            ("variable", "VARIABLE"),
            ("type", "TYPE"),
            ("module", "MODULE"),
            ("file", "FILE"),
            ("package", "PACKAGE"),
        ],
    )
    def test_transform_symbol_maps_all_valid_kinds(
        self, adapter, kind, expected_graphql_kind
    ):
        """Test that all valid symbol kinds are mapped correctly.

        Validates: Requirement 2.2
        """
        symbol = create_test_symbol(kind=kind)

        result = adapter._transform_symbol_to_input(
            symbol,
            workspace="personal-work",
            package="TestPackage",
        )

        assert result["kind"] == expected_graphql_kind

    def test_transform_symbol_kind_case_insensitive(self, adapter):
        """Test that symbol kind mapping is case-insensitive.

        Validates: Requirement 2.2
        """
        symbol = create_test_symbol(kind="FUNCTION")

        result = adapter._transform_symbol_to_input(
            symbol,
            workspace="personal-work",
            package="TestPackage",
        )

        assert result["kind"] == "FUNCTION"

    def test_transform_symbol_invalid_kind_raises_error(self, adapter):
        """Test that invalid symbol kind raises GraphSyncAdapterError.

        Validates: Requirement 2.2
        """
        symbol = create_test_symbol(kind="invalid_kind")

        with pytest.raises(GraphSyncAdapterError) as exc_info:
            adapter._transform_symbol_to_input(
                symbol,
                workspace="personal-work",
                package="TestPackage",
            )

        assert "Invalid symbol kind" in str(exc_info.value)
        assert "invalid_kind" in str(exc_info.value)


class TestTransformRelationshipToInput:
    """Tests for _transform_relationship_to_input method.

    Validates: Requirements 3.1, 3.2
    """

    @pytest.fixture
    def adapter(self):
        """Create a GraphSyncAdapter with mock service."""
        mock_service = MockGraphQLService()
        return GraphSyncAdapter(mock_service)

    def test_transform_relationship_maps_all_fields(self, adapter):
        """Test that relationship transformation maps all required fields.

        Validates: Requirement 3.2
        """
        relationship = create_test_relationship(
            from_arn="arn:archon:code:personal-work/TestPackage/src/main.py#MyClass",
            to_arn="arn:archon:code:personal-work/TestPackage/src/main.py#my_method",
            rel_type="contains",
        )

        result = adapter._transform_relationship_to_input(relationship)

        assert result["fromArn"] == relationship.from_arn
        assert result["toArn"] == relationship.to_arn
        assert result["type"] == "CONTAINS"

    @pytest.mark.parametrize(
        "rel_type,expected_graphql_type",
        [
            ("contains", "CONTAINS"),
            ("references", "REFERENCES"),
            ("implements", "IMPLEMENTS"),
            ("extends", "EXTENDS"),
            ("imports", "IMPORTS"),
            ("documents", "DOCUMENTS"),
        ],
    )
    def test_transform_relationship_maps_all_valid_types(
        self, adapter, rel_type, expected_graphql_type
    ):
        """Test that all valid relationship types are mapped correctly.

        Validates: Requirement 3.2
        """
        relationship = create_test_relationship(rel_type=rel_type)

        result = adapter._transform_relationship_to_input(relationship)

        assert result["type"] == expected_graphql_type

    def test_transform_relationship_type_case_insensitive(self, adapter):
        """Test that relationship type mapping is case-insensitive.

        Validates: Requirement 3.2
        """
        relationship = create_test_relationship(rel_type="CONTAINS")

        result = adapter._transform_relationship_to_input(relationship)

        assert result["type"] == "CONTAINS"

    def test_transform_relationship_invalid_type_raises_error(self, adapter):
        """Test that invalid relationship type raises GraphSyncAdapterError.

        Validates: Requirement 3.2
        """
        relationship = create_test_relationship(rel_type="invalid_type")

        with pytest.raises(GraphSyncAdapterError) as exc_info:
            adapter._transform_relationship_to_input(relationship)

        assert "Invalid relationship type" in str(exc_info.value)
        assert "invalid_type" in str(exc_info.value)


class TestSync:
    """Tests for sync method (batch upsert operations).

    Validates: Requirements 2.1, 2.3, 2.4, 3.1, 3.3, 3.4
    """

    def test_sync_empty_parse_result_calls_mutation(self):
        """Test that sync with empty parse result still calls mutation.

        Validates: Requirement 2.3
        """
        mock_service = MockGraphQLService(
            sync_result={
                "nodesCreated": 0,
                "nodesUpdated": 0,
                "edgesCreated": 0,
                "edgesRemoved": 0,
            }
        )
        adapter = GraphSyncAdapter(mock_service)
        parse_result = create_test_parse_result(symbols=[], relationships=[])

        async def run_test():
            result = await adapter.sync(parse_result)

            assert len(mock_service.execute_calls) == 1
            assert result.nodes_created == 0
            assert result.nodes_updated == 0
            assert result.edges_created == 0
            assert result.edges_removed == 0

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_transforms_symbols_to_inputs(self):
        """Test that sync transforms all symbols to GraphQL inputs.

        Validates: Requirements 2.1, 2.3
        """
        mock_service = MockGraphQLService(
            sync_result={
                "nodesCreated": 2,
                "nodesUpdated": 0,
                "edgesCreated": 0,
                "edgesRemoved": 0,
            }
        )
        adapter = GraphSyncAdapter(mock_service)

        symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/a.py#func_a",
                name="func_a",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/b.py#func_b",
                name="func_b",
            ),
        ]
        parse_result = create_test_parse_result(symbols=symbols)

        async def run_test():
            await adapter.sync(parse_result)

            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert len(variables["symbols"]) == 2
            assert variables["symbols"][0]["arn"] == symbols[0].arn
            assert variables["symbols"][1]["arn"] == symbols[1].arn

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_transforms_relationships_to_inputs(self):
        """Test that sync transforms all relationships to GraphQL inputs.

        Validates: Requirements 3.1, 3.3
        """
        mock_service = MockGraphQLService(
            sync_result={
                "nodesCreated": 0,
                "nodesUpdated": 0,
                "edgesCreated": 2,
                "edgesRemoved": 0,
            }
        )
        adapter = GraphSyncAdapter(mock_service)

        relationships = [
            create_test_relationship(
                from_arn="arn:archon:code:personal-work/TestPackage/src/a.py#ClassA",
                to_arn="arn:archon:code:personal-work/TestPackage/src/a.py#method_a",
                rel_type="contains",
            ),
            create_test_relationship(
                from_arn="arn:archon:code:personal-work/TestPackage/src/b.py#func_b",
                to_arn="arn:archon:code:personal-work/TestPackage/src/a.py#func_a",
                rel_type="references",
            ),
        ]
        parse_result = create_test_parse_result(relationships=relationships)

        async def run_test():
            await adapter.sync(parse_result)

            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert len(variables["relationships"]) == 2
            assert variables["relationships"][0]["fromArn"] == relationships[0].from_arn
            assert variables["relationships"][0]["toArn"] == relationships[0].to_arn
            assert variables["relationships"][0]["type"] == "CONTAINS"
            assert variables["relationships"][1]["type"] == "REFERENCES"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_passes_package_path_and_hash(self):
        """Test that sync passes package_path and index_hash to mutation.

        Validates: Requirement 2.3
        """
        mock_service = MockGraphQLService()
        adapter = GraphSyncAdapter(mock_service)

        parse_result = create_test_parse_result(
            package_path="/home/user/projects/MyPackage",
            index_hash="hash123abc",
        )

        async def run_test():
            await adapter.sync(parse_result)

            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert variables["packagePath"] == "/home/user/projects/MyPackage"
            assert variables["indexHash"] == "hash123abc"

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_returns_sync_result_with_counts(self):
        """Test that sync returns SyncResult with correct counts.

        Validates: Requirement 2.4
        """
        mock_service = MockGraphQLService(
            sync_result={
                "nodesCreated": 5,
                "nodesUpdated": 3,
                "edgesCreated": 10,
                "edgesRemoved": 2,
            }
        )
        adapter = GraphSyncAdapter(mock_service)
        parse_result = create_test_parse_result()

        async def run_test():
            result = await adapter.sync(parse_result)

            assert isinstance(result, SyncResult)
            assert result.nodes_created == 5
            assert result.nodes_updated == 3
            assert result.edges_created == 10
            assert result.edges_removed == 2

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_raises_error_on_graphql_errors(self):
        """Test that sync raises GraphSyncAdapterError on GraphQL errors.

        Validates: Requirement 2.3
        """
        mock_service = MockGraphQLService(
            errors=[{"message": "Database connection failed"}]
        )
        adapter = GraphSyncAdapter(mock_service)
        parse_result = create_test_parse_result()

        async def run_test():
            with pytest.raises(GraphSyncAdapterError) as exc_info:
                await adapter.sync(parse_result)

            assert "Failed to sync SCIP parse result" in str(exc_info.value)
            assert "Database connection failed" in str(exc_info.value)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_with_mixed_symbols_and_relationships(self):
        """Test sync with both symbols and relationships.

        Validates: Requirements 2.3, 3.3
        """
        mock_service = MockGraphQLService(
            sync_result={
                "nodesCreated": 3,
                "nodesUpdated": 1,
                "edgesCreated": 2,
                "edgesRemoved": 0,
            }
        )
        adapter = GraphSyncAdapter(mock_service)

        symbols = [
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#MyClass",
                name="MyClass",
                kind="class",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#method_a",
                name="method_a",
                kind="method",
            ),
            create_test_symbol(
                arn="arn:archon:code:personal-work/TestPackage/src/main.py#method_b",
                name="method_b",
                kind="method",
            ),
        ]
        relationships = [
            create_test_relationship(
                from_arn=symbols[0].arn,
                to_arn=symbols[1].arn,
                rel_type="contains",
            ),
            create_test_relationship(
                from_arn=symbols[0].arn,
                to_arn=symbols[2].arn,
                rel_type="contains",
            ),
        ]
        parse_result = create_test_parse_result(
            symbols=symbols,
            relationships=relationships,
        )

        async def run_test():
            result = await adapter.sync(parse_result)

            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert len(variables["symbols"]) == 3
            assert len(variables["relationships"]) == 2
            assert result.nodes_created == 3
            assert result.edges_created == 2

        asyncio.get_event_loop().run_until_complete(run_test())


class TestPrune:
    """Tests for prune method (stale node pruning).

    Validates: Requirements 6.1, 6.2
    """

    def test_prune_calls_mutation_with_package_and_arns(self):
        """Test that prune calls prunePackage mutation with correct parameters.

        Validates: Requirement 6.1
        """
        mock_service = MockGraphQLService(prune_result=0)
        adapter = GraphSyncAdapter(mock_service)

        keep_arns = [
            "arn:archon:code:personal-work/TestPackage/src/a.py#func_a",
            "arn:archon:code:personal-work/TestPackage/src/b.py#func_b",
        ]

        async def run_test():
            await adapter.prune("TestPackage", keep_arns)

            assert len(mock_service.execute_calls) == 1
            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert variables["package"] == "TestPackage"
            assert variables["keepArns"] == keep_arns

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_returns_count_of_removed_nodes(self):
        """Test that prune returns count of removed nodes.

        Validates: Requirement 6.2
        """
        mock_service = MockGraphQLService(prune_result=5)
        adapter = GraphSyncAdapter(mock_service)

        async def run_test():
            removed = await adapter.prune("TestPackage", [])

            assert removed == 5

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_with_empty_keep_arns_removes_all(self):
        """Test that prune with empty keep_arns removes all nodes.

        Validates: Requirement 6.2
        """
        mock_service = MockGraphQLService(prune_result=10)
        adapter = GraphSyncAdapter(mock_service)

        async def run_test():
            removed = await adapter.prune("TestPackage", [])

            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert variables["keepArns"] == []
            assert removed == 10

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_raises_error_on_graphql_errors(self):
        """Test that prune raises GraphSyncAdapterError on GraphQL errors.

        Validates: Requirement 6.1
        """
        mock_service = MockGraphQLService(
            errors=[{"message": "Package not found"}]
        )
        adapter = GraphSyncAdapter(mock_service)

        async def run_test():
            with pytest.raises(GraphSyncAdapterError) as exc_info:
                await adapter.prune("NonExistentPackage", [])

            assert "Failed to prune package" in str(exc_info.value)
            assert "NonExistentPackage" in str(exc_info.value)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_prune_with_many_keep_arns(self):
        """Test prune with many ARNs to keep.

        Validates: Requirement 6.2
        """
        mock_service = MockGraphQLService(prune_result=2)
        adapter = GraphSyncAdapter(mock_service)

        keep_arns = [
            f"arn:archon:code:personal-work/TestPackage/src/file{i}.py#func{i}"
            for i in range(100)
        ]

        async def run_test():
            removed = await adapter.prune("TestPackage", keep_arns)

            call = mock_service.execute_calls[0]
            variables = call["variables"]

            assert len(variables["keepArns"]) == 100
            assert removed == 2

        asyncio.get_event_loop().run_until_complete(run_test())


class TestErrorHandling:
    """Tests for error handling in GraphSyncAdapter.

    Validates: Requirements 2.2, 3.2
    """

    @pytest.fixture
    def adapter(self):
        """Create a GraphSyncAdapter with mock service."""
        mock_service = MockGraphQLService()
        return GraphSyncAdapter(mock_service)

    def test_invalid_symbol_kind_error_message_includes_valid_kinds(self, adapter):
        """Test that invalid kind error includes list of valid kinds.

        Validates: Requirement 2.2
        """
        symbol = create_test_symbol(kind="unknown_kind")

        with pytest.raises(GraphSyncAdapterError) as exc_info:
            adapter._transform_symbol_to_input(
                symbol,
                workspace="personal-work",
                package="TestPackage",
            )

        error_message = str(exc_info.value)
        for valid_kind in SCIP_KIND_TO_GRAPHQL.keys():
            assert valid_kind in error_message

    def test_invalid_relationship_type_error_message_includes_valid_types(self, adapter):
        """Test that invalid type error includes list of valid types.

        Validates: Requirement 3.2
        """
        relationship = create_test_relationship(rel_type="unknown_type")

        with pytest.raises(GraphSyncAdapterError) as exc_info:
            adapter._transform_relationship_to_input(relationship)

        error_message = str(exc_info.value)
        for valid_type in SCIP_TYPE_TO_GRAPHQL.keys():
            assert valid_type in error_message

    def test_sync_with_invalid_symbol_kind_raises_error(self):
        """Test that sync raises error when symbol has invalid kind.

        Validates: Requirement 2.2
        """
        mock_service = MockGraphQLService()
        adapter = GraphSyncAdapter(mock_service)

        symbols = [create_test_symbol(kind="invalid_kind")]
        parse_result = create_test_parse_result(symbols=symbols)

        async def run_test():
            with pytest.raises(GraphSyncAdapterError) as exc_info:
                await adapter.sync(parse_result)

            assert "Invalid symbol kind" in str(exc_info.value)

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_sync_with_invalid_relationship_type_raises_error(self):
        """Test that sync raises error when relationship has invalid type.

        Validates: Requirement 3.2
        """
        mock_service = MockGraphQLService()
        adapter = GraphSyncAdapter(mock_service)

        relationships = [create_test_relationship(rel_type="invalid_type")]
        parse_result = create_test_parse_result(relationships=relationships)

        async def run_test():
            with pytest.raises(GraphSyncAdapterError) as exc_info:
                await adapter.sync(parse_result)

            assert "Invalid relationship type" in str(exc_info.value)

        asyncio.get_event_loop().run_until_complete(run_test())


class TestAdapterInitialization:
    """Tests for GraphSyncAdapter initialization.

    Validates: Requirements 2.1, 3.1
    """

    def test_adapter_accepts_graph_service(self):
        """Test that adapter accepts GraphQLService parameter."""
        mock_service = MagicMock(spec=GraphQLService)

        adapter = GraphSyncAdapter(mock_service)

        assert adapter._service == mock_service

    def test_adapter_stores_service_reference(self):
        """Test that adapter stores service reference for later use."""
        mock_service = MockGraphQLService()

        adapter = GraphSyncAdapter(mock_service)

        assert adapter._service is mock_service


class TestKindAndTypeMappings:
    """Tests for SCIP kind and type mapping constants.

    Validates: Requirements 2.2, 3.2
    """

    def test_all_symbol_kinds_have_graphql_mapping(self):
        """Test that all expected symbol kinds have GraphQL mappings.

        Validates: Requirement 2.2
        """
        expected_kinds = [
            "function",
            "class",
            "method",
            "variable",
            "type",
            "module",
            "file",
            "package",
        ]

        for kind in expected_kinds:
            assert kind in SCIP_KIND_TO_GRAPHQL, f"Missing mapping for kind: {kind}"

    def test_all_relationship_types_have_graphql_mapping(self):
        """Test that all expected relationship types have GraphQL mappings.

        Validates: Requirement 3.2
        """
        expected_types = [
            "contains",
            "references",
            "implements",
            "extends",
            "imports",
            "documents",
        ]

        for rel_type in expected_types:
            assert rel_type in SCIP_TYPE_TO_GRAPHQL, f"Missing mapping for type: {rel_type}"

    def test_graphql_kind_values_are_uppercase(self):
        """Test that GraphQL kind values are uppercase enum values.

        Validates: Requirement 2.2
        """
        for scip_kind, graphql_kind in SCIP_KIND_TO_GRAPHQL.items():
            assert graphql_kind.isupper(), f"GraphQL kind should be uppercase: {graphql_kind}"
            assert graphql_kind == scip_kind.upper(), f"GraphQL kind should match uppercase SCIP kind"

    def test_graphql_type_values_are_uppercase(self):
        """Test that GraphQL type values are uppercase enum values.

        Validates: Requirement 3.2
        """
        for scip_type, graphql_type in SCIP_TYPE_TO_GRAPHQL.items():
            assert graphql_type.isupper(), f"GraphQL type should be uppercase: {graphql_type}"
            assert graphql_type == scip_type.upper(), f"GraphQL type should match uppercase SCIP type"
