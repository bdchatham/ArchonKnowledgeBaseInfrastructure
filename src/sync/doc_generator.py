"""Documentation generator for SCIP symbol data.

Generates structured markdown documentation from SCIP-indexed symbols.
Produces one GeneratedDoc per source file, with embedded ARN metadata
for linking back to the Code Graph. Targets 400-800 tokens per section
for optimal RAG chunking.

Source:
- .kiro/specs/scip-sync-pipeline/design.md (Doc Generator section)
- .kiro/specs/scip-sync-pipeline/requirements.md (Requirements 6.1-6.4)
- ArchonDocumentationMCPTools/src/lib/doc_generator.ts (reference implementation)
"""

from collections import defaultdict

from src.sync.models import GeneratedDoc, ScipRelationship, ScipSymbol


KIND_DESCRIPTIONS: dict[str, str] = {
    "function": "function",
    "class": "class",
    "method": "method",
    "variable": "variable",
    "type": "type definition",
    "module": "module",
}

KIND_PLURAL: dict[str, str] = {
    "function": "Functions",
    "class": "Classes",
    "method": "Methods",
    "variable": "Variables",
    "type": "Types",
    "module": "Modules",
}

RELATIONSHIP_LABELS: dict[str, str] = {
    "contains": "Contains",
    "references": "References",
    "implements": "Implements",
    "extends": "Extends",
    "imports": "Imports",
}


def _doc_path_for(source_path: str) -> str:
    last_dot = source_path.rfind(".")
    if last_dot > 0:
        return f"{source_path[:last_dot]}.archon.md"
    return f"{source_path}.archon.md"


def _file_arn(symbol: ScipSymbol) -> str:
    return f"arn:archon:doc:{symbol.workspace}/{symbol.package}/{symbol.path}"


def _extract_symbol_name_from_arn(arn: str) -> str:
    hash_index = arn.rfind("#")
    if hash_index >= 0 and hash_index < len(arn) - 1:
        return arn[hash_index + 1 :]
    slash_index = arn.rfind("/")
    if slash_index >= 0 and slash_index < len(arn) - 1:
        segment = arn[slash_index + 1 :]
        dot_index = segment.rfind(".")
        if dot_index > 0:
            return segment[:dot_index]
        return segment
    return arn


def _build_symbol_section(
    symbol: ScipSymbol,
    relationships: list[ScipRelationship],
) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    referenced_arns: list[str] = []

    lines.append(f"### {symbol.name}")
    lines.append("")
    lines.append(f"<!-- source-arn: {symbol.arn} -->")
    lines.append("")

    kind_label = KIND_DESCRIPTIONS.get(symbol.kind, symbol.kind)
    lines.append(
        f"**{kind_label}** defined at `{symbol.location.file}` line {symbol.location.line}"
    )
    lines.append("")

    if symbol.documentation:
        lines.append(symbol.documentation)
        lines.append("")

    if symbol.signature:
        lines.append("```")
        lines.append(symbol.signature)
        lines.append("```")
        lines.append("")

    outgoing = [r for r in relationships if r.from_arn == symbol.arn]
    grouped: dict[str, list[str]] = defaultdict(list)
    for rel in outgoing:
        grouped[rel.type].append(rel.to_arn)

    if grouped:
        lines.append("**Related:**")
        lines.append("")
        for rel_type, arns in grouped.items():
            label = RELATIONSHIP_LABELS.get(rel_type, rel_type)
            for arn in arns:
                name = _extract_symbol_name_from_arn(arn)
                lines.append(f"- {label}: [{name}]({arn})")
                referenced_arns.append(arn)
        lines.append("")

    return lines, referenced_arns


def generate_for_file(
    file_path: str,
    symbols: list[ScipSymbol],
    relationships: list[ScipRelationship],
) -> GeneratedDoc:
    """Generate documentation for all symbols in a single source file.

    Produces a structured markdown document with one section per symbol,
    embedding each symbol's ARN in an HTML comment for Code Graph linking.

    Args:
        file_path: Source file path relative to the package root.
        symbols: Symbols located in this file.
        relationships: All relationships that may involve these symbols.

    Returns:
        A GeneratedDoc with aggregated content for the file.
    """
    if not symbols:
        doc_arn = f"arn:archon:doc:unknown/unknown/{file_path}"
        return GeneratedDoc(
            source_path=file_path,
            doc_path=_doc_path_for(file_path),
            content=f"# {file_path.split('/')[-1]}\n\n<!-- source-arn: {doc_arn} -->\n\nNo symbols found.\n",
            arn=doc_arn,
            referenced_arns=[],
        )

    first_symbol = symbols[0]
    doc_arn = _file_arn(first_symbol)
    doc_path = _doc_path_for(file_path)
    referenced_arns: list[str] = []

    file_name = file_path.split("/")[-1]
    lines: list[str] = []

    lines.append(f"# {file_name}")
    lines.append("")
    lines.append("<!-- archon:generated -->")
    lines.append(f"<!-- source-arn: {doc_arn} -->")
    lines.append("")

    lines.append("## Overview")
    lines.append("")
    lines.append(f"Source file: `{file_path}`")
    lines.append("")

    grouped_by_kind: dict[str, list[ScipSymbol]] = defaultdict(list)
    for sym in symbols:
        grouped_by_kind[sym.kind].append(sym)

    contents_parts = []
    for kind, kind_symbols in grouped_by_kind.items():
        plural = KIND_PLURAL.get(kind, f"{kind}s")
        contents_parts.append(f"{len(kind_symbols)} {plural.lower()}")
    if contents_parts:
        lines.append(f"**Contents:** {', '.join(contents_parts)}")
        lines.append("")

    lines.append("## Symbols")
    lines.append("")

    for symbol in symbols:
        section_lines, section_arns = _build_symbol_section(symbol, relationships)
        lines.extend(section_lines)
        referenced_arns.extend(section_arns)

    lines.append("---")
    lines.append("")
    lines.append("**Source**")
    lines.append(f"- `{file_path}`")
    lines.append("")

    content = "\n".join(lines)

    return GeneratedDoc(
        source_path=file_path,
        doc_path=doc_path,
        content=content,
        arn=doc_arn,
        referenced_arns=referenced_arns,
    )


def generate_for_package(
    symbols: list[ScipSymbol],
    relationships: list[ScipRelationship],
) -> list[GeneratedDoc]:
    """Generate documentation for a package, one doc per source file.

    Groups symbols by their source file path and produces a GeneratedDoc
    for each unique file.

    Args:
        symbols: All symbols in the package.
        relationships: All relationships between symbols in the package.

    Returns:
        List of GeneratedDoc instances, one per unique source file.
    """
    if not symbols:
        return []

    symbols_by_file: dict[str, list[ScipSymbol]] = defaultdict(list)
    for symbol in symbols:
        symbols_by_file[symbol.location.file].append(symbol)

    docs: list[GeneratedDoc] = []
    for file_path in sorted(symbols_by_file.keys()):
        file_symbols = symbols_by_file[file_path]
        doc = generate_for_file(file_path, file_symbols, relationships)
        docs.append(doc)

    return docs
