"""Unit and property-based tests for language auto-detection.

Feature: scip-sync-pipeline

Source:
- src/sync/language_detector.py
- .kiro/specs/scip-sync-pipeline/design.md (Language Auto-Detection section)

Validates:
    Requirement 1.4
"""

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.language_detector import LANGUAGE_MANIFEST_MAP, detect_languages


class TestTypescriptDetection:
    """Tests for TypeScript/JavaScript detection via package.json and tsconfig.json.

    Validates: Requirement 1.4
    """

    def test_detects_typescript_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")

        result = detect_languages(str(tmp_path))

        assert result == ["typescript"]

    def test_detects_typescript_from_tsconfig_json(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text("{}")

        result = detect_languages(str(tmp_path))

        assert result == ["typescript"]

    def test_deduplicates_typescript_from_both_manifests(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "tsconfig.json").write_text("{}")

        result = detect_languages(str(tmp_path))

        assert result == ["typescript"]


class TestPythonDetection:
    """Tests for Python detection via pyproject.toml, setup.py, requirements.txt.

    Validates: Requirement 1.4
    """

    def test_detects_python_from_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["python"]

    def test_detects_python_from_setup_py(self, tmp_path):
        (tmp_path / "setup.py").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["python"]

    def test_detects_python_from_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["python"]

    def test_deduplicates_python_from_multiple_manifests(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "setup.py").write_text("")
        (tmp_path / "requirements.txt").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["python"]


class TestGoDetection:
    """Tests for Go detection via go.mod.

    Validates: Requirement 1.4
    """

    def test_detects_go_from_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["go"]


class TestRustDetection:
    """Tests for Rust detection via Cargo.toml.

    Validates: Requirement 1.4
    """

    def test_detects_rust_from_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["rust"]


class TestJavaDetection:
    """Tests for Java detection via pom.xml and build.gradle.

    Validates: Requirement 1.4
    """

    def test_detects_java_from_pom_xml(self, tmp_path):
        (tmp_path / "pom.xml").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["java"]

    def test_detects_java_from_build_gradle(self, tmp_path):
        (tmp_path / "build.gradle").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["java"]

    def test_deduplicates_java_from_both_manifests(self, tmp_path):
        (tmp_path / "pom.xml").write_text("")
        (tmp_path / "build.gradle").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["java"]


class TestMultiLanguageDetection:
    """Tests for packages containing multiple languages.

    Validates: Requirement 1.4
    """

    def test_detects_multiple_languages(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "pyproject.toml").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["python", "typescript"]

    def test_detects_all_five_languages(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "go.mod").write_text("")
        (tmp_path / "Cargo.toml").write_text("")
        (tmp_path / "pom.xml").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == ["go", "java", "python", "rust", "typescript"]

    def test_result_is_sorted(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("")
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "go.mod").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == sorted(result)


class TestEdgeCases:
    """Tests for edge cases and error conditions.

    Validates: Requirement 1.4
    """

    def test_returns_empty_list_for_empty_directory(self, tmp_path):
        result = detect_languages(str(tmp_path))

        assert result == []

    def test_returns_empty_list_for_nonexistent_path(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist"

        result = detect_languages(str(nonexistent))

        assert result == []

    def test_returns_empty_list_when_path_is_a_file(self, tmp_path):
        file_path = tmp_path / "some_file.txt"
        file_path.write_text("content")

        result = detect_languages(str(file_path))

        assert result == []

    def test_ignores_unrecognized_files(self, tmp_path):
        (tmp_path / "README.md").write_text("")
        (tmp_path / "Makefile").write_text("")
        (tmp_path / ".gitignore").write_text("")

        result = detect_languages(str(tmp_path))

        assert result == []

    def test_manifest_in_subdirectory_not_detected(self, tmp_path):
        """Only top-level manifest files are inspected."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "package.json").write_text("{}")

        result = detect_languages(str(tmp_path))

        assert result == []


# --- Property-Based Tests ---

import shutil
import tempfile

ALL_MANIFEST_FILES = list(LANGUAGE_MANIFEST_MAP.keys())

manifest_subsets = st.lists(
    st.sampled_from(ALL_MANIFEST_FILES),
    min_size=1,
    max_size=len(ALL_MANIFEST_FILES),
    unique=True,
)

non_manifest_filenames = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="._-"),
    min_size=1,
    max_size=30,
).filter(lambda name: name not in LANGUAGE_MANIFEST_MAP)


class TestLanguageDetectionProperty:
    """Property-based tests for language auto-detection.

    Feature: scip-sync-pipeline, Property 1: Language auto-detection from package contents

    **Validates: Requirements 1.4**
    """

    @given(manifest_files=manifest_subsets)
    @settings(max_examples=200)
    def test_returns_correct_languages_for_manifest_subset(self, manifest_files):
        """For any non-empty subset of manifest files, detect_languages returns
        exactly the expected sorted, deduplicated languages."""
        package_dir = tempfile.mkdtemp()
        try:
            for manifest in manifest_files:
                Path(package_dir, manifest).write_text("")

            expected_languages = sorted(set(
                LANGUAGE_MANIFEST_MAP[m] for m in manifest_files
            ))

            result = detect_languages(package_dir)

            assert result == expected_languages
        finally:
            shutil.rmtree(package_dir)

    @given(manifest_files=manifest_subsets)
    @settings(max_examples=200)
    def test_result_is_always_sorted(self, manifest_files):
        """The result list is always in sorted order regardless of input."""
        package_dir = tempfile.mkdtemp()
        try:
            for manifest in manifest_files:
                Path(package_dir, manifest).write_text("")

            result = detect_languages(package_dir)

            assert result == sorted(result)
        finally:
            shutil.rmtree(package_dir)

    @given(manifest_files=manifest_subsets)
    @settings(max_examples=200)
    def test_result_is_always_deduplicated(self, manifest_files):
        """The result list never contains duplicate language entries."""
        package_dir = tempfile.mkdtemp()
        try:
            for manifest in manifest_files:
                Path(package_dir, manifest).write_text("")

            result = detect_languages(package_dir)

            assert len(result) == len(set(result))
        finally:
            shutil.rmtree(package_dir)

    @given(
        filenames=st.lists(non_manifest_filenames, min_size=0, max_size=5, unique=True)
    )
    @settings(max_examples=100)
    def test_empty_result_for_no_manifest_files(self, filenames):
        """For any directory with no known manifest files, the result is empty."""
        package_dir = tempfile.mkdtemp()
        try:
            for filename in filenames:
                Path(package_dir, filename).write_text("")

            result = detect_languages(package_dir)

            assert result == []
        finally:
            shutil.rmtree(package_dir)
