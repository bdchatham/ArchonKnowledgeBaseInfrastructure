"""Unit tests for the doc generator module.

Feature: scip-sync-pipeline

Source:
- src/sync/doc_generator.py
- .kiro/specs/scip-sync-pipeline/design.md (Doc Generator section)

Validates:
    Requirements 6.1, 6.2, 6.3, 6.4
"""

import re
import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.doc_generator import generate_for_file, generate_for_package
from src.sync.models import GeneratedDoc, ScipRelationship, ScipSymbol, SymbolLocation


def _make_symbol(
    name: str = "my_function",
    kind: str = "function",
    file: str = "src/main.py",
    line: int = 10,
    package: str = "MyPackage",
    workspace: str = "personal-work",
    signature: Optional[str] = "def my_function(x: int) -> str",
    documentation: Optional[str] = "Converts an integer to a string.",
    symbol_id: Optional[str] = None,
) -> ScipSymbol:
    sym_id = symbol_id or name
    arn = f"arn:archon:code:{workspace}/{package}/{file}#{sym_id}"
    return ScipSymbol(
        arn=arn,
        type="code",
        workspace=workspace,
        package=package,
        path=file,
        symbol=sym_id,
        kind=kind,
        name=name,
        signature=signature,
        documentation=documentation,
        location=SymbolLocation(file=file, line=line, column=0),
    )


