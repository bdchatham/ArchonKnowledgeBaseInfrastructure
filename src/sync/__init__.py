"""Sync module for synchronizing external data sources with storage.

This module provides adapters and services for syncing data from various sources
into the Code Graph and Vector Store. The adapters transform source-specific
data formats into appropriate storage operations.

Components:
- models: Input data models for SCIP parse results and Archon documentation,
          plus result type models for sync operation outcomes
- graph_adapter: Adapter for syncing SCIP parse results to the Code Graph
- vector_adapter: Adapter for syncing Archon docs to the Vector Store
- change_detector: Hash-based change detection for efficient synchronization
- language_detector: Auto-detection of programming languages from package manifests
- service: KnowledgeBaseSyncService orchestrating the complete sync workflow

Source:
- .kiro/specs/sync-service/design.md (Data Models section)
- .kiro/specs/sync-service/requirements.md (Requirements 1.1, 1.2, 1.3, 7.1, 7.2, 9.1, 9.2, 9.3, 9.4)
- .kiro/specs/code-graph-storage/design.md (Sync Adapter section)
- .kiro/specs/code-graph-storage/requirements.md (Requirements 6.1-6.4)
- .kiro/specs/vector-store-arn/design.md (Vector Sync Adapter section)
- .kiro/specs/vector-store-arn/requirements.md (Requirement 2.1)
"""

from src.sync.change_detector import ChangeDetector
from src.sync.graph_adapter import (
    GraphSyncAdapter,
    SyncResult,
)
from src.sync.graph_adapter import (
    ScipParseResult as GraphScipParseResult,
    ScipRelationship as GraphScipRelationship,
    ScipSymbol as GraphScipSymbol,
)
from src.sync.doc_generator import generate_for_file, generate_for_package
from src.sync.language_detector import detect_languages
from src.sync.models import (
    GeneratedDoc,
    GraphSyncResult,
    HashState,
    PackageSyncResult,
    ScipParseResult,
    ScipRelationship,
    ScipSymbol,
    SymbolLocation,
    SyncResult as ModelSyncResult,
    VectorSyncResult as ModelVectorSyncResult,
    WorkspaceSyncResult,
)
from src.sync.service import (
    KnowledgeBaseSyncService,
    SyncServiceError,
    ValidationError,
)
from src.sync.vector_adapter import (
    VectorSyncAdapter,
    VectorSyncAdapterError,
    VectorSyncResult,
)

__all__ = [
    # Input data models
    "SymbolLocation",
    "ScipSymbol",
    "ScipRelationship",
    "ScipParseResult",
    "GeneratedDoc",
    "HashState",
    # Result type models
    "ModelSyncResult",
    "PackageSyncResult",
    "WorkspaceSyncResult",
    "GraphSyncResult",
    "ModelVectorSyncResult",
    # Graph sync (legacy models for backward compatibility)
    "GraphScipSymbol",
    "GraphScipRelationship",
    "GraphScipParseResult",
    # Graph sync adapter
    "GraphSyncAdapter",
    "SyncResult",
    # Vector sync
    "VectorSyncAdapter",
    "VectorSyncAdapterError",
    "VectorSyncResult",
    # Change detection
    "ChangeDetector",
    # Language detection
    "detect_languages",
    # Doc generation
    "generate_for_file",
    "generate_for_package",
    # Sync service
    "KnowledgeBaseSyncService",
    "SyncServiceError",
    "ValidationError",
]
