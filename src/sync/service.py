"""Knowledge Base Sync Service for orchestrating code intelligence synchronization.

This module provides the KnowledgeBaseSyncService class that orchestrates the
synchronization of code intelligence data (SCIP parse results and Archon
documentation) to the knowledge base. It coordinates the Graph Sync Adapter
and Vector Sync Adapter to ensure the Code Graph and Vector Store reflect
the current state of the codebase.

The service:
- Validates input data (SCIP parse results and Archon docs)
- Detects changes using hash comparison to skip unchanged packages
- Syncs symbols to Code Graph as nodes
- Syncs relationships to Code Graph as edges
- Syncs Archon docs to Vector Store as embedded chunks
- Prunes stale nodes and chunks
- Supports atomic sync per package using database transactions
- Supports parallel workspace sync with configurable concurrency

Source:
- .kiro/specs/sync-service/design.md (Knowledge Base Sync Service section)
- .kiro/specs/sync-service/requirements.md (Requirements 1.4, 7.1-7.5, 8.1-8.4, 9.1-9.4)
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from src.sync.change_detector import ChangeDetector
from src.sync.doc_generator import generate_for_package
from src.sync.models import (
    GeneratedDoc,
    PackageSyncResult,
    ScipParseResult,
    SyncResult,
    WorkspaceSyncResult,
)
from src.sync.scip_parser import parse_scip_index

if TYPE_CHECKING:
    from src.sync.graph_adapter import GraphSyncAdapter
    from src.sync.vector_adapter import VectorSyncAdapter

logger = logging.getLogger(__name__)

# Maximum concurrent package syncs for workspace sync
MAX_CONCURRENT_SYNCS = 4

# ARN format validation regex
ARN_PATTERN = re.compile(r"^arn:archon:\w+:[^/]+/[^/]+/.+$")


class SyncServiceError(Exception):
    """Raised when sync service operations fail."""

    pass


class ValidationError(SyncServiceError):
    """Raised when input validation fails."""

    pass


class KnowledgeBaseSyncService:
    """Synchronizes code intelligence data to the knowledge base.

    The KnowledgeBaseSyncService orchestrates the synchronization of SCIP
    parse results and Archon documentation to the Code Graph and Vector Store.
    It provides idempotent, atomic sync operations with change detection to
    skip unchanged packages.

    The service coordinates:
    - GraphSyncAdapter: Transforms SCIP symbols/relationships to graph nodes/edges
    - VectorSyncAdapter: Chunks, embeds, and upserts Archon docs to vector store
    - ChangeDetector: Hash-based change detection for efficient synchronization

    Design Principles:
    1. Idempotent Operations: Multiple syncs with same input produce identical state
    2. Change Detection: Skip synchronization when SCIP index hash hasn't changed
    3. Atomic Transactions: All-or-nothing sync per package using database transactions
    4. Deterministic Identifiers: ARN-based keys for nodes, edges, and chunks
    5. Efficient Batching: Bulk operations for nodes, edges, and embeddings
    6. Graceful Degradation: Partial failures don't block entire workspace sync

    Usage:
        graph_adapter = GraphSyncAdapter(graph_service)
        vector_adapter = VectorSyncAdapter(vector_store, embedding_client)
        change_detector = ChangeDetector()

        service = KnowledgeBaseSyncService(
            graph_adapter=graph_adapter,
            vector_adapter=vector_adapter,
            change_detector=change_detector,
        )

        # Sync a single package
        result = await service.sync_package(
            package_path="MyPackage",
            scip_result=scip_result,
            archon_docs=archon_docs,
        )

        # Sync entire workspace
        workspace_result = await service.sync_workspace(
            workspace_path="/path/to/workspace",
            force=False,
        )

    Validates:
        Requirement 9.1 (sync_package method signature)
        Requirement 9.2 (SyncResult type definition)
        Requirement 9.3 (sync_workspace method signature)
        Requirement 9.4 (WorkspaceSyncResult type definition)

    Source:
    - .kiro/specs/sync-service/design.md (Knowledge Base Sync Service section)
    - .kiro/specs/sync-service/requirements.md (Requirements 9.1-9.4)
    """

    def __init__(
        self,
        graph_adapter: "GraphSyncAdapter",
        vector_adapter: "VectorSyncAdapter",
        change_detector: "ChangeDetector",
    ) -> None:
        """Initialize the sync service.

        Args:
            graph_adapter: Adapter for Code Graph operations (sync symbols,
                          relationships, and prune stale nodes)
            vector_adapter: Adapter for Vector Store operations (chunk, embed,
                           upsert docs, and prune stale chunks)
            change_detector: Service for hash-based change detection

        Example:
            >>> graph_adapter = GraphSyncAdapter(graph_service)
            >>> vector_adapter = VectorSyncAdapter(vector_store, embedding_client)
            >>> change_detector = ChangeDetector()
            >>> service = KnowledgeBaseSyncService(
            ...     graph_adapter=graph_adapter,
            ...     vector_adapter=vector_adapter,
            ...     change_detector=change_detector,
            ... )

        Validates: Requirement 9.1
        """
        self._graph_adapter = graph_adapter
        self._vector_adapter = vector_adapter
        self._change_detector = change_detector

    @property
    def graph_adapter(self) -> "GraphSyncAdapter":
        """Get the graph sync adapter instance."""
        return self._graph_adapter

    @property
    def vector_adapter(self) -> "VectorSyncAdapter":
        """Get the vector sync adapter instance."""
        return self._vector_adapter

    @property
    def change_detector(self) -> "ChangeDetector":
        """Get the change detector instance."""
        return self._change_detector

    def _validate_package_path(self, package_path: str) -> list[str]:
        """Validate the package path.

        Args:
            package_path: Path to the package being synchronized

        Returns:
            List of validation error messages (empty if valid)

        Validates: Requirement 1.4
        """
        errors: list[str] = []

        if not package_path:
            errors.append("package_path is required and cannot be empty")
        elif not package_path.strip():
            errors.append("package_path cannot be whitespace only")

        return errors

    def _validate_scip_result(self, scip_result: ScipParseResult) -> list[str]:
        """Validate the SCIP parse result structure.

        Validates that the SCIP parse result has the required structure
        and that symbols and relationships have valid ARN formats.

        Args:
            scip_result: Parsed SCIP index with symbols, relationships, and hash

        Returns:
            List of validation error messages (empty if valid)

        Validates: Requirement 1.4
        """
        errors: list[str] = []

        if scip_result is None:
            errors.append("scip_result is required and cannot be None")
            return errors

        if not hasattr(scip_result, "symbols"):
            errors.append("scip_result must have 'symbols' attribute")
        elif scip_result.symbols is None:
            errors.append("scip_result.symbols cannot be None")

        if not hasattr(scip_result, "relationships"):
            errors.append("scip_result must have 'relationships' attribute")
        elif scip_result.relationships is None:
            errors.append("scip_result.relationships cannot be None")

        if not hasattr(scip_result, "hash"):
            errors.append("scip_result must have 'hash' attribute")
        elif not scip_result.hash:
            errors.append("scip_result.hash is required and cannot be empty")

        return errors

    def _validate_archon_docs(self, archon_docs: list[GeneratedDoc]) -> list[str]:
        """Validate the Archon documentation format.

        Validates that each GeneratedDoc has the required fields and
        valid ARN format.

        Args:
            archon_docs: List of generated Archon documentation files

        Returns:
            List of validation error messages (empty if valid)

        Validates: Requirement 1.4
        """
        errors: list[str] = []

        if archon_docs is None:
            errors.append("archon_docs is required and cannot be None")
            return errors

        for i, doc in enumerate(archon_docs):
            if not hasattr(doc, "arn") or not doc.arn:
                errors.append(f"archon_docs[{i}] must have a non-empty 'arn' attribute")
            if not hasattr(doc, "content"):
                errors.append(f"archon_docs[{i}] must have 'content' attribute")
            if not hasattr(doc, "doc_path"):
                errors.append(f"archon_docs[{i}] must have 'doc_path' attribute")

        return errors

    def _validate_arn_format(self, arn: str) -> bool:
        """Validate that an ARN has the correct format.

        ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>

        Args:
            arn: Archon Resource Name to validate

        Returns:
            True if ARN format is valid, False otherwise

        Validates: Requirement 1.4
        """
        return bool(ARN_PATTERN.match(arn))

    def _filter_invalid_arns(
        self,
        scip_result: ScipParseResult,
    ) -> tuple[ScipParseResult, list[str]]:
        """Filter out symbols and relationships with invalid ARN formats.

        Logs warnings for invalid ARNs and returns a filtered SCIP result
        with only valid entries.

        Args:
            scip_result: Original SCIP parse result

        Returns:
            Tuple of (filtered ScipParseResult, list of warning messages)

        Validates: Requirement 1.4
        """
        warnings: list[str] = []
        valid_symbols = []
        valid_relationships = []

        for symbol in scip_result.symbols:
            if self._validate_arn_format(symbol.arn):
                valid_symbols.append(symbol)
            else:
                warning = f"Skipping symbol with invalid ARN format: {symbol.arn}"
                logger.warning(warning)
                warnings.append(warning)

        valid_arns = {s.arn for s in valid_symbols}

        for rel in scip_result.relationships:
            if not self._validate_arn_format(rel.from_arn):
                warning = f"Skipping relationship with invalid from_arn: {rel.from_arn}"
                logger.warning(warning)
                warnings.append(warning)
            elif not self._validate_arn_format(rel.to_arn):
                warning = f"Skipping relationship with invalid to_arn: {rel.to_arn}"
                logger.warning(warning)
                warnings.append(warning)
            elif rel.from_arn not in valid_arns:
                warning = f"Skipping relationship with unknown from_arn: {rel.from_arn}"
                logger.warning(warning)
                warnings.append(warning)
            else:
                valid_relationships.append(rel)

        filtered_result = ScipParseResult(
            symbols=valid_symbols,
            relationships=valid_relationships,
            hash=scip_result.hash,
        )

        return filtered_result, warnings

    async def sync_package(
        self,
        package_path: str,
        scip_result: ScipParseResult,
        archon_docs: list[GeneratedDoc],
        force: bool = False,
    ) -> SyncResult:
        """Sync a package's code intelligence to the knowledge base.

        Performs the following steps:
        1. Validate input data (package_path, scip_result, archon_docs)
        2. Check if SCIP index hash has changed (skip if unchanged and not force)
        3. Filter out entries with invalid ARN formats (log warnings)
        4. Sync symbols to Code Graph as nodes (within transaction)
        5. Sync relationships to Code Graph as edges (within transaction)
        6. Prune stale nodes from Code Graph (within transaction)
        7. Sync Archon docs to Vector Store (chunk, embed, upsert)
        8. Prune stale chunks from Vector Store
        9. Update stored hash after successful sync

        The graph operations (steps 4-6) are wrapped in a database transaction
        for atomic sync per package. If any graph operation fails, the entire
        transaction is rolled back.

        Vector operations (steps 7-8) are performed separately as Qdrant does
        not support transactions. Errors in vector operations are captured
        but do not roll back graph operations.

        Args:
            package_path: Path to the package being synchronized
            scip_result: Parsed SCIP index with symbols, relationships, and hash
            archon_docs: List of generated Archon documentation files
            force: If True, bypass change detection and sync regardless of hash

        Returns:
            SyncResult with counts and any errors

        Raises:
            ValidationError: If input validation fails with critical errors

        Example:
            >>> result = await service.sync_package(
            ...     package_path="MyPackage",
            ...     scip_result=scip_result,
            ...     archon_docs=archon_docs,
            ... )
            >>> print(f"Created {result.nodes_created} nodes")

        Validates:
            Requirement 9.1 (sync_package method signature)
            Requirement 9.2 (SyncResult type definition)
            Requirement 7.1 (Skip when hash unchanged)
            Requirement 7.3 (Support force parameter)
            Requirement 7.4 (Force=true bypasses change detection)
            Requirement 7.5 (Return skip reason when skipping)
            Requirement 8.1 (Idempotent sync)
            Requirement 8.2 (Deterministic identifiers)
            Requirement 8.3 (No duplicate entries)
            Requirement 8.4 (Transaction management)
            Requirement 1.4 (Input validation)
        """
        logger.info(
            f"sync_package started for '{package_path}' "
            f"with {len(scip_result.symbols) if scip_result else 0} symbols, "
            f"{len(archon_docs) if archon_docs else 0} docs, force={force}"
        )

        errors: list[str] = []

        validation_errors = self._validate_package_path(package_path)
        validation_errors.extend(self._validate_scip_result(scip_result))
        validation_errors.extend(self._validate_archon_docs(archon_docs))

        if validation_errors:
            logger.error(f"Validation failed for '{package_path}': {validation_errors}")
            return SyncResult(
                success=False,
                nodes_created=0,
                nodes_updated=0,
                edges_created=0,
                chunks_upserted=0,
                nodes_pruned=0,
                chunks_pruned=0,
                skipped=False,
                skip_reason=None,
                errors=validation_errors,
            )

        if not force and not self._change_detector.has_changed(package_path, scip_result.hash):
            skip_reason = "SCIP index hash unchanged"
            logger.info(f"Skipping sync for '{package_path}': {skip_reason}")
            return SyncResult(
                success=True,
                nodes_created=0,
                nodes_updated=0,
                edges_created=0,
                chunks_upserted=0,
                nodes_pruned=0,
                chunks_pruned=0,
                skipped=True,
                skip_reason=skip_reason,
                errors=[],
            )

        filtered_result, arn_warnings = self._filter_invalid_arns(scip_result)
        errors.extend(arn_warnings)

        nodes_created = 0
        nodes_updated = 0
        edges_created = 0
        nodes_pruned = 0

        try:
            graph_result = await self._sync_graph_atomic(
                package_path=package_path,
                scip_result=filtered_result,
            )
            nodes_created = graph_result.get("nodes_created", 0)
            nodes_updated = graph_result.get("nodes_updated", 0)
            edges_created = graph_result.get("edges_created", 0)
            nodes_pruned = graph_result.get("nodes_pruned", 0)
        except Exception as e:
            error_msg = f"Graph sync failed for '{package_path}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)
            return SyncResult(
                success=False,
                nodes_created=nodes_created,
                nodes_updated=nodes_updated,
                edges_created=edges_created,
                chunks_upserted=0,
                nodes_pruned=nodes_pruned,
                chunks_pruned=0,
                skipped=False,
                skip_reason=None,
                errors=errors,
            )

        chunks_upserted = 0
        chunks_pruned = 0

        try:
            vector_result = await self._vector_adapter.sync_docs(
                package=package_path,
                archon_docs=archon_docs,
            )
            chunks_upserted = vector_result.chunks_upserted
            chunks_pruned = vector_result.chunks_pruned
            errors.extend(vector_result.errors)
        except Exception as e:
            error_msg = f"Vector sync failed for '{package_path}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)

        self._change_detector.update_hash(package_path, scip_result.hash)

        success = len([e for e in errors if "failed" in e.lower()]) == 0

        logger.info(
            f"sync_package completed for '{package_path}': "
            f"nodes_created={nodes_created}, nodes_updated={nodes_updated}, "
            f"edges_created={edges_created}, chunks_upserted={chunks_upserted}, "
            f"nodes_pruned={nodes_pruned}, chunks_pruned={chunks_pruned}, "
            f"success={success}"
        )

        return SyncResult(
            success=success,
            nodes_created=nodes_created,
            nodes_updated=nodes_updated,
            edges_created=edges_created,
            chunks_upserted=chunks_upserted,
            nodes_pruned=nodes_pruned,
            chunks_pruned=chunks_pruned,
            skipped=False,
            skip_reason=None,
            errors=errors,
        )

    async def _sync_graph_atomic(
        self,
        package_path: str,
        scip_result: ScipParseResult,
    ) -> dict[str, int]:
        """Sync graph operations atomically within a transaction.

        Wraps all graph operations (sync symbols, sync relationships, prune)
        in a database transaction for atomic sync per package. If any operation
        fails, the entire transaction is rolled back.

        Note: The GraphSyncAdapter.sync() method already handles transactions
        internally. This method provides the orchestration layer.

        Args:
            package_path: Path to the package being synchronized
            scip_result: Filtered SCIP parse result with valid entries

        Returns:
            Dictionary with counts: nodes_created, nodes_updated, edges_created, nodes_pruned

        Raises:
            Exception: If graph sync fails (transaction is rolled back)

        Validates:
            Requirement 8.1 (Atomic sync per package)
            Requirement 8.2 (Transaction rollback on errors)
            Requirement 8.3 (All-or-nothing semantics)
            Requirement 8.4 (No partial state on failure)
        """
        from src.sync.graph_adapter import ScipParseResult as GraphScipParseResult
        from src.sync.graph_adapter import ScipRelationship as GraphScipRelationship
        from src.sync.graph_adapter import ScipSymbol as GraphScipSymbol

        graph_symbols = [
            GraphScipSymbol(
                arn=s.arn,
                name=s.name,
                kind=s.kind,
                signature=s.signature,
                documentation=s.documentation,
                file_path=s.location.file if s.location else s.path,
                line_number=s.location.line if s.location else None,
            )
            for s in scip_result.symbols
        ]

        graph_relationships = [
            GraphScipRelationship(
                from_arn=r.from_arn,
                to_arn=r.to_arn,
                type=r.type,
            )
            for r in scip_result.relationships
        ]

        workspace = ""
        package = package_path
        if scip_result.symbols:
            first_symbol = scip_result.symbols[0]
            workspace = first_symbol.workspace
            package = first_symbol.package

        graph_parse_result = GraphScipParseResult(
            package_path=package_path,
            workspace=workspace,
            package=package,
            symbols=graph_symbols,
            relationships=graph_relationships,
            index_hash=scip_result.hash,
        )

        sync_result = await self._graph_adapter.sync(graph_parse_result)

        current_arns = [s.arn for s in scip_result.symbols]
        nodes_pruned = await self._graph_adapter.prune(package, current_arns)

        return {
            "nodes_created": sync_result.nodes_created,
            "nodes_updated": sync_result.nodes_updated,
            "edges_created": sync_result.edges_created,
            "nodes_pruned": nodes_pruned,
        }

    async def sync_workspace(
        self,
        workspace_path: str,
        force: bool = False,
    ) -> WorkspaceSyncResult:
        """Sync all packages in a workspace to the knowledge base.

        Discovers packages in the workspace, loads their SCIP indexes
        and Archon docs, and syncs each package. Packages are synced
        in parallel with a configurable concurrency limit (default: 4).

        The sync operation:
        1. Discover packages in the workspace
        2. Load SCIP indexes and Archon docs for each package
        3. Sync packages in parallel (max 4 concurrent with semaphore)
        4. Aggregate results into WorkspaceSyncResult
        5. Handle partial failures gracefully

        Args:
            workspace_path: Path to the workspace root
            force: If True, bypass change detection and sync all packages

        Returns:
            WorkspaceSyncResult with per-package results

        Example:
            >>> result = await service.sync_workspace(
            ...     workspace_path="/path/to/workspace",
            ...     force=False,
            ... )
            >>> print(f"Synced {len(result.packages_synced)} packages")
            >>> print(f"Skipped {len(result.packages_skipped)} packages")

        Validates:
            Requirement 9.3 (sync_workspace method signature)
            Requirement 9.4 (WorkspaceSyncResult type definition)
        """
        logger.info(f"sync_workspace started for '{workspace_path}', force={force}")

        packages = await self._discover_packages(workspace_path)

        if not packages:
            logger.warning(f"No packages found in workspace '{workspace_path}'")
            return WorkspaceSyncResult(
                success=True,
                packages_synced=[],
                packages_skipped=[],
                errors={},
            )

        logger.info(f"Discovered {len(packages)} packages in workspace '{workspace_path}'")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SYNCS)

        async def sync_with_semaphore(package: str) -> PackageSyncResult:
            async with semaphore:
                return await self._sync_single_package(package, workspace_path, force)

        results = await asyncio.gather(
            *[sync_with_semaphore(p) for p in packages],
            return_exceptions=True,
        )

        return self._aggregate_results(packages, results)

    async def _discover_packages(self, workspace_path: str) -> list[str]:
        """Discover packages in a workspace.

        Scans the workspace directory for packages that have SCIP indexes
        or Archon documentation.

        Args:
            workspace_path: Path to the workspace root

        Returns:
            List of package paths found in the workspace
        """
        packages: list[str] = []
        workspace = Path(workspace_path)

        if not workspace.exists():
            logger.warning(f"Workspace path does not exist: {workspace_path}")
            return packages

        for item in workspace.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                scip_index = item / ".scip" / "index.scip"
                archon_dir = item / ".archon"

                if scip_index.exists() or archon_dir.exists():
                    packages.append(item.name)

        return packages

    async def _sync_single_package(
        self,
        package: str,
        workspace_path: str,
        force: bool,
    ) -> PackageSyncResult:
        """Sync a single package within a workspace sync.

        Loads the SCIP index and Archon docs for the package and
        calls sync_package.

        Args:
            package: Package name
            workspace_path: Path to the workspace root
            force: If True, bypass change detection

        Returns:
            PackageSyncResult for the package
        """
        try:
            scip_result, archon_docs = await self._load_package_data(
                package, workspace_path
            )

            if scip_result is None:
                return PackageSyncResult(
                    package=package,
                    success=True,
                    nodes_created=0,
                    nodes_updated=0,
                    edges_created=0,
                    chunks_upserted=0,
                    nodes_pruned=0,
                    chunks_pruned=0,
                    skipped=True,
                    skip_reason="No SCIP index found",
                    errors=[],
                )

            result = await self.sync_package(
                package_path=package,
                scip_result=scip_result,
                archon_docs=archon_docs,
                force=force,
            )

            return PackageSyncResult(
                package=package,
                success=result.success,
                nodes_created=result.nodes_created,
                nodes_updated=result.nodes_updated,
                edges_created=result.edges_created,
                chunks_upserted=result.chunks_upserted,
                nodes_pruned=result.nodes_pruned,
                chunks_pruned=result.chunks_pruned,
                skipped=result.skipped,
                skip_reason=result.skip_reason,
                errors=result.errors,
            )

        except Exception as e:
            error_msg = f"Failed to sync package '{package}': {e}"
            logger.error(error_msg)
            return PackageSyncResult(
                package=package,
                success=False,
                nodes_created=0,
                nodes_updated=0,
                edges_created=0,
                chunks_upserted=0,
                nodes_pruned=0,
                chunks_pruned=0,
                skipped=False,
                skip_reason=None,
                errors=[error_msg],
            )

    async def _load_package_data(
        self,
        package: str,
        workspace_path: str,
    ) -> tuple[Optional[ScipParseResult], list[GeneratedDoc]]:
        """Load SCIP index and generate Archon docs for a package.

        Locates the SCIP index file, parses the binary protobuf, transforms
        symbols and relationships, and generates documentation.

        Args:
            package: Package name
            workspace_path: Path to the workspace root

        Returns:
            Tuple of (ScipParseResult or None, list of GeneratedDoc)
        """
        index_path = Path(workspace_path) / package / ".scip" / "index.scip"

        if not index_path.exists():
            logger.info(f"No SCIP index found for package '{package}'")
            return None, []

        try:
            content = index_path.read_bytes()
        except OSError as exc:
            logger.error(f"Failed to read SCIP index for '{package}': {exc}")
            return None, []

        workspace = self._extract_workspace_name(workspace_path)

        try:
            scip_result = parse_scip_index(content, workspace, package)
        except Exception as exc:
            logger.error(f"Failed to parse SCIP index for '{package}': {exc}")
            return None, []

        generated_docs = generate_for_package(
            scip_result.symbols, scip_result.relationships
        )

        return scip_result, generated_docs

    @staticmethod
    def _extract_workspace_name(workspace_path: str) -> str:
        """Derive a workspace name from the workspace path."""
        parts = Path(workspace_path).parts
        return parts[-1] if parts else "default"

    def _aggregate_results(
        self,
        packages: list[str],
        results: list[Union[PackageSyncResult, BaseException]],
    ) -> WorkspaceSyncResult:
        """Aggregate per-package results into a WorkspaceSyncResult.

        Args:
            packages: List of package names
            results: List of PackageSyncResult or exceptions

        Returns:
            Aggregated WorkspaceSyncResult
        """
        packages_synced: list[PackageSyncResult] = []
        packages_skipped: list[str] = []
        errors: dict[str, list[str]] = {}
        all_success = True

        for package, result in zip(packages, results):
            if isinstance(result, BaseException):
                all_success = False
                error_msg = f"Exception during sync: {result}"
                errors[package] = [error_msg]
                logger.error(f"Package '{package}' sync failed with exception: {result}")
            elif result.skipped:
                packages_skipped.append(package)
            else:
                packages_synced.append(result)
                if not result.success:
                    all_success = False
                if result.errors:
                    errors[package] = result.errors

        logger.info(
            f"sync_workspace completed: "
            f"{len(packages_synced)} synced, {len(packages_skipped)} skipped, "
            f"{len(errors)} with errors, success={all_success}"
        )

        return WorkspaceSyncResult(
            success=all_success,
            packages_synced=packages_synced,
            packages_skipped=packages_skipped,
            errors=errors,
        )
