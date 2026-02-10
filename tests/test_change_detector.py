"""Unit tests for ChangeDetector.

This module contains unit tests for the ChangeDetector class, testing
hash comparison, state file loading/saving, and hash update operations.

Feature: sync-service

Source:
- src/sync/change_detector.py
- .kiro/specs/sync-service/design.md

Validates:
    Requirements 7.1, 7.2
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.change_detector import ChangeDetector
from src.sync.models import HashState


class TestHashComparison:
    """Tests for has_changed and get_stored_hash methods.

    Validates: Requirement 7.1
    """

    def test_has_changed_returns_true_when_no_stored_hash(self, tmp_path):
        """Test that has_changed returns True when no stored hash exists.

        Validates: Requirement 7.1
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        result = detector.has_changed("TestPackage", "abc123")

        assert result is True

    def test_has_changed_returns_true_when_hash_differs(self, tmp_path):
        """Test that has_changed returns True when hash differs from stored.

        Validates: Requirement 7.1
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "TestPackage": {
                    "index_hash": "old_hash_123",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.has_changed("TestPackage", "new_hash_456")

        assert result is True

    def test_has_changed_returns_false_when_hash_matches(self, tmp_path):
        """Test that has_changed returns False when hash matches stored.

        Validates: Requirement 7.1
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "TestPackage": {
                    "index_hash": "same_hash_123",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.has_changed("TestPackage", "same_hash_123")

        assert result is False

    def test_get_stored_hash_returns_none_when_no_state_file(self, tmp_path):
        """Test that get_stored_hash returns None when state file doesn't exist.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        result = detector.get_stored_hash("TestPackage")

        assert result is None

    def test_get_stored_hash_returns_none_when_package_not_found(self, tmp_path):
        """Test that get_stored_hash returns None when package not in state.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "OtherPackage": {
                    "index_hash": "hash_123",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.get_stored_hash("TestPackage")

        assert result is None

    def test_get_stored_hash_returns_hash_when_found(self, tmp_path):
        """Test that get_stored_hash returns hash when package exists in state.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "TestPackage": {
                    "index_hash": "expected_hash_123",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.get_stored_hash("TestPackage")

        assert result == "expected_hash_123"


class TestStateFileLoading:
    """Tests for load_state method.

    Validates: Requirement 7.2
    """

    def test_load_state_returns_empty_dict_when_file_missing(self, tmp_path):
        """Test that load_state returns empty dict when state file doesn't exist.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        result = detector.load_state()

        assert result == {}

    def test_load_state_returns_empty_dict_on_invalid_json(self, tmp_path):
        """Test that load_state returns empty dict when JSON is invalid.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("not valid json {{{")

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.load_state()

        assert result == {}

    def test_load_state_returns_empty_dict_when_packages_key_missing(self, tmp_path):
        """Test that load_state returns empty dict when 'packages' key is missing.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"other_key": "value"}))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.load_state()

        assert result == {}

    def test_load_state_parses_single_package(self, tmp_path):
        """Test that load_state correctly parses a single package.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "TestPackage": {
                    "index_hash": "hash_123",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.load_state()

        assert len(result) == 1
        assert "TestPackage" in result
        assert isinstance(result["TestPackage"], HashState)
        assert result["TestPackage"].package == "TestPackage"
        assert result["TestPackage"].index_hash == "hash_123"
        assert result["TestPackage"].last_synced == "2024-01-15T10:30:00Z"

    def test_load_state_parses_multiple_packages(self, tmp_path):
        """Test that load_state correctly parses multiple packages.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "PackageA": {
                    "index_hash": "hash_a",
                    "last_synced": "2024-01-15T10:30:00Z",
                },
                "PackageB": {
                    "index_hash": "hash_b",
                    "last_synced": "2024-01-15T11:00:00Z",
                },
                "PackageC": {
                    "index_hash": "hash_c",
                    "last_synced": "2024-01-15T11:30:00Z",
                },
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.load_state()

        assert len(result) == 3
        assert result["PackageA"].index_hash == "hash_a"
        assert result["PackageB"].index_hash == "hash_b"
        assert result["PackageC"].index_hash == "hash_c"

    def test_load_state_handles_missing_fields_gracefully(self, tmp_path):
        """Test that load_state handles missing fields with empty strings.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "TestPackage": {}
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        result = detector.load_state()

        assert len(result) == 1
        assert result["TestPackage"].index_hash == ""
        assert result["TestPackage"].last_synced == ""


class TestStateFileSaving:
    """Tests for save_state method.

    Validates: Requirement 7.2
    """

    def test_save_state_creates_parent_directories(self, tmp_path):
        """Test that save_state creates parent directories if they don't exist.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / "deep" / "nested" / "path" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        state = {
            "TestPackage": HashState(
                package="TestPackage",
                index_hash="hash_123",
                last_synced="2024-01-15T10:30:00Z",
            )
        }

        detector.save_state(state)

        assert state_file.exists()
        assert state_file.parent.exists()

    def test_save_state_writes_valid_json(self, tmp_path):
        """Test that save_state writes valid JSON to the state file.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        state = {
            "TestPackage": HashState(
                package="TestPackage",
                index_hash="hash_123",
                last_synced="2024-01-15T10:30:00Z",
            )
        }

        detector.save_state(state)

        content = state_file.read_text()
        parsed = json.loads(content)

        assert "packages" in parsed
        assert "TestPackage" in parsed["packages"]
        assert parsed["packages"]["TestPackage"]["index_hash"] == "hash_123"
        assert parsed["packages"]["TestPackage"]["last_synced"] == "2024-01-15T10:30:00Z"

    def test_save_state_overwrites_existing_file(self, tmp_path):
        """Test that save_state overwrites existing state file.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"packages": {"OldPackage": {"index_hash": "old"}}}))

        detector = ChangeDetector(state_file=str(state_file))

        state = {
            "NewPackage": HashState(
                package="NewPackage",
                index_hash="new_hash",
                last_synced="2024-01-15T10:30:00Z",
            )
        }

        detector.save_state(state)

        content = state_file.read_text()
        parsed = json.loads(content)

        assert "OldPackage" not in parsed["packages"]
        assert "NewPackage" in parsed["packages"]

    def test_save_state_preserves_multiple_packages(self, tmp_path):
        """Test that save_state correctly saves multiple packages.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        state = {
            "PackageA": HashState(
                package="PackageA",
                index_hash="hash_a",
                last_synced="2024-01-15T10:30:00Z",
            ),
            "PackageB": HashState(
                package="PackageB",
                index_hash="hash_b",
                last_synced="2024-01-15T11:00:00Z",
            ),
        }

        detector.save_state(state)

        content = state_file.read_text()
        parsed = json.loads(content)

        assert len(parsed["packages"]) == 2
        assert parsed["packages"]["PackageA"]["index_hash"] == "hash_a"
        assert parsed["packages"]["PackageB"]["index_hash"] == "hash_b"

    def test_save_state_empty_state_writes_empty_packages(self, tmp_path):
        """Test that save_state with empty state writes empty packages dict.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        detector.save_state({})

        content = state_file.read_text()
        parsed = json.loads(content)

        assert parsed == {"packages": {}}


