"""Property-based tests for SCIP index parsing.

Feature: scip-sync-pipeline, Property 5: SCIP index parsing produces valid ScipParseResult

For any valid SCIP index binary file, parsing SHALL produce a ScipParseResult
with a non-empty list of symbols (each having a valid ARN, name, kind, and
location), a list of relationships (each having valid from_arn, to_arn, and
type), and a non-empty SHA-256 content hash.

**Validates: Requirements 5.1, 5.2**

Source:
- src/sync/scip_parser.py
- src/sync/proto/scip_pb2.py
- .kiro/specs/scip-sync-pipeline/design.md (Property 5)
"""

import re
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.proto import scip_pb2
from src.sync.scip_parser import compute_index_hash, parse_scip_index

VALID_KINDS = {"function", "class", "method", "variable", "type", "module"}
VALID_RELATIONSHIP_TYPES = {"contains", "references", "implements", "extends"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

PROTO_KINDS = [
    scip_pb2.Function,
    scip_pb2.Class,
    scip_pb2.Method,
    scip_pb2.Variable,
    scip_pb2.Type,
    scip_pb2.Module,
    scip_pb2.Interface,
]

PROTO_RELATIONSHIP_FLAGS = [
    "is_implementation",
    "is_type_definition",
    "is_definition",
    "is_reference",
]


def _safe_identifier(min_size: int = 2, max_size: int = 20) -> st.SearchStrategy[str]:
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu"),
            whitelist_characters="_",
        ),
        min_size=min_size,
        max_size=max_size,
    ).filter(lambda s: s[0].isalpha())


def _file_path() -> st.SearchStrategy[str]:
    directory = _safe_identifier(min_size=2, max_size=10)
    filename = _safe_identifier(min_size=2, max_size=12)
    extension = st.sampled_from([".py", ".ts", ".go", ".rs", ".java"])
    return st.builds(lambda d, f, ext: f"{d}/{f}{ext}", directory, filename, extension)


@st.composite
def scip_symbol_info_strategy(draw: st.DrawFn):
    """Build a protobuf SymbolInformation with valid fields."""
    name = draw(_safe_identifier(min_size=2, max_size=15))
    kind = draw(st.sampled_from(PROTO_KINDS))

    sym_info = scip_pb2.SymbolInformation()
    sym_info.symbol = f"local {name}"
    sym_info.display_name = name
    sym_info.kind = kind

    include_docs = draw(st.booleans())
    if include_docs:
        sym_info.documentation.append(f"Documentation for {name}.")

    include_sig = draw(st.booleans())
    if include_sig:
        sig_doc = scip_pb2.SignatureDocumentation()
        sig_doc.text = f"def {name}(x: int) -> str"
        sym_info.signature_documentation.CopyFrom(sig_doc)

    num_rels = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_rels):
        rel = sym_info.relationships.add()
        target_name = draw(_safe_identifier(min_size=2, max_size=15))
        rel.symbol = f"local {target_name}"
        flag = draw(st.sampled_from(PROTO_RELATIONSHIP_FLAGS))
        setattr(rel, flag, True)

    return sym_info, name


@st.composite
def scip_document_strategy(draw: st.DrawFn):
    """Build a protobuf Document with symbols and definition occurrences."""
    relative_path = draw(_file_path())
    num_symbols = draw(st.integers(min_value=1, max_value=3))

    doc = scip_pb2.Document()
    doc.relative_path = relative_path

    symbol_names = []
    for _ in range(num_symbols):
        sym_info, name = draw(scip_symbol_info_strategy())
        doc.symbols.append(sym_info)
        symbol_names.append(name)

        line = draw(st.integers(min_value=0, max_value=500))
        col = draw(st.integers(min_value=0, max_value=80))

        occ = doc.occurrences.add()
        occ.symbol = sym_info.symbol
        occ.symbol_roles = 0x1
        occ.range.extend([line, col, line, col + len(name)])

    return doc, symbol_names


@st.composite
def scip_index_strategy(draw: st.DrawFn):
    """Build a complete SCIP Index protobuf with 1-5 documents.

    Returns (serialized_bytes, total_symbol_count, has_relationships).
    """
    num_docs = draw(st.integers(min_value=1, max_value=5))

    index = scip_pb2.Index()

    total_symbols = 0
    has_relationships = False

    for _ in range(num_docs):
        doc, names = draw(scip_document_strategy())
        index.documents.append(doc)
        total_symbols += len(names)
        for sym_info in doc.symbols:
            if len(sym_info.relationships) > 0:
                has_relationships = True

    content = index.SerializeToString()
    return content, total_symbols, has_relationships


