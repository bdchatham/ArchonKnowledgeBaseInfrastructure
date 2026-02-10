"""SCIP index parser for extracting symbols and relationships.

Parses binary SCIP protobuf index files into ScipSymbol and ScipRelationship
instances. Handles kind mapping, ARN construction, and definition location
extraction from occurrences.

Source:
- .kiro/specs/scip-sync-pipeline/design.md (Sync Service _load_package_data section)
- .kiro/specs/scip-sync-pipeline/requirements.md (Requirements 5.1, 5.2)
"""

import hashlib
import logging
from pathlib import Path
from typing import Optional

from google.protobuf.message import DecodeError

from src.sync.models import (
    ScipParseResult,
    ScipRelationship,
    ScipSymbol,
    SymbolLocation,
)
from src.sync.proto import scip_pb2

logger = logging.getLogger(__name__)

DEFINITION_ROLE = 0x1

KIND_MAP: dict[int, str] = {
    scip_pb2.Function: "function",
    scip_pb2.Class: "class",
    scip_pb2.Method: "method",
    scip_pb2.Variable: "variable",
    scip_pb2.Type: "type",
    scip_pb2.Module: "module",
    scip_pb2.Interface: "type",
}

RELATIONSHIP_TYPE_MAP: list[tuple[str, str]] = [
    ("is_implementation", "implements"),
    ("is_type_definition", "extends"),
    ("is_definition", "contains"),
    ("is_reference", "references"),
]


def compute_index_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _extract_workspace(workspace_path: str) -> str:
    parts = Path(workspace_path).parts
    return parts[-1] if parts else "default"


def _map_kind(proto_kind: int) -> str:
    return KIND_MAP.get(proto_kind, "variable")


def _find_definition_location(
    symbol_name: str,
    occurrences: list,
    relative_path: str,
) -> SymbolLocation:
    """Find the definition occurrence for a symbol and return its location."""
    for occ in occurrences:
        if occ.symbol == symbol_name and (occ.symbol_roles & DEFINITION_ROLE):
            return _occurrence_to_location(occ, relative_path)
    return SymbolLocation(file=relative_path, line=1, column=0)


def _occurrence_to_location(occ, relative_path: str) -> SymbolLocation:
    """Convert a protobuf Occurrence range to a SymbolLocation."""
    range_values = list(occ.range)
    if len(range_values) >= 3:
        return SymbolLocation(
            file=relative_path,
            line=range_values[0] + 1,
            column=range_values[1],
        )
    return SymbolLocation(file=relative_path, line=1, column=0)


def _build_arn(workspace: str, package: str, path: str, symbol_name: str) -> str:
    return f"arn:archon:code:{workspace}/{package}/{path}#{symbol_name}"


def _extract_signature(sym_info) -> Optional[str]:
    if sym_info.HasField("signature_documentation"):
        sig_text = sym_info.signature_documentation.text
        if sig_text:
            return sig_text
    return None


def _extract_documentation(sym_info) -> Optional[str]:
    if sym_info.documentation:
        return "\n".join(sym_info.documentation)
    return None


def _symbol_display_name(sym_info) -> str:
    if sym_info.display_name:
        return sym_info.display_name
    raw = sym_info.symbol
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    cleaned = raw.rstrip(".()`#")
    if "." in cleaned:
        cleaned = cleaned.rsplit(".", 1)[-1]
    return cleaned or raw.strip(".()`#") or raw


def _determine_relationship_type(proto_rel) -> str:
    for attr, rel_type in RELATIONSHIP_TYPE_MAP:
        if getattr(proto_rel, attr, False):
            return rel_type
    return "references"


def parse_document_symbols(
    document,
    workspace: str,
    package: str,
) -> tuple[list[ScipSymbol], list[ScipRelationship]]:
    """Extract symbols and relationships from a single SCIP Document."""
    symbols: list[ScipSymbol] = []
    relationships: list[ScipRelationship] = []
    relative_path = document.relative_path

    for sym_info in document.symbols:
        name = _symbol_display_name(sym_info)
        arn = _build_arn(workspace, package, relative_path, name)
        location = _find_definition_location(
            sym_info.symbol, document.occurrences, relative_path
        )

        symbols.append(
            ScipSymbol(
                arn=arn,
                type="code",
                workspace=workspace,
                package=package,
                path=relative_path,
                symbol=name,
                kind=_map_kind(sym_info.kind),
                name=name,
                signature=_extract_signature(sym_info),
                documentation=_extract_documentation(sym_info),
                location=location,
            )
        )

        for proto_rel in sym_info.relationships:
            target_name = _symbol_display_name_from_raw(proto_rel.symbol)
            target_arn = _build_arn(workspace, package, relative_path, target_name)
            relationships.append(
                ScipRelationship(
                    from_arn=arn,
                    to_arn=target_arn,
                    type=_determine_relationship_type(proto_rel),
                )
            )

    return symbols, relationships


def _symbol_display_name_from_raw(raw_symbol: str) -> str:
    if "/" in raw_symbol:
        raw_symbol = raw_symbol.rsplit("/", 1)[-1]
    cleaned = raw_symbol.rstrip(".()`#")
    if "." in cleaned:
        cleaned = cleaned.rsplit(".", 1)[-1]
    return cleaned or raw_symbol.strip(".()`#") or raw_symbol


def parse_scip_index(
    content: bytes,
    workspace: str,
    package: str,
) -> ScipParseResult:
    """Parse a binary SCIP index into a ScipParseResult.

    Args:
        content: Raw bytes of the SCIP index protobuf.
        workspace: Workspace name for ARN construction.
        package: Package name for ARN construction.

    Returns:
        ScipParseResult with symbols, relationships, and content hash.

    Raises:
        DecodeError: If the protobuf content is corrupted.
    """
    index = scip_pb2.Index()
    index.ParseFromString(content)

    all_symbols: list[ScipSymbol] = []
    all_relationships: list[ScipRelationship] = []

    for document in index.documents:
        doc_symbols, doc_relationships = parse_document_symbols(
            document, workspace, package
        )
        all_symbols.extend(doc_symbols)
        all_relationships.extend(doc_relationships)

    return ScipParseResult(
        symbols=all_symbols,
        relationships=all_relationships,
        hash=compute_index_hash(content),
    )