class TestGenerateForFileSingleSymbol:
    """Tests for generating docs from a single symbol in a file.

    Validates: Requirements 6.1, 6.2
    """

    def test_produces_generated_doc(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert isinstance(result, GeneratedDoc)

    def test_source_path_matches_input(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert result.source_path == "src/main.py"

    def test_doc_path_ends_in_archon_md(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert result.doc_path.endswith(".archon.md")

    def test_doc_path_replaces_extension(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert result.doc_path == "src/main.archon.md"

    def test_content_contains_symbol_name(self):
        symbol = _make_symbol(name="calculate_total")
        result = generate_for_file("src/main.py", [symbol], [])

        assert "calculate_total" in result.content

    def test_content_contains_kind(self):
        symbol = _make_symbol(kind="function")
        result = generate_for_file("src/main.py", [symbol], [])

        assert "function" in result.content

    def test_content_contains_signature(self):
        symbol = _make_symbol(signature="def calc(x: int) -> int")
        result = generate_for_file("src/main.py", [symbol], [])

        assert "def calc(x: int) -> int" in result.content

    def test_content_contains_documentation(self):
        symbol = _make_symbol(documentation="Computes the total price.")
        result = generate_for_file("src/main.py", [symbol], [])

        assert "Computes the total price." in result.content

    def test_content_contains_file_location(self):
        symbol = _make_symbol(file="src/utils.py", line=42)
        result = generate_for_file("src/utils.py", [symbol], [])

        assert "src/utils.py" in result.content
        assert "42" in result.content

    def test_content_embeds_arn_in_html_comment(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert f"<!-- source-arn: {symbol.arn} -->" in result.content

    def test_arn_is_set(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert result.arn != ""


class TestGenerateForFileMultipleSymbols:
    """Tests for generating docs from multiple symbols in one file.

    Validates: Requirements 6.1, 6.3
    """

    def test_all_symbol_names_present(self):
        symbols = [
            _make_symbol(name="func_a", line=10),
            _make_symbol(name="func_b", line=20),
            _make_symbol(name="ClassC", kind="class", line=30),
        ]
        result = generate_for_file("src/main.py", symbols, [])

        assert "func_a" in result.content
        assert "func_b" in result.content
        assert "ClassC" in result.content

    def test_all_symbol_arns_embedded(self):
        symbols = [
            _make_symbol(name="func_a", symbol_id="func_a", line=10),
            _make_symbol(name="func_b", symbol_id="func_b", line=20),
        ]
        result = generate_for_file("src/main.py", symbols, [])

        for sym in symbols:
            assert f"<!-- source-arn: {sym.arn} -->" in result.content

    def test_produces_single_doc(self):
        symbols = [
            _make_symbol(name="func_a", line=10),
            _make_symbol(name="func_b", line=20),
        ]
        result = generate_for_file("src/main.py", symbols, [])

        assert isinstance(result, GeneratedDoc)


class TestGenerateForPackage:
    """Tests for package-level doc generation grouping by file.

    Validates: Requirements 6.3
    """

    def test_produces_one_doc_per_file(self):
        symbols = [
            _make_symbol(name="func_a", file="src/a.py", line=1),
            _make_symbol(name="func_b", file="src/b.py", line=1),
            _make_symbol(name="func_c", file="src/c.py", line=1),
        ]
        result = generate_for_package(symbols, [])

        assert len(result) == 3

    def test_groups_symbols_by_file(self):
        symbols = [
            _make_symbol(name="func_a", file="src/main.py", line=1),
            _make_symbol(name="func_b", file="src/main.py", line=10),
            _make_symbol(name="func_c", file="src/utils.py", line=1),
        ]
        result = generate_for_package(symbols, [])

        assert len(result) == 2
        source_paths = {doc.source_path for doc in result}
        assert source_paths == {"src/main.py", "src/utils.py"}

    def test_each_doc_path_ends_in_archon_md(self):
        symbols = [
            _make_symbol(name="func_a", file="src/a.py", line=1),
            _make_symbol(name="func_b", file="src/b.ts", line=1),
        ]
        result = generate_for_package(symbols, [])

        for doc in result:
            assert doc.doc_path.endswith(".archon.md")

    def test_empty_symbols_returns_empty_list(self):
        result = generate_for_package([], [])

        assert result == []

    def test_docs_are_sorted_by_file_path(self):
        symbols = [
            _make_symbol(name="func_c", file="src/z.py", line=1),
            _make_symbol(name="func_a", file="src/a.py", line=1),
            _make_symbol(name="func_b", file="src/m.py", line=1),
        ]
        result = generate_for_package(symbols, [])

        paths = [doc.source_path for doc in result]
        assert paths == sorted(paths)


class TestArnEmbedding:
    """Tests for ARN embedding in HTML comments.

    Validates: Requirements 6.2
    """

    def test_file_level_arn_embedded(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        file_arn = f"arn:archon:doc:{symbol.workspace}/{symbol.package}/{symbol.path}"
        assert f"<!-- source-arn: {file_arn} -->" in result.content

    def test_symbol_arn_embedded(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert f"<!-- source-arn: {symbol.arn} -->" in result.content

    def test_arn_extractable_via_regex(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        pattern = r"<!-- source-arn: (arn:archon:[^ ]+) -->"
        matches = re.findall(pattern, result.content)
        assert len(matches) >= 1


class TestMissingOptionalFields:
    """Tests for symbols with missing optional fields.

    Validates: Requirements 6.1
    """

    def test_no_signature(self):
        symbol = _make_symbol(signature=None)
        result = generate_for_file("src/main.py", [symbol], [])

        assert symbol.name in result.content
        assert "```" not in result.content

    def test_no_documentation(self):
        symbol = _make_symbol(documentation=None)
        result = generate_for_file("src/main.py", [symbol], [])

        assert symbol.name in result.content

    def test_no_signature_and_no_documentation(self):
        symbol = _make_symbol(signature=None, documentation=None)
        result = generate_for_file("src/main.py", [symbol], [])

        assert symbol.name in result.content
        assert result.content.strip() != ""


class TestRelationshipInclusion:
    """Tests for relationship inclusion in generated docs.

    Validates: Requirements 6.1
    """

    def test_outgoing_relationship_included(self):
        symbol = _make_symbol(name="caller")
        target_arn = "arn:archon:code:personal-work/MyPackage/src/utils.py#helper"
        rel = ScipRelationship(
            from_arn=symbol.arn,
            to_arn=target_arn,
            type="references",
        )
        result = generate_for_file("src/main.py", [symbol], [rel])

        assert "helper" in result.content
        assert target_arn in result.content
        assert target_arn in result.referenced_arns

    def test_incoming_relationship_not_included(self):
        symbol = _make_symbol(name="target")
        other_arn = "arn:archon:code:personal-work/MyPackage/src/other.py#other_func"
        rel = ScipRelationship(
            from_arn=other_arn,
            to_arn=symbol.arn,
            type="references",
        )
        result = generate_for_file("src/main.py", [symbol], [rel])

        assert other_arn not in result.referenced_arns

    def test_multiple_relationship_types(self):
        symbol = _make_symbol(name="MyClass", kind="class")
        ref_arn = "arn:archon:code:personal-work/MyPackage/src/base.py#BaseClass"
        imp_arn = "arn:archon:code:personal-work/MyPackage/src/iface.py#Interface"
        rels = [
            ScipRelationship(from_arn=symbol.arn, to_arn=ref_arn, type="extends"),
            ScipRelationship(from_arn=symbol.arn, to_arn=imp_arn, type="implements"),
        ]
        result = generate_for_file("src/main.py", [symbol], rels)

        assert ref_arn in result.referenced_arns
        assert imp_arn in result.referenced_arns


class TestMarkdownStructure:
    """Tests for markdown structure of generated docs.

    Validates: Requirements 6.4
    """

    def test_contains_markdown_heading(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        heading_lines = [l for l in result.content.split("\n") if l.startswith("#")]
        assert len(heading_lines) >= 1

    def test_content_is_non_empty(self):
        symbol = _make_symbol()
        result = generate_for_file("src/main.py", [symbol], [])

        assert len(result.content) > 0


# =============================================================================
# Property-Based Tests
# =============================================================================

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from src.sync.doc_generator import KIND_DESCRIPTIONS


VALID_KINDS = ["function", "class", "method", "variable", "type", "module"]


def _safe_text(min_size: int = 1, max_size: int = 30) -> st.SearchStrategy[str]:
    """Generate non-empty text strings safe for use in identifiers and paths."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="_",
        ),
        min_size=min_size,
        max_size=max_size,
    ).filter(lambda s: s[0].isalpha())


def _file_path_strategy() -> st.SearchStrategy[str]:
    """Generate valid file paths like 'src/module.py'."""
    directory = st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll",),
            whitelist_characters="_/",
        ),
        min_size=1,
        max_size=20,
    ).filter(lambda s: s[0].isalpha() and not s.endswith("/"))

    extension = st.sampled_from([".py", ".ts", ".go", ".rs", ".java", ".js"])

    filename = _safe_text(min_size=1, max_size=15)

    return st.builds(
        lambda d, f, ext: f"{d}/{f}{ext}",
        directory,
        filename,
        extension,
    )


@st.composite
def scip_symbol_strategy(draw: st.DrawFn) -> ScipSymbol:
    """Generate a valid ScipSymbol with constrained, realistic values.

    Produces symbols with:
    - Valid ARNs in format arn:archon:code:{workspace}/{package}/{path}#{symbol}
    - Valid kind values from the known set
    - Non-empty names starting with a letter
    - Valid file paths
    - Positive line numbers
    """
    workspace = draw(_safe_text(min_size=2, max_size=15))
    package = draw(_safe_text(min_size=2, max_size=15))
    file_path = draw(_file_path_strategy())
    name = draw(_safe_text(min_size=1, max_size=25))
    kind = draw(st.sampled_from(VALID_KINDS))
    line = draw(st.integers(min_value=1, max_value=10000))
    column = draw(st.integers(min_value=0, max_value=200))

    symbol_id = name
    arn = f"arn:archon:code:{workspace}/{package}/{file_path}#{symbol_id}"

    signature = draw(
        st.one_of(
            st.none(),
            st.builds(lambda n: f"def {n}(x: int) -> str", st.just(name)),
        )
    )
    documentation = draw(
        st.one_of(
            st.none(),
            st.text(min_size=5, max_size=100).filter(lambda s: s.strip()),
        )
    )

    location = SymbolLocation(file=file_path, line=line, column=column)

    return ScipSymbol(
        arn=arn,
        type="code",
        workspace=workspace,
        package=package,
        path=file_path,
        symbol=symbol_id,
        kind=kind,
        name=name,
        signature=signature,
        documentation=documentation,
        location=location,
    )


class TestDocContentCompletenessAndArnEmbedding:
    """Property-based tests for doc content completeness and ARN embedding.

    Feature: scip-sync-pipeline, Property 7: Doc content completeness and ARN embedding

    **Validates: Requirements 6.1, 6.2**
    """

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_content_contains_symbol_name(self, symbol: ScipSymbol) -> None:
        """For any ScipSymbol, the generated doc content contains the symbol name.

        **Validates: Requirements 6.1**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        assert symbol.name in result.content, (
            f"Symbol name '{symbol.name}' not found in generated content"
        )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_content_contains_kind_description(self, symbol: ScipSymbol) -> None:
        """For any ScipSymbol, the generated doc content contains a kind description.

        **Validates: Requirements 6.1**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        kind_label = KIND_DESCRIPTIONS.get(symbol.kind, symbol.kind)
        assert kind_label in result.content, (
            f"Kind description '{kind_label}' for kind '{symbol.kind}' "
            f"not found in generated content"
        )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_content_contains_file_location(self, symbol: ScipSymbol) -> None:
        """For any ScipSymbol, the generated doc content contains the file location.

        **Validates: Requirements 6.1**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        assert symbol.location.file in result.content, (
            f"File location '{symbol.location.file}' not found in generated content"
        )
        assert str(symbol.location.line) in result.content, (
            f"Line number '{symbol.location.line}' not found in generated content"
        )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_content_embeds_arn_in_html_comment(self, symbol: ScipSymbol) -> None:
        """For any ScipSymbol, the generated doc content contains the symbol ARN
        embedded in an HTML comment matching <!-- source-arn: ... -->.

        **Validates: Requirements 6.2**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        expected_comment = f"<!-- source-arn: {symbol.arn} -->"
        assert expected_comment in result.content, (
            f"ARN HTML comment '{expected_comment}' not found in generated content"
        )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_all_arn_comments_match_pattern(self, symbol: ScipSymbol) -> None:
        """For any ScipSymbol, all ARN HTML comments in the generated content
        match the pattern <!-- source-arn: arn:archon:... -->.

        **Validates: Requirements 6.2**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        arn_pattern = re.compile(r"<!-- source-arn: (arn:archon:\S+) -->")
        matches = arn_pattern.findall(result.content)

        assert len(matches) >= 1, (
            "Expected at least one ARN HTML comment in generated content"
        )

        for matched_arn in matches:
            assert matched_arn.startswith("arn:archon:"), (
                f"ARN '{matched_arn}' does not start with 'arn:archon:'"
            )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_combined_completeness(self, symbol: ScipSymbol) -> None:
        """For any ScipSymbol with a name, kind, and location, the generated
        GeneratedDoc content contains the symbol name, a kind description,
        the file location, and the symbol's ARN embedded in an HTML comment.

        This is the combined property as stated in the design document.

        **Validates: Requirements 6.1, 6.2**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        assert symbol.name in result.content, (
            f"Symbol name '{symbol.name}' missing from content"
        )

        kind_label = KIND_DESCRIPTIONS.get(symbol.kind, symbol.kind)
        assert kind_label in result.content, (
            f"Kind description '{kind_label}' missing from content"
        )

        assert symbol.location.file in result.content, (
            f"File location '{symbol.location.file}' missing from content"
        )

        expected_comment = f"<!-- source-arn: {symbol.arn} -->"
        assert expected_comment in result.content, (
            f"ARN comment '{expected_comment}' missing from content"
        )


@st.composite
def multi_file_symbols_strategy(draw: st.DrawFn) -> list[ScipSymbol]:
    """Generate a list of ScipSymbol instances spanning multiple distinct files.

    Strategy:
    1. Draw N distinct file paths (1 to 5)
    2. For each file, draw 1-3 symbols assigned to that file path
    3. Return the flat list of all symbols

    This ensures the generated list always has a known number of distinct
    source files, enabling precise assertions about grouping behavior.
    """
    workspace = draw(_safe_text(min_size=2, max_size=15))
    package = draw(_safe_text(min_size=2, max_size=15))

    num_files = draw(st.integers(min_value=1, max_value=5))
    file_paths = draw(
        st.lists(
            _file_path_strategy(),
            min_size=num_files,
            max_size=num_files,
            unique=True,
        )
    )

    all_symbols: list[ScipSymbol] = []
    for file_path in file_paths:
        num_symbols = draw(st.integers(min_value=1, max_value=3))
        for _ in range(num_symbols):
            name = draw(_safe_text(min_size=1, max_size=25))
            kind = draw(st.sampled_from(VALID_KINDS))
            line = draw(st.integers(min_value=1, max_value=10000))
            column = draw(st.integers(min_value=0, max_value=200))

            symbol_id = name
            arn = f"arn:archon:code:{workspace}/{package}/{file_path}#{symbol_id}"

            signature = draw(
                st.one_of(
                    st.none(),
                    st.builds(lambda n: f"def {n}(x: int) -> str", st.just(name)),
                )
            )
            documentation = draw(
                st.one_of(
                    st.none(),
                    st.text(min_size=5, max_size=100).filter(lambda s: s.strip()),
                )
            )

            location = SymbolLocation(file=file_path, line=line, column=column)

            all_symbols.append(
                ScipSymbol(
                    arn=arn,
                    type="code",
                    workspace=workspace,
                    package=package,
                    path=file_path,
                    symbol=symbol_id,
                    kind=kind,
                    name=name,
                    signature=signature,
                    documentation=documentation,
                    location=location,
                )
            )

    return all_symbols


class TestDocGenerationGroupsBySourceFile:
    """Property-based tests for file grouping in doc generation.

    Feature: scip-sync-pipeline, Property 8: Doc generation groups symbols by source file

    **Validates: Requirements 6.3**
    """

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_doc_count_equals_distinct_file_count(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any list of ScipSymbol instances spanning N distinct source files,
        generate_for_package produces exactly N GeneratedDoc instances.

        **Validates: Requirements 6.3**
        """
        distinct_files = {s.location.file for s in symbols}
        result = generate_for_package(symbols, [])

        assert len(result) == len(distinct_files), (
            f"Expected {len(distinct_files)} docs for {len(distinct_files)} "
            f"distinct files, got {len(result)}"
        )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_each_doc_source_path_is_a_distinct_file(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any list of ScipSymbol instances, each GeneratedDoc's source_path
        corresponds to exactly one of the distinct file paths in the input.

        **Validates: Requirements 6.3**
        """
        distinct_files = {s.location.file for s in symbols}
        result = generate_for_package(symbols, [])

        for doc in result:
            assert doc.source_path in distinct_files, (
                f"Doc source_path '{doc.source_path}' is not one of the "
                f"input file paths: {distinct_files}"
            )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_output_source_paths_equal_input_file_paths(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any list of ScipSymbol instances, the set of source_paths in the
        output equals the set of distinct file paths in the input.

        **Validates: Requirements 6.3**
        """
        distinct_files = {s.location.file for s in symbols}
        result = generate_for_package(symbols, [])
        output_paths = {doc.source_path for doc in result}

        assert output_paths == distinct_files, (
            f"Output source_paths {output_paths} do not match "
            f"input file paths {distinct_files}"
        )


class TestGeneratedDocMarkdownStructure:
    """Property-based tests for markdown structure of generated docs.

    Feature: scip-sync-pipeline, Property 9: Generated doc content has markdown structure

    **Validates: Requirements 6.4**
    """

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_single_symbol_has_markdown_heading(
        self, symbol: ScipSymbol
    ) -> None:
        """For any single ScipSymbol, generate_for_file produces content
        with at least one markdown heading (line starting with '#').

        **Validates: Requirements 6.4**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        heading_lines = [
            line for line in result.content.split("\n") if line.startswith("#")
        ]
        assert len(heading_lines) >= 1, (
            "Generated content must contain at least one markdown heading "
            f"(line starting with '#'), but found none in:\n{result.content[:200]}"
        )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_single_symbol_has_non_empty_content(
        self, symbol: ScipSymbol
    ) -> None:
        """For any single ScipSymbol, generate_for_file produces content
        with length greater than zero.

        **Validates: Requirements 6.4**
        """
        result = generate_for_file(symbol.location.file, [symbol], [])

        assert len(result.content) > 0, (
            "Generated content length must be greater than zero"
        )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_package_docs_each_have_heading_and_content(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any list of symbols, each doc from generate_for_package has
        at least one markdown heading and non-empty content.

        **Validates: Requirements 6.4**
        """
        docs = generate_for_package(symbols, [])

        for doc in docs:
            assert len(doc.content) > 0, (
                f"Doc for '{doc.source_path}' has empty content"
            )

            heading_lines = [
                line for line in doc.content.split("\n") if line.startswith("#")
            ]
            assert len(heading_lines) >= 1, (
                f"Doc for '{doc.source_path}' must contain at least one "
                f"markdown heading, but found none"
            )


class TestArnRoundTripThroughDocGeneration:
    """Property-based tests for ARN round-trip through doc generation.

    Feature: scip-sync-pipeline, Property 10: ARN round-trip through doc generation

    For all valid ScipParseResult inputs with at least one symbol, generating
    GeneratedDoc instances and then extracting ARNs from the generated content
    (via <!-- source-arn: ... --> comment parsing) SHALL produce a set of ARNs
    that contains every symbol ARN from the original input.

    **Validates: Requirements 6.5**
    """

    ARN_PATTERN = re.compile(r"<!-- source-arn: (arn:archon:\S+) -->")

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_package_round_trip_preserves_all_symbol_arns(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any list of ScipSymbol instances (at least 1), generate docs via
        generate_for_package, extract all ARNs from the generated content using
        the regex pattern, and verify that every symbol ARN from the input is
        present in the extracted set.

        **Validates: Requirements 6.5**
        """
        input_arns = {s.arn for s in symbols}
        docs = generate_for_package(symbols, [])

        extracted_arns: set[str] = set()
        for doc in docs:
            matches = self.ARN_PATTERN.findall(doc.content)
            extracted_arns.update(matches)

        missing = input_arns - extracted_arns
        assert not missing, (
            f"ARN round-trip failed: {len(missing)} symbol ARN(s) missing from "
            f"generated content.\nMissing: {missing}\n"
            f"Extracted: {extracted_arns}"
        )

    @given(symbol=scip_symbol_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_single_file_round_trip_preserves_symbol_arn(
        self, symbol: ScipSymbol
    ) -> None:
        """For a single symbol, generate doc via generate_for_file, extract ARNs,
        and verify the symbol's ARN is in the extracted set.

        **Validates: Requirements 6.5**
        """
        doc = generate_for_file(symbol.location.file, [symbol], [])

        extracted_arns = set(self.ARN_PATTERN.findall(doc.content))

        assert symbol.arn in extracted_arns, (
            f"ARN round-trip failed: symbol ARN '{symbol.arn}' not found in "
            f"generated content.\nExtracted ARNs: {extracted_arns}"
        )


class TestGeneratedDocCompleteness:
    """Property-based tests for GeneratedDoc completeness.

    Feature: scip-sync-pipeline, Property 6: Doc generation produces complete GeneratedDoc instances

    For any non-empty list of ScipSymbol instances, the doc generator SHALL
    produce GeneratedDoc instances where each doc has a non-empty ARN,
    non-empty content, a valid source_path, and a doc_path ending in `.archon.md`.

    **Validates: Requirements 5.3**
    """

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_every_doc_has_non_empty_arn(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any non-empty list of symbols, every GeneratedDoc from
        generate_for_package has a non-empty arn.

        **Validates: Requirements 5.3**
        """
        docs = generate_for_package(symbols, [])

        assert len(docs) > 0, "Expected at least one doc for non-empty symbol list"
        for doc in docs:
            assert doc.arn, (
                f"GeneratedDoc for '{doc.source_path}' has empty or None arn"
            )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_every_doc_has_non_empty_content(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any non-empty list of symbols, every GeneratedDoc from
        generate_for_package has non-empty content.

        **Validates: Requirements 5.3**
        """
        docs = generate_for_package(symbols, [])

        assert len(docs) > 0, "Expected at least one doc for non-empty symbol list"
        for doc in docs:
            assert doc.content, (
                f"GeneratedDoc for '{doc.source_path}' has empty or None content"
            )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_every_doc_has_non_empty_source_path(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any non-empty list of symbols, every GeneratedDoc from
        generate_for_package has a non-empty source_path.

        **Validates: Requirements 5.3**
        """
        docs = generate_for_package(symbols, [])

        assert len(docs) > 0, "Expected at least one doc for non-empty symbol list"
        for doc in docs:
            assert doc.source_path, (
                f"GeneratedDoc has empty or None source_path (arn: {doc.arn})"
            )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_every_doc_path_ends_in_archon_md(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any non-empty list of symbols, every GeneratedDoc from
        generate_for_package has a doc_path ending in '.archon.md'.

        **Validates: Requirements 5.3**
        """
        docs = generate_for_package(symbols, [])

        assert len(docs) > 0, "Expected at least one doc for non-empty symbol list"
        for doc in docs:
            assert doc.doc_path.endswith(".archon.md"), (
                f"GeneratedDoc for '{doc.source_path}' has doc_path "
                f"'{doc.doc_path}' that does not end in '.archon.md'"
            )

    @given(symbols=multi_file_symbols_strategy())
    @settings(max_examples=100, deadline=None)
    def test_property_combined_completeness(
        self, symbols: list[ScipSymbol]
    ) -> None:
        """For any non-empty list of ScipSymbol instances, all four completeness
        properties hold simultaneously for every GeneratedDoc produced by
        generate_for_package: non-empty arn, non-empty content, non-empty
        source_path, and doc_path ending in '.archon.md'.

        **Validates: Requirements 5.3**
        """
        docs = generate_for_package(symbols, [])

        assert len(docs) > 0, "Expected at least one doc for non-empty symbol list"
        for doc in docs:
            assert doc.arn, (
                f"GeneratedDoc for '{doc.source_path}' has empty arn"
            )
            assert doc.content, (
                f"GeneratedDoc for '{doc.source_path}' has empty content"
            )
            assert doc.source_path, (
                f"GeneratedDoc has empty source_path (arn: {doc.arn})"
            )
            assert doc.doc_path.endswith(".archon.md"), (
                f"GeneratedDoc for '{doc.source_path}' has doc_path "
                f"'{doc.doc_path}' not ending in '.archon.md'"
            )