class TestScipIndexParsingProducesValidResult:
    """Property-based tests for SCIP index parsing.

    Feature: scip-sync-pipeline, Property 5: SCIP index parsing produces valid ScipParseResult

    **Validates: Requirements 5.1, 5.2**
    """

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_non_empty_symbols_list(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """For any valid SCIP index with at least one symbol, the result has
        a non-empty symbols list.

        **Validates: Requirements 5.1**
        """
        content, total_symbols, _ = data
        result = parse_scip_index(content, workspace, package)

        assert len(result.symbols) > 0, (
            f"Expected non-empty symbols list for index with {total_symbols} symbols"
        )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_every_symbol_has_valid_arn(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """Every symbol has a non-empty ARN starting with 'arn:archon:code:'.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        for symbol in result.symbols:
            assert symbol.arn, f"Symbol '{symbol.name}' has empty ARN"
            assert symbol.arn.startswith("arn:archon:code:"), (
                f"Symbol ARN '{symbol.arn}' does not start with 'arn:archon:code:'"
            )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_every_symbol_has_non_empty_name(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """Every symbol has a non-empty name.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        for symbol in result.symbols:
            assert symbol.name, (
                f"Symbol with ARN '{symbol.arn}' has empty name"
            )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_every_symbol_has_valid_kind(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """Every symbol has a valid kind from the known set.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        for symbol in result.symbols:
            assert symbol.kind in VALID_KINDS, (
                f"Symbol '{symbol.name}' has invalid kind '{symbol.kind}'. "
                f"Expected one of: {VALID_KINDS}"
            )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_every_symbol_has_valid_location(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """Every symbol has a valid location with a file path and positive line number.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        for symbol in result.symbols:
            assert symbol.location is not None, (
                f"Symbol '{symbol.name}' has no location"
            )
            assert symbol.location.file, (
                f"Symbol '{symbol.name}' has empty file path in location"
            )
            assert symbol.location.line >= 1, (
                f"Symbol '{symbol.name}' has non-positive line number "
                f"{symbol.location.line}"
            )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_every_relationship_has_valid_arns(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """Every relationship has valid from_arn and to_arn starting with
        'arn:archon:code:'.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        for rel in result.relationships:
            assert rel.from_arn, "Relationship has empty from_arn"
            assert rel.from_arn.startswith("arn:archon:code:"), (
                f"Relationship from_arn '{rel.from_arn}' does not start with "
                f"'arn:archon:code:'"
            )
            assert rel.to_arn, "Relationship has empty to_arn"
            assert rel.to_arn.startswith("arn:archon:code:"), (
                f"Relationship to_arn '{rel.to_arn}' does not start with "
                f"'arn:archon:code:'"
            )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_every_relationship_has_valid_type(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """Every relationship has a valid type from the known set.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        for rel in result.relationships:
            assert rel.type in VALID_RELATIONSHIP_TYPES, (
                f"Relationship has invalid type '{rel.type}'. "
                f"Expected one of: {VALID_RELATIONSHIP_TYPES}"
            )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_hash_is_valid_sha256(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """The hash is a non-empty string of 64 hex characters (SHA-256).

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)

        assert result.hash, "ScipParseResult hash is empty"
        assert SHA256_PATTERN.match(result.hash), (
            f"Hash '{result.hash}' is not a valid 64-character hex SHA-256 string"
        )

    @given(data=scip_index_strategy(), workspace=_safe_identifier(), package=_safe_identifier())
    @settings(max_examples=100, deadline=None)
    def test_property_hash_matches_compute_index_hash(
        self, data: tuple, workspace: str, package: str
    ) -> None:
        """The hash matches compute_index_hash(content) for the same content.

        **Validates: Requirements 5.2**
        """
        content, _, _ = data
        result = parse_scip_index(content, workspace, package)
        expected_hash = compute_index_hash(content)

        assert result.hash == expected_hash, (
            f"Hash mismatch: result.hash='{result.hash}' != "
            f"compute_index_hash='{expected_hash}'"
        )
