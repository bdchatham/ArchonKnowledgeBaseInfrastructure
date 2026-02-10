"""Language auto-detection for SCIP indexing.

Inspects a package directory for known manifest files and returns
the programming languages detected. The SCIP runner uses this to
select the appropriate indexer (scip-typescript, scip-python, etc.).

Source:
- .kiro/specs/scip-sync-pipeline/design.md (Language Auto-Detection section)
- .kiro/specs/scip-sync-pipeline/requirements.md (Requirement 1.4)
"""

from pathlib import Path

LANGUAGE_MANIFEST_MAP: dict[str, str] = {
    "package.json": "typescript",
    "tsconfig.json": "typescript",
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java",
    "build.gradle": "java",
}


def detect_languages(package_path: str) -> list[str]:
    """Detect programming languages in a package by inspecting manifest files.

    Scans the given directory for known manifest files (e.g., package.json,
    pyproject.toml, go.mod) and returns the corresponding language identifiers.
    A single package may contain multiple languages.

    Args:
        package_path: Filesystem path to the package directory.

    Returns:
        Sorted, deduplicated list of detected language identifiers.
        Returns an empty list if no known manifest files are found.
    """
    directory = Path(package_path)
    if not directory.is_dir():
        return []

    detected: set[str] = set()
    for manifest_file, language in LANGUAGE_MANIFEST_MAP.items():
        if (directory / manifest_file).exists():
            detected.add(language)

    return sorted(detected)
