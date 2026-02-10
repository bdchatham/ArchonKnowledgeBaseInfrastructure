"""Property-based tests for graph service main module.

Feature: graph-service-deployment, Property 2: Database URL Construction

For any valid combination of POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
POSTGRES_USER, and POSTGRES_PASSWORD values, the constructed database URL
SHALL follow the format ``postgresql://{user}:{password}@{host}:{port}/{db}``
and contain all five input values.

**Validates: Requirements 1.5**

Source:
- src/graph/main.py
- .kiro/specs/graph-service-deployment/design.md (Property 2)
"""

import ast
import sys
import textwrap
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

# Extract build_database_url without importing main.py (which requires fastapi).
# Parse the source AST and compile only the target function.
_main_path = Path(__file__).parent.parent / "src" / "graph" / "main.py"
_source = _main_path.read_text()
_tree = ast.parse(_source)

for _node in ast.walk(_tree):
    if isinstance(_node, ast.FunctionDef) and _node.name == "build_database_url":
        _func_source = ast.get_source_segment(_source, _node)
        break
else:
    raise ImportError("build_database_url not found in src/graph/main.py")

_ns: dict = {}
exec(compile(textwrap.dedent(_func_source), "<build_database_url>", "exec"), _ns)
build_database_url = _ns["build_database_url"]


# Strategy for non-empty strings without characters that would break URL structure
safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters="/:@",
    ),
    min_size=1,
    max_size=50,
)

port_text = st.integers(min_value=1, max_value=65535).map(str)


# Feature: graph-service-deployment, Property 2: Database URL Construction
# **Validates: Requirements 1.5**
@given(
    host=safe_text,
    port=port_text,
    db=safe_text,
    user=safe_text,
    password=safe_text,
)
@settings(max_examples=200)
def test_property_database_url_construction(host, port, db, user, password):
    """For any valid combination of Postgres connection parameters,
    the constructed URL follows postgresql://{user}:{password}@{host}:{port}/{db}
    and contains all five input values.

    **Validates: Requirements 1.5**
    """
    url = build_database_url(host=host, port=port, db=db, user=user, password=password)

    assert url.startswith("postgresql://"), f"URL must start with postgresql://, got: {url}"

    expected = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    assert url == expected, f"URL format mismatch: expected {expected}, got {url}"

    assert host in url, f"host {host!r} not found in URL"
    assert port in url, f"port {port!r} not found in URL"
    assert db in url, f"db {db!r} not found in URL"
    assert user in url, f"user {user!r} not found in URL"
    assert password in url, f"password {password!r} not found in URL"
