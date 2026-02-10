"""ARN validation utilities for the Vector Store.

This module provides utilities for validating Archon Resource Names (ARNs)
used in the Vector Store for graph traversal and cross-referencing.

ARN Format: arn:archon:<type>:<workspace>/<package>/<path>#<symbol>

Components:
- type: Resource type (code, doc, k8s, infra)
- workspace: Workspace identifier (e.g., personal-work)
- package: Package/repository name (e.g., ArchonDocumentationMCPTools)
- path: File path relative to package (e.g., src/tools/scip_indexing.ts)
- symbol: Symbol name (optional, e.g., generateScipIndex)

Examples of valid ARNs:
- arn:archon:doc:personal-work/ArchonDocumentationMCPTools/src/tools/scip_indexing.archon.md
- arn:archon:code:personal-work/ArchonAgent/src/orchestrator/main.py#Orchestrator
- arn:archon:code:personal-work/AphexCLI/cmd/root.go#Execute

Source:
- .kiro/specs/vector-store-arn/design.md
- .kiro/specs/vector-store-arn/requirements.md

Validates:
    Requirements 4.1, 4.2
"""

import re
from dataclasses import dataclass
from typing import Optional


VALID_ARN_TYPES = frozenset({"code", "doc", "k8s", "infra"})

ARN_PATTERN = re.compile(
    r"^arn:archon:(code|doc|k8s|infra):([^/]+)/([^/]+)/([^#]+)(#(.+))?$"
)


@dataclass(frozen=True)
class ARNComponents:
    """Parsed components of an Archon Resource Name.

    Attributes:
        type: Resource type (code, doc, k8s, infra)
        workspace: Workspace identifier
        package: Package/repository name
        path: File path relative to package
        symbol: Symbol name (optional, None if not present)
    """

    type: str
    workspace: str
    package: str
    path: str
    symbol: Optional[str] = None


def validate_arn(arn: str) -> bool:
    """Validate ARN format.

    Checks if the given string conforms to the ARN format:
    arn:archon:<type>:<workspace>/<package>/<path>#<symbol>

    The symbol component is optional. All other components are required.

    Args:
        arn: The ARN string to validate

    Returns:
        True if the ARN is valid, False otherwise

    Examples:
        >>> validate_arn("arn:archon:doc:personal-work/MyPackage/src/file.md")
        True
        >>> validate_arn("arn:archon:code:workspace/Package/path/file.py#Symbol")
        True
        >>> validate_arn("invalid-arn")
        False
        >>> validate_arn("")
        False
    """
    if not arn:
        return False
    return bool(ARN_PATTERN.match(arn))


def parse_arn(arn: str) -> Optional[ARNComponents]:
    """Parse an ARN string into its components.

    Extracts the type, workspace, package, path, and optional symbol
    from a valid ARN string.

    Args:
        arn: The ARN string to parse

    Returns:
        ARNComponents if the ARN is valid, None otherwise

    Examples:
        >>> result = parse_arn("arn:archon:code:workspace/Package/src/file.py#Symbol")
        >>> result.type
        'code'
        >>> result.workspace
        'workspace'
        >>> result.package
        'Package'
        >>> result.path
        'src/file.py'
        >>> result.symbol
        'Symbol'

        >>> result = parse_arn("arn:archon:doc:ws/Pkg/path/file.md")
        >>> result.symbol is None
        True
    """
    if not arn:
        return None

    match = ARN_PATTERN.match(arn)
    if not match:
        return None

    return ARNComponents(
        type=match.group(1),
        workspace=match.group(2),
        package=match.group(3),
        path=match.group(4),
        symbol=match.group(6),
    )


def validate_arn_type(arn_type: str) -> bool:
    """Validate that an ARN type is one of the allowed values.

    Args:
        arn_type: The type string to validate

    Returns:
        True if the type is valid (code, doc, k8s, infra), False otherwise

    Examples:
        >>> validate_arn_type("code")
        True
        >>> validate_arn_type("doc")
        True
        >>> validate_arn_type("invalid")
        False
    """
    return arn_type in VALID_ARN_TYPES


def build_arn(
    arn_type: str,
    workspace: str,
    package: str,
    path: str,
    symbol: Optional[str] = None,
) -> str:
    """Build an ARN string from its components.

    Constructs a valid ARN string from the provided components.
    Does not validate the components - use validate_arn() on the result
    if validation is needed.

    Args:
        arn_type: Resource type (code, doc, k8s, infra)
        workspace: Workspace identifier
        package: Package/repository name
        path: File path relative to package
        symbol: Symbol name (optional)

    Returns:
        The constructed ARN string

    Examples:
        >>> build_arn("code", "workspace", "Package", "src/file.py", "Symbol")
        'arn:archon:code:workspace/Package/src/file.py#Symbol'
        >>> build_arn("doc", "ws", "Pkg", "path/file.md")
        'arn:archon:doc:ws/Pkg/path/file.md'
    """
    base = f"arn:archon:{arn_type}:{workspace}/{package}/{path}"
    if symbol:
        return f"{base}#{symbol}"
    return base