class TestHashUpdate:
    """Tests for update_hash method.

    Validates: Requirement 7.2
    """

    def test_update_hash_creates_new_entry(self, tmp_path):
        """Test that update_hash creates a new entry for a new package.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        detector.update_hash("NewPackage", "new_hash_123")

        state = detector.load_state()
        assert "NewPackage" in state
        assert state["NewPackage"].index_hash == "new_hash_123"

    def test_update_hash_updates_existing_entry(self, tmp_path):
        """Test that update_hash updates an existing package entry.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "TestPackage": {
                    "index_hash": "old_hash",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        detector.update_hash("TestPackage", "updated_hash")

        state = detector.load_state()
        assert state["TestPackage"].index_hash == "updated_hash"

    def test_update_hash_sets_timestamp(self, tmp_path):
        """Test that update_hash sets the last_synced timestamp.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        fixed_time = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        with patch("src.sync.change_detector.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_time
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            detector.update_hash("TestPackage", "hash_123")

        state = detector.load_state()
        assert state["TestPackage"].last_synced == "2024-06-15T12:30:45+00:00"

    def test_update_hash_preserves_other_packages(self, tmp_path):
        """Test that update_hash preserves other packages in state.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_data = {
            "packages": {
                "ExistingPackage": {
                    "index_hash": "existing_hash",
                    "last_synced": "2024-01-15T10:30:00Z",
                }
            }
        }
        state_file.write_text(json.dumps(state_data))

        detector = ChangeDetector(state_file=str(state_file))

        detector.update_hash("NewPackage", "new_hash")

        state = detector.load_state()
        assert len(state) == 2
        assert state["ExistingPackage"].index_hash == "existing_hash"
        assert state["NewPackage"].index_hash == "new_hash"

    def test_update_hash_persists_to_file(self, tmp_path):
        """Test that update_hash persists changes to the state file.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        detector.update_hash("TestPackage", "persisted_hash")

        new_detector = ChangeDetector(state_file=str(state_file))
        stored_hash = new_detector.get_stored_hash("TestPackage")

        assert stored_hash == "persisted_hash"


class TestInitialization:
    """Tests for ChangeDetector initialization.

    Validates: Requirement 7.2
    """

    def test_default_state_file_path(self):
        """Test that default state file path is .archon/sync-state.json."""
        detector = ChangeDetector()

        assert detector.state_file == Path(".archon/sync-state.json")

    def test_custom_state_file_path(self, tmp_path):
        """Test that custom state file path is accepted."""
        custom_path = tmp_path / "custom" / "state.json"
        detector = ChangeDetector(state_file=str(custom_path))

        assert detector.state_file == custom_path

    def test_state_file_is_path_object(self, tmp_path):
        """Test that state_file is stored as a Path object."""
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        assert isinstance(detector.state_file, Path)


class TestRoundTrip:
    """Tests for round-trip save/load operations.

    Validates: Requirement 7.2
    """

    def test_save_then_load_preserves_data(self, tmp_path):
        """Test that saving and loading preserves all data.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        original_state = {
            "PackageA": HashState(
                package="PackageA",
                index_hash="hash_a_123",
                last_synced="2024-01-15T10:30:00Z",
            ),
            "PackageB": HashState(
                package="PackageB",
                index_hash="hash_b_456",
                last_synced="2024-01-15T11:00:00Z",
            ),
        }

        detector.save_state(original_state)
        loaded_state = detector.load_state()

        assert len(loaded_state) == 2
        assert loaded_state["PackageA"].package == "PackageA"
        assert loaded_state["PackageA"].index_hash == "hash_a_123"
        assert loaded_state["PackageA"].last_synced == "2024-01-15T10:30:00Z"
        assert loaded_state["PackageB"].package == "PackageB"
        assert loaded_state["PackageB"].index_hash == "hash_b_456"
        assert loaded_state["PackageB"].last_synced == "2024-01-15T11:00:00Z"

    def test_multiple_updates_accumulate(self, tmp_path):
        """Test that multiple update_hash calls accumulate correctly.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        detector.update_hash("Package1", "hash1")
        detector.update_hash("Package2", "hash2")
        detector.update_hash("Package3", "hash3")

        state = detector.load_state()

        assert len(state) == 3
        assert state["Package1"].index_hash == "hash1"
        assert state["Package2"].index_hash == "hash2"
        assert state["Package3"].index_hash == "hash3"

    def test_update_same_package_multiple_times(self, tmp_path):
        """Test that updating the same package multiple times keeps latest.

        Validates: Requirement 7.2
        """
        state_file = tmp_path / ".archon" / "sync-state.json"
        detector = ChangeDetector(state_file=str(state_file))

        detector.update_hash("TestPackage", "hash_v1")
        detector.update_hash("TestPackage", "hash_v2")
        detector.update_hash("TestPackage", "hash_v3")

        state = detector.load_state()

        assert len(state) == 1
        assert state["TestPackage"].index_hash == "hash_v3"
